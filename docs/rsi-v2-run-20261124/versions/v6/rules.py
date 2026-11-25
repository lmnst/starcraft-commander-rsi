IDLE_DEFENSE_FRAMES = 1440


def decide_reflex(summary: dict, active_tool: str | None, memory: dict) -> dict | None:
    defense = summary["situation"]["defense"]
    idle = min(defense["defense_locked_frames"], defense["frames_since_enemy_near_base"])
    if summary["stance"] == "defend" and idle >= IDLE_DEFENSE_FRAMES:
        return {"rule_name": "release_idle_defense", "action_type": "key", "target": "-", "args": {},
                "reason": f"Defense lock idle for {idle} frames with no enemy near own bases."}
    return None
