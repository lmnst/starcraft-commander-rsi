"""Comprehensive offline unit tests for RSI v2 (cpp_gate, L0 scoring, ledger, Champion/Challenger loop)."""

from io import BytesIO
from itertools import count
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.rsi import build_config, cpp_gate, evolution, ledger, scoring, situation, v2_loop
from app.rsi.agentic_review import _build_log
from app.rsi.model_client import ModelCircuit, write_json
from app.rsi.primitives import fallback_summarize
from app.rsi.runner import LiveRunner


ROOT = Path(__file__).resolve().parents[1]
BASE_CPP = (ROOT / "app/MacroBot.cpp").read_text(encoding="utf-8")
CALL_IDS = count(1000)


def _fake_compile(source, output_dll, *, repo_root, **kwargs):
    output_dll = Path(output_dll)
    output_dll.parent.mkdir(parents=True, exist_ok=True)
    output_dll.write_bytes(b"FAKE_DLL:" + source[:64].encode("utf-8"))
    return {
        "dll": str(output_dll),
        "dll_sha256": cpp_gate._sha256_file(output_dll),
        "source_sha256": "deadbeef",
        "seconds": 0.01,
        "exports": ["gameInit", "newAIModule"],
    }


def _make_obs(frames, *, workers=20, army=10, healthy=10, zone="mid_map",
              own_engaged=2, enemy_engaged=2, core=True, dragoons=4, bases=2,
              detectors=1, minerals=4000, opponent="Steamhammer"):
    rows = []
    for f in frames:
        units = []
        for idx in range(workers):
            units.append({"id": 100 + idx, "self": True, "completed": True, "type": 64, "type_name": "Probe",
                          "position": [100, 100], "sight": 256, "attacking": False, "cooldown": 0})
        for idx in range(dragoons):
            units.append({"id": 200 + idx, "self": True, "completed": True, "type": 66, "type_name": "Dragoon",
                          "position": [1000, 1000], "sight": 256, "attacking": idx < own_engaged, "cooldown": 0})
        for idx in range(max(0, army - dragoons)):
            units.append({"id": 300 + idx, "self": True, "completed": True, "type": 65, "type_name": "Zealot",
                          "position": [1000, 1000], "sight": 224, "attacking": False, "cooldown": 0})
        for idx in range(bases):
            units.append({"id": 400 + idx, "self": True, "completed": True, "type": 154, "type_name": "Nexus",
                          "position": [100 + idx * 500, 100], "sight": 352})
        if core:
            units.append({"id": 450, "self": True, "completed": True, "type": 164, "type_name": "Cybernetics Core",
                          "position": [150, 150], "sight": 320})
        for idx in range(detectors):
            units.append({"id": 460 + idx, "self": True, "completed": True, "type": 84, "type_name": "Observer",
                          "position": [1000, 1000], "sight": 288})
        for idx in range(enemy_engaged):
            units.append({"id": 900 + idx, "self": False, "completed": True, "type": 66, "type_name": "Dragoon",
                          "position": [1100, 1100], "attacking": True, "cooldown": 5})
        center = [1000, 1000] if zone != "at_home" else [120, 120]
        raw = {
            **fallback_summarize({"frame": f, "workers": workers, "army": army, "healthy_army": healthy}),
            "completed_workers": workers,
            "gathered_minerals": int(minerals * (f / max(1, frames[-1]))),
            "gathered_gas": 500,
            "own_base_positions": [[100, 100]] * bases,
            "center": center,
            "units": units,
            "tasks": [],
            "recent_events": [],
            "opponent": opponent,
        }
        rows.append(raw)
    return situation.attach(rows)


