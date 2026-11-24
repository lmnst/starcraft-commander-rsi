"""Run two native v17 matches with model-led review and policy handoff."""
import argparse
import difflib
import json
import subprocess
import time

import matches
from stage1 import COMMANDS, decision_messages, model_arguments, model_call, run_once


OBSERVATION_FIELDS = ("workers", "army", "healthy_army", "gathered_minerals", "gathered_gas",
                      "main_task", "squad_task", "mining_lock", "scout", "evade")
INITIAL_STRATEGY = "Prefer `-` while routine baseline mining is working. Use `2` only when the observed economy justifies taking direct control of mining.\n"
REVIEW_PROMPT = (
    'Review this finished Protoss game using only supplied self-side evidence. '
    'Pick one issue. Compare the previous expectation with the evidence when present. '
    'Do not claim causality from one match. Return a JSON object only, with exactly these fields: '
    '{"prediction_check":"text", "evidence":[{"event":"state|command|end","frame":120,'
    '"field":"workers","value":5}], "hypothesis":"text", "expectation":"next-match measurable text", '
    '"decision":"keep|modify|revert", "candidate":null}. '
    'Cite one or more exact supplied event, frame, field and value tuples. For modify only, '
    'candidate must be {"field":"workers|army|healthy_army|gathered_minerals|gathered_gas", '
    '"op":"<=|>=|==", "threshold":0, "key":"2|-", "otherwise":"2|-"}. '
    'That candidate replaces strategy.md only. '
    'Use `2` for mining lock and `-` for baseline. Revert means restore the parent policy if available. '
    'Keep if evidence does not support a useful specific change.'
)


