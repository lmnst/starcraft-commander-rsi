"""Offline checks for native telemetry, the frame scheduler and model transports."""

from concurrent.futures import Future
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
from subprocess import CompletedProcess
import sys
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.rsi.adapter import DEFAULT_MATCH_TIMEOUT, DEFAULT_SPEED, EngineAdapter, KEYMAP, NativeV17Adapter, _NativeTelemetry, _rows
from app.rsi.model_client import ModelCircuit, ModelClient
from app.rsi.primitives import fallback_summarize
from app.rsi.runner import LiveRunner
from app.rsi.seed_v1 import SEED_SOURCES, write_seed


ARCHIVES = Path(__file__).resolve().parents[1] / "matches/normal-codex/20261123-142700-941090"
KEEP = {"action_type": "keep_current", "target": None, "args": {}, "reason": "hold"}


def observation(frame, **changes):
    return fallback_summarize({"frame": frame, "workers": 5, "army": 9, "healthy_army": 9, **changes})


def receipt(key, frame, modifier="none"):
    return {"event": "command", "key": key, "frame": frame, "keymap": KEYMAP, "modifier": modifier}


def nexus(unit_id, position, *, own=False, hp=750, shield=750):
    return {"id": unit_id, "position": position, "type": 154, "self": own, "hp": hp, "shield": shield,
            "completed": True, "detected": True, "invincible": False}


def army_unit(unit_id, target, *, attacking=False):
    return {"id": unit_id, "position": [2140, 3790], "type": 65, "self": True, "hp": 100, "completed": True,
            "target": target, "attacking": attacking, "cooldown": 0}


class ControlledExecutor:
    def __init__(self, **kwargs):
        self.pending = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        assert self.pending is None

    def submit(self, call, *args):
        assert self.pending is None
        future = Future()
        self.pending = (future, call, args)
        return future

    def complete(self):
        future, call, args = self.pending
        self.pending = None
        future.set_result(call(*args))