class CppGateTests(unittest.TestCase):
    def test_base_and_v2_seed_pass_security_and_outline(self):
        self.assertEqual(cpp_gate.check_source(BASE_CPP)["changed_lines"], 0)
        v2_cpp = cpp_gate.seed_v2_macrobot(BASE_CPP)
        info = cpp_gate.check_source(v2_cpp, parent_source=BASE_CPP)
        self.assertLessEqual(info["changed_lines"], 30)
        # Proactive cannon must now appear AFTER Cybernetics Core line.
        self.assertGreater(
            v2_cpp.index("early(cfg.proactive_cannon_frame) && count(Protoss_Forge)==0"),
            v2_cpp.index("count(Protoss_Assimilator)>0 && core==0"),
        )
        toc = cpp_gate.outline(v2_cpp)
        names = [f["name"] for f in toc["functions"]]
        self.assertIn("nextBuilding", names)
        self.assertIn("combat", names)

    def test_word_boundary_allows_std_remove_and_opponent_identifier(self):
        # std::remove_if is already in BASE_CPP; add std::remove and opponentBase variable.
        tweaked = BASE_CPP.replace(
            "  Position home, enemyStart, rally;\n",
            "  Position home, enemyStart, rally, opponentBase;\n",
        ).replace(
            "  void scouting() {\n",
            "  void scouting() {\n    std::vector<int> v{1,2}; v.erase(std::remove(v.begin(), v.end(), 1), v.end());\n",
        )
        cpp_gate.check_source(tweaked, parent_source=BASE_CPP)

    def test_blocks_cheat_apis_obfuscation_and_bad_paths(self):
        bad_cases = {
            "enableFlag": BASE_CPP.replace("void onFrame() override {", "void onFrame() override { Broodwar->enableFlag(1);"),
            "setRevealAll": BASE_CPP.replace("void onFrame() override {", "void onFrame() override { Broodwar->setRevealAll();"),
            "setVision": BASE_CPP.replace("void onFrame() override {", "void onFrame() override { Broodwar->setVision(me);"),
            "setAlliance": BASE_CPP.replace("void onFrame() override {", "void onFrame() override { Broodwar->setAlliance(me);"),
            "extra leaveGame": BASE_CPP.replace("void onFrame() override {", "void onFrame() override { Broodwar->leaveGame();"),
            "unicode escape": BASE_CPP.replace("void onFrame() override {", "void onFrame() override { int \\u0061 = 1;"),
            "token paste": BASE_CPP + "\n// ok\n#define BAD(a,b) a##b\n",
            "pragma": "#pragma comment(lib, \"user32\")\n" + BASE_CPP,
            "line splice": BASE_CPP.replace("void scouting() {\n", "void scouting() { \\\n"),
            "bad include": '#include "secret.h"\n' + BASE_CPP,
            "raw string opponent": BASE_CPP.replace("void scouting() {", 'void scouting() { const char* s = R"(opponent/log)";'),
            "bad ofstream": BASE_CPP.replace("void scouting() {", 'void scouting() { std::ofstream f("hack.txt");'),
            "bare C remove": BASE_CPP.replace("void scouting() {", 'void scouting() { remove("x");'),
            "global ::remove": BASE_CPP.replace("void scouting() {", 'void scouting() { ::remove("x");'),
            "reinterpret_cast": BASE_CPP.replace("void scouting() {", "void scouting() { auto p = reinterpret_cast<int*>(0x1234);"),
            "MACRO_PARAMS broken": BASE_CPP.replace(" X(cannons_per_base)", ""),
            "redefine MACRO_DECLARE": BASE_CPP + "\n#define MACRO_DECLARE Broodwar->enableFlag(1);\n",
            "brace-init ifstream": BASE_CPP.replace("void scouting() {", 'void scouting() { std::ifstream f{"secret.txt"};'),
            "temp ifstream": BASE_CPP.replace("void scouting() {", 'void scouting() { std::ifstream("secret.txt").get();'),
            "arrow open": BASE_CPP.replace("void scouting() {", 'void scouting() { std::ifstream f; (&f)->open("secret.txt");'),
            "basic_ifstream": BASE_CPP.replace("void scouting() {", 'void scouting() { std::basic_ifstream<char> f("x");'),
            "_Pragma": BASE_CPP.replace("void scouting() {", 'void scouting() { _Pragma("comment(lib, \\"user32\\")")'),
            "digraph": BASE_CPP.replace("void scouting() {", "void scouting() { %:include <windows.h>\n"),
            "digit separator cheat hide": BASE_CPP.replace(
                "void scouting() {",
                "void scouting() {\n    int a = 1'000;\n    Broodwar->enableFlag(1);\n    int b = 2'000;\n",
            ),
        }
        for label, src in bad_cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                cpp_gate.check_source(src)

    def test_valid_cpp17_digit_separators_pass(self):
        with_sep = BASE_CPP.replace("frame>8000", "frame>8'000")
        cpp_gate.check_source(with_sep, parent_source=BASE_CPP)

    def test_diff_line_cap_enforced(self):
        bloated = BASE_CPP + "".join(f"// filler line {i}\n" for i in range(160))
        with self.assertRaisesRegex(ValueError, "limit is 150"):
            cpp_gate.check_source(bloated, parent_source=BASE_CPP)


