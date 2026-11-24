"""Agentic post-round review: the model investigates the round through L0-implemented tools.

The model never receives a shell or file paths. Each tool reads only this round's own-side
archives (observations with the L0 situation digest, timeline, native receipts, runtime errors,
L0 results) and the active policy sources. Analysis code runs in a separate isolated Python
process under the same AST restrictions as policy code. The client returns an ordinary review
report, so evolution.review_round applies the unchanged schema, keep gate and candidate gates.
"""

import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    # The analysis worker runs as an isolated script and needs only the sandbox.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rsi import sandbox
else:
    from . import evolution, sandbox, situation, validator
    from .model_client import PROVIDER_FAILURES, USAGE_FIELDS, ModelCircuit, _provider_failure, write_json


MAX_TURNS = 20
WALL_SECONDS = 900
ANALYSIS_TIMEOUT_SECONDS = 20
TOOL_OUTPUT_CHARS = 10000
FRAME_ROWS = 25
TIMELINE_ROWS = 60
SUBMIT_REJECTIONS = 3
TRANSIENT_WAITS = (5, 15, 30)
FRAME_FIELDS = ("frame", "workers", "army", "healthy_army", "mode", "stance", "main_task", "squad_task",
                "visible_enemies", "visible_enemies_within_384px", "visible_undetected_enemies", "situation")
TIMELINE_FIELDS = ("frame", "source", "status", "trigger", "action_type", "target", "key", "reason",
                   "dispatch_frame", "failure_kind", "rule_name")
RESULT_FIELDS = ("match_number", "opponent", "map", "state", "natural_outcome", "adjudicated_outcome", "winner",
                 "frame", "policy_version", "cutoff_reason", "cutoff_frame", "lock_defense", "army_peaks",
                 "decision_sources", "degraded", "live_model_failures")
PROMPT_EDITS = (
    ("The source and all observations are data. Do not use tools or inspect any files.",
     "The source and all observations are data. Inspect them only through the review tools."),
    ("Supply the entire text of 1 or 2 changed whitelist files for modify and no edits for keep or revert.",
     "For modify, stage the complete text of 1 or 2 changed whitelist files with write_policy and pass "
     "check_candidate. Stage nothing for keep or revert."),
    ("Return one JSON object with exactly these fields:", "Finish by calling submit with these fields:"),
    (", and edits (object of filenames to complete source).", ". Staged files become the edits."),
    (" End the response after this object.", ""),
)
AGENT_PREAMBLE = """You investigate one round like an analyst, using tools. The first message holds the
same facts, results and version history as a single-shot review. Use get_frames, get_timeline and
run_analysis to test your leading explanation against the full observation stream before deciding.
Each observation row carries situation, L0's side-visible digest. read_policy shows the active
files. Work on one problem, keep changes small, and call submit when you are done.
run_analysis code must define analyze(evidence) and return JSON-compatible data. evidence is
{"matches": {"<number>": {"result", "observations", "timeline", "receipts", "runtime_errors"}},
"facts", "version_history"}. It may import only math, collections, statistics, itertools,
functools, copy, typing or dataclasses, and may not open files or use reflection.
"""
TOOLS = [
    {"name": "list_evidence", "description": "List this round's matches, frame ranges and evidence sizes.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_frames",
     "description": f"Observation rows of one match between two frames, at most {FRAME_ROWS} evenly spaced rows.",
     "parameters": {"type": "object", "required": ["match_number", "start_frame", "end_frame"], "properties": {
         "match_number": {"type": "integer"}, "start_frame": {"type": "integer"}, "end_frame": {"type": "integer"},
         "fields": {"type": "array", "items": {"type": "string"},
                    "description": "Top-level observation fields. Defaults to core fields plus situation."}}}},
    {"name": "get_timeline",
     "description": f"Decision timeline rows of one match between two frames, at most {TIMELINE_ROWS} rows.",
     "parameters": {"type": "object", "required": ["match_number", "start_frame", "end_frame"], "properties": {
         "match_number": {"type": "integer"}, "start_frame": {"type": "integer"},
         "end_frame": {"type": "integer"}}}},
    {"name": "run_analysis",
     "description": "Run restricted Python analyze(evidence) over every observation, timeline row and receipt.",
     "parameters": {"type": "object", "required": ["code"], "properties": {"code": {"type": "string"}}}},
    {"name": "read_policy", "description": "Read one whitelist file: the staged version if any, else the active one.",
     "parameters": {"type": "object", "required": ["file"], "properties": {
         "file": {"type": "string", "enum": ["strategy.md", "observer.py", "tools.py", "rules.py"]}}}},
    {"name": "write_policy", "description": "Stage the complete new text of one whitelist file (at most 2 files).",
     "parameters": {"type": "object", "required": ["file", "content"], "properties": {
         "file": {"type": "string", "enum": ["strategy.md", "observer.py", "tools.py", "rules.py"]},
         "content": {"type": "string"}}}},
    {"name": "discard_policy", "description": "Drop a staged file.",
     "parameters": {"type": "object", "required": ["file"], "properties": {"file": {"type": "string"}}}},
    {"name": "check_candidate",
     "description": "Run L0's source, contract and shadow-replay gates on the staged files against this round.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "submit", "description": "Finish the review. Staged files become the edits of a modify decision.",
     "parameters": {"type": "object", "required": [
         "prediction_check", "evidence", "diagnosis", "hypothesis", "expectation", "decision", "distillation_type"],
         "properties": {
             "prediction_check": {"type": "string"},
             "evidence": {"type": "array", "items": {"type": "integer"}, "description": "fact_id values"},
             "diagnosis": {"type": "string"}, "hypothesis": {"type": "string"}, "expectation": {"type": "string"},
             "decision": {"type": "string", "enum": ["keep", "modify", "revert"]},
             "distillation_type": {"type": "string", "enum": sorted(
                 ["none", "strategy_only", "add_observation", "add_tool", "distill_rule", "bugfix_code"])}}}},
]


