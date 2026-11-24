"""MacroBot equivalence, the build_config.json schema and its L0 gates (offline)."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.rsi import build_config, macrobot_check
from app.rsi.agentic_review import ReviewWorkspace
from app.rsi.evolution import initialize_batch, read_json
from app.rsi.model_client import write_json
from app.rsi.primitives import fallback_summarize
from app.rsi.seed_v1 import write_seed
from app.rsi.validator import validate_candidate


ROOT = Path(__file__).resolve().parents[1]
FACTS = [{"fact_id": 7, "frame": 120}]


def edited(**values):
    return build_config.dumps({**build_config.defaults(), **values})


class EquivalenceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.macro = Path(temporary.name) / "MacroBot.cpp"
        self.source = (ROOT / "app/MacroBot.cpp").read_text(encoding="utf-8")

    def check(self, text):
        self.macro.write_text(text, encoding="utf-8", newline="\n")
        return macrobot_check.check(self.macro)

    def test_default_parameters_reproduce_the_frozen_baseline(self):
        report = macrobot_check.check()
        self.assertTrue(report["equivalent"])
        self.assertEqual(report["baseline_sha256"], json.loads(
            (ROOT / "baseline/manifest.json").read_text(encoding="utf-8"))["files"]["Bot.cpp"])
        self.assertEqual(report["parameters"], len(build_config.SCHEMA))

    def test_a_parameter_that_changes_a_literal_is_detected(self):
        with self.assertRaisesRegex(ValueError, "differs from baseline"):
            self.check(self.source.replace("frame>cfg.gas1_frame", "frame>=cfg.gas1_frame"))

    def test_added_code_must_be_inert_at_defaults(self):
        line = "    /*+*/if(early(cfg.proactive_cannon_frame) && count(Protoss_Forge)==0) return Protoss_Forge;/*-*/"
        self.assertIn(line, self.source)
        for span in ("/*+*/if(count(Protoss_Forge)==0) return Protoss_Forge;/*-*/",
                     "/*+*/if(early(cfg.gas1_frame) && count(Protoss_Forge)==0) return Protoss_Forge;/*-*/",
                     "/*+*/if(early(cfg.proactive_cannon_frame) || count(Protoss_Forge)==0) return Protoss_Forge;/*-*/"):
            with self.subTest(span=span), self.assertRaisesRegex(ValueError, "not inert"):
                self.check(self.source.replace(line, "    " + span))

    def test_every_schema_key_is_declared_and_used(self):
        with self.assertRaisesRegex(ValueError, "MACRO_PARAMS"):
            self.check(self.source.replace(" X(cannons_per_base)", ""))


class SchemaTests(unittest.TestCase):
    def test_default_text_is_canonical_and_valid(self):
        self.assertEqual(build_config.parse(build_config.DEFAULT_TEXT), build_config.defaults())
        self.assertEqual(build_config.dumps(json.loads(build_config.DEFAULT_TEXT)), build_config.DEFAULT_TEXT)

    def test_invalid_files(self):
        defaults = build_config.defaults()
        missing = dict(defaults)
        del missing["gate2_workers"]
        cases = {
            "missing keys": json.dumps(missing),
            "unknown keys": json.dumps({**defaults, "attack_army": 6}),
            "must be an integer": json.dumps({**defaults, "gas1_frame": 4400.5}),
            "must be an integer from": json.dumps({**defaults, "proactive_cannon_frame": True}),
            "from 8 to 30": json.dumps({**defaults, "gate2_workers": 4}),
            "duplicate key": build_config.DEFAULT_TEXT.replace('"gates_base": 2', '"gates_base": 2, "gates_base": 3'),
            "invalid JSON": "{",
            "one JSON object": "[]",
        }
        for message, text in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                build_config.parse(text)

    def test_edit_limits(self):
        self.assertEqual(build_config.check_edit(build_config.DEFAULT_TEXT, edited(
            proactive_observer_frame=6000, proactive_cannon_frame=7000)),
            {"proactive_observer_frame": [-1, 6000], "proactive_cannon_frame": [-1, 7000]})
        with self.assertRaisesRegex(ValueError, "no value changed"):
            build_config.check_edit(build_config.DEFAULT_TEXT, build_config.DEFAULT_TEXT.replace("\n", " "))
        with self.assertRaisesRegex(ValueError, "changed 4 keys"):
            build_config.check_edit(build_config.DEFAULT_TEXT, edited(
                gate2_workers=10, gates_base=3, gas1_frame=3000, robo_frame=6000))


class GateTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.batch = Path(temporary.name) / "batch"
        write_seed(self.batch)
        self.observations = [fallback_summarize({"frame": frame, "workers": 4, "healthy_army": 8})
                             for frame in (0, 120, 600)]

    def proposal(self, edits):
        return validate_candidate(
            self.batch, {"decision": "modify", "diagnosis": "No detector before the first DT",
                         "distillation_type": "tune_macro", "evidence": [7], "edits": edits},
            FACTS, self.observations, match_number=1)

    def test_seed_holds_the_default_config(self):
        self.assertEqual((self.batch / "versions/v1/build_config.json").read_text(encoding="utf-8"),
                         build_config.DEFAULT_TEXT)

    def test_config_edit_promotes_and_records_changes(self):
        result = self.proposal({"build_config.json": edited(proactive_observer_frame=6500)})
        self.assertEqual(result["validation"], "accepted", result.get("validation_error"))
        self.assertEqual(result["build_config_changes"], {"proactive_observer_frame": [-1, 6500]})
        self.assertEqual(build_config.parse((self.batch / "versions/v2/build_config.json").read_text(
            encoding="utf-8"))["proactive_observer_frame"], 6500)
        event = json.loads((self.batch / "evolution-log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(event["build_config_changes"], result["build_config_changes"])

    def test_config_edits_are_rejected_when_combined_or_too_broad(self):
        rules = (self.batch / "versions/v1/rules.py").read_text(encoding="utf-8") + "\n# note\n"
        combined = self.proposal({"build_config.json": edited(robo_frame=7000), "rules.py": rules})
        self.assertEqual(combined["validation"], "rejected")
        self.assertIn("only file changed", combined["validation_error"])
        broad = self.proposal({"build_config.json": edited(gate2_workers=10, gates_base=3, gas1_frame=3000,
                                                           robo_frame=6000)})
        self.assertTrue(broad["validation_error"].startswith("gate2"))
        invalid = self.proposal({"build_config.json": edited(robo_frame=99999)})
        self.assertIn("robo_frame must be an integer", invalid["validation_error"])
        self.assertEqual(sorted(path.name for path in (self.batch / "versions").iterdir()), ["v1"])


class SeedTests(unittest.TestCase):
    def test_pre_l4_seed_starts_from_the_default_config(self):
        with TemporaryDirectory() as temporary:
            old = Path(temporary) / "old"
            write_seed(old)
            (old / "versions/v1/build_config.json").unlink()
            version = initialize_batch(Path(temporary) / "new", old / "versions/v1")
            self.assertEqual((version / "build_config.json").read_text(encoding="utf-8"), build_config.DEFAULT_TEXT)
            self.assertEqual(read_json(version / "manifest.json")["build_config_source"], "default")

    def test_seed_config_is_kept_and_checked(self):
        with TemporaryDirectory() as temporary:
            old = Path(temporary) / "old"
            write_seed(old)
            (old / "versions/v1/build_config.json").write_text(edited(cannons_per_base=2), encoding="utf-8")
            version = initialize_batch(Path(temporary) / "new", old / "versions/v1")
            self.assertEqual(build_config.parse((version / "build_config.json").read_text(
                encoding="utf-8"))["cannons_per_base"], 2)
            self.assertEqual(read_json(version / "manifest.json")["build_config_source"], "seed")
            (old / "versions/v1/build_config.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing keys"):
                initialize_batch(Path(temporary) / "other", old / "versions/v1")


class ReviewWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.batch = Path(temporary.name) / "batch"
        initialize_batch(self.batch)
        run = self.batch / "match-1"
        write_json(run / "observations.json", [fallback_summarize({"frame": frame}) for frame in (0, 120)])
        trace = run / "bwapi-data/write/astra-trace.jsonl"
        trace.parent.mkdir(parents=True)
        trace.write_text("\n".join(json.dumps(row) for row in (
            {"event": "start", "version": 6}, {"event": "config_loaded", "values": build_config.defaults()},
            {"event": "build_plan", "frame": 1200, "type": "Protoss_Pylon"},
            {"event": "cloak_seen", "frame": 7400, "flag": "cloak", "type": "Protoss_Dark_Templar"},
            {"event": "state", "frame": 7440, "workers": 20}, {"event": "end", "winner": False})) + "\n",
            encoding="utf-8")
        sources = {name: (self.batch / "versions/v1" / name).read_text(encoding="utf-8")
                   for name in ("strategy.md", "observer.py", "tools.py", "rules.py", "build_config.json")}
        self.workspace = ReviewWorkspace(self.batch, {
            "current_sources": sources, "facts": FACTS,
            "matches": [{"match_number": 1, "state": "finished", "opponent": "PylonPuller"}]})

    def test_build_log_rows_carry_frames_and_filter(self):
        self.assertEqual(self.workspace.list_evidence()["1"]["build_log_rows"], 3)
        rows = self.workspace.get_build_log(1, 0, 8000, events=["cloak_seen"])["rows"]
        self.assertEqual([row["frame"] for row in rows], [7400])

    def test_check_candidate_reports_config_changes(self):
        self.workspace.write_policy("build_config.json", edited(proactive_cannon_frame=5000))
        report = self.workspace.check_candidate()
        self.assertEqual(report["build_config_changes"], {"proactive_cannon_frame": [-1, 5000]})
        self.workspace.write_policy("rules.py", self.workspace.sources["rules.py"] + "\n")
        with self.assertRaisesRegex(ValueError, "only file changed"):
            self.workspace.check_candidate()


class ConfirmTests(unittest.TestCase):
    def test_match_requires_the_bot_to_echo_the_installed_config(self):
        import sys
        sys.path.insert(0, str(ROOT / "app"))
        from matches import confirm_build_config
        with TemporaryDirectory() as temporary:
            run = Path(temporary)
            trace = run / "bwapi-data/write/astra-trace.jsonl"
            trace.parent.mkdir(parents=True)
            expected = build_config.defaults()
            with self.assertRaisesRegex(RuntimeError, "None"):
                confirm_build_config(run, expected)
            trace.write_text('{"event":"start"}\n{"event":"config_error","error":"missing x"}\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "missing x"):
                confirm_build_config(run, expected)
            trace.write_text('{"event":"start"}\n' + json.dumps({"event": "config_loaded", "values": {
                **expected, "gate2_workers": 13}}) + "\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                confirm_build_config(run, expected)
            trace.write_text('{"event":"start"}\n' + json.dumps({"event": "config_loaded", "values": expected})
                             + "\n", encoding="utf-8")
            confirm_build_config(run, expected)


if __name__ == "__main__":
    unittest.main()
