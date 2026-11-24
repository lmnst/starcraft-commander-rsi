"""Run a text-only model turn and retain the request, output and available usage."""

import json
import math
import os
from pathlib import Path
import re
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


USAGE_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens")
# Fatal account problems open the circuit. Transient service trouble never does.
PROVIDER_FAILURES = {"quota", "auth"}
TRANSIENT_FAILURES = {"transient", "timeout"}
STATUS_PREFIX = r"\b(?:http(?:/\S+)?|status(?:\s+code)?|code|returned)[\s\"':=]+"
# Quota exhaustion is often reported with HTTP 429, so message text is checked before status codes.
QUOTA_ERROR = re.compile(
    r"usage[_ ]limit|insufficient[_ ]quota|quota[_ ](?:exceeded|exhausted|reached)|exceeded.{0,35}quota|"
    r"insufficient (?:balance|credits)|"
    r"credit balance is too low|billing[_ ]hard[_ ]limit|\b402 Payment Required\b|"
    + STATUS_PREFIX + r"402\b", re.I,
)
TRANSIENT_ERROR = re.compile(
    r"rate[_ ]limit|too many requests|temporarily unable|overloaded|service unavailable|bad gateway|"
    r"gateway time-?out|internal server error|"
    + STATUS_PREFIX + r"(?:429|5\d\d)\b", re.I,
)
RETRY_HINT = re.compile(r"try again (?:at|in) ([^.\"\\\n]{1,40})", re.I)
AUTH_ERROR = re.compile(
    r"invalid[_ ](?:api[_ ]key|authentication|token)|incorrect api key|"
    r"authentication[_ ](?:error|failed|required)|unauthori[sz]ed|"
    r"(?:api key|access token).{0,30}(?:invalid|expired|missing|revoked)|"
    r"(?:missing|expired|revoked).{0,30}(?:api key|access token)|"
    r"not logged in|run.{0,12}codex login|token refresh failed|"
    + STATUS_PREFIX + r"401\b", re.I,
)


class ModelCircuit:
    """One hard circuit shared by the batch's live and review clients."""

    def __init__(self):
        self.failures = 0
        self.reason = None
        self.retry_hint = None
        self._lock = Lock()

    @property
    def open(self):
        return self.reason is not None

    def record(self, failure_kind, retry_hint=None):
        with self._lock:
            # A transient failure says nothing about the account, so it neither counts nor resets.
            if self.open or failure_kind in TRANSIENT_FAILURES:
                return
            self.failures = self.failures + 1 if failure_kind in PROVIDER_FAILURES else 0
            if failure_kind in PROVIDER_FAILURES and retry_hint:
                self.retry_hint = retry_hint
            if self.failures >= 3:
                hint = f"; provider says try again at {self.retry_hint}" if self.retry_hint else ""
                self.reason = f"3 consecutive model quota/auth failures (last: {failure_kind}{hint})"


class BackendFailure(RuntimeError):
    def __init__(self, message, kind=None):
        super().__init__(message)
        self.kind = kind or "backend_error"


def _provider_failure(messages, *, status=None):
    for message in messages:
        if QUOTA_ERROR.search(message):
            return "quota"
        if AUTH_ERROR.search(message):
            return "auth"
    if status == 401:
        return "auth"
    if status == 402:
        return "quota"
    if status is not None and (status == 429 or 500 <= status <= 599):
        return "transient"
    if any(TRANSIENT_ERROR.search(message) for message in messages):
        return "transient"
    return None


def _retry_hint(report):
    for field in ("response", "stderr", "stdout", "error"):
        if isinstance(report.get(field), str) and (match := RETRY_HINT.search(report[field])):
            return match.group(1).strip()
    return None


