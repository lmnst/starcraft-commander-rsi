"""L0 v17 key map and minimum legal observation summary."""

from copy import deepcopy


BASE_PRIMITIVES = {
    "1": ("rescue_probe", "Rescue the scouting or threatened Probe."),
    "2": ("lock_mining", "Lock Probe mining while preserving gas gathering and construction."),
    "3": ("select_squad", "Select a squad with at least 8 healthy combat units, split about a quarter and leave at least 6 healthy reserves. The main force is locked to base defense."),
    "4": ("select_all", "Select the whole army for future orders. The main force stays in its current task, including locked defense."),
    "5": ("attack_nearby", "Attack nearby enemy buildings or known expansions in the selected scope. An expansion_unknown receipt means the existing task continues. Consider 6 only with a known enemy main, or '-' to return to baseline."),
    "6": ("clear_main", "Clear the known enemy main base area in the selected scope. Check enemy_main first."),
    "7": ("guard_local", "Guard locally, ignoring workers; repeat to update the guard position."),
    "8": ("assemble_front", "Assemble at the current front and wait for reinforcements."),
    "9": ("retreat_safe", "Withdraw to safety and assemble while waiting for reinforcements."),
    "0": ("defend_all", "Recall and lock the whole army to defend own bases, clearing squad scope and preserving the mining lock."),
    "-": ("restore_auto", "Release the defense lock and other manual orders, including Probe overrides, returning control to the baseline Bot."),
}

_CORE_DEFAULTS = {
    "frame": 0,
    "workers": 0,
    "army": 0,
    "healthy_army": 0,
    "mode": "all",
    "stance": "baseline",
    "squad_formed": False,
    "mining_lock": False,
    "visible_enemies": {},
    "visible_enemies_within_384px": {"own_bases": {}, "army_center": {}, "squad": {}},
    "visible_undetected_enemies": {},
}
CORE_SUMMARY_FIELDS = frozenset(_CORE_DEFAULTS)


def is_valid_key(key):
    return type(key) is str and key in BASE_PRIMITIVES


def fallback_summarize(raw_obs):
    """Return independent core fields from a legal v17 observation."""
    return {name: deepcopy(raw_obs.get(name, default)) for name, default in _CORE_DEFAULTS.items()}