class FakeAdapter(EngineAdapter):
    def __init__(self, script, speed, wall_seconds):
        self.script = script
        self.cutoffs = 0
        self.keys = []
        self.frame = None
        self.speed = speed
        self.match_timeout = wall_seconds
        self.map_name = "Destination"
        self.opponent_name = "PylonPuller"

    def run(self, on_observation, on_receipt, on_idle):
        on_idle()
        for row in self.script:
            if row is None:
                on_idle()
            elif callable(row):
                row()
            elif row.get("event") == "command":
                on_receipt(row)
            else:
                self.frame = row["frame"]
                on_observation(row)
            if self.cutoffs:
                break
        return {"state": "finished", "frame": self.frame, "winner": False,
                "natural_outcome": "loss", "replay": "test.rep"}

    def send_key(self, key):
        self.keys.append((self.frame, key))
        return True

    def cutoff(self):
        self.cutoffs += 1
        return True


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.batch = Path(temporary.name)
        self.seed = write_seed(self.batch)
        self.executor = ControlledExecutor()
        patcher = patch("app.rsi.runner.ThreadPoolExecutor", return_value=self.executor)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.requests = []
        self.replies = iter([])
        self.circuit = ModelCircuit()

    def write_policy(self, **sources):
        for module, source in sources.items():
            (self.seed / f"{module}.py").write_text(source, encoding="utf-8")

    def scripted(self, script, replies=(), *, speed=DEFAULT_SPEED, wall_seconds=DEFAULT_MATCH_TIMEOUT, **kwargs):
        self.replies = iter(replies)
        self.adapter = FakeAdapter(script, speed, wall_seconds)
        self.runner = LiveRunner(self.adapter, self, self.seed, self.batch / "match-1", **kwargs)
        return self.runner.run()

    def call(self, prompt, payload, archive):
        self.requests.append(deepcopy(payload))
        reply = next(self.replies, KEEP)
        seconds = 0.25
        if isinstance(reply, tuple):
            reply, seconds = reply
        report = {"decoded": reply, "seconds": seconds, "input_tokens": 5, "output_tokens": 2}
        if reply in ("auth", "quota"):
            report.update(decoded=None, failure_kind=reply, error="simulated provider failure")
            self.circuit.record(reply)
        elif reply == "transient":
            report.update(decoded=None, failure_kind=reply, error="simulated busy service",
                          input_tokens=None, output_tokens=None)
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(json.dumps(report), encoding="utf-8")
        return report

    def test_new_llm_tool_steps_once_and_receipt_serializes_dispatch(self):
        tool = {"action_type": "tool", "target": "squad_attack_nearby", "args": {}, "reason": "split"}
        self.write_policy(observer=SEED_SOURCES["observer.py"].replace(
            'if elapsed is None:', 'if elapsed is None or frame == 40:').replace('frame < 120', 'frame < 10'))
        result = self.scripted([
            observation(10), self.executor.complete, observation(20), observation(20),
            receipt("4", 21), receipt("3", 21, "shift"), observation(25),
            receipt("3", 26), observation(30, mode="squad"), receipt("5", 31),
            observation(40, mode="squad"), self.executor.complete, observation(50), receipt("8", 51),
        ], [tool, {"action_type": "key", "target": "8", "args": {}, "reason": "gather"}])
        self.assertEqual(self.adapter.keys, [(20, "3"), (30, "5"), (50, "8")])
        self.assertEqual(result["decision_sources"]["llm_tool"], 1)
        self.assertEqual(result["decision_sources"]["llm_key"], 1)
        self.assertEqual(result["decision_sources"]["tool_step"], 3)
        self.assertEqual(result["tokens"]["live_input"], 10)
        self.assertEqual(result["usage_missing"]["cached_input_tokens"], 2)
        self.assertEqual(len([r for r in self.runner.timeline if r["status"] == "native_receipt"]), 3)
        self.assertEqual(self.requests[1]["latest_receipt"]["frame"], 31)

    def test_rule_preempts_waiting_tool_then_cools_down_and_dedupes(self):
        self.write_policy(observer=SEED_SOURCES["observer.py"].split('def should_wake_llm')[0] +
                          'def should_wake_llm(summary, prev_summary, meta):\n    return None\n',
                          rules='''def decide_reflex(summary, active_tool, memory):
    if summary["frame"] == 0:
        return {"rule_name": "prepare", "action_type": "tool", "target": "squad_attack_nearby", "args": {}, "reason": "try"}
    return {"rule_name": "recall", "action_type": "key", "target": "0", "args": {}, "reason": "recall"}
''')
        result = self.scripted([
            observation(0), observation(10), receipt("3", 11), observation(119), observation(120),
            receipt("0", 121), observation(130), observation(240), observation(360, army=8), receipt("0", 361),
        ])
        self.assertEqual(self.adapter.keys, [(10, "3"), (120, "0"), (360, "0")])
        self.assertEqual(result["decision_sources"]["rule"], 3)
        self.assertTrue(any(r["frame"] == 120 and r["status"] == "aborted_by_override" for r in self.runner.timeline))
        self.assertEqual([r["frame"] for r in self.runner.timeline if r["status"] == "deduped"], [240])
        self.assertEqual(self.requests, [])

    def test_expired_invalid_and_keep_replies_never_post_keys(self):
        self.write_policy(observer=SEED_SOURCES["observer.py"].replace(
            'if elapsed is None:', 'if frame in {120, 692, 700, 710}:'))
        key = {"action_type": "key", "target": "9", "args": {}, "reason": "retreat"}
        result = self.scripted([
            observation(120), observation(360), self.executor.complete, observation(692),
            self.executor.complete, observation(700), self.executor.complete, observation(710),
            self.executor.complete, observation(720),
        ], [key, (key, 24.0001), {**key, "target": "X"}, KEEP])
        self.assertEqual(self.adapter.keys, [])
        self.assertEqual(result["decision_sources"]["expired"], 2)
        self.assertEqual(result["decision_sources"]["invalid"], 1)
        self.assertEqual(result["decision_sources"]["llm_keep_current"], 1)
        self.assertEqual(result["decision_max_lag_frames"], 571)

    def test_long_reason_and_omitted_args_keep_a_valid_order(self):
        # Opus replies in matches/rsi/20261124-132204-490957/match-1 ran 163-407 characters and
        # 15 of 16 valid orders were discarded.
        self.write_policy(observer=SEED_SOURCES["observer.py"].replace(
            'if elapsed is None:', 'if frame in {120, 240}:'))
        attack = {"action_type": "key", "target": "6", "args": {}, "reason": "Attack the known main. " * 12}
        keep = {"action_type": "keep_current", "target": None, "reason": "Hold."}
        result = self.scripted([
            observation(120), self.executor.complete, observation(130), receipt("6", 131),
            observation(240), self.executor.complete, observation(250),
        ], [attack, keep])
        self.assertEqual(self.adapter.keys, [(130, "6")])
        self.assertEqual((result["decision_sources"]["llm_key"], result["decision_sources"]["llm_keep_current"],
                          result["decision_sources"]["invalid"]), (1, 1, 0))
        row = next(r for r in self.runner.timeline if r.get("key") == "6")
        self.assertEqual((len(row["reason"]), row["reason_truncated"]), (160, True))
        self.assertEqual(len(self.requests[1]["recent_decisions"][-1]["reason"]), 160)

    def test_expiry_uses_speed_and_elapsed_wall_clock_even_when_frames_stop(self):
        self.write_policy(observer=SEED_SOURCES["observer.py"].replace(
            'if elapsed is None:', 'if frame in {120, 480, 960}:'))
        key = {"action_type": "key", "target": "9", "args": {}, "reason": "retreat"}
        ticks = [0]
        result = self.scripted([
            observation(120), self.executor.complete, observation(478), receipt("9", 479),
            observation(480), self.executor.complete, observation(839),
            observation(960), lambda: ticks.__setitem__(0, 25), self.executor.complete, observation(964),
        ], [key, key, key], speed=67, clock=lambda: ticks[0])
        self.assertEqual(result["decision_max_lag_frames"], 358)
        self.assertEqual(result["decision_sources"]["expired"], 2)
        self.assertEqual(self.adapter.keys, [(478, "9")])

    def test_cutoff_precedes_ready_model_and_policy_cannot_mutate_archived_observation(self):
        self.write_policy(observer=SEED_SOURCES["observer.py"].replace(
            '    summary = {name:', '    raw_obs["workers"] = 0\n    raw_obs["army"] = 0\n    summary = {name:'))
        result = self.scripted([observation(120), self.executor.complete, observation(240)],
                               [{**KEEP, "action_type": "key", "target": "9"}],
                               max_frames=240, stalemate_zero_frames=10)
        self.assertEqual(self.adapter.keys, [])
        self.assertEqual(result["cutoff"]["reason"], "max_frames")
        self.assertIsNone(result["cutoff"]["zero_since_frame"])
        self.assertEqual(result["natural_outcome"], "unknown")
        self.assertEqual(result["adjudicated_outcome"], "inconclusive")
        self.assertEqual(self.runner.timeline[0]["status"], "ended_during_request")
        self.assertEqual([raw["army"] for raw in self.runner.observations], [9, 9])

    def test_runtime_failures_fall_back_and_abort_invalid_tool(self):
        self.write_policy(observer='''def summarize(raw_obs, memory):
    raw_obs["workers"] = 0
    if raw_obs["frame"] == 0:
        return {"data": "x" * 9000}
    return 1 / 0
def should_wake_llm(summary, prev_summary, meta):
    if summary["frame"] == 0:
        return "opening"
    return []
''', rules='''def decide_reflex(summary, active_tool, memory):
    summary["workers"] = 10 / 0
''', tools='''def step(summary, args, state):
    summary["army"] = 0
    state["bad"] = {1, 2}
    return {"status": "emit_key", "key": "X", "reason": "invalid"}
TOOLS = {"bad": {"description": "bad", "args_schema": {}, "step": step}}
''')
        result = self.scripted([observation(0), self.executor.complete, observation(10)],
                               [{**KEEP, "action_type": "tool", "target": "bad"}])
        self.assertEqual(result["state"], "finished")
        self.assertEqual(self.adapter.keys, [])
        self.assertEqual(self.requests[0]["summary"]["workers"], 5)
        self.assertEqual(self.runner.observations[-1]["army"], 9)
        self.assertEqual(result["runtime_error_count"], 6)
        self.assertEqual({e["hook"] for e in self.runner.runtime_errors}, {
            "observer.summarize", "observer.should_wake_llm", "rules.decide_reflex", "tools.bad.step",
        })

    def test_tool_key_budget_lifetime_and_unconfirmed_receipt(self):
        self.write_policy(tools='''def step(summary, args, state):
    return {"status": "emit_key", "reason": "test", "key": "1"}
TOOLS = {"loop": {"description": "loop", "args_schema": {}, "step": step}}
''')
        tool = {**KEEP, "action_type": "tool", "target": "loop"}
        self.scripted([
            observation(120), self.executor.complete, observation(130), receipt("1", 131),
            observation(140), receipt("1", 141), observation(150), receipt("1", 151),
            observation(160), receipt("1", 161), observation(170),
            observation(1920), self.executor.complete, observation(1930), observation(2050),
            observation(3720), self.executor.complete, observation(3730), receipt("1", 3731), observation(4690),
        ], [tool, tool, tool])
        self.assertEqual(self.adapter.keys, [(130, "1"), (140, "1"), (150, "1"), (160, "1"),
                                             (1930, "1"), (3730, "1")])
        statuses = {r["status"] for r in self.runner.timeline}
        self.assertTrue({"aborted_key_limit", "aborted_missing_receipt", "aborted_frame_limit"} <= statuses)

    def test_real_observation_archives_use_exact_zero_streak_frames(self):
        for number, first_zero, cutoff in ((1, 13560, 13920), (2, 41640, 42000)):
            with self.subTest(match=number):
                self.requests.clear()
                archive = ARCHIVES / f"match-{number}" / "observations.json"
                rows = json.loads(archive.read_text(encoding="utf-8"))
                script = []
                for row in rows:
                    script.extend((self.complete_if_pending, row))
                result = self.scripted(script, max_frames=45000, speed=67)
                self.assertEqual(self.adapter.cutoffs, 1)
                self.assertEqual(result["cutoff"]["zero_since_frame"], first_zero)
                self.assertEqual(result["frame"], cutoff)
                self.assertEqual(result["natural_outcome"], "unknown")
                self.assertEqual(result["adjudicated_outcome"], "cutoff_loss")
                self.assertEqual(result["cutoff"]["adjudication"]["rule"], "zero_own_workers_and_army")
                self.assertLess(len(self.requests), 176)
                self.assertTrue(all(request["summary"]["workers"] or request["summary"]["army"]
                                    for request in self.requests))
                self.assertIsNone(result["winner"])

    def test_one_army_cutoff_requires_an_unbroken_streak(self):
        result = self.scripted([
            observation(0, workers=0, army=1, healthy_army=1),
            observation(120, workers=0, army=1, healthy_army=1), self.complete_if_pending,
            observation(240, workers=1, army=1, healthy_army=1), self.complete_if_pending,
            observation(360, workers=0, army=1, healthy_army=1), self.complete_if_pending,
            observation(480, workers=0, army=0, healthy_army=0), self.complete_if_pending,
            observation(600, workers=0, army=1, healthy_army=1), self.complete_if_pending,
            observation(720, workers=0, army=1, healthy_army=1), self.complete_if_pending,
            observation(840, workers=0, army=1, healthy_army=1),
        ])
        self.assertEqual(result["adjudicated_outcome"], "cutoff_loss")
        self.assertEqual(result["cutoff"]["reason"], "stalemate_one_army_or_less")
        self.assertEqual(result["cutoff"]["one_army_or_less_since_frame"], 360)
        self.assertEqual(result["frame"], 840)

    def test_destination_hint_is_available_before_enemy_sighting(self):
        opening = {**observation(0), "units": [nexus(1, [1056, 272], own=True)]}
        self.scripted([opening, observation(120), self.complete_if_pending])
        self.assertEqual(self.requests[0]["enemy_main"], {
            "position": [2112, 3824], "source": "map_start", "last_seen_frame": None,
        })

    def test_default_frame_limit_is_earlier_than_archived_second_match_zero_streak(self):
        archive = ARCHIVES / "match-2/observations.json"
        rows = json.loads(archive.read_text(encoding="utf-8"))
        script = [entry for row in rows for entry in (self.complete_if_pending, row)]
        result = self.scripted(script)
        self.assertEqual(result["cutoff"]["reason"], "max_frames")
        self.assertEqual(result["frame"], 36000)
        self.assertEqual(result["adjudicated_outcome"], "inconclusive")

    def test_defense_lock_uses_sample_and_receipt_frame_intervals(self):
        result = self.scripted([
            observation(0),
            {**receipt("0", 10), "stance": "defend", "main_task": 3},
            observation(20, stance="defend", army=11, healthy_army=8),
            {**receipt("4", 21), "stance": "defend", "main_task": 3},
            observation(40, stance="defend", army=11, healthy_army=10),
            {**receipt("-", 45), "stance": "baseline", "main_task": 0},
            observation(50),
            {**receipt("3", 52), "stance": "defend", "main_task": 4},
            observation(100, stance="defend"),
        ])
        self.assertEqual(result["natural_outcome"], "loss")
        self.assertIsNone(result["adjudicated_outcome"])
        metrics = result["lock_defense"]
        self.assertEqual((metrics["locked_frames"], metrics["tracked_frames"], metrics["fraction"]), (83, 100, .83))
        self.assertEqual([row["frame"] for row in metrics["transitions"]], [0, 10, 45, 52])
        self.assertEqual(result["army_peaks"], {"army": {"value": 11, "frame": 20},
                                                "healthy_army": {"value": 10, "frame": 40}})

    def test_terminal_judgment_excludes_mistaken_and_insufficient_evidence(self):
        main_site = [2112, 3824]
        own = nexus(1, [1056, 272], own=True)
        main = nexus(2, main_site, hp=140, shield=0)
        prior = nexus(2, main_site, hp=190, shield=0)
        attack = [army_unit(3, 2, attacking=True), army_unit(4, 2)]
        cases = [
            (prior, [main, *attack], "cutoff_win", "near_destroyed_visible_main_nexus_under_attack"),
            (prior, attack, "inconclusive", "no_completed_visible_enemy_main_nexus"),
            (prior, [{**main, "position": [2400, 3900]}, *attack],
             "inconclusive", "no_completed_visible_enemy_main_nexus"),
            (prior, [{**main, "completed": False}, *attack], "inconclusive", "no_completed_visible_enemy_main_nexus"),
            (prior, [main, nexus(5, [2448, 3990]), *attack], "inconclusive", "other_completed_enemy_nexus_visible"),
            (prior, [main, attack[0]], "inconclusive", "insufficient_current_attack_evidence"),
            (prior, [{**main, "shield": 100}, *attack], "inconclusive", "enemy_main_nexus_above_vitality_threshold"),
            (main, [main, *attack], "inconclusive", "insufficient_recent_damage_to_main_nexus"),
            (None, [main, *attack], "inconclusive", "no_consecutive_sighting_of_same_main_nexus"),
        ]
        self.write_policy(observer=SEED_SOURCES["observer.py"].split("def should_wake_llm")[0] +
                          "def should_wake_llm(summary, prev_summary, meta):\n    return None\n")
        for previous, terminal, outcome, reason in cases:
            with self.subTest(reason=reason):
                r = self.scripted([
                    {**observation(0), "units": [own]},
                    {**observation(120), "units": [own, previous] if previous else [own]},
                    {**observation(240), "units": [own, *terminal]},
                ], max_frames=240)
                self.assertEqual(r["adjudicated_outcome"], outcome)
                self.assertEqual(r["cutoff"]["adjudication"]["reason"], reason)
                self.assertEqual(r["natural_outcome"], "unknown")

    def test_wall_timeout_uses_idle_with_no_new_frames_and_rejects_stale_evidence(self):
        self.write_policy(observer=SEED_SOURCES["observer.py"].split("def should_wake_llm")[0] +
                          "def should_wake_llm(summary, prev_summary, meta):\n    return None\n")
        seconds = [0]
        own = nexus(1, [1056, 272], own=True)
        prior = nexus(2, [2112, 3824], hp=210, shield=0)
        latest = {**prior, "hp": 150}
        result = self.scripted([
            {**observation(0), "units": [own]},
            {**observation(120), "units": [own, prior]},
            {**observation(240), "units": [own, latest, army_unit(3, 2, attacking=True), army_unit(4, 2)]},
            lambda: seconds.__setitem__(0, 10), None, observation(360),
        ], wall_seconds=10, clock=lambda: seconds[0])
        self.assertEqual(result["cutoff"]["reason"], "max_wall_seconds")
        self.assertEqual(result["frame"], 240)
        self.assertEqual(result["cutoff"]["adjudication"]["reason"], "missing_or_stale_terminal_observation")
        self.assertEqual(result["adjudicated_outcome"], "inconclusive")

    def test_operator_stop_and_model_circuit_cut_off_without_a_scored_loss(self):
        stopped = Event()
        manual = self.scripted([observation(0), stopped.set, None],
                               stop_requested=stopped.is_set)
        self.assertEqual(manual["state"], "manual_stop")
        self.assertIsNone(manual["adjudicated_outcome"])

        self.write_policy(observer=SEED_SOURCES["observer.py"].replace(
            'if elapsed is None:', 'if frame in {120, 240, 360}:'))
        circuit = self.scripted([
            observation(120), self.executor.complete, observation(126),
            observation(240), self.executor.complete, observation(246),
            observation(360), self.executor.complete, None, observation(366),
        ], ["auth", "quota", "auth"])
        self.assertEqual(circuit["state"], "model_circuit_open")
        self.assertEqual(circuit["cutoff"]["circuit_failures"], 3)
        self.assertEqual(circuit["natural_outcome"], "unknown")
        self.assertIsNone(circuit["adjudicated_outcome"])
        self.assertEqual(self.adapter.keys, [])
        self.assertEqual(len([row for row in self.runner.timeline if row["status"] == "model_service_failure"]), 3)

    def test_missing_usage_keeps_known_tokens_and_mostly_failed_calls_mark_the_match_degraded(self):
        self.write_policy(observer=SEED_SOURCES["observer.py"].replace(
            'if elapsed is None:', 'if frame in {120, 240, 360, 480}:'))
        result = self.scripted([
            observation(120), self.executor.complete, observation(126),
            observation(240), self.executor.complete, observation(246),
            observation(360), self.executor.complete, observation(366),
            observation(480), self.executor.complete, observation(486),
        ], [KEEP, "transient", "transient", "transient"])
        self.assertEqual(result["state"], "finished")
        self.assertEqual(result["tokens"]["live_input"], 5)
        self.assertEqual(result["usage_missing"]["input_tokens"], 3)
        self.assertEqual(result["live_model_failures"], {"calls": 4, "failed": 3, "kinds": {"transient": 3}})
        self.assertTrue(result["degraded"])
        self.assertFalse(self.circuit.open)

    def test_live_payload_carries_situation_and_compact_receipt(self):
        self.write_policy(observer=SEED_SOURCES["observer.py"].replace('if elapsed is None:', 'if frame == 240:'))
        self.scripted([
            observation(120), {**receipt("4", 121), "units": [1, 2], "army_ids": [1, 2]},
            observation(240), self.complete_if_pending,
        ])
        payload = self.requests[0]
        self.assertEqual(payload["summary"]["situation"]["defense"]["stance"], "baseline")
        self.assertEqual(payload["opponent"], "PylonPuller")
        self.assertEqual(payload["latest_receipt"]["units_count"], 2)
        self.assertNotIn("army_ids", payload["latest_receipt"])
        self.assertIn("situation", self.runner.observations[0])

    def complete_if_pending(self):
        if self.executor.pending:
            self.executor.complete()