def _output_text(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""


def _codex_events(output):
    events = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _codex_failure(events, stdout, stderr):
    for event in events:
        if event.get("type") not in {"error", "turn.failed"}:
            continue
        kind = _provider_failure([json.dumps(event, ensure_ascii=False)])
        if kind:
            return kind
    # Ignore structured agent output. Plain process diagnostics can be written to either stream.
    plain_lines = [line for line in stdout.splitlines() if not line.lstrip().startswith("{")]
    return _provider_failure([stderr, *plain_lines])


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, ensure_ascii=True, allow_nan=False, indent=2)
            stream.write("\n")
            stream.close()
            temporary.replace(path)
        finally:
            stream.close()
            temporary.unlink(missing_ok=True)


def _count(value):
    return value if type(value) is int and value >= 0 else None


def _embedded_objects(text):
    decoder = json.JSONDecoder()
    found = []
    start = text.find("{")
    while start != -1:
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        found.append(value)
        start = text.find("{", end)
    return found


def _decode(text):
    # Some compatible endpoints ignore JSON mode: Opus at times writes a few sentences and then the
    # object, bare or in a Markdown fence (matches/rsi/20261124-133626-533308, match-1 decision-006
    # and match-2 decision-010). It also writes an object, a line such as "Fix: single target field." and a
    # corrected object (matches/rsi/20261124-143925-255737, match-2 decision-007, -008 and -022), so the
    # last embedded object is its answer.
    try:
        answer = json.loads(text)
    except ValueError:
        objects = _embedded_objects(text)
        if not objects:
            raise
        answer = objects[-1]
    if type(answer) is not dict:
        raise ValueError("expected one JSON object")
    json.dumps(answer, allow_nan=False)
    return answer


