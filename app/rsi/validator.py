"""L0 candidate gates and offline replay of side-visible v17 observations."""

import argparse
import ast
from collections import Counter, deque
from copy import deepcopy
from datetime import datetime, timezone
from difflib import unified_diff
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory, NamedTemporaryFile

if __package__ in {None, ""}:
    # The worker is invoked as an isolated script, so add only this app directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rsi import sandbox, situation
    from rsi.primitives import CORE_SUMMARY_FIELDS, is_valid_key
    from rsi.seed_v1 import VERSION_FILES, write_seed
else:
    from . import sandbox, situation
    from .primitives import CORE_SUMMARY_FIELDS, is_valid_key
    from .seed_v1 import VERSION_FILES, write_seed


TIMEOUT_SECONDS = 3
MAX_SUMMARY_BYTES = 8192
MAX_REASON_CHARS = 160
SHADOW_MAX_FRAMES = 200
SOURCE_LIMITS = {name: 2000 if name == "strategy.md" else 12000 for name in VERSION_FILES}
TOOL_STATUSES = {"emit_key", "wait", "done", "abort"}
VERSION_NAME = re.compile(r"v[1-9][0-9]*")
RULE_NAME = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")
REPLAY_META_FIELDS = {"latest_receipt", "active_tool", "recent_decisions"}
# Same transition categories used by stage2_live.py. The seed ignores per-order noise.
TRANSITIONS = {
    "base_notice", "building_complete", "assembly_point", "assembly_refuge", "rendezvous_point",
    "squad_rebalance", "detection_guard", "detection_resumed", "target_selection",
}
EVENT_FIELDS = {"event", "frame", "kind", "phase", "state", "task_id", "reason"}


def _json(value, **kwargs):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, **kwargs)


def _json_text(value):
    return _json(value, indent=2) + "\n"


def _atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                            delete=False) as temporary:
        temp_path = Path(temporary.name)
        try:
            temporary.write(content)
            temporary.close()
            temp_path.replace(path)
        finally:
            temporary.close()
            temp_path.unlink(missing_ok=True)


def _read_sources(path):
    return {name: (path / name).read_text(encoding="utf-8") for name in VERSION_FILES}


def _short_text(value):
    return type(value) is str and 0 < len(value) <= MAX_REASON_CHARS and bool(value.strip())


def check_summary(summary, frame):
    if type(summary) is not dict:
        raise ValueError("expected a summary dict")
    if missing := CORE_SUMMARY_FIELDS - summary.keys():
        raise ValueError(f"summary is missing fields: {', '.join(sorted(missing))}")
    if type(summary["frame"]) is not int or summary["frame"] != frame:
        raise ValueError("summary frame differs from the observation")
    size = len(_json(summary, separators=(",", ":")).encode("utf-8"))
    if size > MAX_SUMMARY_BYTES:
        raise ValueError(f"summary is {size} bytes, limit is {MAX_SUMMARY_BYTES}")
    return summary


def check_tools(tools):
    if type(tools) is not dict:
        raise ValueError("tools.py TOOLS must be a dict")
    for name, tool in tools.items():
        if (not _short_text(name) or type(tool) is not dict
                or type(tool.get("description")) is not str or not tool["description"].strip()
                or type(tool.get("args_schema")) is not dict or not callable(tool.get("step"))):
            raise ValueError(f"tools.py invalid TOOLS entry {name!r}")
        _json(tool["args_schema"])
    return tools


def check_reflex(action, tools):
    if action is None:
        return None
    if type(action) is not dict:
        raise ValueError("expected None or a reflex action dict")
    name = action.get("rule_name")
    kind = action.get("action_type")
    target = action.get("target")
    if (not _short_text(name) or not RULE_NAME.fullmatch(name)
            or not _short_text(action.get("reason"))):
        raise ValueError("reflex requires a snake_case rule_name and a short reason")
    if kind == "key" and is_valid_key(target) and type(action.get("args", {})) is dict:
        _json(action)
        return action
    if kind == "tool" and type(target) is str and target in tools and type(action.get("args")) is dict:
        _json(action)
        return action
    raise ValueError(f"invalid reflex action {kind!r} -> {target!r}")


def check_tool_result(result):
    if (type(result) is not dict or type(result.get("status")) is not str
            or result["status"] not in TOOL_STATUSES or not _short_text(result.get("reason"))):
        raise ValueError("tool step requires a valid status and short reason")
    if result["status"] == "emit_key" and not is_valid_key(result.get("key")):
        raise ValueError(f"invalid tool key {result.get('key')!r}")
    _json(result)
    return result


