"""Offline checks for model failure classes, review backoff, batch error tolerance and map starts."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event
import unittest
from unittest.mock import patch

from app.rsi import evolution
from app.rsi.adjudication import map_enemy_start
from app.rsi.evolution import initialize_batch, read_json, review_round
from app.rsi.model_client import ModelCircuit, _decode, _provider_failure, _retry_hint, write_json
from app.rsi.primitives import fallback_summarize
from app.rsi_live import _next_wave, _parser, _preflight, _run_live


# Error text recorded in matches/rsi/20261123-180332-804294/match-2 (decision-050 and decision-078).
CODEX_USAGE_LIMIT = ('{"type":"turn.failed","error":{"message":"You’ve hit your usage limit. Upgrade to Pro '
                     '(https://chatgpt.com/explore/pro), visit https://chatgpt.com/settings/usage to purchase more '
                     'credits or try again at 7:42 PM."}}')
CODEX_UNAUTHORIZED = ('{"type":"turn.failed","error":{"message":"unexpected status 401 Unauthorized: Missing bearer '
                      'or basic authentication in header, url: https://api.openai.com/v1/responses"}}')
SERVICE_BUSY = "stream error: The service is temporarily unable to handle this request. Please try again later."
KEEP = {"prediction_check": "N/A", "evidence": [], "diagnosis": "Nothing new.", "hypothesis": "Stable.",
        "expectation": "Same result.", "decision": "keep", "distillation_type": "none", "edits": {}}


class FailureClassTests(unittest.TestCase):
    def test_message_text_decides_before_status(self):
        self.assertEqual(_provider_failure([CODEX_USAGE_LIMIT]), "quota")
        self.assertEqual(_provider_failure([CODEX_UNAUTHORIZED]), "auth")
        self.assertEqual(_provider_failure([SERVICE_BUSY]), "transient")
        self.assertEqual(_provider_failure(['{"error":{"code":"insufficient_quota"}}'], status=429), "quota")
        self.assertEqual(_provider_failure(["{}"], status=429), "transient")
        self.assertEqual(_provider_failure(["{}"], status=503), "transient")
        self.assertEqual(_provider_failure(["{}"], status=401), "auth")
        self.assertIsNone(_provider_failure(["{}"], status=400))
        self.assertEqual(_retry_hint({"stdout": CODEX_USAGE_LIMIT}), "7:42 PM")

    def test_transient_failures_neither_count_nor_reset_the_circuit(self):
        circuit = ModelCircuit()
        for kind in ("quota", "transient", "timeout", "quota"):
            circuit.record(kind, "7:42 PM" if kind == "quota" else None)
        self.assertEqual((circuit.failures, circuit.open), (2, False))
        circuit.record("auth")
        self.assertTrue(circuit.open)
        self.assertIn("7:42 PM", circuit.reason)
        circuit = ModelCircuit()
        for kind in ("quota", "quota", None, "quota"):
            circuit.record(kind)
        self.assertFalse(circuit.open)

    def test_last_embedded_object_is_decoded(self):
        self.assertEqual(_decode('```json\n{"ok": true}\n```'), {"ok": True})
        self.assertEqual(_decode('```\n{"ok": {"nested": [1]}}\n```\n'), {"ok": {"nested": [1]}})
        # Opus reply shapes from matches/rsi/20261124-133626-533308 (match-1 decision-006, match-2 decision-010).
        self.assertEqual(_decode('A Probe scouts us; keep the baseline.\n\n```json\n{"ok": true}\n```'),
                         {"ok": True})
        self.assertEqual(_decode('A DT shadows the army.\n\n{"ok": true}'), {"ok": True})
        # Self-corrected replies from matches/rsi/20261124-143925-255737 (match-2 decision-007 and -022).
        corrected = ('{"action_type":"keep_current","target":null,"target":null,"args":{},"reason":"Routine opening."}'
                     '\n\nFix: single target field.\n\n'
                     '{"action_type":"keep_current","target":null,"args":{},"reason":"Routine opening."}')
        self.assertEqual(_decode(corrected),
                         {"action_type": "keep_current", "target": None, "args": {}, "reason": "Routine opening."})
        self.assertEqual(_decode('{"a": 1, "reason": "long"}\n\nFix duplicate key.\n\n{"a": 1, "reason": "short"}'),
                         {"a": 1, "reason": "short"})
        with self.assertRaises(ValueError):
            _decode('No object here.')


class ScriptedModel:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0
        self.circuit = ModelCircuit()

    def call(self, prompt, payload, archive):
        self.calls += 1
        answer = self.answers.pop(0) if self.answers else "transient"
        report = {"decoded": answer, "error": None, "failure_kind": None, "seconds": 0.1,
                  "input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 0}
        if isinstance(answer, str):
            report.update(decoded=None, error=f"simulated {answer}", failure_kind=answer,
                          input_tokens=None, output_tokens=None, cached_input_tokens=None)
        write_json(archive, report)
        return report


class BatchTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.batch = Path(temporary.name) / "batch"
        initialize_batch(self.batch)
        self.waits = []
        patcher = patch.object(evolution, "_sleep", side_effect=self.waits.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def match(self, number, state="finished", winner=True):
        run = self.batch / f"match-{number}"
        write_json(run / "observations.json", [fallback_summarize({"frame": frame, "workers": 4})
                                               for frame in (0, 120, 240)])
        write_json(run / "timeline.json", [])
        write_json(run / "runtime-errors.json", [])
        return {"match_number": number, "state": state, "winner": winner, "frame": 245,
                "policy_version": "v1", "seconds": 1.0, "decision_sources": {}, "tokens": {"live_input": 3}}

    def test_transient_review_failures_back_off_without_using_schema_attempts(self):
        model = ScriptedModel("transient", "transient", KEEP)
        result = review_round([self.match(1)], self.batch, model, round_number=1)
        self.assertEqual(result["validation"], "kept")
        self.assertEqual(model.calls, 3)
        self.assertEqual(len(self.waits), 5 + 15)
        self.assertEqual(result["review_usage"]["known_usage"]["input_tokens"], 10)
        self.assertEqual(read_json(self.batch / "match-1/review-attempt-1.json")["backoff_seconds"], 5)

    def test_persistent_transient_failures_stop_after_the_budget(self):
        model = ScriptedModel()
        result = review_round([self.match(1)], self.batch, model, round_number=1)
        self.assertEqual(result["validation"], "model_unavailable")
        self.assertEqual(len(self.waits), 5 + 15 + 45 * 12)
        self.assertEqual(model.calls, 15)
        self.assertEqual(read_json(self.batch / "batch.json")["state"], "model_unavailable")

    def run_batch(self, states, reviewer=None, rounds=2, per_round=2, parallel=1, barrier=None):
        played = []

        def play(_args, batch, _version, _model, number, **_kwargs):
            if barrier:
                barrier.wait()  # Times out unless the whole wave is in flight at once.
            played.append(number)
            state = states[number - 1]
            record = self.match(number, state)
            return {**record, "winner": None, "natural_outcome": "unknown"} if state == "error" else record

        args = _parser().parse_args(["--rounds", str(rounds), "--matches-per-round", str(per_round), "--no-evolve",
                                     "--parallel", str(parallel)])
        with patch("app.rsi_live._play_one", side_effect=play):
            code = _run_live(args, self.batch, ScriptedModel(), reviewer or ScriptedModel(), Event())
        return code, played

    def test_parallel_waves_run_together_and_keep_match_order(self):
        code, played = self.run_batch(["finished"] * 4, parallel=2, barrier=Barrier(2, timeout=5))
        self.assertEqual((code, sorted(played)), (0, [1, 2, 3, 4]))
        index = read_json(self.batch / "batch.json")
        self.assertEqual([round_["match_numbers"] for round_ in index["rounds"]], [[1, 2], [3, 4]])

    def test_a_java_opponent_plays_its_wave_alone(self):
        def waves(opponents, count=4, parallel=2):
            args = _parser().parse_args(["--parallel", str(parallel), "--opponent", opponents])
            pending, result = list(range(1, count + 1)), []
            while pending:
                wave, pending = _next_wave(args, pending)
                result.append(wave)
            return result

        self.assertEqual(waves("Locutus,McRave"), [[1, 2], [3, 4]])
        self.assertEqual(waves("PurpleWave,HladHammer"), [[1], [2], [3], [4]])
        self.assertEqual(waves("HladHammer,PurpleWave,Locutus"), [[1], [2], [3, 4]])
        self.assertEqual(waves("Locutus,McRave,PurpleWave", count=6, parallel=3), [[1, 2], [3], [4, 5], [6]])

    def test_single_match_error_does_not_stop_the_batch(self):
        code, played = self.run_batch(["error", "finished", "finished", "finished"])
        self.assertEqual((code, played), (0, [1, 2, 3, 4]))
        index = read_json(self.batch / "batch.json")
        self.assertEqual((index["statistics"]["errors"], index["state"]), (1, "completed"))

    def test_three_consecutive_match_errors_stop_the_batch(self):
        code, played = self.run_batch(["finished", "error", "error", "error"], rounds=2, per_round=3)
        self.assertEqual((code, played), (1, [1, 2, 3, 4]))
        index = read_json(self.batch / "batch.json")
        self.assertEqual((index["state"], index["consecutive_match_errors"]), ("repeated_match_errors", 3))

    def test_preflight_requires_every_client_to_answer(self):
        ok = _preflight(self.batch, {"live": ScriptedModel({"ok": True}), "review": ScriptedModel("auth")})
        self.assertFalse(ok)
        report = read_json(self.batch / "preflight.json")
        self.assertEqual((report["live"]["ok"], report["review"]["failure_kind"]), (True, "auth"))
        self.assertTrue(_preflight(self.batch, {"live": ScriptedModel({"ok": True})}))


class MapStartTests(unittest.TestCase):
    def test_every_frozen_map_names_the_other_spawn(self):
        def opening(position):
            return {"frame": 0, "units": [{"self": True, "type": 154, "position": position, "completed": True}]}

        self.assertEqual(map_enemy_start("Destination", opening([1056, 272])), [2112, 3824])
        self.assertEqual(map_enemy_start("Benzene", opening([3808, 464])), [288, 3120])
        self.assertEqual(map_enemy_start("Heartbreak Ridge", opening([288, 1232])), [3808, 1840])
        self.assertIsNone(map_enemy_start("Heartbreak Ridge", opening([1056, 272])))
        self.assertIsNone(map_enemy_start("Unknown", opening([1056, 272])))


if __name__ == "__main__":
    unittest.main()
