"""Focused offline tests for RSI v1 and its four candidate gates."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from app.rsi.primitives import BASE_PRIMITIVES, fallback_summarize
from app.rsi.sandbox import load_version
from app.rsi.seed_v1 import write_seed
from app.rsi.validator import replay_version, validate_candidate


FACTS = [{"fact_id": 7, "frame": 120}]


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.batch = Path(temporary.name)
        self.seed = write_seed(self.batch)
        self.observations = [fallback_summarize({"frame": frame, "workers": 4, "healthy_army": 8})
                             for frame in (0, 120, 600, 1200, 1920, 2400)]
        self.pointer = self.batch / "active.json"
        self.before = self.pointer.read_bytes()

    def proposal(self, edits, evidence=None, **kwargs):
        return validate_candidate(
            self.batch, {"decision": "modify", "diagnosis": "Observed signal",
                         "distillation_type": "distill_rule",
                         "evidence": [7] if evidence is None else evidence, "edits": edits},
            FACTS, self.observations, match_number=1, tokens={"live_input": 12},
            decision_sources={"rule": 0}, **kwargs,
        )

    def check_rejection(self, result, gate, text):
        self.assertEqual(result["validation"], "rejected")
        self.assertTrue(result["validation_error"].startswith(gate))
        self.assertIn(text, result["validation_error"])
        self.assertEqual(self.pointer.read_bytes(), self.before)
        self.assertEqual(sorted(path.name for path in (self.batch / "versions").iterdir()), ["v1"])
        review = json.loads((self.batch / "match-1" / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(review["validation_error"], result["validation_error"])
        events = (self.batch / "evolution-log.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(json.loads(events[-1])["validation_error"], result["validation_error"])

    def test_valid_edit_promotes_with_same_frame_comparison(self):
        result = self.proposal({"rules.py": '''from dataclasses import dataclass
from math import hypot

@dataclass
class Signal:
    frame: int

def decide_reflex(summary, active_tool, memory):
    if Signal(summary["frame"]).frame == 120 and hypot(3, 4) == 5:
        return {"rule_name": "opening_guard", "action_type": "key",
                "target": "7", "reason": "Guard at the opening."}
    return None
'''}, max_frames=4)
        self.assertEqual(result["validation"], "accepted", result)
        active = json.loads(self.pointer.read_text(encoding="utf-8"))
        self.assertEqual(active, {"version": "v2", "parent": "v1", "path": "versions/v2"})
        installed = self.batch / active["path"]
        self.assertEqual((installed / "observer.py").read_bytes(),
                         (self.seed / "observer.py").read_bytes())
        manifest = json.loads((installed / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["created_after_match"], 1)
        path = self.batch / "match-1" / "candidate-check.json"
        report = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(report["sampled"])
        self.assertEqual(report["source_observations"], 6)
        self.assertEqual(report["versions"]["parent"]["observations_replayed"], 4)
        self.assertEqual(report["versions"]["parent"]["reflex_trigger_count"], 0)
        self.assertEqual(report["versions"]["candidate"]["reflex_trigger_count"], 1)
        self.assertEqual(report["reflex_frame_diffs"][0]["frame"], 120)
        event = json.loads((self.batch / "evolution-log.jsonl").read_text(encoding="utf-8"))
        self.assertEqual(event["changed_functions"], {"rules.py": ["decide_reflex"]})
        self.assertEqual(event["tokens"], {"live_input": 12})
        self.assertEqual(event["decision_sources"], {"rule": 0})

    def test_forbidden_imports_and_reflection(self):
        for prefix, error in (
            ("import os\n", "forbidden import os"),
            ("import subprocess\n", "forbidden import subprocess"),
            ("from copy import sys\n", "unavailable import sys"),
            ("from . import rules\n", "forbidden import"),
        ):
            with self.subTest(prefix=prefix):
                self.check_rejection(self.proposal({
                    "rules.py": prefix + "def decide_reflex(a, b, c): return None\n",
                }), "gate2", error)
        self.check_rejection(self.proposal({
            "rules.py": "def decide_reflex(a, b, c): return __builtins__\n",
        }), "gate2", "__builtins__")
        self.check_rejection(self.proposal({
            "rules.py": "def decide_reflex(a, b, c): return a.__class__\n",
        }), "gate2", "__class__")

    def test_edit_count_paths_limits_and_evidence(self):
        for edits, error in (
            ({"strategy.md": "", "observer.py": "", "rules.py": ""}, "whitelist"),
            ({"../rules.py": ""}, "whitelist"),
            ({"strategy.md": "中" * 2001}, "2000"),
            ({"rules.py": " " * 12001}, "12000"),
        ):
            with self.subTest(error=error):
                self.check_rejection(self.proposal(edits), "gate1", error)
        for evidence in ([True], [7.0], [8], []):
            with self.subTest(evidence=evidence):
                self.check_rejection(self.proposal({"strategy.md": "Changed."}, evidence),
                                     "gate1", "integer IDs")

    def test_division_failure_is_located(self):
        self.check_rejection(self.proposal({
            "rules.py": "def decide_reflex(a, b, c): return 1 / 0\n",
        }), "gate3", "rules.decide_reflex at frame 0: ZeroDivisionError")

    def test_top_level_execution_deadline(self):
        self.check_rejection(self.proposal({
            "rules.py": "while True:\n    pass\n\ndef decide_reflex(a, b, c): return None\n",
        }), "gate3", "exceeded 3s loading or executing policy")

    def test_invalid_shadow_contracts(self):
        seed = (self.seed / "observer.py").read_text(encoding="utf-8")
        bad_scripts = (
            ("observer.py", seed.replace('return summary', 'return {}'), "missing fields"),
            ("observer.py", seed.replace('return summary', 'summary["big"] = "中" * 3000\n    return summary'),
             "limit is 8192"),
            ("observer.py", seed.replace('return "opening"', 'return ""'), "wake reason"),
            ("rules.py", '''def decide_reflex(a, b, c):
    return {"rule_name": "bad_key", "action_type": "key", "target": "x", "reason": "Test."}
''', "invalid reflex action"),
            ("tools.py", '''def step(summary, args, state):
    return {"status": "emit_key", "key": "x", "reason": "Test."}

TOOLS = {"bad": {"description": "Test.", "args_schema": {}, "step": step}}
''', "tools.bad.step at frame 0"),
        )
        for name, source, error in bad_scripts:
            with self.subTest(error=error):
                self.check_rejection(self.proposal({name: source}), "gate3", error)

    def test_failed_atomic_pointer_keeps_original_version(self):
        replace = Path.replace

        def blocked_pointer(path, target):
            if target == self.pointer:
                raise PermissionError("pointer update failed")
            return replace(path, target)

        with patch.object(Path, "replace", blocked_pointer):
            result = self.proposal({"strategy.md": "Revised strategy."})
        self.check_rejection(result, "gate4", "pointer update failed")
        log = (self.batch / "evolution-log.jsonl").read_text(encoding="utf-8")
        self.assertEqual(len(log.splitlines()), 1)

    def test_seed_tools_wait_for_confirmation(self):
        policy = load_version(self.seed)
        for name, next_key in (("squad_attack_nearby", "5"), ("squad_assemble_front", "8")):
            with self.subTest(name=name):
                step = policy.tools.TOOLS[name]["step"]
                state = {}
                self.assertEqual(step({"mode": "all", "healthy_army": 7}, {}, {})["status"], "abort")
                self.assertEqual(step({"mode": "all", "healthy_army": 8}, {}, state)["key"], "3")
                self.assertEqual(step({"mode": "all", "healthy_army": 8}, {}, state)["status"], "wait")
                self.assertEqual(step({"mode": "squad", "healthy_army": 8}, {}, state)["key"], next_key)
                self.assertEqual(step({"mode": "squad", "healthy_army": 8}, {}, state)["status"], "done")

    def test_seed_period_uses_last_actual_shadow_wake(self):
        replay = replay_version(self.seed, self.observations)
        self.assertEqual(replay["wake_frames"], [{"frame": 120, "reason": "opening"},
                                                 {"frame": 1920, "reason": "periodic"}])
        self.assertEqual(replay["reflex_trigger_count"], 0)
        self.assertEqual(set(BASE_PRIMITIVES), set("1234567890-"))


if __name__ == "__main__":
    unittest.main()