class ScoringAndAdjudicationTests(unittest.TestCase):
    def test_anti_turtling_and_inconclusive_zero_bonus(self):
        active_obs = _make_obs(list(range(0, 14401, 240)), army=22, healthy=20, zone="mid_map",
                               own_engaged=5, enemy_engaged=3, dragoons=12, bases=2, minerals=7000)
        turtle_obs = _make_obs(list(range(0, 30001, 240)), army=8, healthy=8, zone="at_home",
                               own_engaged=0, enemy_engaged=0, dragoons=2, bases=1, minerals=2000)
        active_m = {"state": "cutoff", "adjudicated_outcome": "cutoff_loss", "frame": 14400,
                    "seconds": 600.0, "lock_defense": {"fraction": 0.05}}
        turtle_m = {"state": "cutoff", "adjudicated_outcome": "inconclusive", "frame": 30000,
                    "seconds": 1250.0, "lock_defense": {"fraction": 0.85}}
        s_active = scoring.score_match(active_m, active_obs)
        s_turtle = scoring.score_match(turtle_m, turtle_obs)
        self.assertEqual(s_turtle["components"]["inconclusive_bonus"], 0.0)
        self.assertEqual(s_active["components"]["survival_capped"], 15.0)
        self.assertEqual(s_turtle["components"]["survival_capped"], 15.0)  # capped at 14400!
        self.assertGreater(s_active["score"], s_turtle["score"] + 30.0)

    def test_fps_regression_and_anti_regression_guard(self):
        core = ["PylonPuller", "Steamhammer", "BananaBrain"]
        champ_recs = {
            "PylonPuller": [{"outcome": "win", "score": 70.0, "fps": 22.0},
                            {"outcome": "win", "score": 72.0, "fps": 22.0}],
            "Steamhammer": [{"outcome": "loss", "score": 40.0, "fps": 22.0}],
            "BananaBrain": [{"outcome": "loss", "score": 35.0, "fps": 22.0}],
        }
        # FPS < 70% of Champion -> immediate fps_regression_reject even with wins!
        slow_chal = {opp: [{"outcome": "win", "score": 90.0, "fps": 14.0}] for opp in core}
        verdict = scoring.adjudicate_challenger(champ_recs, slow_chal, core)
        self.assertEqual((verdict["decision"], verdict["reason"]), ("rejected", "fps_regression_reject"))

        # Champion has 2 games & 100% WR on PylonPuller; Challenger loses PylonPuller & wins Steamhammer (same WR) -> regression reject!
        reg_chal = {
            "PylonPuller": [{"outcome": "loss", "score": 45.0, "fps": 22.0}],
            "Steamhammer": [{"outcome": "win", "score": 75.0, "fps": 22.0}],
            "BananaBrain": [{"outcome": "loss", "score": 50.0, "fps": 22.0}],
        }
        verdict2 = scoring.adjudicate_challenger(champ_recs, reg_chal, core)
        self.assertEqual((verdict2["decision"], verdict2["reason"]), ("rejected", "regression_on_PylonPuller"))

        # If Champion had only 1 game on PylonPuller, anti-regression guard does NOT fire!
        champ_1game = {**champ_recs, "PylonPuller": [champ_recs["PylonPuller"][0]]}
        reg_chal_better_score = {
            "PylonPuller": [{"outcome": "loss", "score": 60.0, "fps": 22.0}],
            "Steamhammer": [{"outcome": "win", "score": 80.0, "fps": 22.0}],
            "BananaBrain": [{"outcome": "loss", "score": 55.0, "fps": 22.0}],
        }
        verdict3 = scoring.adjudicate_challenger(champ_1game, reg_chal_better_score, core)
        self.assertEqual(verdict3["decision"], "promoted")

    def test_turtle_inconclusive_cannot_beat_active_loss_and_zero_game_champ_opp_handled(self):
        core = ["PylonPuller", "Steamhammer", "BananaBrain"]
        champ = {
            "PylonPuller": [{"outcome": "win", "score": 75.0, "fps": 22.0}],
            "Steamhammer": [{"outcome": "loss", "score": 55.0, "fps": 22.0}],
            "BananaBrain": [{"outcome": "loss", "score": 45.0, "fps": 22.0}],
        }
        turtle_chal = {
            "PylonPuller": [{"outcome": "win", "score": 70.0, "fps": 22.0}],
            "Steamhammer": [{"outcome": "inconclusive", "score": 15.0, "fps": 22.0}],
            "BananaBrain": [{"outcome": "loss", "score": 40.0, "fps": 22.0}],
        }
        verdict = scoring.adjudicate_challenger(champ, turtle_chal, core)
        self.assertEqual(verdict["decision"], "rejected")

        # Champion with 0 valid games on Steamhammer must NOT average 0.0 into champ_score!
        champ_missing = {
            "PylonPuller": [{"outcome": "loss", "score": 60.0, "fps": 22.0}],
            "Steamhammer": [{"outcome": "excluded", "score": 0.0, "fps": None}],
            "BananaBrain": [{"outcome": "loss", "score": 60.0, "fps": 22.0}],
        }
        worse_chal = {
            "PylonPuller": [{"outcome": "loss", "score": 50.0, "fps": 22.0}],
            "Steamhammer": [{"outcome": "loss", "score": 50.0, "fps": 22.0}],
            "BananaBrain": [{"outcome": "loss", "score": 50.0, "fps": 22.0}],
        }
        verdict2 = scoring.adjudicate_challenger(champ_missing, worse_chal, core)
        self.assertEqual(verdict2["decision"], "rejected")
        self.assertEqual(verdict2["details"]["compared_opponents"], ["PylonPuller", "BananaBrain"])

    def test_frame_stall_at_frame_zero_is_challenger_crash_not_flake(self):
        stall_m = {"state": "error", "frame": 0, "error": "frame_stall: no new observations for 240.0s"}
        self.assertTrue(scoring.is_challenger_crash(stall_m))
        self.assertFalse(scoring.is_pre_game_flake(stall_m))

    def test_agent_failure_stops_immediately_after_3_submit_rejections_and_retries_disconnect(self):
        import http.client
        with TemporaryDirectory() as tmp:
            batch = Path(tmp) / "batch"
            v2_loop.initialize_v2_batch(batch, ROOT, {"core_opponents": ["PylonPuller"]}, compile_fn=_fake_compile)
            ws = v2_loop.V2ReviewWorkspace(batch, ROOT, v2_loop._read_v2_sources(batch / "versions/v1"), [], {})
            waits = []
            client = v2_loop.V2AgenticReviewClient("m", "high", "https://api.invalid/v1", 30,
                                                   api_key="k", max_turns=8, sleep=waits.append)
            bad_submit = {"role": "assistant", "content": None, "tool_calls": [
                {"id": "x1", "type": "function", "function": {"name": "submit", "arguments": json.dumps({
                    "prediction_check": "x", "evidence": ["m@1"], "diagnosis": "d", "hypothesis": "h",
                    "expectation": "e", "decision": "keep", "distillation_type": "none",
                })}}
            ]}
            calls = [0]
            def flaky_then_reject(req, timeout):
                calls[0] += 1
                if calls[0] == 1:
                    raise http.client.RemoteDisconnected("closed")
                resp = MagicMock()
                resp.read.return_value = json.dumps({"choices": [{"message": bad_submit}], "usage": {}}).encode()
                ctx = MagicMock()
                ctx.__enter__.return_value = resp
                return ctx
            with patch("app.rsi.v2_loop.urlopen", side_effect=flaky_then_reject):
                rep = client.review({"round": 1}, ws, batch / "review-call-1.json", allow_keep=False)
            self.assertEqual(waits, [5])  # RemoteDisconnected retried via transient backoff!
            self.assertEqual(rep["turns"], 3)  # Stopped right at 3 rejections, NOT max_turns!
            self.assertIn("submit rejected 3 times", rep["error"])