def _cache_breakpoints(messages):
    """Mark the system prompt and the newest message for prompt caching.

    Every turn resends the whole conversation, and the round facts alone are ~50k tokens. Round 1 of
    matches/rsi/20261124-133626-533308 spent 586k input tokens in 9 turns. With these marks each turn
    reads the previous turn's prefix from cache; the router passes cache_control through to Anthropic.
    """
    marked = [dict(message) for message in messages]
    for index in {0, len(marked) - 1}:
        content = marked[index].get("content")
        if isinstance(content, str) and content:
            marked[index]["content"] = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
    return marked


class AgentFailure(RuntimeError):
    def __init__(self, message, kind):
        super().__init__(message)
        self.kind = kind


def _text(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(text) > TOOL_OUTPUT_CHARS:
        return text[:TOOL_OUTPUT_CHARS] + f"... [truncated {len(text) - TOOL_OUTPUT_CHARS} characters]"
    return text


def _sample(rows, limit):
    if len(rows) <= limit:
        return rows
    return [rows[index * (len(rows) - 1) // (limit - 1)] for index in range(limit)]


def agent_prompt(prompt):
    for old, new in PROMPT_EDITS:
        if old not in prompt:
            raise ValueError(f"review prompt no longer contains {old!r}")
        prompt = prompt.replace(old, new)
    return AGENT_PREAMBLE + "\n" + prompt


class ReviewWorkspace:
    """Own-side evidence of one round and the staged candidate files. Only L0 touches disk."""

    def __init__(self, batch, payload):
        self.batch = Path(batch)
        self.payload = payload
        self.sources = dict(payload["current_sources"])
        self.staged = {}
        self.matches = {}
        for result in payload["matches"]:
            number = result["match_number"]
            run = self.batch / f"match-{number}"
            timeline = evolution.read_json(run / "timeline.json", [])
            receipts = evolution.read_json(run / "native-receipts.json")
            self.matches[number] = {
                "result": {name: result[name] for name in RESULT_FIELDS if name in result},
                "observations": situation.attach(evolution.read_json(run / "observations.json", [])),
                "timeline": timeline,
                "receipts": receipts if receipts is not None else [row["receipt"] for row in timeline
                                                                     if row.get("receipt")],
                "runtime_errors": evolution.read_json(run / "runtime-errors.json", []),
            }

    def match(self, number):
        if number not in self.matches:
            raise ValueError(f"match {number} is not part of this round; use one of {sorted(self.matches)}")
        return self.matches[number]

    def list_evidence(self):
        return {str(number): {
            "result": data["result"],
            "observations": len(data["observations"]),
            "first_frame": data["observations"][0]["frame"] if data["observations"] else None,
            "last_frame": data["observations"][-1]["frame"] if data["observations"] else None,
            "timeline_rows": len(data["timeline"]), "receipts": len(data["receipts"]),
            "runtime_errors": len(data["runtime_errors"]),
        } for number, data in self.matches.items()}

    def get_frames(self, match_number, start_frame, end_frame, fields=None):
        fields = tuple(fields or FRAME_FIELDS)
        rows = [row for row in self.match(match_number)["observations"] if start_frame <= row["frame"] <= end_frame]
        return {"rows_in_range": len(rows),
                "rows": [{name: row[name] for name in ("frame", *fields) if name in row}
                         for row in _sample(rows, FRAME_ROWS)]}

    def get_timeline(self, match_number, start_frame, end_frame):
        rows = [row for row in self.match(match_number)["timeline"]
                if type(row.get("frame")) is int and start_frame <= row["frame"] <= end_frame]
        projected = []
        for row in rows:
            item = {name: row[name] for name in TIMELINE_FIELDS if name in row}
            if isinstance(row.get("receipt"), dict):
                item["receipt"] = {name: row["receipt"].get(name) for name in ("frame", "key", "status", "stance")}
            projected.append(item)
        return {"rows_in_range": len(rows), "rows": projected[:TIMELINE_ROWS]}

    def evidence(self):
        return {"matches": {str(number): data for number, data in self.matches.items()},
                "facts": self.payload["facts"], "version_history": self.payload.get("version_history")}

    def read_policy(self, file):
        if file not in self.sources:
            raise ValueError(f"unknown policy file {file!r}")
        return {"file": file, "staged": file in self.staged, "content": self.staged.get(file, self.sources[file])}

    def write_policy(self, file, content):
        if file not in self.sources:
            raise ValueError(f"unknown policy file {file!r}")
        if file not in self.staged and len(self.staged) >= 2:
            raise ValueError("at most 2 files may change; discard one first")
        limit = validator.SOURCE_LIMITS[file]
        if type(content) is not str or len(content) > limit:
            raise ValueError(f"{file} must be text of at most {limit} characters")
        self.staged[file] = content
        return {"staged": sorted(self.staged), "characters": len(content)}

    def discard_policy(self, file):
        self.staged.pop(file, None)
        return {"staged": sorted(self.staged)}

    def check_candidate(self):
        if not self.staged:
            raise ValueError("no staged files")
        if unchanged := [name for name, text in self.staged.items() if text == self.sources[name]]:
            raise ValueError(f"staged files are unchanged: {', '.join(unchanged)}")
        for name, text in self.staged.items():
            if name.endswith(".py"):
                sandbox.check_source(name, text)
        after = {**self.sources, **self.staged}
        report = {}
        for number, data in self.matches.items():
            if not data["observations"]:
                continue
            compared = validator._compare_versions(self.sources, after, data["observations"],
                                                   validator.SHADOW_MAX_FRAMES)
            candidate = compared["versions"]["candidate"]
            report[str(number)] = {
                "candidate_wakes": candidate["wake_llm_count"], "candidate_reflexes": candidate["reflex_trigger_count"],
                "wake_reasons": candidate["wake_reasons"],
                "wake_frame_diffs": (compared["wake_frame_diffs"] or [])[:10],
                "reflex_frame_diffs": (compared["reflex_frame_diffs"] or [])[:10],
                "tool_smoke": candidate["tool_smoke"],
            }
        return {"passed": True, "matches": report}

    def run_analysis(self, code):
        request = json.dumps({"source": code, "evidence": self.evidence()}, ensure_ascii=False)
        try:
            result = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--analysis-worker"],
                                    input=request, encoding="utf-8", capture_output=True,
                                    timeout=ANALYSIS_TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            raise ValueError(f"analysis exceeded {ANALYSIS_TIMEOUT_SECONDS}s") from None
        if result.returncode:
            raise ValueError(f"analysis worker exited with code {result.returncode}: {result.stderr.strip()[-800:]}")
        reply = json.loads(result.stdout)
        if "error" in reply:
            raise ValueError(reply["error"])
        return reply["result"]


class AgenticReviewClient:
    """A review client with the ModelClient call signature that runs a bounded tool loop."""

    backend = "openai"

    def __init__(self, model, reasoning_effort, endpoint, timeout, *, api_key, circuit=None,
                 max_turns=MAX_TURNS, wall_seconds=WALL_SECONDS, sleep=time.sleep):
        self.model = model
        self.reasoning_effort = reasoning_effort
        endpoint = endpoint.rstrip("/")
        self.endpoint = endpoint if endpoint.endswith("/chat/completions") else endpoint + "/chat/completions"
        self.timeout = timeout
        self.api_key = api_key
        self.circuit = circuit if circuit is not None else ModelCircuit()
        self.max_turns = max_turns
        self.wall_seconds = wall_seconds
        self.sleep = sleep

    def call(self, prompt, payload, archive):
        archive = Path(archive)
        trace_path = archive.with_name(archive.name.replace("review-call", "review-trace")).with_suffix(".jsonl")
        report = dict.fromkeys(USAGE_FIELDS, 0)
        report.update(backend="agentic", model=self.model, reasoning_effort=self.reasoning_effort,
                      decoded=None, error=None, failure_kind=None, reported_cost=None, cost_currency=None,
                      invoked=True, turns=0, tool_calls={}, usage_missing_calls=0, trace=trace_path.name)
        started = time.monotonic()
        if self.circuit.open:
            report.update(error=self.circuit.reason, failure_kind="circuit_open", invoked=False)
        else:
            trace_path.unlink(missing_ok=True)
            try:
                workspace = ReviewWorkspace(archive.parent.parent, payload)
                report["decoded"] = self._loop(prompt, payload, workspace, report, trace_path, started)
                self._write_workspace(archive, workspace, report["decoded"])
            except AgentFailure as error:
                report.update(error=str(error), failure_kind=error.kind)
            except Exception as error:  # An L0 tool bug must not stop a long batch; the round records it.
                report.update(error=f"{type(error).__name__}: {error}", failure_kind="backend_error")
        report["seconds"] = round(time.monotonic() - started, 3)
        report["usage_missing"] = [field for field in USAGE_FIELDS if report["usage_missing_calls"]]
        report["circuit"] = {"open": self.circuit.open, "reason": self.circuit.reason,
                             "failures": self.circuit.failures}
        write_json(archive, report)
        return report

    def _loop(self, prompt, payload, workspace, report, trace_path, started):
        overview = {key: value for key, value in payload.items() if key != "current_sources"}
        messages = [{"role": "system", "content": agent_prompt(prompt)},
                    {"role": "user", "content": json.dumps(overview, ensure_ascii=False)}]
        rejections = 0
        for turn in range(1, self.max_turns + 3):
            if time.monotonic() - started > self.wall_seconds:
                raise AgentFailure(f"agentic review exceeded {self.wall_seconds}s", "invalid_response")
            if turn == self.max_turns + 1:
                messages.append({"role": "user", "content": "The tool budget is spent. Call submit now."})
            message = self._chat(messages, report)
            report["turns"] = turn
            calls = message.get("tool_calls") or []
            messages.append({key: message[key] for key in ("role", "content", "tool_calls") if key in message})
            if not calls:
                messages.append({"role": "user", "content": "Continue with the tools and finish by calling submit."})
                self._trace(trace_path, turn, message, [])
                continue
            results = []
            for call in calls:
                name = call["function"]["name"]
                report["tool_calls"][name] = report["tool_calls"].get(name, 0) + 1
                try:
                    arguments = json.loads(call["function"].get("arguments") or "{}")
                    if name == "submit":
                        proposal, problem = self._submit(arguments, workspace, payload)
                        if problem is None:
                            self._trace(trace_path, turn, message, [{"tool": name, "accepted": True}])
                            return proposal
                        rejections += 1
                        if rejections >= SUBMIT_REJECTIONS:
                            self._trace(trace_path, turn, message, [{"tool": name, "accepted": False,
                                                                     "error": problem}])
                            return proposal
                        output = {"error": problem, "rejections_left": SUBMIT_REJECTIONS - rejections}
                    elif name in {"list_evidence", "get_frames", "get_timeline", "run_analysis", "read_policy",
                                  "write_policy", "discard_policy", "check_candidate"}:
                        output = getattr(workspace, name)(**arguments)
                    else:
                        output = {"error": f"unknown tool {name!r}"}
                except Exception as error:  # Tool errors go back to the model as data.
                    output = {"error": f"{type(error).__name__}: {error}"}
                text = _text(output)
                results.append({"tool": name, "arguments": call["function"].get("arguments"), "output": text[:2000]})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
            self._trace(trace_path, turn, message, results)
        raise AgentFailure("agentic review ended without an accepted submit", "invalid_response")

    def _submit(self, arguments, workspace, payload):
        proposal = {name: arguments.get(name) for name in
                    ("prediction_check", "evidence", "diagnosis", "hypothesis", "expectation", "decision",
                     "distillation_type")}
        proposal["edits"] = dict(workspace.staged) if proposal["decision"] == "modify" else {}
        version = payload["active_version"]["version"]
        base = {"from_version": version, "to_version": version, "round": payload["round"],
                "match_numbers": [match["match_number"] for match in payload["matches"]]}
        result = evolution._decision_from_report({"decoded": proposal}, base, payload, None)
        if result["validation"] == "rejected":
            return proposal, result["validation_error"]
        if proposal["decision"] == "modify":
            try:
                workspace.check_candidate()
            except Exception as error:
                return proposal, f"check_candidate failed: {type(error).__name__}: {error}"
        return proposal, None

    def _chat(self, messages, report):
        body = {"model": self.model, "messages": _cache_breakpoints(messages),
                "tools": [{"type": "function", "function": tool} for tool in TOOLS]}
        if self.reasoning_effort != "omit":
            body["reasoning_effort"] = self.reasoning_effort
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        waits = list(TRANSIENT_WAITS)
        while True:
            try:
                with urlopen(Request(self.endpoint, data, headers), timeout=self.timeout) as response:
                    decoded = json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                with error:
                    try:
                        text = error.read().decode("utf-8", errors="replace")
                    except (OSError, ValueError):
                        text = ""
                kind = _provider_failure([text], status=error.code) or "backend_error"
                problem = f"HTTP {error.code}: {text[:300]}"
            except (URLError, TimeoutError) as error:
                kind, problem = "transient", f"model API unreachable: {error}"
            else:
                if not decoded.get("error"):
                    break
                text = json.dumps(decoded["error"], ensure_ascii=False)
                kind = _provider_failure([text]) or "backend_error"
                problem = f"model API error: {text[:300]}"
            self.circuit.record(kind)
            if kind != "transient" or not waits:
                raise AgentFailure(problem, kind)
            self.sleep(waits.pop(0))
        self.circuit.record(None)
        usage = decoded.get("usage") or {}
        if not usage:
            report["usage_missing_calls"] += 1
        report["input_tokens"] += usage.get("prompt_tokens") or 0
        report["output_tokens"] += usage.get("completion_tokens") or 0
        report["cached_input_tokens"] += (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        try:
            return decoded["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise AgentFailure("model API response has no message", "invalid_response") from None

    @staticmethod
    def _trace(path, turn, message, results):
        row = {"turn": turn, "content": message.get("content"),
               "tool_calls": [{"name": call["function"]["name"], "arguments": call["function"].get("arguments")}
                              for call in message.get("tool_calls") or []],
               "results": results}
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")

    @staticmethod
    def _write_workspace(archive, workspace, proposal):
        folder = archive.with_name(archive.stem.replace("review-call", "review-workspace"))
        for name, text in workspace.staged.items():
            (folder / "candidate").mkdir(parents=True, exist_ok=True)
            (folder / "candidate" / name).write_text(text, encoding="utf-8", newline="\n")
        write_json(folder / "handoff.json", proposal)


def _analysis_worker():
    request = json.load(sys.stdin)
    try:
        analyze = sandbox.load_analysis(request["source"])
        result = analyze(request["evidence"])
        text = json.dumps({"result": result}, ensure_ascii=False, allow_nan=False)
    except Exception as error:
        text = json.dumps({"error": f"{type(error).__name__}: {error}"}, ensure_ascii=False)
    sys.stdout.write(text)


if __name__ == "__main__" and sys.argv[1:] == ["--analysis-worker"]:
    _analysis_worker()
