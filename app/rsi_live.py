"""Launch an RSI batch or review an archived observation stream without starting a game."""

import argparse
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import sys
from threading import Event
from time import monotonic

if __package__ in {None, ""}:
    # Keep this entry point runnable from both the CLI and unittest imports.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rsi import build_config, v2_loop
from app.rsi.adapter import DEFAULT_MATCH_TIMEOUT, DEFAULT_SPEED, NativeV17Adapter
from app.rsi.adjudication import MAP_STARTS
from app.rsi.agentic_review import AgenticReviewClient
from app.rsi.evolution import active_pointer, initialize_batch, normalized_match, read_json, review_round
from app.rsi.model_client import ModelCircuit, ModelClient, USAGE_FIELDS, write_json
from app.rsi.runner import DECISION_SOURCES, LiveRunner, MAX_AGE_SECONDS, MAX_FRAMES, STALEMATE_ZERO_FRAMES
from app.rsi.validator import read_v17_archive, replay_version


ROOT = Path(__file__).resolve().parents[1]
MAX_CONSECUTIVE_MATCH_ERRORS = 3
PREFLIGHT_PROMPT = ('Connectivity check before a match. Return exactly this JSON object and nothing else: '
                    '{"ok": true}')
HISTORICAL_FAILURES = {"model_error", "invalid", "invalid_or_failed_model_turn",
                       "no_native_receipt", "not_dispatched", "nonzero_exit",
                       "model_timeout", "bad_response"}


def positive_integer(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return number


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=positive_integer, default=1)
    parser.add_argument("--matches-per-round", type=positive_integer, default=1)
    parser.add_argument("--parallel", type=positive_integer, default=1,
                        help="matches of one round played at the same time (default: %(default)s)")
    parser.add_argument("--speed", type=positive_integer, default=DEFAULT_SPEED,
                        help="engine milliseconds per frame (default: %(default)s)")
    parser.add_argument("--max-frames", type=positive_integer, default=MAX_FRAMES,
                        help="in-game frame limit (default: %(default)s)")
    parser.add_argument("--stalemate-zero-frames", type=positive_integer, default=STALEMATE_ZERO_FRAMES)
    parser.add_argument("--map", default="Destination")
    parser.add_argument("--opponent", default="PylonPuller",
                        help="one recorded opponent, or a comma-separated list that matches cycle through")
    parser.add_argument("--match-timeout", type=positive_integer, default=DEFAULT_MATCH_TIMEOUT,
                        help="wall-clock seconds from game start (default: %(default)s)")
    parser.add_argument("--batch-dir", type=Path)
    parser.add_argument("--live-model", default="gpt-6-luna")
    parser.add_argument("--review-model", default="gpt-6-luna")
    parser.add_argument("--live-timeout", type=positive_integer, default=MAX_AGE_SECONDS)
    parser.add_argument("--review-timeout", type=positive_integer, default=180)
    parser.add_argument("--backend", choices=("codex", "openai"), default="codex")
    parser.add_argument("--endpoint", help="OpenAI-compatible base URL or chat/completions endpoint")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY",
                        help="environment variable holding the HTTP API key (never archived)")
    parser.add_argument("--reasoning-effort", default="low",
                        help="live reasoning effort. Use omit if the HTTP endpoint does not accept it")
    parser.add_argument("--review-mode", choices=("single", "agentic"), default="single",
                        help="agentic lets the reviewer investigate through L0 tools (HTTP backend only)")
    parser.add_argument("--evolution-mode", choices=("v1", "v2"), default="v1",
                        help="v2 enables evolvable MacroBot.cpp + Champion/Challenger L0 selection")
    parser.add_argument("--review-reasoning-effort", default="medium",
                        help="review reasoning effort. Use omit if the HTTP endpoint does not accept it")
    parser.add_argument("--codex-executable", default="codex")
    parser.add_argument("--no-evolve", action="store_true")
    parser.add_argument("--seed-version", type=Path, metavar="DIR")
    parser.add_argument("--offline-evolve-test", type=Path, metavar="OBSERVATIONS_JSON")
    parser.add_argument("--offline-candidate-experiment", action="store_true",
                        help="request a justified, non-tactical candidate to exercise the offline gates")
    return parser


def _client(args, model, timeout, circuit, *, reasoning_effort):
    return ModelClient(args.backend, model, reasoning_effort, args.endpoint, timeout, api_key=_api_key(args),
                       codex_executable=args.codex_executable, circuit=circuit)


def _api_key(args):
    return os.environ.get(args.api_key_env) if args.backend == "openai" else None