class MalformedJsonlAndStrategyLimitTests(unittest.TestCase):
    def test_malformed_build_log_lines_do_not_crash(self):
        import sys
        sys.path.insert(0, str(ROOT / "app"))
        from matches import confirm_build_config

        with TemporaryDirectory() as tmp:
            run = Path(tmp)
            trace = run / "bwapi-data/write/astra-trace.jsonl"
            trace.parent.mkdir(parents=True)
            cfg = build_config.defaults()
            trace.write_text(
                '{"event":"start"}\n{BROKEN JSON LINE\n'
                + json.dumps({"event": "config_loaded", "values": cfg}) + "\n"
                + '{"event":"build_plan","frame":1200,"type":"Protoss_Cybernetics_Core"}\n'
                + 'NOT JSON AT ALL\n',
                encoding="utf-8",
            )
            confirm_build_config(run, cfg)
            rows = _build_log(trace)
            self.assertEqual(len(rows), 1)
            diag = ledger.extract_self_reported_build_diag(run)
            self.assertEqual(diag["malformed_lines"], 2)
            self.assertEqual(diag["core_plan_frame"], 1200)

    def test_live_runner_accepts_6000_char_strategy_in_v2(self):
        with TemporaryDirectory() as tmp:
            batch = Path(tmp) / "batch"
            v1 = v2_loop.initialize_v2_batch(batch, ROOT, {"core_opponents": ["PylonPuller"]}, compile_fn=_fake_compile)
            (v1 / "strategy.md").write_text("S" * 4500, encoding="utf-8")
            fake_adapter = SimpleNamespace(
                speed=42, match_timeout=1800, map_name="Destination", opponent_name="PylonPuller",
                run=lambda obs, rec, idle: {"state": "finished", "frame": 120, "winner": True},
                send_key=lambda k: True, cutoff=lambda: True,
            )
            runner = LiveRunner(fake_adapter, MagicMock(), v1, batch / "match-1", max_strategy_chars=6000)
            res = runner.run()
            self.assertEqual(res["state"], "finished")


