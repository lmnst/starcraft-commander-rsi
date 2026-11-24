"""Play the frozen Baseline.dll and MacroBot.dll (default build_config) once each, no LLM, and compare
their production traces. Start positions and the opponent vary between games, so the build order is
expected to match in sequence while frames may drift once the games diverge.

    py -3.12 validation/macrobot_parity.py --opponent PylonPuller --frames 3000
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))

import matches  # noqa: E402
from app.rsi import build_config  # noqa: E402

PRODUCTION_EVENTS = ("build_plan", "detect_step", "cloak_seen", "range_upgrade_started", "unit_complete")


def trace(run):
    path = run / "bwapi-data/write/astra-trace.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def compare(base_rows, macro_rows):
    def plans(rows):
        return [(row["frame"], row["type"]) for row in rows if row["event"] == "build_plan"]
    base, macro = plans(base_rows), plans(macro_rows)
    shared = 0
    while shared < min(len(base), len(macro)) and base[shared][1] == macro[shared][1]:
        shared += 1
    return {
        "baseline_plans": len(base), "macrobot_plans": len(macro), "same_type_prefix": shared,
        "first_difference": None if shared == min(len(base), len(macro)) else
        {"index": shared, "baseline": base[shared], "macrobot": macro[shared]},
        "frame_drift": [macro[index][0] - base[index][0] for index in range(shared)],
        "baseline_sequence": base, "macrobot_sequence": macro,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", choices=matches.MAPS, default="Destination")
    parser.add_argument("--opponent", default="PylonPuller")
    parser.add_argument("--frames", type=int, default=3000)
    parser.add_argument("--speed", type=int, default=0)
    parser.add_argument("--macrobot-first", action="store_true", help="Play MacroBot before Baseline.")
    args = parser.parse_args()
    folder = ROOT / "validation" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-macrobot-parity")
    results = {}
    order = [("baseline", None), ("macrobot", build_config.DEFAULT_TEXT)]
    for name, config in (order[::-1] if args.macrobot_first else order):
        run = folder / name
        status = matches.playing(run, args.map, args.opponent, speed=args.speed, fullscreen=False,
                                 smoke_frames=args.frames, keep_results=False, render_profile="standard",
                                 data_only=True, build_config=config)
        rows = trace(run)
        results[name] = {"state": status["state"], "frame": status.get("frame"),
                         "delegate": status.get("delegate", "Baseline"),
                         "config_loaded": any(row["event"] == "config_loaded" for row in rows), "rows": rows}
        print(name, status["state"], status.get("frame"), flush=True)
    report = {"map": args.map, "opponent": args.opponent, "frames": args.frames,
              "states": {name: {key: value for key, value in result.items() if key != "rows"}
                         for name, result in results.items()},
              **compare(results["baseline"]["rows"], results["macrobot"]["rows"])}
    (folder / "parity.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ("baseline_sequence", "macrobot_sequence")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
