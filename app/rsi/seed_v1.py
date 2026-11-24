"""Materialize the neutral v1 policy without importing any live game code."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .build_config import DEFAULT_TEXT as BUILD_CONFIG


STRATEGY = """Let the baseline produce, expand and handle routine opening play. Intervene when visible pressure,
unit health or a concrete objective supports it. Consider a separate squad when enough healthy units exist.
"""

OBSERVER = '''PERIOD_FRAMES = 1800
MIN_EVENT_FRAMES = 480
CORE_FIELDS = (
    "frame", "workers", "army", "healthy_army", "mode", "stance", "squad_formed",
    "mining_lock", "visible_enemies", "visible_enemies_within_384px",
    "visible_undetected_enemies",
)
TACTICAL_EVENTS = {
    "base_notice", "building_complete", "assembly_point", "assembly_refuge",
    "rendezvous_point", "detection_guard", "detection_resumed",
}


def summarize(raw_obs: dict, memory: dict) -> dict:
    summary = {name: raw_obs[name] for name in CORE_FIELDS}
    for name in ("workers", "healthy_army"):
        summary[name + "_delta"] = summary[name] - memory.get(name, summary[name])
        memory[name] = summary[name]
    summary["enemy_at_own_base"] = bool(
        summary["visible_enemies_within_384px"].get("own_bases")
    )
    summary["task_state"] = [
        [task["task_id"], task["phase"]] for task in raw_obs.get("tasks", ())
    ]
    return summary


def should_wake_llm(summary: dict, prev_summary: dict | None, meta: dict) -> str | None:
    elapsed = meta["frames_since_last_llm"]
    frame = summary["frame"]
    if frame < 120:
        return None
    if elapsed is None:
        return "opening"
    if prev_summary is not None:
        near = summary["visible_enemies_within_384px"]
        prev_near = prev_summary["visible_enemies_within_384px"]
        if near.get("own_bases") and not prev_near.get("own_bases"):
            return "contact_change"
        if elapsed >= MIN_EVENT_FRAMES:
            if any(bool(near.get(area)) != bool(prev_near.get(area)) for area in near):
                return "contact_change"
            visible = summary["visible_enemies"]
            prev_visible = prev_summary["visible_enemies"]
            if (visible.keys() != prev_visible.keys()
                    or abs(sum(visible.values()) - sum(prev_visible.values())) >= 3
                    or summary["visible_undetected_enemies"]
                    != prev_summary["visible_undetected_enemies"]):
                return "visible_enemy_change"
            if (prev_summary["healthy_army"] < 8 <= summary["healthy_army"]
                    or abs(summary["healthy_army"] - prev_summary["healthy_army"]) >= 3
                    or prev_summary["workers"] - summary["workers"] >= 2):
                return "own_force_change"
            if (summary.get("task_state", []) != prev_summary.get("task_state", [])
                    or any(event["event"] in TACTICAL_EVENTS
                           and prev_summary["frame"] < event["frame"] <= frame
                           for event in meta["recent_events"])):
                return "task_or_base_event"
    return "periodic" if elapsed >= PERIOD_FRAMES else None
'''

TOOLS = '''def squad_step(summary, state, key):
    phase = state.get("phase")
    if phase == "dispatched":
        return {"status": "done", "reason": "Squad order was dispatched."}
    if summary["mode"] == "squad":
        state["phase"] = "dispatched"
        return {"status": "emit_key", "key": key, "reason": "Squad scope is confirmed."}
    if phase == "selecting":
        return {"status": "wait", "reason": "Waiting for the observation to confirm squad mode."}
    if summary["healthy_army"] < 8:
        return {"status": "abort", "reason": "Selecting a squad requires 8 healthy units."}
    state["phase"] = "selecting"
    return {"status": "emit_key", "key": "3", "reason": "Request squad mode."}


def squad_attack_nearby_step(summary, args, state):
    return squad_step(summary, state, "5")


def squad_assemble_front_step(summary, args, state):
    return squad_step(summary, state, "8")


TOOLS = {
    "squad_attack_nearby": {
        "description": "Select squad, wait for squad mode, then attack nearby enemies.",
        "args_schema": {},
        "step": squad_attack_nearby_step,
    },
    "squad_assemble_front": {
        "description": "Select squad, wait for squad mode, then assemble at the front.",
        "args_schema": {},
        "step": squad_assemble_front_step,
    },
}
'''

RULES = '''IDLE_DEFENSE_FRAMES = 1440


def decide_reflex(summary: dict, active_tool: str | None, memory: dict) -> dict | None:
    defense = summary["situation"]["defense"]
    idle = min(defense["defense_locked_frames"], defense["frames_since_enemy_near_base"])
    if summary["stance"] == "defend" and idle >= IDLE_DEFENSE_FRAMES:
        return {"rule_name": "release_idle_defense", "action_type": "key", "target": "-", "args": {},
                "reason": f"Defense lock idle for {idle} frames with no enemy near own bases."}
    return None
'''

SEED_SOURCES = {
    "strategy.md": STRATEGY, "observer.py": OBSERVER, "tools.py": TOOLS, "rules.py": RULES,
    # L4: MacroBot production parameters. The default reproduces the frozen baseline.
    "build_config.json": BUILD_CONFIG,
}
VERSION_FILES = tuple(SEED_SOURCES)


def write_seed(batch_dir: Path) -> Path:
    """Write a complete v1 directory and initialize an empty batch's active pointer."""
    batch_dir = Path(batch_dir)
    versions = batch_dir / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    destination = versions / "v1"
    active = batch_dir / "active.json"
    with TemporaryDirectory(prefix=".seed-", dir=versions) as temporary:
        staging = Path(temporary) / "v1"
        if not destination.exists():
            staging.mkdir()
            for name, source in SEED_SOURCES.items():
                (staging / name).write_text(source, encoding="utf-8", newline="\n")
            (staging / "manifest.json").write_text(json.dumps({
                "version": "v1", "parent": None, "created_after_match": 0, "summary": "seed",
            }, indent=2) + "\n", encoding="utf-8", newline="\n")
            staging.rename(destination)
        if not active.exists():
            pointer = Path(temporary) / "active.json"
            pointer.write_text(json.dumps({
                "version": "v1", "parent": None, "path": "versions/v1",
            }, indent=2) + "\n", encoding="utf-8", newline="\n")
            pointer.replace(active)
    return destination