class CandidateExperimentClient(ModelClient):
    def call(self, prompt, payload, archive):
        diagnostic = self._diagnostic(payload, archive)
        facts = payload["facts"]
        if facts[-1]["event"] != "current_policy_shadow_replay":
            facts.append({"fact_id": len(facts) + 1, "match_number": 1, "event": "current_policy_shadow_replay",
                          **diagnostic})
        return super().call(
            prompt + "\nThis separately labeled offline experiment tests the candidate gate on a "
            "non-tactical observer change. The facts include a full shadow replay of the current "
            "observer, including actual wake frames while no own workers or army were present. "
            "You may use that replay as direct evidence of observer behavior. Do not infer a "
            "change in live outcomes or override L0 cutoff. Consider whether the measured wake "
            "pattern justifies a narrow observational or wake-up improvement. Edit only observer.py "
            "if warranted. Do not emit any orders or alter strategic or tactical rules. Keep if "
            "the replay gives insufficient evidence even for this narrow experiment.",
            payload, archive,
        )

    def _diagnostic(self, payload, archive):
        path = archive.parent / "experiment-parent-replay.json"
        previous = read_json(path)
        if previous is not None:
            return previous["diagnostic"]
        observations = read_json(archive.parent / "observations.json")
        current = archive.parent.parent / payload["active_version"]["path"]
        replay = replay_version(current, observations)
        by_frame = {row["frame"]: row for row in observations}
        no_forces = [row for row in observations if row["workers"] == row["army"] == 0]
        wakes_without_forces = []
        for wake in replay["wake_frames"]:
            row = by_frame[wake["frame"]]
            if row["workers"] == row["army"] == 0:
                wakes_without_forces.append({
                    **wake, "workers": row["workers"], "army": row["army"],
                    "visible_enemies": row["visible_enemies"],
                })
        diagnostic = {
            "observations_replayed": replay["observations_replayed"],
            "wake_llm_count": replay["wake_llm_count"],
            "wake_reasons": replay["wake_reasons"],
            "reflex_trigger_count": replay["reflex_trigger_count"],
            "zero_force_frames_sampled": len(no_forces),
            "zero_force_first_frame": no_forces[0]["frame"] if no_forces else None,
            "zero_force_last_frame": no_forces[-1]["frame"] if no_forces else None,
            "zero_force_wakes": wakes_without_forces,
        }
        write_json(path, {"diagnostic": diagnostic, "replay": replay})
        return diagnostic


def _historical_usage(archive):
    if not archive:
        return {}
    completed = [event for event in archive.get("events", []) if event.get("type") == "turn.completed"]
    usage = (completed[-1].get("usage") or {}) if completed else archive
    return {key: usage[key] for key in USAGE_FIELDS if type(usage.get(key)) is int and usage[key] >= 0}


def _historical_metrics(parent, timeline, sources):
    tokens = dict.fromkeys(USAGE_FIELDS, 0)
    missing = dict.fromkeys(USAGE_FIELDS, 0)
    for row in timeline:
        call = row.get("call")
        if call:
            if Path(call).name != call or not call.startswith("decision-") or not call.endswith(".json"):
                raise ValueError("unexpected archived decision log name")
            usage = _historical_usage(read_json(parent / call))
            for key in USAGE_FIELDS:
                tokens[key] += usage.get(key, 0)
                missing[key] += key not in usage
        status = row.get("status", "")
        if status == "kept_without_key":
            sources["llm_keep_current"] += 1
        elif status in {"native_receipt", "key_posted", "ended_before_receipt"}:
            sources["llm_key"] += 1
        elif status.startswith("expired"):
            sources["expired"] += 1
        elif status in HISTORICAL_FAILURES:
            sources["invalid"] += 1
    return {key: count if not missing[key] else None for key, count in tokens.items()}, missing


def _seconds(result):
    if result.get("seconds") is not None:
        return result["seconds"]
    if result.get("started_at") and result.get("ended_at"):
        return round((datetime.fromisoformat(result["ended_at"])
                      - datetime.fromisoformat(result["started_at"])).total_seconds(), 3)
    return None


def _receipts(path):
    with path.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            if row["event"] == "command":
                yield row


