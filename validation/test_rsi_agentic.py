"""Offline checks for the agentic review tool loop with a scripted HTTP model."""

from io import BytesIO
from itertools import count
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from app.rsi.agentic_review import AgentFailure, AgenticReviewClient, agent_prompt
from app.rsi.evolution import REVIEW_PROMPT, active_pointer, initialize_batch, read_json, review_round
from app.rsi.model_client import write_json
from app.rsi.primitives import fallback_summarize


REFLEX = """def decide_reflex(summary, active_tool, memory):
    if summary["frame"] == 120:
        return {"rule_name": "opening_guard", "action_type": "key", "target": "7", "reason": "Guard at opening."}
    return None
"""
ANALYSIS = """def analyze(evidence):
    rows = evidence["matches"]["1"]["observations"]
    return {"frames": len(rows), "max_workers": max(row["workers"] for row in rows)}
"""
CALL_IDS = count()
SUBMIT = {"prediction_check": "N/A", "evidence": [1], "diagnosis": "Frame 120 lacks a guard.",
          "hypothesis": "A local guard is reproducible.", "expectation": "One reflex at frame 120.",
          "decision": "modify", "distillation_type": "distill_rule"}


def turn(*calls):
    message = {"role": "assistant", "content": None, "tool_calls": [
        {"id": f"call-{next(CALL_IDS)}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        for name, args in calls]}
    return {"choices": [{"message": message}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}


class ScriptedHTTP:
    def __init__(self, *steps):
        self.steps = list(steps)
        self.bodies = []

    def __call__(self, request, timeout):
        self.bodies.append(json.loads(request.data))
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        response = MagicMock()
        response.read.return_value = json.dumps(step).encode()
        context = MagicMock()
        context.__enter__.return_value = response
        return context

    def tool_output(self, name):
        """Return the tool message content sent back after the first call of this tool."""
        for body in self.bodies:
            calls = {call["id"]: call["function"]["name"] for message in body["messages"]
                     for call in message.get("tool_calls") or []}
            for message in body["messages"]:
                if message["role"] == "tool" and calls.get(message["tool_call_id"]) == name:
                    content = text(message)
                    return json.loads(content) if content.startswith("{") else content
        return None


def text(message):
    """Message text whether or not it carries a cache breakpoint."""
    content = message["content"]
    return content if isinstance(content, str) else "".join(part["text"] for part in content)


class AgenticReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.batch = Path(temporary.name) / "batch"
        initialize_batch(self.batch)
        self.waits = []
        self.client = AgenticReviewClient("model", "high", "https://api.invalid/v1", 30, api_key="secret-token",
                                          sleep=self.waits.append)

    def match(self, number, winner=True):
        run = self.batch / f"match-{number}"
        write_json(run / "observations.json", [fallback_summarize({"frame": frame, "workers": 4 + frame // 120})
                                               for frame in (0, 120, 240)])
        write_json(run / "timeline.json", [{"frame": 120, "source": "llm", "status": "kept_without_key",
                                            "reason": "hold"}])
        write_json(run / "runtime-errors.json", [])
        (run / "opponent").mkdir()
        (run / "opponent" / "secret.jsonl").write_text("enemy view", encoding="utf-8")
        return {"match_number": number, "state": "finished", "winner": winner, "frame": 245,
                "policy_version": "v1", "seconds": 1.0, "decision_sources": {}, "tokens": {}}

    def test_prompt_edits_still_apply_to_the_review_prompt(self):
        prompt = agent_prompt(REVIEW_PROMPT)
        self.assertIn("Inspect them only through the review tools", prompt)
        self.assertNotIn("Do not use tools", prompt)

    def test_tool_loop_investigates_stages_checks_and_promotes(self):
        http = ScriptedHTTP(
            turn(("list_evidence", {}), ("get_frames", {"match_number": 1, "start_frame": 0, "end_frame": 240})),
            turn(("run_analysis", {"code": ANALYSIS}), ("run_analysis", {"code": "def analyze(e):\n    return open('x')\n"})),
            turn(("get_frames", {"match_number": 9, "start_frame": 0, "end_frame": 10})),
            turn(("write_policy", {"file": "rules.py", "content": REFLEX}), ("check_candidate", {})),
            turn(("submit", SUBMIT)),
        )
        with patch("app.rsi.agentic_review.urlopen", side_effect=http):
            result = review_round([self.match(1)], self.batch, self.client, round_number=1)
        self.assertEqual((result["validation"], active_pointer(self.batch)["version"]), ("accepted", "v2"))
        self.assertEqual(http.tool_output("run_analysis"), {"frames": 3, "max_workers": 6})
        self.assertIn("not part of this round", json.dumps(http.bodies[3]["messages"][-1]))
        self.assertTrue(http.tool_output("check_candidate")["passed"])
        forbidden = [text(message) for message in http.bodies[2]["messages"] if message["role"] == "tool"][-1]
        self.assertIn("forbidden name open", forbidden)
        report = read_json(self.batch / "match-1/review-call-1.json")
        self.assertEqual((report["turns"], report["input_tokens"]), (5, 500))
        self.assertEqual(report["tool_calls"]["run_analysis"], 2)
        self.assertTrue((self.batch / "match-1/review-trace-1.jsonl").is_file())
        self.assertTrue((self.batch / "match-1/review-workspace-1/candidate/rules.py").is_file())
        archived = "".join(path.read_text(encoding="utf-8") for path in (self.batch / "match-1").rglob("review-*")
                           if path.is_file())
        self.assertNotIn("secret-token", archived)
        self.assertNotIn("enemy view", json.dumps(http.bodies))
        self.assertEqual(http.bodies[0]["reasoning_effort"], "high")
        self.assertNotIn("current_sources", text(http.bodies[0]["messages"][1]))
        # Cache breakpoints: the system prompt and only the newest message of each request.
        for body in http.bodies:
            marked = [index for index, message in enumerate(body["messages"])
                      if isinstance(message["content"], list) and message["content"][-1].get("cache_control")]
            self.assertEqual(marked, [0, len(body["messages"]) - 1])

    def test_rejected_submit_returns_the_reason_to_the_model(self):
        previous = self.batch
        review_round([self.match(1, winner=False)], previous, MagicMock(call=MagicMock(return_value={
            "decoded": {**SUBMIT, "evidence": [], "decision": "keep", "distillation_type": "none", "edits": {}},
            "error": None, "seconds": 0.1})), round_number=1)
        self.batch = previous.parent / "second"
        initialize_batch(self.batch, previous / "versions/v1")
        keep = {**SUBMIT, "decision": "keep", "distillation_type": "none", "evidence": []}
        http = ScriptedHTTP(
            turn(("submit", keep)),
            turn(("write_policy", {"file": "rules.py", "content": REFLEX}), ("submit", SUBMIT)),
        )
        with patch("app.rsi.agentic_review.urlopen", side_effect=http):
            result = review_round([self.match(1, winner=False)], self.batch, self.client, round_number=1)
        self.assertIn("2 losses and no wins", http.tool_output("submit")["error"])
        self.assertEqual(result["validation"], "accepted")

    def test_transient_http_errors_wait_and_fatal_errors_stop(self):
        def busy():
            return HTTPError("https://api.invalid/v1", 503, "busy", None, BytesIO(b"{}"))

        http = ScriptedHTTP(busy(), busy(), turn(("submit", {**SUBMIT, "decision": "keep", "evidence": [],
                                                       "distillation_type": "none"})))
        with patch("app.rsi.agentic_review.urlopen", side_effect=http):
            result = review_round([self.match(1)], self.batch, self.client, round_number=1)
        self.assertEqual(result["validation"], "kept")
        self.assertEqual(self.waits, [5, 15])

        denied = HTTPError("https://api.invalid/v1", 401, "denied", None, BytesIO(b'{"error":"invalid api key"}'))
        report = {"usage_missing_calls": 0, "input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
        with patch("app.rsi.agentic_review.urlopen", side_effect=ScriptedHTTP(denied)),                 self.assertRaises(AgentFailure) as failure:
            self.client._chat([{"role": "user", "content": "x"}], report)
        self.assertEqual(failure.exception.kind, "auth")
        self.assertEqual(self.client.circuit.failures, 1)


if __name__ == "__main__":
    unittest.main()