class BoundaryTests(unittest.TestCase):
    def test_native_telemetry_pairs_rows_and_keeps_units_and_receipts(self):
        observations, receipts = [], []
        native = _NativeTelemetry(lambda units, state: {"visible_enemies": {}},
                                  lambda unit: "Probe", observations.append, receipts.append)
        rows = [
            {"event": "start", "frame": 0, "keymap": KEYMAP, "human_layer": True, "delegate_loaded": True},
            {"event": "validation_native", "frame": 120, "units": [{"id": 1, "self": True}]},
            {"event": "base_notice", "frame": 120, "kind": "own", "unlisted": 4},
            {"event": "state", "frame": 120, "main_task": 0, "squad_task": 0, "keymap": KEYMAP},
            receipt("2", 121), {"event": "end", "frame": 122, "winner": True},
        ]
        stream = BytesIO(b"".join(json.dumps(row).encode() + b"\n" for row in rows) + b"{")
        for row in _rows(stream):
            native.consume(row)
        self.assertEqual(native.end["frame"], 122)
        self.assertEqual(observations[0]["units"][0]["type_name"], "Probe")
        self.assertEqual(observations[0]["recent_events"], [{"event": "base_notice", "frame": 120, "kind": "own"}])
        self.assertEqual(receipts, [receipt("2", 121)])
        self.assertEqual(stream.read(), b"{")
        with self.assertRaisesRegex(RuntimeError, "matching side-visible"):
            native.consume({**rows[3], "frame": 121})

    def test_cutoff_uses_existing_native_sentinel(self):
        with TemporaryDirectory() as temporary:
            adapter = NativeV17Adapter(temporary)
            self.assertTrue(adapter.cutoff())
            self.assertEqual((Path(temporary) / "unused-test-input").read_text(), "1 -1\n")

    def test_native_runner_confirms_end_and_replay_after_cutoff(self):
        with TemporaryDirectory() as temporary:
            run = Path(temporary) / "match-1"
            adapter = NativeV17Adapter(run)
            cutoff_posted = Event()

            def playing(path, map_name, opponent, **settings):
                self.assertEqual(path, run)
                self.assertEqual(settings["speed"], 42)
                self.assertGreater(settings["match_timeout"], 1800)
                self.assertTrue(settings["visible_telemetry"] and settings["data_only"])
                self.assertFalse(settings["keep_results"])
                adapter.output.mkdir(parents=True)
                (run / "match.json").write_text('{"state":"playing"}', encoding="utf-8")
                start = {"event": "start", "frame": 0, "keymap": KEYMAP,
                         "human_layer": True, "delegate_loaded": True}
                units = {"event": "validation_native", "frame": 120, "units": []}
                state = {"event": "state", "frame": 120, "keymap": KEYMAP, "main_task": 0, "squad_task": 0}
                log = adapter.output / "commander-layer.jsonl"
                log.write_text("".join(json.dumps(row) + "\n" for row in (start, units, state)), encoding="utf-8")
                settings["update"]("playing")
                self.assertTrue(cutoff_posted.wait(2))
                self.assertTrue((run / "unused-test-input").exists())
                end = {"frame": 121, "winner": False}
                with log.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"event": "end", **end}) + "\n")
                adapter.end_file.write_text(json.dumps(end), encoding="utf-8")
                (run / "match.rep").write_bytes(b"replay")
                return {"state": "finished", "replay": "match.rep", **end}

            def observe(_):
                self.assertTrue(adapter.cutoff())
                cutoff_posted.set()

            def read_json(path):
                return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

            matches = SimpleNamespace(playing=playing, read_json=read_json)
            stage2 = SimpleNamespace(scout_snapshot=lambda units, state: {}, type_name=lambda unit: "Probe")
            with patch.dict(sys.modules, {"matches": matches, "stage2_live": stage2}):
                result = adapter.run(observe, self.fail, lambda: None)
            self.assertEqual(result["state"], "cutoff")
            self.assertEqual(result["natural_outcome"], "unknown")
            self.assertEqual(result["native_end"]["frame"], 121)
            self.assertIsNone(result["winner"])

    def test_native_idle_deadline_posts_cutoff_and_persists_replay(self):
        with TemporaryDirectory() as temporary:
            batch = Path(temporary)
            run = batch / "match-1"
            seed = write_seed(batch)
            adapter = NativeV17Adapter(run, match_timeout=1)
            cutoff_posted = Event()
            times = iter((0, 0, 1))

            def cutoff():
                self.assertTrue(post_cutoff())
                cutoff_posted.set()
                return True

            def playing(path, map_name, opponent, **settings):
                self.assertGreater(settings["match_timeout"], 1)
                adapter.output.mkdir(parents=True)
                (run / "match.json").write_text('{"state":"playing"}', encoding="utf-8")
                log = adapter.output / "commander-layer.jsonl"
                start = {"event": "start", "frame": 0, "keymap": KEYMAP,
                         "human_layer": True, "delegate_loaded": True}
                log.write_text(json.dumps(start) + "\n", encoding="utf-8")
                settings["update"]("playing")
                self.assertTrue(cutoff_posted.wait(2))
                self.assertEqual((run / "unused-test-input").read_text(encoding="ascii"), "1 -1\n")
                end = {"frame": 9, "winner": False}
                with log.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps({"event": "end", **end}) + "\n")
                adapter.end_file.write_text(json.dumps(end), encoding="utf-8")
                (run / "match.rep").write_bytes(b"replay")
                return {"state": "finished", "replay": "match.rep", **end}

            def read_json(path):
                return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

            matches = SimpleNamespace(playing=playing, read_json=read_json)
            stage2 = SimpleNamespace(scout_snapshot=self.fail, type_name=self.fail)
            post_cutoff = adapter.cutoff
            with patch.dict(sys.modules, {"matches": matches, "stage2_live": stage2}), patch.object(
                adapter, "cutoff", side_effect=cutoff,
            ):
                result = LiveRunner(adapter, ModelClient(), seed, run, clock=lambda: next(times, 2)).run()
            self.assertEqual(result["cutoff"]["reason"], "max_wall_seconds")
            self.assertIsNone(result["cutoff"]["frame"])
            self.assertEqual(result["cutoff"]["terminal_frame"], 9)
            self.assertEqual(result["replay"], "match.rep")
            self.assertEqual(result["adjudicated_outcome"], "inconclusive")
            self.assertEqual(read_json(run / "match.rep.json")["natural_outcome"], "unknown")

    def test_codex_and_http_calls_archive_raw_output_usage_and_text_only_flags(self):
        with TemporaryDirectory() as temporary:
            archive = Path(temporary) / "call.json"
            events = [
                {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(KEEP)}},
                {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}},
            ]
            stdout = "\n".join(map(json.dumps, events))
            with patch("app.rsi.model_client.subprocess.run", return_value=CompletedProcess([], 0, stdout, "")) as run:
                result = ModelClient().call("json", {}, archive)
            command = run.call_args.args[0]
            self.assertIn("read-only", command)
            self.assertIn("unified_exec", command)
            self.assertIn('model_reasoning_effort="low"', command)
            self.assertEqual(result["decoded"], KEEP)
            self.assertEqual(result["input_tokens"], 10)
            self.assertIsNone(result["cached_input_tokens"])
            self.assertEqual(json.loads(archive.read_text())["stdout"], stdout)

            events.append({"type": "item.completed", "item": {"type": "command_execution"}})
            with patch("app.rsi.model_client.subprocess.run", return_value=CompletedProcess(
                    [], 0, "\n".join(map(json.dumps, events)), "")):
                result = ModelClient().call("json", {}, archive)
            self.assertIsNone(result["decoded"])
            self.assertEqual(result["output_tokens"], 4)

            body = {"choices": [{"message": {"content": json.dumps(KEEP)}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 7,
                              "prompt_tokens_details": {"cached_tokens": 3}}}
            response = MagicMock(status=200)
            response.read.return_value = json.dumps(body).encode()
            with patch("app.rsi.model_client.urlopen") as urlopen:
                urlopen.return_value.__enter__.return_value = response
                result = ModelClient(backend="openai", endpoint="https://example.invalid/v1/",
                                     api_key="fake-token").call("json", {}, archive)
            http_request = urlopen.call_args.args[0]
            self.assertEqual(http_request.full_url, "https://example.invalid/v1/chat/completions")
            self.assertNotIn("fake-token", archive.read_text())
            self.assertEqual((result["input_tokens"], result["output_tokens"], result["cached_input_tokens"]), (12, 7, 3))


if __name__ == "__main__":
    unittest.main()
