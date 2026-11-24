"""Offline checks for the L0 situation digest and the seed idle-defense reflex."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.rsi import situation
from app.rsi.primitives import fallback_summarize
from app.rsi.runner import compact_receipt, decision_payload
from app.rsi.sandbox import load_sources
from app.rsi.seed_v1 import SEED_SOURCES, write_seed
from app.rsi.validator import replay_version


HOME = [2112, 3824]


def unit(unit_id, name, type_id, position, *, own=True, completed=True, **fields):
    return {"id": unit_id, "self": own, "type": type_id, "type_name": name, "position": position,
            "completed": completed, "sight": 256, **fields}


def raw(frame, units=(), **fields):
    row = {"frame": frame, "workers": 1, "army": 2, "healthy_army": 2, "center": HOME,
           "own_base_positions": [HOME], "units": list(units), **fields}
    return {**fallback_summarize(row), **row}


class SituationTests(unittest.TestCase):
    def test_own_composition_detectors_tech_and_progress(self):
        digest = situation.build_situation(raw(0, [
            unit(1, "Probe", 64, HOME), unit(2, "Dragoon", 66, HOME), unit(3, "Zealot", 65, HOME),
            unit(4, "Observer", 84, HOME), unit(5, "Nexus", 154, HOME), unit(6, "Gateway", 160, HOME),
            unit(7, "Photon Cannon", 162, HOME), unit(8, "Robotics Support Bay", 171, HOME),
            unit(9, "Templar Archives", 165, HOME, completed=False),
        ]), {})
        own = digest["own"]
        self.assertEqual(own["workers"], 1)
        self.assertEqual(own["army_types"], {"Dragoon": 1, "Zealot": 1})
        self.assertEqual(own["detectors"], {"Observer": 1, "Photon Cannon": 1})
        self.assertEqual(own["bases"], 1)
        self.assertEqual(own["production"], {"Gateway": 1, "Robotics Facility": 0, "Stargate": 0})
        self.assertEqual(own["tech"], ["Robotics Support Bay"])
        self.assertEqual(own["in_progress"], {"Templar Archives": 1})

    def test_army_zone_uses_own_bases_and_remembered_enemy_buildings(self):
        memory = {}
        enemy_gate = unit(50, "Gateway", 160, [1056, 400], own=False)
        self.assertEqual(situation.build_situation(raw(0), memory)["army"]["zone"], "at_home")
        self.assertEqual(situation.build_situation(raw(120, [enemy_gate], center=[1100, 900]), memory)["army"]["zone"],
                         "near_enemy")
        self.assertEqual(situation.build_situation(raw(240, center=[1600, 2400]), memory)["army"]["zone"], "mid_map")
        self.assertEqual(situation.build_situation(raw(360, army=0), memory)["army"]["zone"], "none")

    def test_enemy_memory_survives_lost_vision_until_the_site_is_seen_empty(self):
        memory = {}
        bay = unit(60, "Robotics Support Bay", 171, [1000, 300], own=False)
        reaver = unit(61, "Reaver", 103, [1500, 1500], own=False, attacking=True)
        first = situation.build_situation(raw(120, [bay, reaver, unit(62, "Scarab", 85, [1500, 1500], own=False)]),
                                          memory)
        self.assertEqual(first["enemy"]["visible_army"], {"Reaver": 1})
        self.assertEqual(first["army"]["enemy_engaged"], 1)
        later = situation.build_situation(raw(600), memory)["enemy"]
        self.assertEqual(later["seen_types"], {"Reaver": {"first": 120, "last": 120},
                                               "Robotics Support Bay": {"first": 120, "last": 120}})
        self.assertEqual(later["known_buildings"], [{"type": "Robotics Support Bay", "position": [1000, 300],
                                                     "last_seen": 120}])
        self.assertEqual(later["last_army"], {"frame": 120, "count": 1, "types": {"Reaver": 1},
                                              "center": [1500, 1500]})
        scout = unit(2, "Probe", 64, [1050, 320])
        cleared = situation.build_situation(raw(720, [scout]), memory)["enemy"]
        self.assertEqual(cleared["known_buildings"], [])
        self.assertIn("Robotics Support Bay", cleared["seen_types"])

    def test_economy_window_and_defense_timers(self):
        memory = {}
        situation.build_situation(raw(0, gathered_minerals=100, gathered_gas=0), memory)
        situation.build_situation(raw(600, gathered_minerals=500, gathered_gas=40,
                                      visible_enemies_within_384px={"own_bases": {"Zealot": 2}}), memory)
        situation.build_situation(raw(1200, gathered_minerals=900, gathered_gas=80, stance="defend"), memory)
        digest = situation.build_situation(raw(1800, gathered_minerals=1300, gathered_gas=120, stance="defend"),
                                           memory)
        self.assertEqual(digest["economy"]["recent_minerals"], 800)
        self.assertEqual(digest["economy"]["recent_window_frames"], 1200)
        self.assertEqual(digest["defense"]["defense_locked_frames"], 600)
        self.assertEqual(digest["defense"]["frames_since_enemy_near_base"], 1200)
        released = situation.build_situation(raw(1920), memory)
        self.assertEqual(released["defense"]["defense_locked_frames"], 0)

    def test_policy_cannot_replace_the_digest(self):
        with self.assertRaisesRegex(ValueError, "reserved"):
            situation.merge({"situation": {}}, {})

    def test_receipt_id_lists_become_counts(self):
        receipt = {"key": "-", "frame": 8, "units": [1, 2, 3], "army_ids": [1, 2], "goal": [1, 2]}
        self.assertEqual(compact_receipt(receipt), {"key": "-", "frame": 8, "goal": [1, 2],
                                                    "units_count": 3, "army_ids_count": 2})
        payload = decision_payload("v1", "", {}, "opening", None, False, {}, None, [], receipt)
        self.assertNotIn("units", payload["latest_receipt"])


class IdleDefenseRuleTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_sources(SEED_SOURCES).rules

    def decide(self, stance, locked, since):
        summary = {"stance": stance, "situation": {"defense": {
            "defense_locked_frames": locked, "frames_since_enemy_near_base": since}}}
        return self.rules.decide_reflex(summary, None, {})

    def test_releases_only_an_idle_lock(self):
        self.assertEqual(self.decide("defend", 1440, 5000)["target"], "-")
        self.assertIsNone(self.decide("defend", 1439, 5000))
        self.assertIsNone(self.decide("defend", 5000, 1000))
        self.assertIsNone(self.decide("baseline", 5000, 5000))

    def test_shadow_replay_recomputes_timers_over_the_full_stream(self):
        contact = {"own_bases": {"Zealot": 1}}
        stream = [raw(frame, stance="defend" if frame >= 1200 else "baseline",
                      visible_enemies_within_384px=contact if frame == 3000 else
                      {"own_bases": {}, "army_center": {}, "squad": {}})
                  for frame in range(0, 4801, 120)]
        with TemporaryDirectory() as temporary:
            seed = write_seed(Path(temporary))
            full = replay_version(seed, stream)
            # Frames 0, 1200, 2400, 3600 and 4800; the contact at 3000 is not sampled.
            sampled = replay_version(seed, stream, max_frames=5)
        self.assertEqual(full["reflex_frames"][0]["frame"], 2640)
        self.assertEqual(full["reflex_frames"][0]["rule_name"], "release_idle_defense")
        self.assertNotIn(3120, [row["frame"] for row in full["reflex_frames"]])
        self.assertEqual([row["frame"] for row in sampled["reflex_frames"]], [4800])


if __name__ == "__main__":
    unittest.main()