def _offline_match(path, run):
    parent = path.parent
    raw = read_json(path)
    if not isinstance(raw, list) or not raw:
        raise ValueError("expected a nonempty JSON array of side-visible observations")
    timeline = read_json(parent / "timeline.json", [])
    errors = read_json(parent / "runtime-errors.json", [])
    match = read_json(parent / "match-error.json") or read_json(parent / "match.json", {})
    sources = dict.fromkeys(DECISION_SOURCES, 0)
    live_calls = sum(bool(row.get("call")) for row in timeline)
    missing = {}
    host_log = parent / "bwapi-data" / "write" / "commander-layer.jsonl"
    historical = host_log.is_file() and "units" not in raw[0]
    if historical:
        raw, _ = read_v17_archive(path)
        write_json(run / "native-receipts.json", list(_receipts(host_log)))
        usage, missing = _historical_metrics(parent, timeline, sources)
    else:
        usage = dict.fromkeys(USAGE_FIELDS, 0 if not live_calls else None)
        for name, value in match.get("decision_sources", {}).items():
            sources[name] = value
        missing = match.get("usage_missing", dict.fromkeys(USAGE_FIELDS, live_calls))
    cutoff = read_json(parent / "cutoff.json", match.get("cutoff"))
    record = {
        "match_number": 1, "state": match.get("state", "archive_only"),
        "winner": match.get("winner"), "cutoff": cutoff, "frame": match.get("frame"),
        "seconds": _seconds(match), "source": str(path), "map": match.get("map"),
        "opponent": match.get("opponent"), "policy_version": "legacy-v17" if historical else match.get("policy_version"),
        "observations": len(raw), "decision_sources": sources, "live_calls": live_calls,
        "tokens": {
            "live_input": match.get("tokens", {}).get("live_input", usage["input_tokens"]),
            "live_output": match.get("tokens", {}).get("live_output", usage["output_tokens"]),
            "review_input": 0, "review_output": 0,
        },
        "usage_missing": missing,
        "historical_live_cached_input_tokens": usage["cached_input_tokens"],
    }
    for name in ("adjudicated_outcome", "lock_defense", "army_peaks", "model_circuit"):
        if name in match:
            record[name] = match[name]
    if record["state"] in {"manual_stop", "model_circuit_open"}:
        record.update(winner=None, natural_outcome="unknown", adjudicated_outcome=None)
    elif cutoff or match.get("state") == "cutoff":
        record.update(state="cutoff", winner=None)
        if cutoff:
            request = cutoff.get("latest_native_before_request", {})
            record["cutoff_frame"] = cutoff.get("frame", request.get("frame"))
    write_json(run / "observations.json", raw)
    write_json(run / "timeline.json", timeline)
    write_json(run / "runtime-errors.json", errors)
    write_json(run / "match.json", record)
    return record


def _run_offline(args, batch, review_model):
    record = _offline_match(args.offline_evolve_test.resolve(strict=True), batch / "match-1")
    result = review_round([record], batch, review_model, round_number=1, no_evolve=args.no_evolve,
                          allow_archive_only=True,
                          allowed_edits={"observer.py"} if args.offline_candidate_experiment else None)
    print(f"{result['validation']}: {result['from_version']} -> {result['to_version']}")
    return 1 if result["validation"] in {"rejected", "model_error", "circuit_open"} else 0


def _opponents(args):
    return args.opponent.split(",")


def _opponent_for(args, number):
    """Match N plays the Nth listed opponent, cycling, so a round of 2 meets 2 different bots."""
    opponents = _opponents(args)
    return opponents[(number - 1) % len(opponents)]


def _failed_pairs(map_name, opponents):
    """Pairs that failed the recorded v17 compatibility check (validation/compatibility.json)."""
    report = read_json(ROOT / "validation/compatibility.json", {})
    failed = {(pair["opponent"], pair["map"]) for pair in report.get("pairs", []) if not pair["passed"]}
    return [name for name in opponents if (name, map_name) in failed]


def _next_wave(args, pending):
    """Split off up to --parallel matches to start together. A Java opponent plays alone.

    JBWAPI joins the first instance in BWAPI's shared game table, and other bots' clients list
    themselves there too. Next to another game, PurpleWave took over HladHammer's player in that
    game while its own player stood idle (matches/rsi/20261124-153350-624112, matches 1 and 2).
    """
    roster = read_json(ROOT / "opponents/recorded-roster.json", [])
    java = {bot["name"] for bot in roster if bot.get("kind") == "java"}
    wave = []
    for number in pending[:args.parallel]:
        alone = _opponent_for(args, number) in java
        if alone and wave:
            break
        wave.append(number)
        if alone:
            break
    return wave, pending[len(wave):]


