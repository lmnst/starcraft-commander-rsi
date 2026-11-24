"""Offline coverage of RSI round evidence, promotion, rollback, and batch accounting."""

from io import BytesIO
import json
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from app.rsi.build_config import DEFAULT_TEXT
from app.rsi.evolution import active_pointer, initialize_batch, read_json, review_round
from app.rsi.model_client import ModelCircuit, ModelClient, write_json
from app.rsi.primitives import fallback_summarize
from app.rsi.seed_v1 import VERSION_FILES
from app.rsi_live import _parser, _play_one, _run_live, main


REFLEX = """def decide_reflex(summary, active_tool, memory):
    if summary["frame"] == 120:
        return {"rule_name": "opening_guard", "action_type": "key",
                "target": "7", "reason": "Guard at opening."}
    return None
"""


def proposal(decision, edits=None):
    return {
        "prediction_check": "N/A", "evidence": [1], "diagnosis": "Frame 0 reports four workers.",
        "hypothesis": "A local guard is reproducible.", "expectation": "One candidate trigger at frame 120.",
        "decision": decision, "distillation_type": "distill_rule" if edits else "none",
        "edits": edits or {},
    }


class MockModel:
    def __init__(self, *answers):
        self.answers = iter(answers)
        self.payloads = []

    def call(self, prompt, payload, archive):
        self.payloads.append(payload)
        answer = next(self.answers)
        report = dict(decoded=answer, error=None, input_tokens=31, output_tokens=11,
                      cached_input_tokens=None, seconds=0.7, model="mock",
                      reported_cost=0.002, cost_currency="USD", usage_missing=["cached_input_tokens"])
        if isinstance(answer, str):
            report.update(decoded=None, error=answer, failure_kind="invalid_response")
        write_json(archive, {**report, "prompt": prompt, "input": payload, "raw": "mock response"})
        return report


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.batch = Path(temporary.name) / "batch"
        self.seed = initialize_batch(self.batch)

    def match(self, number, state="finished", *, winner=True, workers=4):
        run = self.batch / f"match-{number}"
        observations = [fallback_summarize({"frame": frame, "workers": workers})
                        for frame in (0, 120, 240)]
        receipt = {"event": "command", "key": "7", "frame": 131, "status": "applied"}
        write_json(run / "observations.json", observations)
        write_json(run / "timeline.json", [{"frame": 120, "dispatch_frame": 120,
                                            "source": "llm", "status": "native_receipt", "receipt": receipt}])
        write_json(run / "runtime-errors.json", [{"frame": 120, "hook": "example", "error": "example"}])
        return {"match_number": number, "state": state, "winner": winner, "frame": 245,
                "policy_version": active_pointer(self.batch)["version"], "seconds": 2.0,
                "decision_sources": {"llm_key": 1}, "tokens": {"live_input": 3, "live_output": 4}}

    def events(self):
        return [json.loads(row) for row in (self.batch / "evolution-log.jsonl")
                .read_text(encoding="utf-8").splitlines()]

    def test_two_rounds_promote_then_revert_and_account_for_multiple_matches(self):
        model = MockModel(proposal("modify", {"rules.py": REFLEX}), proposal("revert"))
        v2 = review_round([self.match(1)], self.batch, model, round_number=1)
        self.assertEqual(v2["validation"], "accepted", v2)
        self.assertEqual((v2["from_version"], v2["to_version"]), ("v1", "v2"))
        report = read_json(self.batch / "match-1/candidate-check.json")
        self.assertEqual(report["versions"]["candidate"]["reflex_trigger_count"], 1)
        self.assertEqual(report["reflex_frame_diffs"][0]["frame"], 120)
        self.assertEqual(set(model.payloads[0]["current_sources"]), set(VERSION_FILES))
        self.assertIsNone(model.payloads[0]["previous_prediction"])

        result = review_round([self.match(2, "cutoff"), self.match(3, "error")],
                              self.batch, model, round_number=2)
        self.assertEqual((result["validation"], result["to_version"]), ("reverted", "v1"))
        self.assertEqual(active_pointer(self.batch)["version"], "v1")
        self.assertTrue((self.batch / "versions/v2/rules.py").is_file())
        payload = model.payloads[1]
        self.assertEqual(payload["previous_prediction"]["expectation"], v2["expectation"])
        facts = payload["facts"]
        self.assertEqual([fact["fact_id"] for fact in facts], list(range(1, len(facts) + 1)))
        receipts = [fact for fact in facts if fact["event"] == "native_receipt"]
        self.assertEqual([fact["match_number"] for fact in receipts], [2, 3])
        receipt = receipts[0]
        self.assertEqual(facts[receipt["before_state"] - 1]["frame"], 120)
        self.assertEqual(facts[receipt["after_state"] - 1]["frame"], 240)
        self.assertEqual([fact["state"] for fact in facts if fact["event"] == "terminal"],
                         ["cutoff", "error"])
        self.assertTrue(all(match["winner"] is None for match in payload["matches"]))
        self.assertEqual([event["validation"] for event in self.events()], ["accepted", "reverted"])
        self.assertEqual(self.events()[0]["changed_functions"], {"rules.py": ["decide_reflex"]})

        index = read_json(self.batch / "batch.json")
        self.assertEqual([index["statistics"][key] for key in ("wins", "losses", "cutoffs", "errors")],
                         [1, 0, 1, 1])
        self.assertEqual(index["statistics"]["tokens"], {"live_input": 9, "live_output": 12,
                                                          "review_input": 62, "review_output": 22})
        self.assertEqual(index["statistics"]["review_usage_missing"]["cached_input_tokens"], 2)
        self.assertEqual(index["statistics"]["runtime_error_count"], 3)
        self.assertEqual(index["rounds"][1]["match_numbers"], [2, 3])
        self.assertEqual(read_json(self.batch / "match-3/review-call-1.json")["raw"], "mock response")

    def test_illegal_candidate_keeps_active_version(self):
        invalid = proposal("modify", {"rules.py": "import os\n" + REFLEX})
        model = MockModel(invalid, invalid, invalid)
        result = review_round([self.match(1)], self.batch, model, round_number=1)
        self.assertEqual(result["validation"], "rejected")
        self.assertIn("gate2", result["validation_error"])
        self.assertEqual(active_pointer(self.batch)["version"], "v1")
        self.assertFalse((self.batch / "versions/v2").exists())
        self.assertFalse((self.batch / "match-1/candidate-check.json").exists())
        self.assertEqual(self.events()[0]["validation"], "rejected")
        audit = read_json(self.batch / "match-1/review-attempt-1.json")
        self.assertIn("+import os", audit["diff"]["rules.py"])
        self.assertIn("gate2", model.payloads[1]["retry_feedback"]["validation_error"])
        self.assertEqual(len(self.events()), 1)

    def test_all_completed_traces_must_pass_before_promotion(self):
        changed = 'def decide_reflex(summary, active_tool, memory):\n    return 1 / (4 - summary["workers"])\n'
        changed = changed.replace('    return 1 /', '    probe = 1 /') + '    return None\n'
        invalid = proposal("modify", {"rules.py": changed})
        model = MockModel(invalid, invalid, invalid)
        result = review_round([self.match(1, workers=8), self.match(2, workers=4)],
                              self.batch, model, round_number=1)
        self.assertIn("ZeroDivisionError", result["validation_error"])
        self.assertEqual(active_pointer(self.batch)["version"], "v1")

    def test_json_and_shadow_retries_publish_once_and_account_for_every_call(self):
        broken_shadow = "def decide_reflex(summary, active_tool, memory): return 1 / 0\n"
        model = MockModel("invalid JSON", proposal("modify", {"rules.py": broken_shadow}),
                          proposal("modify", {"rules.py": REFLEX}))
        record = self.match(1, "cutoff")
        record.update(adjudicated_outcome="cutoff_loss", cutoff={"reason": "max_wall_seconds", "frame": 245},
                      lock_defense={"fraction": 0.25}, army_peaks={"army": {"value": 4, "frame": 120}})
        result = review_round([record], self.batch, model, round_number=1)
        self.assertEqual((result["validation"], result["to_version"]), ("accepted", "v2"))
        self.assertEqual(model.payloads[1]["retry_feedback"]["validation_error"], "invalid JSON")
        self.assertIn("gate3", model.payloads[2]["retry_feedback"]["validation_error"])
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(len(result["review_attempts"]), 3)
        terminal = next(fact for fact in model.payloads[0]["facts"] if fact["event"] == "terminal")
        self.assertEqual((terminal["natural_outcome"], terminal["adjudicated_outcome"]), ("unknown", "cutoff_loss"))
        self.assertEqual(terminal["lock_defense"]["fraction"], 0.25)
        self.assertEqual(terminal["army_peaks"]["army"]["value"], 4)
        self.assertEqual(result["review_usage"]["input_tokens"], 93)
        self.assertAlmostEqual(result["review_usage"]["reported_cost"], 0.006)
        index = read_json(self.batch / "batch.json")
        self.assertEqual(index["statistics"]["review_calls"], 3)
        self.assertEqual(index["statistics"]["review_usage_missing"]["cached_input_tokens"], 3)
        self.assertEqual(index["statistics"]["adjudicated_losses"], 1)
        self.assertEqual(index["statistics"]["losses"], 0)

    def test_seed_history_counts_toward_the_cumulative_keep_gate(self):
        previous_batch = self.batch
        first = review_round([self.match(1, winner=False)], previous_batch, MockModel(proposal("keep")),
                             round_number=1)
        self.assertEqual(first["validation"], "kept")
        self.batch = previous_batch.parent / "single-round"
        initialize_batch(self.batch, previous_batch / "versions/v1")
        reviewer = MockModel(proposal("keep"), proposal("modify", {"rules.py": REFLEX}))
        result = review_round([self.match(1, winner=False)], self.batch, reviewer, round_number=1)
        history = reviewer.payloads[0]["version_history"]
        self.assertEqual(history["totals"]["losses"], 1)
        self.assertEqual(history["matches"][0]["native_key_statuses"], {"7": {"applied": 1}})
        self.assertEqual(history["decisions"][0]["decision"], "keep")
        self.assertIn("2 losses and no wins", reviewer.payloads[1]["retry_feedback"]["validation_error"])
        self.assertEqual((result["validation"], active_pointer(self.batch)["version"]), ("accepted", "v2"))

    def test_inconclusive_and_degraded_results_do_not_count_as_losses(self):
        timeout = {**self.match(1, "cutoff"), "adjudicated_outcome": "inconclusive"}
        degraded = {**self.match(2, winner=False), "degraded": True}
        result = review_round([timeout, degraded, self.match(3, winner=False)], self.batch,
                              MockModel(proposal("keep")), round_number=1)
        self.assertEqual(result["validation"], "kept")
        statistics = read_json(self.batch / "batch.json")["statistics"]
        self.assertEqual((statistics["inconclusive"], statistics["degraded"], statistics["losses"]), (1, 1, 2))

    def test_revert_without_parent_is_retried(self):
        model = MockModel(proposal("revert"), proposal("keep"))
        result = review_round([self.match(1)], self.batch, model, round_number=1)
        self.assertIn("no parent", model.payloads[1]["retry_feedback"]["validation_error"])
        self.assertEqual(result["validation"], "kept")

    def test_schema_retry_sends_schema_failure(self):
        model = MockModel({"decision": "keep"}, proposal("keep"))
        result = review_round([self.match(1)], self.batch, model, round_number=1)
        self.assertEqual(result["validation"], "kept")
        self.assertIn("schema:", model.payloads[1]["retry_feedback"]["validation_error"])
        self.assertEqual(len(self.events()), 1)

    def test_codex_circuit_counts_outer_calls_and_stops_next_match(self):
        circuit = ModelCircuit()
        active = ModelClient(circuit=circuit)
        reviewer = ModelClient(circuit=circuit)
        quota_message = "You’ve hit your usage limit."
        quota = json.dumps({"type": "error", "message": quota_message})
        failed = json.dumps({"type": "turn.failed", "error": {"message": quota_message}})
        malformed = json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": "not JSON: invalid api key",
        }}) + '\n{"type":"turn.completed","usage":{"input_tokens":3}}'
        responses = [
            CompletedProcess([], 1, stdout=quota + "\n" + failed, stderr="Reconnecting...\n" + quota_message),
            CompletedProcess([], 0, stdout=malformed, stderr=""),
            CompletedProcess([], 1, stdout=quota + "\n" + failed, stderr=""),
            CompletedProcess([], 1, stdout="", stderr="Server returned 401 Unauthorized"),
            TimeoutExpired([], 1, output=quota.encode(), stderr=b"Reconnecting..."),
        ]

        def play(_args, batch, _version, model, number, **_kwargs):
            run = batch / f"match-{number}"
            quota_report = model.call("prompt", {}, run / "decision-1.json")
            self.assertEqual(quota_report["circuit"]["failures"], 1)
            bad_json = model.call("prompt", {}, run / "decision-2.json")
            self.assertEqual((bad_json["failure_kind"], circuit.failures), ("invalid_response", 0))
            model.call("prompt", {}, run / "decision-3.json")
            return self.match(number)

        args = _parser().parse_args(["--rounds", "2"])
        with patch("app.rsi_live._play_one", side_effect=play) as engine, \
                patch("app.rsi.model_client.subprocess.run", side_effect=responses) as subprocess_run:
            self.assertEqual(_run_live(args, self.batch, active, reviewer, Event()), 1)
            after = reviewer.call("prompt", {}, self.batch / "blocked.json")
        engine.assert_called_once()
        self.assertEqual(subprocess_run.call_count, 5)
        self.assertTrue(circuit.open)
        self.assertEqual(after["failure_kind"], "circuit_open")
        first_review = read_json(self.batch / "match-1/review-call-1.json")
        second_review = read_json(self.batch / "match-1/review-call-2.json")
        self.assertEqual((first_review["failure_kind"], second_review["failure_kind"]), ("auth", "quota"))
        self.assertFalse((self.batch / "match-1/review-call-3.json").exists())
        index = read_json(self.batch / "batch.json")
        self.assertEqual(index["state"], "model_circuit_open")
        self.assertEqual(index["statistics"]["adjudicated_losses"], 0)

    def test_openai_compatible_usage_and_http_failure_classification(self):
        client = ModelClient("openai", "test-model", "omit", "https://api.invalid/v1", api_key="test-key")
        success = Mock(status=200)
        success.read.return_value = json.dumps({
            "usage": {"prompt_tokens": 19, "completion_tokens": 7, "cost": 0.013,
                      "cost_currency": "USD", "prompt_tokens_details": {"cached_tokens": 5}},
            "choices": [{"message": {"content": '{"decision":"keep"}'}}],
        }).encode()
        context = Mock()
        context.__enter__ = Mock(return_value=success)
        context.__exit__ = Mock(return_value=False)
        busy = HTTPError("https://api.invalid/v1", 429, "rate limit", None, BytesIO(b"{}"))
        quota = HTTPError("https://api.invalid/v1", 429, "too many requests", None,
                          BytesIO(b'{"error":{"code":"insufficient_quota"}}'))
        auth = HTTPError("https://api.invalid/v1", 403, "forbidden", None,
                         BytesIO(b'{"error":{"code":"invalid_api_key"}}'))
        with patch("app.rsi.model_client.urlopen", side_effect=[context, busy, quota, auth]) as http:
            report = client.call("prompt", {}, self.batch / "ok.json")
            throttled = client.call("prompt", {}, self.batch / "busy.json")
            blocked = client.call("prompt", {}, self.batch / "quota.json")
            denied = client.call("prompt", {}, self.batch / "auth.json")
        self.assertEqual((report["input_tokens"], report["output_tokens"], report["cached_input_tokens"]), (19, 7, 5))
        self.assertEqual((report["reported_cost"], report["cost_currency"]), (0.013, "USD"))
        self.assertEqual((throttled["failure_kind"], blocked["failure_kind"], denied["failure_kind"]),
                         ("transient", "quota", "auth"))
        self.assertEqual(client.circuit.failures, 2)
        request = http.call_args_list[0].args[0]
        self.assertEqual(request.full_url, "https://api.invalid/v1/chat/completions")
        self.assertNotIn("reasoning_effort", json.loads(request.data))
        self.assertNotIn("test-key", (self.batch / "ok.json").read_text(encoding="utf-8"))

    def test_multiple_completed_matches_have_independent_comparisons(self):
        model = MockModel(proposal("modify", {"rules.py": REFLEX}))
        result = review_round([self.match(1), self.match(2, "cutoff")],
                              self.batch, model, round_number=1)
        self.assertEqual(result["validation"], "accepted")
        report = read_json(self.batch / "match-2/candidate-check.json")
        self.assertEqual(set(report["matches"]), {"1", "2"})
        for comparison in report["matches"].values():
            self.assertEqual(comparison["versions"]["candidate"]["reflex_trigger_count"], 1)

    def test_candidate_can_repair_an_installed_runtime_error(self):
        rules = self.seed / "rules.py"
        rules.write_text("def decide_reflex(summary, active_tool, memory): return 1 / 0\n", encoding="utf-8")
        model = MockModel(proposal("modify", {"rules.py": REFLEX}))
        result = review_round([self.match(1)], self.batch, model, round_number=1)
        self.assertEqual(result["validation"], "accepted")
        report = read_json(self.batch / "match-1/candidate-check.json")
        self.assertIn("ZeroDivisionError", report["versions"]["parent"]["error"])
        self.assertIsNone(report["reflex_frame_diffs"])

    def test_rollback_pointer_failure_restores_log_and_preserves_snapshot(self):
        model = MockModel(proposal("modify", {"rules.py": REFLEX}), proposal("revert"))
        review_round([self.match(1)], self.batch, model, round_number=1)
        replace = Path.replace

        def reject_rollback(path, target):
            if path.parent.name.startswith(".rollback-") and Path(target).name == "active.json":
                raise OSError("simulated pointer replacement failure")
            return replace(path, target)

        with patch.object(Path, "replace", reject_rollback):
            result = review_round([self.match(2)], self.batch, model, round_number=2)
        self.assertEqual(result["validation"], "rejected")
        self.assertIn("rollback failed", result["validation_error"])
        self.assertEqual(active_pointer(self.batch)["version"], "v2")
        self.assertEqual([event["validation"] for event in self.events()], ["accepted", "rejected"])

    def test_seeded_batch_checks_the_seed_version_prediction(self):
        from shutil import copytree
        from app.rsi.evolution import _prior_prediction

        copytree(self.seed, self.batch / "versions/v2")
        index = read_json(self.batch / "batch.json")
        for number, (validation, version) in enumerate((("accepted", "v2"), ("kept", "v3")), 1):
            write_json(self.batch / f"review-{number}.json", {"validation": validation, "to_version": version,
                                                              "hypothesis": f"h{number}", "expectation": f"e{number}"})
            index["rounds"].append({"round": number, "review_path": f"review-{number}.json", "to_version": version})
        write_json(self.batch / "batch.json", index)
        seeded = self.batch.parent / "seeded"
        initialize_batch(seeded, self.batch / "versions/v2")
        self.assertEqual(_prior_prediction(seeded), {"hypothesis": "h1", "expectation": "e1", "to_version": "v2"})
        fresh = self.batch.parent / "fresh"
        initialize_batch(fresh)
        self.assertIsNone(_prior_prediction(fresh))

    def test_error_only_round_never_calls_model(self):
        model = Mock()
        result = review_round([self.match(1, "error")], self.batch, model, round_number=1)
        self.assertEqual(result["validation"], "skipped")
        model.call.assert_not_called()

    def test_live_entry_leaves_match_directory_creation_to_the_engine(self):
        run = self.batch / "match-1"
        adapter = Mock()
        adapter.speed = 42
        adapter.match_timeout = 1800

        def play(on_observation, on_receipt, on_idle=None):
            self.assertFalse(run.exists())
            run.mkdir()
            return {"state": "finished", "frame": 110, "winner": True, "natural_outcome": "win"}

        adapter.run.side_effect = play
        model = Mock()
        with patch("app.rsi_live.NativeV17Adapter", return_value=adapter) as native:
            result = _play_one(_parser().parse_args([]), self.batch, self.seed, model, 1)
        # The seed's build_config.json is the baseline-equivalent default; MacroBot plays with it.
        native.assert_called_once_with(run, "Destination", "PylonPuller", 42, match_timeout=1800, hold_lock=False,
                                       build_config=DEFAULT_TEXT)
        self.assertEqual(_parser().parse_args([]).max_frames, 36000)
        model.call.assert_not_called()
        self.assertEqual(result["state"], "finished")
        self.assertTrue((run / "match.json").is_file())

    def test_matches_cycle_through_listed_opponents(self):
        from app.rsi_live import _failed_pairs, _opponent_for

        args = _parser().parse_args(["--opponent", "PylonPuller,Locutus,McRave"])
        self.assertEqual([_opponent_for(args, number) for number in range(1, 6)],
                         ["PylonPuller", "Locutus", "McRave", "PylonPuller", "Locutus"])
        adapter = Mock(speed=42, match_timeout=1800)
        adapter.run.return_value = {"state": "finished", "frame": 110, "winner": True, "natural_outcome": "win"}
        with patch("app.rsi_live.NativeV17Adapter", return_value=adapter) as native:
            result = _play_one(args, self.batch, self.seed, Mock(), 2)
        self.assertEqual(native.call_args.args[2], "Locutus")
        self.assertEqual(result["opponent"], "Locutus")
        # validation/compatibility.json records these two pairs as failed.
        self.assertEqual(_failed_pairs("Destination", ["PylonPuller", "Skynet"]), ["Skynet"])
        self.assertEqual(_failed_pairs("Heartbreak Ridge", ["PurpleWave", "Skynet"]), ["PurpleWave"])

    def test_offline_fixed_seed_never_runs_engine(self):
        archive = self.batch / "match-1/observations.json"
        self.match(1)
        alternate = self.batch.parent / "alternate"
        with patch("app.rsi_live.NativeV17Adapter") as engine, patch("app.rsi_live.ModelClient") as model:
            result = main(["--offline-evolve-test", str(archive), "--batch-dir", str(alternate),
                           "--seed-version", str(self.seed), "--no-evolve"])
        self.assertEqual(result, 0)
        engine.assert_not_called()
        model.return_value.call.assert_not_called()
        self.assertEqual(active_pointer(alternate)["version"], "v1")
        seed_manifest = read_json(alternate / "versions/v1/manifest.json")
        self.assertEqual(seed_manifest["seed_source"], str(self.seed.resolve()))
        record = read_json(alternate / "batch.json")["matches"][0]
        self.assertEqual(record["state"], "archive_only")


if __name__ == "__main__":
    unittest.main()
