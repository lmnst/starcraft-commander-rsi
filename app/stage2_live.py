"""Run two full native v17 matches with live Codex decisions and policy handoff."""
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from difflib import unified_diff
import argparse
import json
import math
from pathlib import Path
import subprocess
from threading import Event, Thread
import time

import matches
from stage1 import KEYMAP, game_window, post_key
from stage2 import save_version


MODEL = "gpt-6-luna"
SPEED = 67
MAX_AGE_SECONDS = 24
MIN_FRAMES = 240
PERIOD_FRAMES = 720
KEYS = {
    "1": "Rescue the current scouting Probe, or a threatened Probe under mining lock.",
    "2": "Lock Probe mining, blocking attack, chase and prior scout commands. Building and gas continue.",
    "3": "Select squad scope. Requires at least 8 healthy army units; form about a quarter and keep 6 healthy in reserve. Main army defends.",
    "4": "Select whole army scope for subsequent orders; existing tasks continue.",
    "5": "Attack nearby enemy buildings or a known enemy expansion in selected scope.",
    "6": "Clear the enemy main base area in selected scope.",
    "7": "Guard local positions in selected scope; fight nearby combat units and static defenses, ignore workers. Repeat re-anchors.",
    "8": "Assemble at the current front in selected scope and wait for reinforcements. Hold positions and fire only in range.",
    "9": "Retreat safely then assemble and wait in selected scope.",
    "0": "Recall all combat units to defend own bases, clearing squad scope. Mining lock stays active.",
    "-": "Globally clear manual tasks and Probe overrides and return control to the baseline.",
    "keep_current": "Continue current tasks or baseline behavior without sending a key.",
}
INITIAL_STRATEGY = (
    "Let the baseline produce, expand and handle routine opening play. Intervene when visible pressure, "
    "health or a concrete objective supports it. Consider separate squad tasks when enough healthy units exist."
)
# IDs from BWAPI UnitType, limited to the units needed for this Protoss match.
UNIT_TYPES = {
    60: "Corsair", 61: "Dark Templar", 63: "Dark Archon", 64: "Probe", 65: "Zealot",
    66: "Dragoon", 67: "High Templar", 68: "Archon", 69: "Shuttle", 70: "Scout",
    71: "Arbiter", 72: "Carrier", 73: "Interceptor", 83: "Reaver", 84: "Observer",
    85: "Scarab", 154: "Nexus", 155: "Robotics Facility", 156: "Pylon",
    157: "Assimilator", 159: "Observatory", 160: "Gateway", 162: "Photon Cannon",
    163: "Citadel of Adun", 164: "Cybernetics Core", 165: "Templar Archives",
    166: "Forge", 167: "Stargate", 169: "Fleet Beacon", 170: "Arbiter Tribunal",
    171: "Robotics Support Bay", 172: "Shield Battery",
}
ECONOMY = {"Probe", "Nexus", "Pylon", "Assimilator", "Gateway", "Forge", "Cybernetics Core"}
TRANSITIONS = {
    "base_notice", "building_complete", "assembly_point", "assembly_refuge", "rendezvous_point",
    "squad_rebalance", "detection_guard", "detection_resumed", "target_selection",
}


