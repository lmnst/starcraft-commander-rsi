PERIOD_FRAMES = 1800
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
