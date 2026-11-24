"""L4 production parameters read by MacroBot.dll and the L0 gate for build_config.json.

Every default equals the literal it replaces in baseline/Bot.cpp, so the default file
reproduces the frozen baseline. scripts/check_macrobot.py verifies that mapping.
Frames are game frames at 24 fps: 1440 frames is one game minute.
"""

import json


FILENAME = "build_config.json"
MAX_CHANGED_KEYS = 3

# name: (default, min, max, description). Order is the order of MACRO_PARAMS in MacroBot.cpp.
SCHEMA = {
    "gate2_workers": (12, 8, 30, "Second Gateway once completed Probes reach this count."),
    "gates_base": (2, 1, 6, "Gateway target with one base, before the worker bonus."),
    "gates_bonus_workers": (18, 10, 60, "Add one Gateway to the target once Probes reach this count."),
    "gates_per_extra_base": (2, 0, 4, "Gateways added to the target per completed Nexus beyond the first."),
    "gates_cap": (8, 2, 12, "Upper bound of the Gateway target."),
    "gate_minerals": (180, 0, 1000, "Build toward the Gateway target when minerals exceed this ..."),
    "gate_army": (6, 0, 40, "... or when the army has at least this many units."),
    "gas1_zealots": (3, 0, 12, "First Assimilator once this many Zealots exist ..."),
    "gas1_frame": (4400, 0, 20000, "... or after this frame (whichever comes first)."),
    "gas_bank_full": (100, 0, 1000, "While banked gas is below this, put gas_workers_full on each Assimilator."),
    "gas_workers_full": (3, 0, 3, "Probes per Assimilator while gas is below gas_bank_full."),
    "gas_bank_reduced": (240, 0, 2000, "While banked gas is below this (and above gas_bank_full), use gas_workers_reduced; above it, 0."),
    "gas_workers_reduced": (2, 0, 3, "Probes per Assimilator between gas_bank_full and gas_bank_reduced."),
    "expand2_workers": (20, 10, 60, "Natural expansion requires at least this many Probes ..."),
    "expand2_army": (8, 0, 40, "... and at least this many army units ..."),
    "expand2_frame": (8000, 0, 30000, "... and a frame later than this (and no threat at home)."),
    "expand3_army": (18, 0, 60, "Third base requires at least this many army units ..."),
    "expand3_minerals": (700, 0, 3000, "... and minerals above this."),
    "probes_first_base": (23, 12, 30, "Probe target with one completed Nexus."),
    "probes_per_extra_base": (18, 0, 30, "Probes added to the target per completed Nexus beyond the first."),
    "probes_cap": (64, 20, 80, "Upper bound of the Probe target."),
    "dragoons_per_zealot": (2, 0, 10, "Train Dragoons while Dragoons < this * max(dragoon_zealot_floor, Zealots); otherwise Zealots."),
    "dragoon_zealot_floor": (3, 0, 20, "Zealot count floor used by the Dragoon ratio."),
    "range_after_dragoons": (1, 0, 20, "Start Singularity Charge once this many Dragoons exist."),
    "weapons_army": (10, 0, 60, "Start Ground Weapons at the Forge once the army has this many units."),
    "robo_frame": (9000, 0, 30000, "Robotics Facility without any cloak sighting: after this frame ..."),
    "robo_army": (10, 0, 40, "... and with at least this many army units."),
    "observers_base": (1, 0, 4, "Observers to keep with one base and no cloak seen."),
    "observers_alert": (2, 0, 4, "Observers to keep once cloak was seen or a second Nexus is done."),
    "reavers_max": (2, 0, 6, "Reavers to keep once a Robotics Support Bay is done."),
    "support_bay_army": (10, 0, 60, "Robotics Support Bay requires this many army units ..."),
    "support_bay_frame": (11500, 0, 36000, "... and a frame later than this."),
    "citadel_zealots": (8, 0, 40, "Citadel of Adun (then Leg Enhancements) once this many Zealots exist ..."),
    "citadel_frame": (9500, 0, 36000, "... and after this frame."),
    "proactive_observer_frame": (-1, -1, 20000, "From this frame, follow the cloak-response tech path without a sighting: "
                                 "Assimilator, Robotics Facility, Observatory, Observer, with resources reserved. "
                                 "Supply Pylons and the first two Gateways still come first. -1 = off."),
    "proactive_cannon_frame": (-1, -1, 20000, "From this frame, build a Forge and Photon Cannons at the bases without a "
                               "cloak sighting. Supply Pylons and the first two Gateways still come first. -1 = off."),
    "cannons_per_base": (1, 1, 4, "Photon Cannon target = this * completed Nexus count, used by both the cloak "
                         "response and proactive_cannon_frame. Uncovered bases are served first."),
}


def defaults():
    return {name: spec[0] for name, spec in SCHEMA.items()}


def dumps(config):
    """Canonical text: schema order, one key per line, a trailing newline."""
    lines = [f'  "{name}": {config[name]}' for name in SCHEMA]
    return "{\n" + ",\n".join(lines) + "\n}\n"


DEFAULT_TEXT = dumps(defaults())


def parse(text):
    """Return the config dict, or raise ValueError naming the first problem."""
    if type(text) is not str:
        raise ValueError(f"{FILENAME}: expected text")

    def unique(pairs):
        names = [name for name, _ in pairs]
        if len(set(names)) != len(names):
            raise ValueError(f"{FILENAME}: duplicate key")
        return dict(pairs)

    try:
        config = json.loads(text, object_pairs_hook=unique)
    except json.JSONDecodeError as error:
        raise ValueError(f"{FILENAME}: invalid JSON ({error.msg})") from None
    if type(config) is not dict:
        raise ValueError(f"{FILENAME}: expected one JSON object")
    if missing := [name for name in SCHEMA if name not in config]:
        raise ValueError(f"{FILENAME}: missing keys {', '.join(missing)}")
    if unknown := sorted(config.keys() - SCHEMA.keys()):
        raise ValueError(f"{FILENAME}: unknown keys {', '.join(unknown)}")
    for name, (_, low, high, _) in SCHEMA.items():
        value = config[name]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{FILENAME}: {name} must be an integer from {low} to {high}, got {value!r}")
    return config


def check_edit(before, after, *, max_changed_keys=MAX_CHANGED_KEYS):
    """Gate a review edit: valid schema and at most max_changed_keys values changed. Returns the changes."""
    old, new = parse(before), parse(after)
    changed = {name: [old[name], new[name]] for name in SCHEMA if old[name] != new[name]}
    if not changed:
        raise ValueError(f"{FILENAME}: no value changed")
    if len(changed) > max_changed_keys:
        raise ValueError(f"{FILENAME}: changed {len(changed)} keys, limit is {max_changed_keys} per round")
    return changed


def describe():
    """Schema text for review prompts."""
    return "\n".join(f"- {name} (default {default}, range {low}..{high}): {text}"
                     for name, (default, low, high, text) in SCHEMA.items())
