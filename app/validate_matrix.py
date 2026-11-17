"""Verify each selected pairing starts, advances, and saves its native replay."""
import argparse
import json
from pathlib import Path
import time

from matches import MAPS, OPPONENTS, VALIDATION, new_run, playing, store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("opponents", nargs="*", choices=tuple(OPPONENTS))
    parser.add_argument("--exclude", nargs="*", default=())
    parser.add_argument("--retry", type=Path, help="Repeat failed pairs from this earlier matrix summary.")
    parser.add_argument("--retry-after", type=int, help="Wait for this many entries before reading the retry summary.")
    parser.add_argument("--speed", type=int, default=20)
    parser.add_argument("--opponent-host", action="store_true")
    args = parser.parse_args()
    entries = args.opponents or list(OPPONENTS)
    pairs = [(name, map_name) for name in entries if name not in args.exclude for map_name in MAPS]
    if args.retry:
        for _ in range(1200):
            records = json.loads(args.retry.read_text(encoding="utf-8"))
            if len(records) >= (args.retry_after or 0):
                break
            time.sleep(1)
        else:
            raise RuntimeError("Earlier matrix did not finish.")
        failed = [(record["opponent"], record["map"]) for record in records if not record["passed"]]
        pairs = list(dict.fromkeys((pairs if args.opponents else []) + failed))
    batch = new_run(VALIDATION / "matrix")
    batch.mkdir(parents=True)
    summary = []
    print(batch, flush=True)
    for name, map_name in pairs:
        run = new_run(batch)
        try:
            result = playing(run, map_name, name, fullscreen=False, speed=args.speed,
                             smoke_frames=360, opponent_hosts=args.opponent_host)
            if OPPONENTS[name]["kind"] == "java":
                opponent_active = "New unit: Probe" in (run / "opponent/java.log").read_text(errors="replace")
            elif OPPONENTS[name]["backend"] == "legacy":
                opponent_active = None
            else:
                events = (run / "opponent/bwapi-data/write/commander-layer.jsonl").read_text().splitlines()
                opponent_active = any(e.get("workers", 0) > 4 for e in map(json.loads, events))
            result["opponent_activity_verified"] = opponent_active
            result["passed"] = bool(result.get("replay") and result.get("frame", 0) >= 360 and opponent_active is not False)
        except Exception as error:
            result = dict(map=map_name, opponent=name, passed=False, error=str(error))
        result["directory"] = str(run)
        summary.append(result)
        print(name, map_name, "PASS" if result["passed"] else "FAIL", result.get("error", ""), flush=True)
        store(batch / "summary.json", summary)


if __name__ == "__main__":
    main()
