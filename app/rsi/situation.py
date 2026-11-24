"""L0 situation digest: objective, side-visible facts derived from the raw observation stream.

The digest is recomputed from the ordered observation stream, so live play, shadow replay and
review all see the same values. Policies read it as summary["situation"]; they cannot replace it.
"""

from math import dist


KEY = "situation"
WORKERS = {"Probe", "SCV", "Drone"}
PROJECTILES = {"Scarab", "Interceptor"}
DETECTORS = ("Observer", "Photon Cannon")
BASES = {"Nexus", "Command Center", "Hatchery", "Lair", "Hive"}
PRODUCTION = ("Gateway", "Robotics Facility", "Stargate")
SUPPORT = {"Pylon", "Assimilator", "Photon Cannon", "Shield Battery", *BASES, *PRODUCTION}
HOME_RADIUS = 768
ENEMY_RADIUS = 768
ECONOMY_WINDOW = 1200
RECENT_PEAK_WINDOW = 3600
MAX_BUILDINGS = 20
TASK_FIELDS = ("task_id", "scope", "action", "phase", "goal", "arrived")


def _name(unit):
    return unit.get("type_name") or f"type_{unit['type']}"


def _is_building(unit):
    return 106 <= unit["type"] <= 201


def _count(names):
    counts = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items()))


def _center(units):
    return [round(sum(unit["position"][0] for unit in units) / len(units)),
            round(sum(unit["position"][1] for unit in units) / len(units))]


def _engaged(unit):
    return bool(unit.get("attacking")) or unit.get("cooldown", 0) > 0


def _nearest(point, positions):
    return min((round(dist(point, position)) for position in positions), default=None)


