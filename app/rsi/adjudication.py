"""Timeout judgment based on native side-visible observations."""

from math import dist


# Start Location units of the frozen 2-player maps (validation/map_start_locations.py).
# The opening player-visible Nexus identifies our spawn, so the other spawn is the enemy main.
MAP_STARTS = {
    "Destination": {(1056, 272), (2112, 3824)},
    "Benzene": {(288, 3120), (3808, 464)},
    "Heartbreak Ridge": {(288, 1232), (3808, 1840)},
}
NEXUS = 154
OBSERVATION_INTERVAL = 120
MAIN_RADIUS = 96
MAX_NEXUS_VITALITY = 200  # 200 of 1500 total HP + shields
MIN_VITALITY_DROP = 32
ATTACK_RADIUS = 224
MIN_ATTACKERS = 2
# Mobile Protoss combat units with a ground attack, excluding Probes and
# short-lived projectiles. Spellcasters and transports do not count as attackers.
GROUND_ATTACKER_TYPES = {61, 65, 66, 68, 70, 71, 72, 83}


def map_enemy_start(map_name, opening):
    starts = MAP_STARTS.get(map_name)
    if starts is None or not opening or opening["frame"] > OBSERVATION_INTERVAL:
        return None
    own_positions = {tuple(unit["position"]) for unit in opening.get("units", [])
                     if unit["self"] and unit["type"] == NEXUS and unit.get("completed") is True}
    if len(own_positions) != 1 or not own_positions <= starts:
        return None
    return list(next(iter(starts - own_positions)))


def _visible_nexuses(observation):
    return [unit for unit in observation.get("units", []) if unit["self"] is False and unit["type"] == NEXUS]


def visible_main_nexus(observation, site):
    if site is None:
        return None
    candidates = [unit for unit in _visible_nexuses(observation) if dist(unit["position"], site) <= MAIN_RADIUS]
    if len(candidates) != 1:
        return None
    nexus = candidates[0]
    if (nexus.get("completed") is not True or nexus.get("detected") is not True
            or nexus.get("invincible") is not False or not isinstance(nexus.get("hp"), int)
            or nexus["hp"] <= 0 or not isinstance(nexus.get("shield"), int) or nexus["shield"] < 0):
        return None
    return nexus


def timeout_judgment(observations, site, observation_age, max_observation_age, terminal_frame):
    latest = observations[-1] if observations else None
    sample_frame = latest["frame"] if latest else None
    evidence = {"rule": "pvp_main_nexus_under_sustained_attack_v2",
                "enemy_main_site": site, "observation_frame": sample_frame, "terminal_frame": terminal_frame,
                "observation_age_seconds": observation_age, "max_observation_age_seconds": max_observation_age,
                "max_observation_frame_gap": OBSERVATION_INTERVAL, "main_radius_px": MAIN_RADIUS,
                "maximum_hp_plus_shields": MAX_NEXUS_VITALITY, "minimum_vitality_drop": MIN_VITALITY_DROP,
                "attacker_radius_px": ATTACK_RADIUS, "minimum_attackers": MIN_ATTACKERS}

    # Anything short of a near-destroyed enemy main is undecided, not a loss.
    def undecided(reason):
        return {**evidence, "outcome": "inconclusive", "reason": reason}

    if site is None:
        return undecided("enemy_main_site_unverified")
    if (latest is None or observation_age is None or observation_age > max_observation_age
            or type(terminal_frame) is not int or not 0 <= terminal_frame - sample_frame <= OBSERVATION_INTERVAL):
        return undecided("missing_or_stale_terminal_observation")
    nexus = visible_main_nexus(latest, site)
    if nexus is None:
        return undecided("no_completed_visible_enemy_main_nexus")
    vitality = nexus["hp"] + nexus["shield"]
    evidence["main_nexus"] = {key: nexus[key] for key in ("id", "position", "hp", "shield")}
    if any(other["id"] != nexus["id"] and other.get("completed") is True for other in _visible_nexuses(latest)):
        return undecided("other_completed_enemy_nexus_visible")
    if vitality > MAX_NEXUS_VITALITY:
        return undecided("enemy_main_nexus_above_vitality_threshold")
    prior = observations[-2] if len(observations) >= 2 else None
    previous_nexus = visible_main_nexus(prior, site) if prior else None
    if (previous_nexus is None or previous_nexus["id"] != nexus["id"]
            or not 0 < sample_frame - prior["frame"] <= OBSERVATION_INTERVAL):
        return undecided("no_consecutive_sighting_of_same_main_nexus")
    vitality_drop = previous_nexus["hp"] + previous_nexus["shield"] - vitality
    evidence.update(prior_frame=prior["frame"], prior_hp=previous_nexus["hp"],
                    prior_shield=previous_nexus["shield"], vitality_drop=vitality_drop)
    if vitality_drop < MIN_VITALITY_DROP:
        return undecided("insufficient_recent_damage_to_main_nexus")
    attackers = [unit for unit in latest["units"] if unit["self"] and unit.get("completed") is True
                 and unit["type"] in GROUND_ATTACKER_TYPES and unit.get("hp", 0) > 0
                 and unit.get("target") == nexus["id"] and dist(unit["position"], nexus["position"]) <= ATTACK_RADIUS]
    evidence["attackers"] = [{key: unit.get(key) for key in ("id", "type", "position", "target", "attacking", "cooldown")}
                             for unit in attackers]
    if len(attackers) < MIN_ATTACKERS or not any(unit.get("attacking") or unit.get("cooldown", 0) > 0 for unit in attackers):
        return undecided("insufficient_current_attack_evidence")
    return {**evidence, "outcome": "cutoff_win", "reason": "near_destroyed_visible_main_nexus_under_attack"}
