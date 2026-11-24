"""Offline acceptance for the RSI situation digest, using archived matches only.

payloads         rebuild the live-model payload at selected frames and write it for reading
shadow           replay the current seed and list where release_idle_defense would fire
decision-replay  rebuild payloads after a defense lock; with --call, ask the live model again

No game is started. decision-replay sends requests only when --call is given.
"""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rsi import sandbox, situation
from app.rsi.adjudication import map_enemy_start, visible_main_nexus
from app.rsi.model_client import ModelClient, write_json
from app.rsi.runner import DECISION_PROMPT, decision_payload
from app.rsi.seed_v1 import SEED_SOURCES, write_seed
from app.rsi.validator import check_summary, check_tools, replay_version

DEFAULT_MATCHES = (
    "matches/rsi/20261123-194749-829148/match-1",
    "matches/rsi/20261123-194749-829148/match-3",
    "matches/rsi/20261123-211014-690000/match-1",
)
# Every loss in this batch locked defense with key 0 and kept it until the end.
LOCKED_LOSSES = tuple(f"matches/rsi/20261123-194749-829148/match-{number}" for number in (1, 2, 3))
IDLE_FRAMES = 480
RECENT_FIELDS = ("frame", "source", "status", "action_type", "target", "key", "reason")


def _read(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def replay_payloads(match_dir, map_name="Destination"):
    """Yield (frame, raw, payload) for every archived observation, as the seed would see it live."""
    observations = situation.attach(_read(match_dir / "observations.json"))
    timeline = _read(match_dir / "timeline.json", [])
    policy = sandbox.load_sources(SEED_SOURCES)
    tools = {name: {"step": tool["step"], "description": tool["description"], "args_schema": tool["args_schema"]}
             for name, tool in check_tools(policy.tools.TOOLS).items()}
    triggers = {row["frame"]: row.get("trigger") for row in timeline if row.get("source") == "llm"}
    enemy_start = map_enemy_start(map_name, observations[0])
    enemy_main = {"position": enemy_start, "source": "map_start", "last_seen_frame": None} if enemy_start else None
    memory = {}
    for raw in observations:
        frame = raw["frame"]
        if nexus := visible_main_nexus(raw, enemy_start):
            enemy_main = {"position": nexus["position"], "source": "visible_nexus", "last_seen_frame": frame}
        summary = check_summary(situation.merge(policy.observer.summarize(deepcopy(raw), memory),
                                                raw[situation.KEY]), frame)
        earlier = [row for row in timeline if type(row.get("frame")) is int and row["frame"] < frame
                   and row.get("source") != "engine"]
        recent = [{name: row[name] for name in RECENT_FIELDS if name in row} for row in earlier[-5:]]
        receipts = [row["receipt"] for row in earlier if row.get("receipt")
                    and row["receipt"].get("frame", frame) <= frame]
        payload = decision_payload("v1", SEED_SOURCES["strategy.md"], summary,
                                   triggers.get(frame) or "offline_check", deepcopy(enemy_main),
                                   raw.get("stance") == "defend", tools, None, recent,
                                   receipts[-1] if receipts else None)
        yield frame, raw, payload


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def digest_line(frame, payload):
    view = payload["summary"]["situation"]
    own, army, enemy, defense = view["own"], view["army"], view["enemy"], view["defense"]
    tech = ", ".join(own["tech"]) or "-"
    seen = ", ".join(name for name in enemy["seen_types"]) or "-"
    return (f"| {frame} | {own['bases']} | {own['workers']} | {own['army_types']} | {own['detectors']} | {tech} | "
            f"{army['zone']} | {enemy['visible_army'] or '-'} | {seen} | {defense['stance']} "
            f"{defense['defense_locked_frames']}/{defense['frames_since_enemy_near_base']} |")


def command_payloads(args):
    out = args.out / "payloads"
    lines = ["# 离线 payload 检查", "",
             "每行是一帧：基地数 | 农民 | 己方兵种 | 反隐 | 科技 | 主力区段 | 可见敌军 | 见过的敌方类型 | "
             "stance 锁防帧/距上次基地有敌帧", ""]
    worst = 0
    for match in args.matches:
        match_dir = ROOT / match
        rows = list(replay_payloads(match_dir))
        llm_frames = sorted({row["frame"] for row in _read(match_dir / "timeline.json", []) if row.get("source") == "llm"})
        picks = [llm_frames[index * (len(llm_frames) - 1) // 4] for index in range(5)] if len(llm_frames) >= 5 else llm_frames
        cloaked = next((frame for frame, raw, _ in rows if raw.get("visible_undetected_enemies")), None)
        picks = sorted({*picks, *(frame for frame in (cloaked, rows[-1][0]) if frame is not None)})
        label = match.replace("matches/rsi/", "").replace("/", "-")
        lines += [f"## {label}", "", "| 帧 | 基地 | 农民 | 兵种 | 反隐 | 科技 | 区段 | 可见敌军 | 见过的敌方类型 | 防守 |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for frame, _, payload in rows:
            if frame not in picks:
                continue
            summary_bytes, payload_bytes = _size(payload["summary"]), _size(payload)
            worst = max(worst, summary_bytes)
            write_json(out / label / f"frame-{frame:05}.json",
                       {"summary_bytes": summary_bytes, "payload_bytes": payload_bytes,
                        "request_chars": len(DECISION_PROMPT) + 2 + len(json.dumps(payload, ensure_ascii=False,
                                                                                   separators=(",", ":"))),
                        "payload": payload})
            lines.append(digest_line(frame, payload))
        lines.append("")
    lines.insert(2, f"最大 summary：{worst} 字节（上限 8192）。完整 payload 在同目录 `<局>/frame-*.json`。")
    (out / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"{out / 'index.md'} (largest summary {worst} bytes)")


def command_shadow(args):
    report = {}
    with TemporaryDirectory() as temporary:
        seed = write_seed(Path(temporary))
        for match in args.matches:
            match_dir = ROOT / match
            observations = _read(match_dir / "observations.json")
            replay = replay_version(seed, observations)
            frames = [row["frame"] for row in replay["reflex_frames"] if row["rule_name"] == "release_idle_defense"]
            # Shadow replay is open loop: the archived stance never changes, so the rule repeats every sample.
            first_fires = [frame for index, frame in enumerate(frames) if index == 0 or frame - frames[index - 1] > 120]
            transitions = (_read(match_dir / "match.json", {}).get("lock_defense") or {}).get("transitions", [])
            report[match] = {
                "observations": len(observations),
                "lock_transitions": [[row["frame"], row["locked"], row.get("key")] for row in transitions],
                "release_idle_defense_first_fire": first_fires,
                "release_idle_defense_samples": len(frames),
                "wake_llm_count": replay["wake_llm_count"],
            }
    write_json(args.out / "shadow.json", report)
    print(json.dumps(report, indent=2))


def command_decision_replay(args):
    out = args.out / "decision-replay"
    client = ModelClient(args.backend, args.model, args.reasoning_effort, args.endpoint, args.timeout,
                         api_key=os.environ.get(args.api_key_env), codex_executable=args.codex_executable
                         ) if args.call else None
    rows = []
    for match in args.matches:
        match_dir = ROOT / match
        timeline = _read(match_dir / "timeline.json", [])
        kept = {row["frame"]: row for row in timeline if row.get("source") == "llm" and row.get("status") == "kept_without_key"}
        candidates = [(frame, raw, payload) for frame, raw, payload in replay_payloads(match_dir)
                      if frame in kept and raw.get("stance") == "defend"]
        # Prefer the deadlock signature: locked while no enemy has been near a base for a while.
        idle = [row for row in candidates
                if row[2]["summary"]["situation"]["defense"]["frames_since_enemy_near_base"] >= IDLE_FRAMES]
        pool = idle or candidates
        picks = [pool[index * (len(pool) - 1) // (args.per_match - 1)]
                 for index in range(args.per_match)] if len(pool) > args.per_match else pool
        label = match.replace("matches/rsi/", "").replace("/", "-")
        for frame, _, payload in picks:
            path = out / label / f"frame-{frame:05}.json"
            row = {"match": label, "frame": frame, "original": "keep_current",
                   "original_reason": kept[frame].get("reason"),
                   "defense": payload["summary"]["situation"]["defense"]}
            if client:
                report = client.call(DECISION_PROMPT, payload, path)
                decoded = report.get("decoded") or {}
                row.update(new_action=decoded.get("action_type"), new_target=decoded.get("target"),
                           new_reason=decoded.get("reason"), error=report.get("error"))
            else:
                write_json(path, {"prompt": DECISION_PROMPT, "payload": payload})
            rows.append(row)
    write_json(out / "summary.json", rows)
    print(f"{len(rows)} frames -> {out / 'summary.json'}" + ("" if args.call else " (dry run, no model calls)"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("payloads", "shadow", "decision-replay"))
    parser.add_argument("--matches", nargs="+",
                        help="match directories relative to the project (defaults depend on the command)")
    parser.add_argument("--out", type=Path, default=ROOT / "matches/rsi/20261124-offline-check")
    parser.add_argument("--per-match", type=int, default=3)
    parser.add_argument("--call", action="store_true", help="send decision-replay requests to the model")
    parser.add_argument("--backend", choices=("codex", "openai"), default="codex")
    parser.add_argument("--endpoint")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--model", default="gpt-6-luna")
    parser.add_argument("--reasoning-effort", default="low")
    parser.add_argument("--timeout", type=int, default=24)
    parser.add_argument("--codex-executable", default="codex")
    args = parser.parse_args(argv)
    if args.matches is None:
        args.matches = list(LOCKED_LOSSES if args.command == "decision-replay" else DEFAULT_MATCHES)
    {"payloads": command_payloads, "shadow": command_shadow,
     "decision-replay": command_decision_replay}[args.command](args)


if __name__ == "__main__":
    main()