def build_situation(raw, memory):
    """Return the digest for one observation and update the L0-owned cross-frame memory."""
    frame = raw["frame"]
    units = raw.get("units", [])
    memory.setdefault("first_frame", frame)

    own = [unit for unit in units if unit["self"] and unit.get("completed") is True]
    own_pending = [unit for unit in units if unit["self"] and unit.get("completed") is not True]
    own_buildings = [_name(unit) for unit in own if _is_building(unit)]
    own_mobile = [unit for unit in own if not _is_building(unit) and _name(unit) not in PROJECTILES]
    own_army = [unit for unit in own_mobile if _name(unit) not in WORKERS and _name(unit) != "Observer"]
    enemy = [unit for unit in units if not unit["self"] and _name(unit) not in PROJECTILES]
    enemy_buildings = [unit for unit in enemy if _is_building(unit)]
    enemy_army = [unit for unit in enemy if not _is_building(unit) and _name(unit) not in WORKERS]

    # Enemy sightings persist after the units leave vision.
    seen = memory.setdefault("seen", {})
    for name in {_name(unit) for unit in enemy}:
        seen.setdefault(name, [frame, frame])[1] = frame
    remembered = memory.setdefault("buildings", {})
    visible_ids = {unit["id"] for unit in enemy}
    for unit in enemy_buildings:
        remembered[str(unit["id"])] = [_name(unit), *unit["position"], frame]
    for unit_id, (_, x, y, _) in list(remembered.items()):
        if int(unit_id) not in visible_ids and any(
                dist(unit["position"], (x, y)) <= unit.get("sight", 0) - 32 for unit in own):
            del remembered[unit_id]
    if enemy_army:
        memory["last_army"] = {"frame": frame, "count": len(enemy_army),
                               "types": _count(map(_name, enemy_army)), "center": _center(enemy_army)}
        if len(enemy_army) > memory.get("peak_army", {}).get("count", 0):
            memory["peak_army"] = {"frame": frame, "count": len(enemy_army)}
        recent_list = memory.setdefault("recent_army_counts", [])
        recent_list.append([frame, len(enemy_army)])
    recent_list = [row for row in memory.get("recent_army_counts", []) if row[0] >= frame - RECENT_PEAK_WINDOW]
    memory["recent_army_counts"] = recent_list
    recent_peak = (
        {"frame": max(recent_list, key=lambda r: (r[1], r[0]))[0],
         "count": max(r[1] for r in recent_list)}
        if recent_list else None
    )

    bases = raw.get("own_base_positions", [])
    enemy_positions = [[x, y] for _, x, y, _ in remembered.values()]
    center = raw.get("center")
    if raw.get("army", 0) and center:
        home = _nearest(center, bases)
        near_enemy = _nearest(center, enemy_positions)
        if home is not None and home <= HOME_RADIUS:
            zone = "at_home"
        elif near_enemy is not None and near_enemy <= ENEMY_RADIUS:
            zone = "near_enemy"
        else:
            zone = "mid_map"
        army = {"center": center, "zone": zone, "distance_home": home,
                "distance_nearest_enemy_building": near_enemy}
    else:
        army = {"center": None, "zone": "none", "distance_home": None,
                "distance_nearest_enemy_building": None}
    army.update(types=_count(map(_name, own_army)),
                engaged=sum(map(_engaged, own_army)),
                enemy_engaged=sum(map(_engaged, enemy_army)))

    minerals, gas = raw.get("gathered_minerals", 0), raw.get("gathered_gas", 0)
    history = [row for row in memory.get("economy", []) if row[0] >= frame - ECONOMY_WINDOW]
    history.append([frame, minerals, gas])
    memory["economy"] = history
    oldest = history[0]

    near_base = bool(raw.get("visible_enemies_within_384px", {}).get("own_bases"))
    if near_base:
        memory["last_enemy_near_base"] = frame
    stance = raw.get("stance")
    if stance == "defend":
        memory.setdefault("defend_since", frame)
    else:
        memory.pop("defend_since", None)

    buildings = sorted(remembered.values(), key=lambda row: -row[3])[:MAX_BUILDINGS]
    return {
        "own": {
            "workers": sum(_name(unit) in WORKERS for unit in own_mobile),
            "army_types": army.pop("types"),
            "bases": sum(name in BASES for name in own_buildings),
            "base_positions": bases,
            "production": {name: own_buildings.count(name) for name in PRODUCTION},
            "tech": sorted({name for name in own_buildings if name not in SUPPORT}),
            "detectors": {name: sum(_name(unit) == name for unit in own) for name in DETECTORS},
            "in_progress": _count(_name(unit) for unit in own_pending),
        },
        "army": army,
        "economy": {
            "gathered_minerals": minerals, "gathered_gas": gas,
            "recent_minerals": minerals - oldest[1], "recent_gas": gas - oldest[2],
            "recent_window_frames": frame - oldest[0],
            "completed_workers": raw.get("completed_workers"),
        },
        "enemy": {
            "visible_army": _count(map(_name, enemy_army)),
            "visible_buildings": _count(map(_name, enemy_buildings)),
            "visible_workers": sum(_name(unit) in WORKERS for unit in enemy),
            "seen_types": {name: {"first": first, "last": last} for name, (first, last) in sorted(seen.items())},
            "known_buildings": [{"type": name, "position": [x, y], "last_seen": last}
                                for name, x, y, last in buildings],
            "last_army": memory.get("last_army"),
            "peak_army": memory.get("peak_army"),
            "recent_peak_army": recent_peak,
        },
        "defense": {
            "stance": stance,
            "enemy_near_base": near_base,
            "defense_locked_frames": frame - memory["defend_since"] if "defend_since" in memory else 0,
            "frames_since_enemy_near_base": frame - memory.get("last_enemy_near_base", memory["first_frame"]),
        },
        "tasks": [{name: task[name] for name in TASK_FIELDS if name in task} for task in raw.get("tasks", [])],
        **({"meta": {"opponent": raw["opponent"]}} if raw.get("opponent") else {}),
    }


def attach(observations):
    """Return copies of an ordered observation stream, each carrying its recomputed digest."""
    memory = {}
    return [{**row, KEY: build_situation(row, memory)} for row in observations]


def merge(summary, situation):
    """Add the L0 digest to a policy summary under the reserved key."""
    if type(summary) is dict and KEY in summary:
        raise ValueError(f"summary key {KEY!r} is reserved for L0")
    return {**summary, KEY: situation} if type(summary) is dict else summary