class V2LoopEndToEndTests(unittest.TestCase):
    def _turn(self, *calls):
        msg = {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c-{next(CALL_IDS)}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
            for n, a in calls
        ]}
        return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 200, "completion_tokens": 50}}

    def test_wave2_retests_champion_promotes_winner_and_fail_fasts_crash(self):
        with TemporaryDirectory() as tmp:
            batch = Path(tmp) / "batch"
            core = ["PylonPuller", "Steamhammer", "BananaBrain"]
            v2_loop.initialize_v2_batch(batch, ROOT, {"core_opponents": core}, compile_fn=_fake_compile)

            played_log = []
            def fake_play(run, vdir, vlabel, opp, mnum, cfg_text, dll_path):
                played_log.append((mnum, vlabel, opp))
                run.mkdir(parents=True, exist_ok=True)
                # c1 improves and beats everyone; c2 crashes on Wave 1!
                if vlabel == "c2":
                    return {"state": "error", "frame": 24, "seconds": 2.0, "winner": None,
                            "natural_outcome": "unknown", "error": "MacroBot crash"}
                won = (vlabel == "c1") or (opp == "PylonPuller")
                obs = _make_obs(list(range(0, 9601, 240)), army=20 if won else 10, healthy=18 if won else 8,
                                opponent=opp)
                write_json(run / "observations.json", obs)
                write_json(run / "timeline.json", [])
                write_json(run / "runtime-errors.json", [])
                return {
                    "state": "finished", "winner": won, "natural_outcome": "win" if won else "loss",
                    "adjudicated_outcome": None, "frame": 9600, "seconds": 400.0,
                    "lock_defense": {"fraction": 0.05}, "decision_sources": {}, "tokens": {}, "live_calls": 5,
                }

            # Round 1 review proposes c1 (edits MacroBot.cpp via edit_policy)
            # Round 2 (c1 promoted -> v2) review proposes c2 (edits MacroBot.cpp)
            # Round 3 (c2 crashes -> fail-fast rejected -> Champion stays v2) is final round
            scripted = [
                self._turn(("read_policy", {"file": "MacroBot.cpp"}), ("read_engine_file", {"file": "CommanderDefence.h"})),
                self._turn(("edit_policy", {
                    "file": "MacroBot.cpp",
                    "old_text": "    if(army.size()>=6) attacking=true;",
                    "new_text": "    if(army.size()>=8) attacking=true;",
                }), ("check_candidate", {})),
                self._turn(("submit", {
                    "prediction_check": "N/A baseline", "evidence": ["match-1@4800"],
                    "diagnosis": "Attacking at 6 units feeds early.", "hypothesis": "Wait for 8 units.",
                    "expectation": "Higher win rate.", "decision": "modify", "distillation_type": "evolve_cpp",
                })),
                # Round 2 review proposes c2
                self._turn(("edit_policy", {
                    "file": "MacroBot.cpp",
                    "old_text": "    if(army.size()>=8) attacking=true;",
                    "new_text": "    if(army.size()>=10) attacking=true;",
                }), ("check_candidate", {})),
                self._turn(("submit", {
                    "prediction_check": "c1 promoted to v2.", "evidence": ["match-5@4800"],
                    "diagnosis": "Test 10 threshold.", "hypothesis": "Try 10.",
                    "expectation": "Better fights.", "decision": "modify", "distillation_type": "evolve_cpp",
                })),
            ]
            def fake_urlopen(req, timeout):
                resp = MagicMock()
                resp.read.return_value = json.dumps(scripted.pop(0)).encode()
                ctx = MagicMock()
                ctx.__enter__.return_value = resp
                return ctx

            args = SimpleNamespace(
                rounds=3, parallel=2, map="Destination", opponent=",".join(core),
                speed=42, match_timeout=1800, max_frames=36000, stalemate_zero_frames=360, no_evolve=False,
            )
            circuit = ModelCircuit()
            live_model = SimpleNamespace(circuit=circuit)
            reviewer = v2_loop.V2AgenticReviewClient("m", "high", "https://api.invalid/v1", 30, api_key="k", circuit=circuit)

            with patch("app.rsi.v2_loop.urlopen", side_effect=fake_urlopen):
                rc = v2_loop.run_v2_live(args, batch, ROOT, live_model, reviewer, Event(),
                                         play_fn=fake_play, compile_fn=_fake_compile)
            self.assertEqual(rc, 0)
            # Round 1: 4 matches on v1. Round 2: 3 matches on c1 + 1 Champion (v1) retest in Wave 2 idle slot!
            # Round 3: c2 crashes in Wave 1 (2 matches), fail-fast skips Wave 2!
            round2_entries = [x for x in played_log if x[0] in (5, 6, 7, 8)]
            self.assertEqual([x[1] for x in round2_entries], ["c1", "c1", "c1", "v1"])
            self.assertEqual(len(played_log), 10)  # 4 + 4 + 2 (fail-fast!)
            self.assertEqual(evolution.active_pointer(batch)["version"], "v2")
            progress = (batch / "PROGRESS.md").read_text(encoding="utf-8")
            self.assertIn("v2", progress)
            self.assertIn("crashed_reject", progress)


if __name__ == "__main__":
    unittest.main()