def review_facts(run, evidence):
    log = run / "bwapi-data/write/commander-layer.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    states = [row for row in rows if row["event"] == "state"]
    sampled = states[::max(1, len(states) // 12)]
    if sampled[-1] != states[-1]:
        sampled.append(states[-1])
    states = [{key: row[key] for key in ("event", "frame", *OBSERVATION_FIELDS)} for row in sampled]
    return [evidence["observation"], evidence["receipt"], *states, rows[-1]]


def get_review(run, args, context, facts, result):
    messages = [
        {"role": "system", "content": REVIEW_PROMPT},
        {"role": "user", "content": json.dumps({**context, "evidence": facts, "result": result})},
    ]
    reply = model_call(args, messages, 800, run / "review-call.json", json_object=True)
    review = json.loads(reply["choices"][0]["message"]["content"])
    if set(review) != {"prediction_check", "evidence", "hypothesis", "expectation", "decision", "candidate"}:
        raise ValueError("复盘字段不符合约定。")
    if not all(isinstance(review[key], str) and review[key].strip()
               for key in ("prediction_check", "hypothesis", "expectation")) or not review["evidence"]:
        raise ValueError("复盘缺少假设、预期或证据。")
    for ref in review["evidence"]:
        if not any(row["event"] == ref["event"] and row["frame"] == ref["frame"] and
                   ref["field"] in row and row[ref["field"]] == ref["value"] for row in facts):
            raise ValueError(f"复盘引用未能在原生日志核对：{ref}")
    return review


def proposed_strategy(review, active, batch):
    proposal = review["candidate"]
    decision = review["decision"]
    if decision == "modify":
        if set(proposal or {}) != {"field", "op", "threshold", "key", "otherwise"} or any(
                proposal[key] not in COMMANDS for key in ("key", "otherwise")):
            raise ValueError("候选必须仅引用现有按键。")
        field, operator, threshold = (proposal[key] for key in ("field", "op", "threshold"))
        if field not in OBSERVATION_FIELDS[:5] or operator not in {"<=", ">=", "=="} or type(threshold) is not int or threshold < 0:
            raise ValueError("候选必须引用支持的数值观测字段和比较运算。")
        return f"When `{field}` {operator} {threshold}, use `{proposal['key']}`. Otherwise use `{proposal['otherwise']}`.\n"
    if decision == "revert" and proposal is None and active["parent"]:
        return (batch / "versions" / active["parent"] / "strategy.md").read_text(encoding="utf-8")
    if decision == "keep" and proposal is None:
        return active["strategy"]
    raise ValueError("无法应用该候选选择。")


def save_version(batch, version, parent, strategy):
    folder = batch / "versions" / version
    folder.mkdir(parents=True)
    (folder / "strategy.md").write_text(strategy, encoding="utf-8")
    active = {"version": version, "parent": parent, "strategy": strategy}
    matches.store(batch / "active.json", active)
    return active


def handoff(batch, run, args, active, review, obs, next_match):
    review["parent_version"] = active["version"]
    try:
        strategy = proposed_strategy(review, active, batch)
        review["diff"] = "".join(difflib.unified_diff(active["strategy"].splitlines(keepends=True),
                                                   strategy.splitlines(keepends=True),
                                                   fromfile="parent/strategy.md", tofile="candidate/strategy.md"))
        if strategy != active["strategy"] and next_match:
            review["candidate_version"] = "v2"
            candidate_context = {"active_policy": {"version": "v2", "parent": active["version"],
                                                   "strategy": strategy}, "previous_review": review}
            reply = model_call(args, decision_messages(obs, candidate_context), 32, run / "candidate-call.json")
            if reply["choices"][0]["message"]["content"].strip() not in COMMANDS:
                raise ValueError("候选旧快照试跑未返回有效按键。")
            active = save_version(batch, "v2", active["version"], strategy)
            review["validation"] = "approved_by_previous_snapshot"
        else:
            review["validation"] = "deferred_no_next_match" if strategy != active["strategy"] else "no_change"
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as error:
        review["validation"] = "rejected"
        review["validation_error"] = str(error)
    review["active_version"] = active["version"]
    matches.store(run / "review.json", review)
    return active


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    model_arguments(parser)
    parser.add_argument("--speed", type=int, default=1, help="Native milliseconds per frame")
    parser.add_argument("--match-timeout", type=int, default=300, help="Seconds before a formal match is aborted")
    args = parser.parse_args()
    batch = matches.new_run(matches.RUNS / "stage2")
    batch.mkdir(parents=True)
    active = save_version(batch, "v1", None, INITIAL_STRATEGY)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=matches.ROOT, text=True).strip()
    index = {"base_commit": revision, "model": args.model, "map": "Destination", "opponent": "PylonPuller",
             "speed": args.speed, "match_timeout_seconds": args.match_timeout, "smoke_frames": 0,
             "decision_window": "one opening decision per match, frame >= 120",
             "review_visibility": "host native telemetry and paired outcome flags",
             "writable_policy": ["strategy.md"], "matches": []}
    print(batch, flush=True)
    previous = None
    batch_started = time.monotonic()
    for number in (1, 2):
        run = batch / f"match-{number}"
        started = time.monotonic()
        context = {"active_policy": active, "previous_review": previous}
        record = {"run": run.name, "policy_version": active["version"], "parent": active["parent"],
                  "state": "running"}
        index["matches"].append(record)
        matches.store(batch / "batch.json", index)
        try:
            evidence = run_once(run, args, report_name="decision.json", context=context,
                                speed=args.speed, smoke_frames=0, match_timeout=args.match_timeout)
            result = evidence["match"]
            peer = matches.read_json(run / "opponent/bwapi-data/write/match-ended.json")
            if not peer or peer["winner"] == result["winner"]:
                record["state"] = "unconfirmed_end"
                raise RuntimeError("双方原生结算未共同确认胜负。")
            result = {"host_won": result["winner"], "host_end_frame": result["frame"], "peer_end": peer}
            record.update(state="finished", result=result, observation_frame=evidence["observation"]["frame"],
                          receipt_frame=evidence["receipt"]["frame"], key=evidence["key"],
                          decision_response_id=evidence["response_id"])
            facts = review_facts(run, evidence)
            review = get_review(run, args, context, facts, result)
            active = handoff(batch, run, args, active, review, evidence["observation"], number == 1)
            previous = review
            record.update(review=review["decision"], active_after_review=active["version"],
                          candidate_validation=review["validation"])
        except Exception as error:
            match = matches.read_json(run / "match.json") or {}
            record["state"] = {"running": match.get("state", "error"), "finished": "review_error"}.get(
                record["state"], record["state"])
            record["error"] = str(error)
            raise
        finally:
            record["seconds"] = round(time.monotonic() - started, 3)
            index["seconds"] = round(time.monotonic() - batch_started, 3)
            matches.store(batch / "batch.json", index)


if __name__ == "__main__":
    main()