def _play_one(args, batch, version, model, number, *, stop_requested=None):
    run = batch / f"match-{number}"
    started = monotonic()
    opponent = _opponent_for(args, number)
    # main() holds the machine-wide game lock for the whole batch, so matches can share it.
    build_config_text = (version / build_config.FILENAME).read_text(encoding="utf-8")
    build_config.parse(build_config_text)
    runner = LiveRunner(NativeV17Adapter(run, args.map, opponent, args.speed,
                                         match_timeout=args.match_timeout, hold_lock=False,
                                         build_config=build_config_text),
                        model, version, run, max_frames=args.max_frames,
                        stalemate_zero_frames=args.stalemate_zero_frames, stop_requested=stop_requested)
    try:
        result = runner.run()
    except Exception as error:  # One broken game is recorded and the batch moves on.
        result = {"state": "error", "winner": None, "natural_outcome": "unknown",
                  "frame": runner.frame, "seconds": round(monotonic() - started, 3),
                  "policy_version": version.name, "decision_sources": runner.decision_sources,
                  "usage_missing": runner.usage_missing, "tokens": runner.tokens,
                  "runtime_error_count": len(runner.runtime_errors),
                  "error": f"{type(error).__name__}: {error}"}
    result = normalized_match({**result, "map": args.map, "opponent": opponent,
                               "match_number": number, "live_calls": runner.call_number})
    filename = "match-error.json" if result["state"] == "error" else "match.json"
    write_json(run / filename, result)
    return result


def _play_wave(args, batch, version, model, numbers, stop_event):
    """Play these matches at the same time and return their results in match order."""
    with ThreadPoolExecutor(len(numbers), thread_name_prefix="rsi-match") as pool:
        futures = [pool.submit(_play_one, args, batch, version, model, number, stop_requested=stop_event.is_set)
                   for number in numbers]
        # Short waits keep the main thread free to run the Ctrl+C handler.
        while wait(futures, timeout=0.5).not_done:
            pass
    return [future.result() for future in futures]


def _preflight(batch, clients):
    """Make one tiny call per client before any game starts."""
    results = {}
    for name, client in clients.items():
        report = client.call(PREFLIGHT_PROMPT, {"check": name}, batch / f"preflight-{name}.json")
        decoded = report.get("decoded")
        results[name] = {"ok": isinstance(decoded, dict) and decoded.get("ok") is True,
                         **{key: report.get(key) for key in ("failure_kind", "error", "seconds", "model", "backend")}}
    write_json(batch / "preflight.json", results)
    return all(row["ok"] for row in results.values())


def _stop_batch(batch, state, **details):
    index = read_json(batch / "batch.json")
    index.update(state=state, **details)
    write_json(batch / "batch.json", index)


