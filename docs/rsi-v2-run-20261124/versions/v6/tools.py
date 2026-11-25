def squad_step(summary, state, key):
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