def _call_checked(label, frame, call, check, *args):
    try:
        return check(call(*args))
    except Exception as error:
        raise ValueError(f"{label} at frame {frame}: {type(error).__name__}: {error}") from error


def _frame_sample(rows, limit):
    if limit is None or len(rows) <= limit:
        return rows
    return [rows[index * (len(rows) - 1) // (limit - 1)] for index in range(limit)]


def _smoke_tools(tools, summaries):
    probes = _frame_sample(summaries, 3)
    while len(probes) < 3:
        probes = [*probes, probes[-1]]
    results = {}
    for name, tool in tools.items():
        state = {}
        statuses = []
        for summary in probes:
            result = _call_checked(f"tools.{name}.step", summary["frame"], tool["step"],
                                   check_tool_result, deepcopy(summary), {}, state)
            statuses.append(result["status"])
            if result["status"] in {"abort", "done"}:
                state = {}
        results[name] = statuses
    return results


def _shadow(sources, observations):
    policy = sandbox.load_sources(sources)
    for module, names in ((policy.observer, ("summarize", "should_wake_llm")),
                          (policy.rules, ("decide_reflex",))):
        for name in names:
            if not callable(getattr(module, name, None)):
                raise ValueError(f"{name} export must be callable")
    tools = check_tools(policy.tools.TOOLS)
    prev_summary = None
    last_wake = None
    obs_memory = {}
    reflex_memory = {}
    summaries = []
    wakes = []
    reflexes = []
    for raw in observations:
        frame = raw["frame"]
        raw_obs = {key: value for key, value in raw.items() if key not in REPLAY_META_FIELDS}
        summary = _call_checked("observer.summarize", frame, policy.observer.summarize,
                                lambda value: check_summary(situation.merge(value, raw[situation.KEY]), frame),
                                deepcopy(raw_obs), obs_memory)
        # A policy can reuse objects stored in its memory. Keep L0 snapshots independent.
        summary = deepcopy(summary)
        summaries.append(summary)
        active_tool = raw.get("active_tool")
        action = _call_checked("rules.decide_reflex", frame, policy.rules.decide_reflex,
                               lambda value: check_reflex(value, tools), deepcopy(summary),
                               active_tool, reflex_memory)
        if action:
            reflexes.append({
                "frame": frame, "rule_name": action["rule_name"], "action_type": action["action_type"],
                "target": action["target"], "args": deepcopy(action.get("args", {})),
                "reason": action["reason"],
            })
        meta = {
            "frames_since_last_llm": None if last_wake is None else frame - last_wake,
            "latest_receipt": raw.get("latest_receipt"),
            "active_tool": active_tool,
            "recent_events": raw.get("recent_events", []),
            "recent_decisions": raw.get("recent_decisions", []),
        }
        reason = _call_checked("observer.should_wake_llm", frame, policy.observer.should_wake_llm,
                               check_trigger, deepcopy(summary), deepcopy(prev_summary),
                               deepcopy(meta))
        if reason is not None:
            last_wake = frame
            wakes.append({"frame": frame, "reason": reason})
        prev_summary = summary
    return {
        "observations_replayed": len(observations),
        "first_frame": observations[0]["frame"],
        "last_frame": observations[-1]["frame"],
        "wake_llm_count": len(wakes),
        "wake_reasons": dict(Counter(wake["reason"] for wake in wakes)),
        "wake_frames": wakes,
        "reflex_trigger_count": len(reflexes),
        "reflex_frames": reflexes,
        "tool_smoke": _smoke_tools(tools, summaries),
    }


def check_trigger(reason):
    if reason is not None and not _short_text(reason):
        raise ValueError("expected None or a nonempty short wake reason")
    return reason


def _replay_request(source_sets, observations, max_frames):
    if not observations:
        raise ValueError("shadow replay requires at least one observation")
    frames = [row["frame"] for row in observations]
    if any(type(frame) is not int or frame < 0 for frame in frames):
        raise ValueError("observation frames must be nonnegative integers")
    if any(left >= right for left, right in zip(frames, frames[1:])):
        raise ValueError("observation frames must be strictly increasing")
    if max_frames is not None and (type(max_frames) is not int or not 2 <= max_frames <= SHADOW_MAX_FRAMES):
        raise ValueError(f"max_frames must be None or an integer from 2 to {SHADOW_MAX_FRAMES}")
    # Recompute the L0 digest over the full stream so sampling cannot change its memory.
    selected = _frame_sample(situation.attach(observations), max_frames)
    request = _json({"versions": source_sets, "observations": selected}, separators=(",", ":"))
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--shadow-worker"],
            input=request, encoding="utf-8", capture_output=True, timeout=TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"shadow replay exceeded {TIMEOUT_SECONDS}s loading or executing policy") from error
    if result.returncode:
        raise ValueError(f"shadow worker exited with code {result.returncode}: {result.stderr.strip()}")
    reply = json.loads(result.stdout)
    if "error" in reply:
        raise ValueError(reply["error"])
    return {
        "source_observations": len(observations),
        "sampled": len(selected) < len(observations),
        "versions": reply,
    }


def _worker():
    request = json.load(sys.stdin.buffer)
    # Both versions share one deadline but never share candidate memory or modified observation data.
    result = {}
    for label, sources in request["versions"].items():
        try:
            result[label] = _shadow(sources, request["observations"])
        except Exception as error:
            result = {"error": f"{label}: {type(error).__name__}: {error}"}
            break
    sys.stdout.buffer.write(_json(result).encode("utf-8"))


def replay_version(path, observations, *, max_frames=None):
    """Replay one installed version and report all trigger frames in the selected view."""
    report = _replay_request({"version": _read_sources(Path(path))}, observations, max_frames)
    return {**report["versions"]["version"], "source_observations": report["source_observations"],
            "sampled": report["sampled"]}


def _check_edits(review, facts):
    edits = review.get("edits")
    if (type(edits) is not dict or not 1 <= len(edits) <= 2
            or not edits.keys() <= SOURCE_LIMITS.keys()):
        raise ValueError("edits must modify 1 or 2 files from the policy whitelist")
    for name, source in edits.items():
        if type(source) is not str or len(source) > SOURCE_LIMITS[name]:
            raise ValueError(f"{name}: expected text of at most {SOURCE_LIMITS[name]} characters")
    evidence = review.get("evidence")
    fact_ids = {fact["fact_id"] for fact in facts if type(fact["fact_id"]) is int}
    if (type(evidence) is not list or not 1 <= len(evidence) <= 3
            or any(type(item) is not int or item not in fact_ids for item in evidence)
            or len(set(evidence)) != len(evidence)):
        raise ValueError("evidence must contain 1 to 3 distinct integer IDs present in facts")
    return edits


def _diff_triggers(parent, candidate, field):
    old = {entry["frame"]: entry for entry in parent[field]}
    new = {entry["frame"]: entry for entry in candidate[field]}
    return [{"frame": frame, "parent": old.get(frame), "candidate": new.get(frame)}
            for frame in sorted(old.keys() | new.keys()) if old.get(frame) != new.get(frame)]


def _funcs(filename, source):
    if not filename.endswith(".py"):
        return {}
    return {node.name: ast.dump(node, include_attributes=False)
            for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _file_changes(parent, candidate, edits):
    changes = {}
    for name in edits:
        before, after = parent[name], candidate[name]
        old_funcs, new_funcs = _funcs(name, before), _funcs(name, after)
        changes[name] = {
            "functions": sorted(function for function in old_funcs.keys() | new_funcs.keys()
                                if old_funcs.get(function) != new_funcs.get(function)),
            "diff": _source_diff(name, before, after),
        }
    return changes


def _source_diff(name, before, after):
    return "".join(unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                fromfile=f"parent/{name}", tofile=f"candidate/{name}"))


def _next_version(versions):
    ids = [int(path.name[1:]) for path in versions.iterdir()
           if VERSION_NAME.fullmatch(path.name)]
    return f"v{max(ids, default=0) + 1}"


def _log_content(path, event):
    prior = path.read_text(encoding="utf-8") if path.exists() else ""
    return prior + _json(event, separators=(",", ":")) + "\n"


def _publish(batch, version, parent, sources, manifest, event):
    versions = batch / "versions"
    log = batch / "evolution-log.jsonl"
    with TemporaryDirectory(prefix=".publish-", dir=batch, ignore_cleanup_errors=True) as temp:
        temporary = Path(temp)
        staged = temporary / version
        staged.mkdir()
        for name, source in sources.items():
            (staged / name).write_text(source, encoding="utf-8", newline="\n")
        (staged / "manifest.json").write_text(_json_text(manifest), encoding="utf-8", newline="\n")
        pointer = temporary / "active.json"
        pointer.write_text(_json_text({"version": version, "parent": parent,
                                      "path": f"versions/{version}"}), encoding="utf-8", newline="\n")
        updated_log = temporary / "evolution-log.jsonl"
        updated_log.write_text(_log_content(log, event), encoding="utf-8", newline="\n")
        backup_log = temporary / "previous-log.jsonl"
        if log.exists():
            shutil.copyfile(log, backup_log)
        destination = versions / version
        staged.rename(destination)
        log_written = False
        try:
            updated_log.replace(log)
            log_written = True
            pointer.replace(batch / "active.json")
        except OSError:
            if log_written:
                if backup_log.exists():
                    backup_log.replace(log)
                else:
                    log.unlink()
            shutil.rmtree(destination)
            raise


def _compare_versions(before, after, observations, max_frames):
    report = _replay_request({"candidate": after}, observations, max_frames)
    candidate = report["versions"]["candidate"]
    try:
        parent = _replay_request({"parent": before}, observations, max_frames)["versions"]["parent"]
    except ValueError as error:
        # A candidate can repair a runtime error in the current version.
        report["versions"]["parent"] = {"error": str(error)}
        report.update(wake_frame_diffs=None, reflex_frame_diffs=None)
    else:
        report["versions"]["parent"] = parent
        report["wake_frame_diffs"] = _diff_triggers(parent, candidate, "wake_frames")
        report["reflex_frame_diffs"] = _diff_triggers(parent, candidate, "reflex_frames")
    return report


def validate_candidate(batch_dir, review, facts, observations, *, match_number, match_dir=None,
                       tokens=None, decision_sources=None, max_frames=SHADOW_MAX_FRAMES,
                       comparison_match_number=None, additional_observations=None,
                       expected_version=None, event_context=None, log_rejections=True):
    """Validate one modify proposal, persist its review and promote only on success."""
    batch = Path(batch_dir)
    match_dir = Path(match_dir) if match_dir is not None else batch / f"match-{match_number}"
    lock = batch / ".validation.lock"
    try:
        lock.touch(exist_ok=False)
    except FileExistsError:
        return {**review, "validation": "rejected",
                "validation_error": "gate4: another validation is publishing in this batch"}
    try:
        return _validate_locked(batch, review, facts, observations, match_number, match_dir,
                                tokens, decision_sources, max_frames, comparison_match_number,
                                additional_observations, expected_version, event_context, log_rejections)
    finally:
        lock.unlink()


def _validate_locked(batch, review, facts, observations, match_number, match_dir, tokens,
                     decision_sources, max_frames, comparison_match_number, additional_observations,
                     expected_version, event_context, log_rejections):
    result = dict(review)
    gate = "gate1"
    event = {
        **(event_context or {}),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "match_number": match_number,
        "parent": None,
        "version": None,
        "changed_files": [],
        "changed_functions": {},
        "distillation_type": review.get("distillation_type"),
        "tokens": tokens,
        "decision_sources": decision_sources,
    }
    try:
        # Read the installed pointer before validating the proposal so rejections retain lineage.
        active = json.loads((batch / "active.json").read_text(encoding="utf-8"))
        parent = active["version"]
        if not VERSION_NAME.fullmatch(parent) or active["path"] != f"versions/{parent}":
            raise ValueError("invalid active version pointer")
        event["parent"] = parent
        if expected_version is not None and expected_version != parent:
            gate = "gate4"
            raise ValueError("active version changed during review")
        if review.get("decision") != "modify":
            raise ValueError("expected a modify proposal")
        before = _read_sources(batch / "versions" / parent)
        proposed = review.get("edits")
        if isinstance(proposed, dict):
            result["diff"] = {name: _source_diff(name, before[name], source)
                              for name, source in proposed.items()
                              if name in before and isinstance(source, str)}
        edits = _check_edits(review, facts)
        event["changed_files"] = sorted(edits)

        if unchanged := [name for name, source in edits.items() if source == before[name]]:
            raise ValueError(f"edits include unchanged files: {', '.join(unchanged)}")

        gate = "gate2"
        for name, source in edits.items():
            if name.endswith(".py"):
                sandbox.check_source(name, source)
        after = {**before, **edits}
        changes = _file_changes(before, after, edits)
        event["changed_functions"] = {name: change["functions"] for name, change in changes.items()}
        result["changed_functions"] = event["changed_functions"]

        gate = "gate3"
        # Each match gets an independent replay and at most 200 evenly spaced observations.
        report = _compare_versions(before, after, observations, max_frames)
        if comparison_match_number is not None:
            report["match_number"] = comparison_match_number
            report["matches"] = {
                str(comparison_match_number): {**report},
                **{str(number): _compare_versions(before, after, trace, max_frames)
                   for number, trace in (additional_observations or {}).items()},
            }
        _atomic_write(match_dir / "candidate-check.json", _json_text(report))

        gate = "gate4"
        version = _next_version(batch / "versions")
        manifest = {"version": version, "parent": parent, "created_after_match": match_number,
                    "summary": review.get("diagnosis", "")}
        event.update(version=version, validation="accepted")
        result.update(validation="accepted", from_version=parent, to_version=version)
        _atomic_write(match_dir / "review.json", _json_text(result))
        _publish(batch, version, parent, after, manifest, event)
        return result
    except (KeyError, TypeError, ValueError, SyntaxError, OSError) as error:
        result.pop("to_version", None)
        message = f"{gate}: {error}"
        result.update(validation="rejected", validation_error=message)
        event.update(version=None, validation="rejected", validation_error=message)
        if log_rejections:
            _atomic_write(match_dir / "review.json", _json_text(result))
            log = batch / "evolution-log.jsonl"
            _atomic_write(log, _log_content(log, event))
        return result


def read_v17_archive(path):
    """Rejoin original side-visible state, unit, transition and command events in log order."""
    path = Path(path)
    archived = json.loads(path.read_text(encoding="utf-8"))
    timeline = json.loads(path.with_name("timeline.json").read_text(encoding="utf-8"))
    decisions = sorted(
        (row for row in timeline if type(row.get("latest_logged_frame_at_response")) is int
         and type(row.get("decision")) is dict and type(row["decision"].get("action")) is str),
        key=lambda row: row["latest_logged_frame_at_response"],
    )
    decision_index = 0
    recent_decisions = deque(maxlen=3)
    recent_events = deque(maxlen=6)
    latest_receipt = None
    units = None
    observations = []
    native_log = path.parent / "bwapi-data" / "write" / "commander-layer.jsonl"
    with native_log.open(encoding="utf-8") as source:
        for line in source:
            row = json.loads(line)
            event = row["event"]
            if event == "validation_native":
                units = row
            elif event == "command":
                latest_receipt = row
            elif event in TRANSITIONS:
                recent_events.append({key: row[key] for key in row.keys() & EVENT_FIELDS})
            elif event == "state":
                index = len(observations)
                frame = row["frame"]
                if (index >= len(archived) or frame != archived[index]["frame"]
                        or units is None or units["frame"] != frame):
                    raise ValueError(f"v17 archive is missing a matching state or units at frame {frame}")
                while (decision_index < len(decisions)
                       and decisions[decision_index]["latest_logged_frame_at_response"] <= frame):
                    decision = decisions[decision_index]
                    recent_decisions.append(decision)
                    decision_index += 1
                visible_decisions = []
                for decision in recent_decisions:
                    status = decision["status"]
                    if status == "native_receipt" and decision["receipt"]["frame"] > frame:
                        status = "key_posted"
                    visible_decisions.append({
                        "frame": decision["frame"], "action": decision["decision"]["action"],
                        "reason": decision["decision"].get("reason"), "status": status,
                    })
                observations.append({
                    **archived[index], "units": units["units"], "recent_events": list(recent_events),
                    "latest_receipt": latest_receipt, "active_tool": None,
                    "recent_decisions": visible_decisions,
                })
    if len(observations) != len(archived):
        raise ValueError("v17 archive ended before all observation frames were found")
    return observations, len(timeline)


def _main():
    parser = argparse.ArgumentParser(description="Replay v1 on archived v17 observations")
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("observations", type=Path, nargs="+")
    args = parser.parse_args()
    version = write_seed(args.batch)
    reports = []
    for path in args.observations:
        observations, historical_count = read_v17_archive(path)
        full = replay_version(version, observations)
        sample = replay_version(version, observations, max_frames=SHADOW_MAX_FRAMES)
        reports.append({
            "source": path.as_posix(),
            "historical_llm_requests": historical_count,
            "wake_reduction_percent": round(
                100 * (historical_count - full["wake_llm_count"]) / historical_count, 2
            ) if historical_count else None,
            "full_replay": full,
            "gate_sample_replay": sample,
        })
    report = {
        "version": "v1",
        "metadata": {
            "observations": "All archived observations in log order, with no cutoff.",
            "unit_details": "The same player's matching validation_native records. No type_name was present.",
            "events_and_receipts": "Same player native log, consumed up to each state in log order.",
            "recent_decisions": "Historical timeline replies visible by their recorded response frame.",
            "active_tool": "Null because the archived v17 loop had no composite tools.",
            "wake_clock": "Updated on each shadow wake. No model calls, receipt waits or orders are simulated.",
        },
        "matches": reports,
    }
    path = args.batch / "m1-replay.json"
    _atomic_write(path, _json_text(report))
    print(path)


if __name__ == "__main__":
    if sys.argv[1:] == ["--shadow-worker"]:
        _worker()
    else:
        _main()