def _run_live(args, batch, live_model, review_model, stop_event):
    match_errors = 0
    for round_number in range(1, args.rounds + 1):
        version = batch / active_pointer(batch)["path"]
        first = (round_number - 1) * args.matches_per_round + 1
        pending = list(range(first, first + args.matches_per_round))
        matches = []
        while pending:
            if stop_event.is_set() or live_model.circuit.open or match_errors >= MAX_CONSECUTIVE_MATCH_ERRORS:
                break
            wave, pending = _next_wave(args, pending)
            results = _play_wave(args, batch, version, live_model, wave, stop_event)
            for match in results:
                matches.append(match)
                match_errors = match_errors + 1 if match["state"] == "error" else 0
            if any(match["state"] in {"model_circuit_open", "manual_stop"} for match in results):
                break
        if not matches:
            index = read_json(batch / "batch.json")
            if stop_event.is_set():
                index["state"] = "manual_stop"
            else:
                circuit = live_model.circuit
                index.update(state="model_circuit_open", model_circuit={"open": True, "reason": circuit.reason,
                                                                         "failures": circuit.failures})
            write_json(batch / "batch.json", index)
            return 130 if index["state"] == "manual_stop" else 1
        review = review_round(matches, batch, review_model, round_number=round_number, no_evolve=args.no_evolve,
                              stop_requested=stop_event.is_set)
        print(f"round {round_number}: {review['validation']} ({review['to_version']})", flush=True)
        if live_model.circuit.open:
            print(live_model.circuit.reason, flush=True)
            return 1
        if stop_event.is_set() or any(match["state"] == "manual_stop" for match in matches):
            return 130
        if review["validation"] == "model_unavailable":
            print(review["validation_error"], flush=True)
            return 1
        if match_errors >= MAX_CONSECUTIVE_MATCH_ERRORS:
            _stop_batch(batch, "repeated_match_errors", consecutive_match_errors=match_errors)
            print(f"{match_errors} consecutive matches failed; stopping the batch", flush=True)
            return 1
    _stop_batch(batch, "completed")
    return 0


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    if args.backend == "openai" and not args.endpoint:
        parser.error("--backend openai requires --endpoint")
    if args.backend == "openai" and not os.environ.get(args.api_key_env):
        parser.error(f"environment variable {args.api_key_env} is empty")
    if args.backend == "codex" and "omit" in {args.reasoning_effort, args.review_reasoning_effort}:
        parser.error("reasoning effort omit is for HTTP endpoints")
    if args.offline_evolve_test and (args.rounds != 1 or args.matches_per_round != 1):
        parser.error("--offline-evolve-test reviews one archive in one round")
    if args.review_mode == "agentic" and (args.backend != "openai" or args.offline_candidate_experiment):
        parser.error("--review-mode agentic requires --backend openai and no candidate experiment")
    if args.evolution_mode == "v2":
        if args.backend != "openai" or args.offline_evolve_test or args.offline_candidate_experiment:
            parser.error("--evolution-mode v2 requires live --backend openai")
        if "PurpleWave" in _opponents(args):
            parser.error("--evolution-mode v2 forbids PurpleWave in the core opponent pool")
    if args.offline_candidate_experiment and (not args.offline_evolve_test or args.no_evolve):
        parser.error("--offline-candidate-experiment requires offline review with evolution enabled")
    if args.offline_evolve_test is None:
        sys.path.insert(0, str(ROOT / "app"))
        import matches
        import portable_runtime
        if game_problem := portable_runtime.game_error(matches.GAME):
            parser.error(f"StarCraft folder {matches.GAME} is not usable: {game_problem} "
                         "Set it in local.settings.json (game) or STARCRAFT_GAME.")
        unknown = [name for name in _opponents(args) if name not in matches.OPPONENTS]
        if args.map not in matches.MAPS or unknown:
            parser.error(f"unknown map or recorded opponent (map {args.map!r}, opponents {unknown})")
        if failed := _failed_pairs(args.map, _opponents(args)):
            parser.error(f"{', '.join(failed)} failed the v17 compatibility check on {args.map}")
        if args.map not in MAP_STARTS:
            parser.error(f"map {args.map!r} has no start-location entry in app/rsi/adjudication.MAP_STARTS")
    batch = (args.batch_dir or ROOT / "matches/rsi" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")).resolve()
    settings = {name: str(value) if isinstance(value, Path) else value for name, value in vars(args).items()
                if name != "batch_dir"}
    if args.evolution_mode == "v2":
        settings["core_opponents"] = _opponents(args)
    else:
        initialize_batch(batch, args.seed_version, settings)
        print(batch, flush=True)
    circuit = ModelCircuit()
    if args.evolution_mode == "v2":
        reviewer = v2_loop.V2AgenticReviewClient(
            args.review_model, args.review_reasoning_effort, args.endpoint,
            args.review_timeout, api_key=_api_key(args), circuit=circuit,
        )
    elif args.offline_candidate_experiment:
        reviewer = CandidateExperimentClient(args.backend, args.review_model, args.review_reasoning_effort,
                                             args.endpoint, args.review_timeout, api_key=_api_key(args),
                                             codex_executable=args.codex_executable, circuit=circuit)
    elif args.review_mode == "agentic":
        reviewer = AgenticReviewClient(args.review_model, args.review_reasoning_effort, args.endpoint,
                                       args.review_timeout, api_key=_api_key(args), circuit=circuit)
    else:
        reviewer = _client(args, args.review_model, args.review_timeout, circuit,
                           reasoning_effort=args.review_reasoning_effort)
    if args.offline_evolve_test:
        return _run_offline(args, batch, reviewer)
    stop_event = Event()
    previous_handler = signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    try:
        live = _client(args, args.live_model, args.live_timeout, circuit, reasoning_effort=args.reasoning_effort)
        # The review check uses a plain call with the review model and effort, also for agentic review.
        review_check = _client(args, args.review_model, args.review_timeout, circuit,
                               reasoning_effort=args.review_reasoning_effort)
        clients = {"live": live} if args.no_evolve else {"live": live, "review": review_check}
        # Held for the whole batch: the desktop app and other batches cannot start a game meanwhile.
        with matches.game_lock():
            if args.evolution_mode == "v2":
                v2_loop.initialize_v2_batch(batch, ROOT, settings)
                print(batch, flush=True)
            if not _preflight(batch, clients):
                _stop_batch(batch, "preflight_failed")
                print(f"model preflight failed; see {batch / 'preflight.json'}", flush=True)
                return 1
            if args.evolution_mode == "v2":
                return v2_loop.run_v2_live(args, batch, ROOT, live, reviewer, stop_event)
            return _run_live(args, batch, live, reviewer, stop_event)
    finally:
        signal.signal(signal.SIGINT, previous_handler)


if __name__ == "__main__":
    sys.exit(main())