class ModelClient:
    def __init__(self, backend="codex", model="gpt-6-luna", reasoning_effort="low", endpoint=None,
                 timeout=24, *, api_key=None, codex_executable="codex", circuit=None):
        if backend not in {"codex", "openai"}:
            raise ValueError(f"unknown model backend {backend!r}")
        if backend == "openai" and not endpoint:
            raise ValueError("OpenAI-compatible API requires an endpoint")
        self.backend = backend
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.endpoint = endpoint
        self.timeout = timeout
        self.api_key = api_key
        self.codex_executable = codex_executable
        self.circuit = circuit if circuit is not None else ModelCircuit()

    def call(self, prompt, payload, archive):
        report = dict.fromkeys(USAGE_FIELDS)
        report.update(backend=self.backend, model=self.model, reasoning_effort=self.reasoning_effort,
                      prompt=prompt, input=payload, decoded=None, error=None, failure_kind=None,
                      reported_cost=None, cost_currency=None)
        started = time.monotonic()
        if self.circuit.open:
            report.update(error=self.circuit.reason, failure_kind="circuit_open", invoked=False)
        else:
            report["invoked"] = True
            try:
                invoke = self._codex if self.backend == "codex" else self._http
                report["decoded"] = _decode(invoke(prompt, payload, report))
            except BackendFailure as error:
                report.update(error=str(error), failure_kind=error.kind)
            except (ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
                report.update(error=f"{type(error).__name__}: {error}", failure_kind="invalid_response")
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                report.update(error=f"{type(error).__name__}: {error}", failure_kind="backend_error")
            if report["failure_kind"] in PROVIDER_FAILURES:
                report["retry_hint"] = _retry_hint(report)
            self.circuit.record(report["failure_kind"], report.get("retry_hint"))
        report["circuit"] = {"open": self.circuit.open, "reason": self.circuit.reason,
                             "failures": self.circuit.failures}
        report["seconds"] = round(time.monotonic() - started, 3)
        report["usage_missing"] = [field for field in USAGE_FIELDS if report[field] is None]
        write_json(archive, report)
        return report

    def _codex(self, prompt, payload, report):
        request = prompt + "\n\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        report["request"] = request
        command = [
            self.codex_executable, "exec", "-m", self.model,
            "-c", f"model_reasoning_effort={json.dumps(self.reasoning_effort)}",
            "-c", 'approval_policy="never"', "-c", "project_doc_max_bytes=0",
            "-c", 'web_search="disabled"', "--ignore-user-config", "--ephemeral",
            "--skip-git-repo-check", "--sandbox", "read-only", "--disable", "shell_tool",
            "--disable", "unified_exec", "--disable", "hooks", "--disable", "enable_mcp_apps",
            "--json", "-",
        ]
        with TemporaryDirectory(prefix="rsi-model-") as empty:
            try:
                result = subprocess.run(command, input=request, encoding="utf-8", errors="replace",
                                        capture_output=True, timeout=self.timeout, cwd=empty,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired as error:
                stdout, stderr = _output_text(error.stdout), _output_text(error.stderr)
                report.update(stdout=stdout, stderr=stderr, returncode=None)
                kind = _codex_failure(_codex_events(stdout), stdout, stderr)
                raise BackendFailure(f"Codex turn timed out after {self.timeout}s", kind or "timeout") from error
        report.update(stdout=result.stdout, stderr=result.stderr, returncode=result.returncode)
        events = _codex_events(result.stdout)
        completed = False
        tool_event = False
        messages = []
        for event in events:
            event_type = event.get("type", "")
            if event_type == "turn.completed":
                completed = True
                usage = event.get("usage") or {}
                report.update({field: _count(usage.get(field)) for field in USAGE_FIELDS})
            if event_type.startswith("item."):
                item = event.get("item") or {}
                if item.get("type") not in {"reasoning", "agent_message"}:
                    tool_event = True
                if event_type == "item.completed" and item.get("type") == "agent_message":
                    messages.append(item.get("text"))
        if result.returncode or not completed:
            kind = _codex_failure(events, result.stdout, result.stderr)
            raise BackendFailure(f"Codex turn did not complete (return code {result.returncode})", kind)
        if len(messages) != 1 or tool_event:
            raise ValueError("expected one Codex answer without tool use")
        report["answer"] = messages[0]
        return messages[0]

    def _http(self, prompt, payload, report):
        endpoint = self.endpoint.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        request = {
            "model": self.model,
            "messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "response_format": {"type": "json_object"},
        }
        if self.reasoning_effort != "omit":
            request["reasoning_effort"] = self.reasoning_effort
        report.update(endpoint=endpoint, request=request)
        headers = {"Content-Type": "application/json"}
        key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            with urlopen(Request(endpoint, json.dumps(request).encode("utf-8"), headers),
                         timeout=self.timeout) as response:
                report.update(http_status=response.status, response=response.read().decode("utf-8"))
        except HTTPError as error:
            report["http_status"] = error.code
            with error:
                try:
                    report["response"] = error.read().decode("utf-8", errors="replace")
                except OSError as read_error:
                    report["response_read_error"] = str(read_error)
            kind = _provider_failure([report.get("response", "")], status=error.code)
            raise BackendFailure(f"HTTP model API returned {error.code}", kind) from error
        except (URLError, TimeoutError) as error:
            raise BackendFailure(f"HTTP model API unreachable: {error}", "transient") from error
        decoded = json.loads(report["response"])
        usage = decoded.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        report.update(input_tokens=_count(usage.get("prompt_tokens")),
                      output_tokens=_count(usage.get("completion_tokens")),
                      cached_input_tokens=_count(details.get("cached_tokens")))
        cost = usage.get("cost")
        if type(cost) in {int, float} and math.isfinite(cost) and cost >= 0:
            report["reported_cost"] = cost
            currency = usage.get("cost_currency")
            report["cost_currency"] = currency if isinstance(currency, str) else None
        if decoded.get("error"):
            kind = _provider_failure([json.dumps(decoded["error"], ensure_ascii=False)])
            raise BackendFailure("HTTP model API returned an error in the response body", kind)
        message = decoded["choices"][0]["message"]
        if message.get("tool_calls") or message.get("function_call"):
            raise ValueError("expected an HTTP model answer without tool use")
        report["answer"] = message["content"]
        return message["content"]