def model_call(prompt, data, archive, timeout):
    request = prompt + "\n\n" + json.dumps(data, ensure_ascii=True, separators=(",", ":"))
    report = {"model": MODEL, "reasoning_effort": "low", "prompt": prompt, "input": data}
    matches.store(archive, report)
    command = [
        "codex", "exec", "-m", MODEL, "-c", 'model_reasoning_effort="low"',
        "-c", "project_doc_max_bytes=0", "--ignore-user-config", "--ignore-rules",
        "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only",
        "--disable", "shell_tool", "--disable", "unified_exec", "--disable", "hooks",
        "--json", "-",
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(command, input=request, encoding="utf-8", errors="replace",
                                capture_output=True, timeout=timeout, cwd=archive.parent,
                                creationflags=matches.CREATE_NO_WINDOW)
        rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        report.update(events=rows, returncode=result.returncode, stderr=result.stderr)
        if result.returncode or not any(row["type"] == "turn.completed" for row in rows):
            raise RuntimeError("Codex turn did not complete")
        items = [row["item"] for row in rows if row["type"] == "item.completed"]
        messages = [item["text"] for item in items if item["type"] == "agent_message"]
        if len(messages) != 1 or any(item["type"] not in {"agent_message", "reasoning"} for item in items):
            raise ValueError("Expected one model answer without tool use")
        answer = messages[0].strip()
        if answer.startswith("```json") and answer.endswith("```"):
            answer = answer[7:-3]
        report["decoded"] = json.loads(answer)
        return report["decoded"]
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        report["error"] = str(error)
        return None
    finally:
        report["seconds"] = round(time.monotonic() - started, 3)
        matches.store(archive, report)


def rows(reader):
    while True:
        offset = reader.tell()
        line = reader.readline()
        if not line.endswith(b"\n"):
            reader.seek(offset)
            return
        yield json.loads(line)


def type_name(unit):
    return UNIT_TYPES.get(unit["type"], f"BWAPI type {unit['type']}")


def scout_snapshot(units, state):
    friendly = [unit for unit in units if unit["self"]]
    enemies = [unit for unit in units if not unit["self"]]
    bases = [unit["position"] for unit in friendly if unit["type"] == 154]
    squad_points = [unit["position"] for unit in friendly if unit["id"] in state["squad_ids"]]
    locations = {"own_bases": bases, "army_center": [state["center"]], "squad": squad_points}
    threat = {}
    for label, positions in locations.items():
        threat[label] = dict(Counter(type_name(unit) for unit in enemies if any(
            math.dist(position, unit["position"]) <= 384 for position in positions)))
    return {
        "own_unit_types": dict(Counter(type_name(unit) for unit in friendly)),
        "visible_enemies": dict(Counter(type_name(unit) for unit in enemies)),
        "visible_enemies_within_384px": threat,
        "visible_undetected_enemies": dict(Counter(type_name(unit) for unit in enemies
                                                 if not unit["detected"])),
        "own_base_positions": bases,
    }


DECISION_PROMPT = (
    "You alone choose the live high-level orders for our Protoss on Destination. Use the supplied "
    "observations, native command receipts and current policy. Enemy counts are current friendly vision only; "
    "positions may be unknown. Time continues while you respond. Choose exactly one key per turn. To assemble "
    "a squad with 3 then 8, send 3 and wait for confirmation that mode is squad before choosing 8 in a later "
    "observation. Holding or repeating a task needs no key unless there is a specific reason. The baseline "
    "handles forces left in automatic control and is responsible for production and construction. "
    'Return only {"action":"one allowed key or keep_current","reason":"short observation-based reason"}. '
    "Do not use tools. The allowed actions are " + json.dumps(KEYS, ensure_ascii=True)
)

REVIEW_PROMPT = (
    "Review your completed Protoss game. Make one tactical judgment based on supplied facts. Cite one to "
    "three fact IDs from the supplied facts, as integers. Mention the relevant values in your judgment. "
    "The game result alone does not prove "
    "a policy effect. Decide keep or modify the next game's high-level text strategy; modify only for a "
    "specific evidenced adjustment. All v17 keys remain available. Do not use tools. Return one JSON object "
    'only with {"judgment":"one judgment","evidence":[1,2],'
    '"decision":"keep or modify","strategy":null}. If modifying, strategy is the full replacement policy '
    "as short plain text, at most 700 characters. If keeping, strategy is null."
)


class LiveGame:
    def __init__(self, run, context):
        self.run = run
        self.output = run / "bwapi-data/write"
        self.context = context
        self.stop = Event()
        self.thread = None
        self.timeline = []
        self.observations = []
        self.receipts = []
        self.error = None

    def update(self, _):
        if self.thread is None and (matches.read_json(self.run / "match.json") or {}).get("state") == "playing":
            self.thread = Thread(target=self.watch)
            self.thread.start()

    def watch(self):
        try:
            with (self.output / "commander-layer.jsonl").open("rb") as reader, ThreadPoolExecutor(1) as calls:
                self.command_loop(reader, calls)
        except Exception as error:
            self.error = str(error)
            matches.store(self.run / "controller-error.json", {"error": self.error})

    def command_loop(self, reader, calls):
        hwnd = game_window(self.output)
        start_ok = False
        state = None
        units = []
        tasks = {}
        events = deque(maxlen=6)
        recent_decisions = deque(maxlen=3)
        latest_frame = 0
        previous = None
        awaiting_receipt = None
        pending = None
        while not self.stop.is_set():
            for row in rows(reader):
                event = row["event"]
                latest_frame = max(latest_frame, row["frame"])
                if event == "end":
                    self.flush_pending(pending)
                    return
                if event == "start":
                    start_ok = row.get("keymap") == KEYMAP and row["human_layer"]
                    if not start_ok:
                        raise RuntimeError("Native v17 commander layer is not active")
                elif event == "validation_native":
                    units = row["units"]
                elif event == "state":
                    state = row
                    if not units:
                        raise RuntimeError("Missing side-visible native unit telemetry")
                    sight = scout_snapshot(units, row)
                    active_ids = {row["main_task"], row["squad_task"]}
                    observation = {
                        **row, **sight, "tasks": [task for key, task in tasks.items() if key in active_ids],
                    }
                    self.observations.append(observation)
                    if len(self.observations) == 1:
                        matches.store(self.run / "native-start.json", {"keymap": KEYMAP, "first_observation": observation})
                    if awaiting_receipt is not None and row["frame"] - awaiting_receipt["latest_logged_frame_at_dispatch"] > 120:
                        awaiting_receipt["status"] = "no_native_receipt"
                        matches.store(self.run / "timeline.json", self.timeline)
                        awaiting_receipt = None
                    for decision in self.timeline[-3:]:
                        receipt = decision.get("receipt")
                        if receipt and "following_state" not in decision and row["frame"] > receipt["frame"]:
                            decision["following_state"] = observation
                            matches.store(self.run / "timeline.json", self.timeline)
                elif event == "battle_state":
                    tasks[row["task_id"]] = {key: row[key] for key in (
                        "frame", "task_id", "scope", "action", "phase", "goal", "arrived")}
                elif event == "command":
                    self.receipts.append(row)
                    if (awaiting_receipt is not None and row["key"] == awaiting_receipt["decision"]["action"]
                            and row["keymap"] == KEYMAP and row["modifier"] == "none"
                            and row["frame"] >= awaiting_receipt["latest_logged_frame_at_dispatch"]):
                        awaiting_receipt.update(receipt=row, status="native_receipt")
                        matches.store(self.run / "timeline.json", self.timeline)
                        awaiting_receipt = None
                elif event in TRANSITIONS:
                    events.append({key: value for key, value in row.items()
                                   if key in {"event", "frame", "kind", "phase", "state", "task_id", "reason"}})
            if pending is not None and pending[0].done():
                future, record, sent_at = pending
                pending = None
                answer = future.result()
                action = answer.get("action") if isinstance(answer, dict) else None
                record.update(decision=answer, latest_logged_frame_at_response=latest_frame,
                              response_seconds=round(time.monotonic() - sent_at, 3))
                if not isinstance(action, str) or action not in KEYS or not isinstance(answer.get("reason"), str):
                    record["status"] = "invalid_or_failed_model_turn"
                elif time.monotonic() - sent_at > MAX_AGE_SECONDS or latest_frame - record["frame"] > 360:
                    record["status"] = "expired"
                elif action == "keep_current":
                    record["status"] = "kept_without_key"
                elif (self.output / "match-ended.json").exists():
                    record["status"] = "after_end"
                else:
                    post_key(hwnd, action)
                    record.update(status="key_posted", latest_logged_frame_at_dispatch=latest_frame)
                    awaiting_receipt = record
                if action in KEYS if isinstance(action, str) else False:
                    recent_decisions.append({"frame": record["frame"], "action": action,
                                             "status": record["status"], "reason": answer["reason"]})
                matches.store(self.run / "timeline.json", self.timeline)
                print(f'{self.run.name}: frame {record["frame"]}, {action}, {record["status"]}', flush=True)
            if start_ok and state and state["frame"] >= 120 and pending is None and awaiting_receipt is None:
                current = self.observations[-1]
                trigger = self.trigger(previous, current, events)
                if trigger:
                    record = {"frame": current["frame"], "trigger": trigger, "status": "requested"}
                    call_file = self.run / f"decision-{len(self.timeline) + 1:03}.json"
                    record["call"] = call_file.name
                    self.timeline.append(record)
                    matches.store(self.run / "timeline.json", self.timeline)
                    payload = {**self.context, "observation": current, "recent_events": list(events),
                               "recent_decisions": list(recent_decisions), "latest_receipt": self.receipts[-1]
                               if self.receipts else None}
                    pending = (calls.submit(model_call, DECISION_PROMPT, payload, call_file, MAX_AGE_SECONDS),
                               record, time.monotonic())
                    previous = current
            self.stop.wait(.15)
        self.flush_pending(pending)

    def flush_pending(self, pending):
        if pending is not None:
            future, record, _ = pending
            record.update(status="ended_during_request", decision=future.result())
            matches.store(self.run / "timeline.json", self.timeline)

    def trigger(self, previous, current, events):
        if previous is None:
            return "opening"
        elapsed = current["frame"] - previous["frame"]
        last_receipt = self.receipts[-1] if self.receipts else {}
        if (last_receipt.get("key") == "3" and last_receipt["frame"] > previous["frame"]
                and current["frame"] > last_receipt["frame"] and current["mode"] == "squad"):
            return "squad_scope_confirmed"
        if elapsed < MIN_FRAMES:
            return None
        before, now = previous["visible_enemies"], current["visible_enemies"]
        before_threat = previous["visible_enemies_within_384px"]
        now_threat = current["visible_enemies_within_384px"]
        if any(bool(before_threat[area]) != bool(now_threat[area]) for area in now_threat):
            return "contact_change"
        if (before.keys() != now.keys() or abs(sum(before.values()) - sum(now.values())) >= 3
                or previous["visible_undetected_enemies"] != current["visible_undetected_enemies"]):
            return "visible_enemy_change"
        if (previous["healthy_army"] < 8 <= current["healthy_army"]
                or abs(previous["healthy_army"] - current["healthy_army"]) >= 3
                or previous["workers"] - current["workers"] >= 2):
            return "own_force_change"
        if any(event["frame"] > previous["frame"] for event in events):
            return "task_or_base_event"
        return "periodic" if elapsed >= PERIOD_FRAMES else None


def review_match(game, result, context, *, cutoff=None):
    states = game.observations
    interval = max(1, len(states) // 16)
    sample_frames = {row["frame"] for row in states[::interval]}
    sample_frames.add(states[-1]["frame"])
    for field in ("workers", "healthy_army"):
        sample_frames.add(max(states, key=lambda row: row[field])["frame"])
    for decision in game.timeline:
        if decision.get("receipt"):
            sample_frames.add(decision["frame"])
            if "following_state" in decision:
                sample_frames.add(decision["following_state"]["frame"])
    review_fields = {"event", "frame", "workers", "army", "healthy_army", "mode", "stance",
                     "squad_formed", "main_task", "squad_task", "mining_lock", "tasks", "visible_enemies",
                     "visible_enemies_within_384px", "gathered_minerals", "gathered_gas"}
    facts = [{key: value for key, value in row.items() if key in review_fields}
             for row in states if row["frame"] in sample_frames]
    receipt_fields = {"event", "frame", "key", "mode", "status", "action", "task_id", "unit_count"}
    facts.extend({key: value for key, value in row.items() if key in receipt_fields} for row in game.receipts)
    if cutoff:
        facts.append({"event": "cutoff", "frame": result["frame"], "natural_outcome": "unknown",
                      "reason": cutoff["reason"]})
    else:
        facts.append({"event": "end", "frame": result["frame"], "winner": result["winner"]})
    decisions = [{key: row[key] for key in ("frame", "decision", "status") if key in row}
                 for row in game.timeline]
    numbered = [{"id": index, **fact} for index, fact in enumerate(facts, start=1)]
    payload = {**context, "facts": numbered, "decisions": decisions}
    prompt = REVIEW_PROMPT
    if cutoff:
        prompt += (" The user ended the stalemate manually. Assess the observed trajectory only. "
                   "The natural result is unknown. Do not report a win, loss or draw.")
        payload["natural_outcome"] = "unknown"
    attempt = len(list(game.run.glob("review-call-*.json"))) + 1
    for attempt in range(attempt, attempt + 2):
        review = model_call(prompt, payload, game.run / f"review-call-{attempt}.json", 70)
        if isinstance(review, dict) and valid_review(review, numbered):
            review["evidence"] = [numbered[index - 1] for index in review["evidence"]]
            return review
        payload["correction"] = "For evidence give a JSON array with one to three integer IDs from facts."
    raise RuntimeError("Model did not return a review with checked evidence")


def valid_review(review, facts):
    evidence = review.get("evidence")
    strategy = review.get("strategy")
    edit_is_valid = (review.get("decision") == "keep" and strategy is None) or (
        review.get("decision") == "modify" and isinstance(strategy, str) and 0 < len(strategy.strip()) <= 700)
    return (set(review) == {"judgment", "evidence", "decision", "strategy"} and edit_is_valid
            and isinstance(review["judgment"], str) and bool(review["judgment"].strip())
            and isinstance(evidence, list) and 1 <= len(evidence) <= 3
            and all(type(index) is int and 1 <= index <= len(facts) for index in evidence))


def measured_speed(output):
    with (output / "delegate-diagnostic.jsonl").open("rb") as reader:
        ticks = [(row["frame"], row["tick_ms"]) for row in rows(reader)
                 if row["stage"] == "frame_return" and row["frame"] >= 120]
    first, last = ticks[0], ticks[-1]
    return {"first": first, "last": last,
            "average_wall_ms_per_frame": round((last[1] - first[1]) / (last[0] - first[0]), 3)}


def finish_review(batch, game, record, active, number):
    prior = review_match(game, record["result"], game.context)
    prior["from_version"] = active["version"]
    if prior["decision"] == "modify" and number == 1:
        strategy = prior["strategy"].strip() + "\n"
        prior["diff"] = "".join(unified_diff(active["strategy"].splitlines(keepends=True),
                                             strategy.splitlines(keepends=True),
                                             fromfile="v1/strategy.md", tofile="v2/strategy.md"))
        active = save_version(batch, "v2", prior["from_version"], strategy)
    prior["active_version"] = active["version"]
    matches.store(game.run / "review.json", prior)
    record.update(state="finished", review=prior["decision"], active_after_review=active["version"])
    record.pop("error", None)
    return active, prior


def resume_first_review(batch, summary, active):
    records = summary["matches"]
    if (len(records) != 1 or records[0]["result"]["state"] != "finished"
            or not records[0].get("peer_pacing") or active["version"] != "v1"):
        raise ValueError("Resume requires a confirmed first game and pending review on v1")
    record = records[0]
    game = LiveGame(batch / record["run"], {"active_policy": active, "previous_review": None})
    game.observations = matches.read_json(game.run / "observations.json")
    game.timeline = matches.read_json(game.run / "timeline.json")
    game.receipts = [row["receipt"] for row in game.timeline if "receipt" in row]
    if len(game.receipts) != record["native_receipts"] or not game.observations:
        raise ValueError("Completed game is missing its archived observations or receipts")
    summary.setdefault("engineering_events", []).append({
        "after_match": 1, "event": "review_retry",
        "reason": "First review attempts returned composite or nested field labels rejected by validation.",
        "change": "Review now cites archived fact IDs. Resumed from confirmed first-game records.",
        "original_replies": sorted(path.name for path in game.run.glob("review-call-*.json")),
    })
    try:
        return finish_review(batch, game, record, active, 1)
    finally:
        matches.store(batch / "batch.json", summary)


def review_cutoff(batch):
    summary = matches.read_json(batch / "batch.json")
    first, record = summary["matches"]
    run = batch / record["run"]
    cutoff_path = run / "cutoff.json"
    cutoff = json.loads(cutoff_path.read_text(encoding="utf-8-sig"))
    active = matches.read_json(batch / "active.json")
    previous = matches.read_json(batch / first["run"] / "review.json")
    game = LiveGame(run, {"active_policy": active, "previous_review": previous})
    game.observations = matches.read_json(run / "observations.json")
    game.timeline = matches.read_json(run / "timeline.json")
    game.receipts = [row["receipt"] for row in game.timeline if "receipt" in row]
    host_end = matches.read_json(game.output / "match-ended.json")
    peer_end = matches.read_json(run / "opponent/bwapi-data/write/match-ended.json")
    if cutoff["status"] != "authorized_stalemate_cutoff" or not game.observations or not host_end:
        raise ValueError("Expected an authorized cutoff with archived live observations and native stop")
    cutoff.update(native_host_after_leave=host_end, native_peer_after_leave=peer_end,
                  completed_at=record["result"]["ended_at"])
    matches.store(cutoff_path, cutoff)
    record["result"].update(state="cutoff", winner=None, natural_outcome="unknown")
    record.pop("peer_end", None)
    record.pop("review", None)
    record.pop("active_after_review", None)
    counts = dict(Counter(row["status"] for row in game.timeline))
    counts["successful_decisions"] = counts.get("native_receipt", 0) + counts.get("kept_without_key", 0)
    record.update(state="cutoff", cutoff=cutoff, decision_counts=counts)
    first_timeline = matches.read_json(batch / first["run"] / "timeline.json")
    first_counts = dict(Counter(row["status"] for row in first_timeline))
    first_counts["successful_decisions"] = (first_counts.get("native_receipt", 0)
                                            + first_counts.get("kept_without_key", 0))
    first["decision_counts"] = first_counts
    matches.store(batch / "batch.json", summary)
    match_record = matches.read_json(run / "match.json")
    matches.store(run / "match.json", {**match_record, "state": "cutoff", "winner": None,
                                        "natural_outcome": "unknown", "cutoff": cutoff})
    replay = run / record["result"]["replay"]
    labels = replay.with_suffix(".rep.json")
    if labels.exists():
        matches.store(labels, {**matches.read_json(labels), "natural_outcome": "unknown", "cutoff": cutoff})
    previous_review_path = run / "review.json"
    if previous_review_path.exists():
        previous_review_path.replace(run / "review-before-cutoff-label.json")
    summary["engineering_events"].append({"after_match": 2, "event": "authorized_cutoff",
                                          "details": "Native exit flags archived as cutoff, not game result."})
    try:
        review = review_match(game, record["result"], game.context, cutoff=cutoff)
        review.update(outcome="unknown", from_version=active["version"], active_version=active["version"])
        if review["decision"] == "modify":
            candidate = run / "candidate-strategy.md"
            candidate.write_text(review["strategy"].strip() + "\n", encoding="utf-8")
            review.update(candidate=candidate.name, candidate_status="unplayed")
        matches.store(run / "review.json", review)
        record.update(review=review["decision"], active_after_review=active["version"])
    finally:
        matches.store(batch / "batch.json", summary)
        (run / "unused-test-input").unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume-batch", type=Path, help="Retry review of a naturally finished first game")
    parser.add_argument("--review-cutoff", type=Path, help="Review an archived, authorized second-game cutoff")
    args = parser.parse_args()
    if args.review_cutoff:
        review_cutoff(args.review_cutoff.resolve())
        return
    if args.resume_batch:
        batch = args.resume_batch.resolve()
        summary = matches.read_json(batch / "batch.json")
        active, prior = resume_first_review(batch, summary, matches.read_json(batch / "active.json"))
        start_number = 2
    else:
        batch = matches.new_run(matches.RUNS / "normal-codex")
        batch.mkdir(parents=True)
        active = save_version(batch, "v1", None, INITIAL_STRATEGY + "\n")
        summary = {"model": MODEL, "reasoning_effort": "low", "speed_ms_per_frame": SPEED,
                   "map": "Destination", "opponent": "PylonPuller", "matches": []}
        prior = None
        start_number = 1
    print(batch, flush=True)
    for number in range(start_number, 3):
        run = batch / f"match-{number}"
        context = {"active_policy": active, "previous_review": prior}
        game = LiveGame(run, context)
        record = {"run": run.name, "policy_version": active["version"], "state": "running"}
        summary["matches"].append(record)
        matches.store(batch / "batch.json", summary)
        started = time.monotonic()
        try:
            try:
                result = matches.playing(run, "Destination", "PylonPuller", speed=SPEED, fullscreen=False,
                                         update=game.update, visible_telemetry=True, data_only=True,
                                         keep_results=False, render_profile="standard")
            finally:
                game.stop.set()
                if game.thread is not None:
                    game.thread.join()
            peer = matches.read_json(run / "opponent/bwapi-data/write/match-ended.json")
            record.update(result=result, peer_end=peer, native_receipts=len(game.receipts),
                          decisions=len(game.timeline), seconds=round(time.monotonic() - started, 3))
            if result["state"] != "finished" or not peer or peer["winner"] == result["winner"] or not result.get("replay"):
                raise RuntimeError("Two-sided natural end and native replay not confirmed")
            for slot, name in ((run, "host_pacing"), (run / "opponent", "peer_pacing")):
                ini = (slot / "bwapi-data/bwapi.ini").read_text(encoding="ascii")
                if f"speed_override={SPEED}" not in ini.splitlines():
                    raise RuntimeError(f"Incorrect native speed in {slot}")
                record[name] = measured_speed(slot / "bwapi-data/write")
            matches.store(run / "observations.json", game.observations)
            if game.error or not any(row["status"] in {"native_receipt", "kept_without_key"} for row in game.timeline):
                raise RuntimeError(game.error or "No valid live model decisions")
            recorded_receipts = [row["receipt"] for row in game.timeline if "receipt" in row]
            if recorded_receipts != game.receipts:
                raise RuntimeError("Native commands could not all be matched to live model decisions")
            record["state"] = "reviewing"
            matches.store(batch / "batch.json", summary)
            active, prior = finish_review(batch, game, record, active, number)
        except Exception as error:
            record.update(state="error", error=str(error))
            raise
        finally:
            matches.store(batch / "batch.json", summary)


if __name__ == "__main__":
    main()
