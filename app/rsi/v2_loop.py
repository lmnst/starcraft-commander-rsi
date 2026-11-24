"""RSI v2 self-evolution engine: whitebox C++ MacroBot evolution + Champion/Challenger L0 selection.

All scoring, FPS checks, and Promote/Reject decisions strictly use L0 telemetry (scoring.py).
MacroBot's self-reported astra-trace.jsonl is diagnostic only for post-round review.
"""

from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import build_config, cpp_gate, evolution, ledger as ledger_mod, sandbox, scoring, situation, validator
from .adapter import NativeV17Adapter
from .agentic_review import (
    ANALYSIS_TIMEOUT_SECONDS,
    BUILD_LOG,
    BUILD_LOG_ROWS,
    FRAME_FIELDS,
    FRAME_ROWS,
    RESULT_FIELDS,
    SUBMIT_REJECTIONS,
    TIMELINE_FIELDS,
    TIMELINE_ROWS,
    TOOL_OUTPUT_CHARS,
    TRANSIENT_WAITS,
    AgentFailure,
    _build_log,
    _cache_breakpoints,
    _sample,
    _text,
)
from .model_client import PROVIDER_FAILURES, TRANSIENT_FAILURES, USAGE_FIELDS, ModelCircuit, _provider_failure, write_json
from .runner import LiveRunner
from .seed_v1 import SEED_SOURCES, VERSION_FILES


V2_VERSION_FILES = (*VERSION_FILES, cpp_gate.CPP_FILENAME)
V2_SOURCE_LIMITS = {
    **validator.SOURCE_LIMITS,
    "strategy.md": 6000,
    cpp_gate.CPP_FILENAME: cpp_gate.MAX_CPP_CHARS,
}
V2_MAX_EDITED_FILES = 3
V2_MAX_CONFIG_KEYS = 8
V2_MAX_TURNS = 28
V2_WALL_SECONDS = 1200
V2_FRAME_STALL_SECONDS = 240
V2_DISTILLATION_TYPES = frozenset({
    "none", "strategy_only", "add_observation", "add_tool",
    "distill_rule", "bugfix_code", "tune_macro", "evolve_cpp", "combined",
})
ENGINE_REFERENCE_FILES = frozenset({
    "CommanderDefence.h", "CommanderBases.h", "CommanderOrders.h",
    "CommanderRules.h", "CommanderTargeting.h", "CommanderStandby.h",
    "CommanderRaid.h", "CommanderClear.h", "CommanderDetection.h",
    "CommanderRendezvous.h",
})

V2_REVIEW_PROMPT = """You are the RSI v2 Bot Architect evolving a StarCraft: Brood War Protoss agent.
Your mission is COMPOUNDING SELF-IMPROVEMENT: build durable, composable capabilities in C++ (MacroBot.cpp)
and Python (observer.py, tools.py, rules.py, strategy.md, build_config.json) so each promoted Champion raises
the bot's true playing strength ceiling across the fixed opponent suite.

=== YOUR EVOLVABLE WORKSPACE (1 to 3 files per round) ===
1. MacroBot.cpp (C++17 BWAPI 4.4.0 + BWEM, <= 60,000 chars, <= 150 changed lines per Challenger):
   - Full read/write access to production priority (nextBuilding), building placement (build/findSite/expansion),
     worker defense, economy/gas management, unit production/upgrades (production), scouting, AND combat micro (combat)!
   - Prefer direct C++ capability evolution when the root cause is build order, scout-reactive tech switching
     (e.g. detecting early Citadel/Forge/cannon rush and adapting dynamically instead of blind delays),
     army composition, macro/expansion logic, or unit micro (focus fire, Dragoon kiting, choke/ramp positioning,
     Zealot/Dragoon coordination, Observer positioning).
   - Keep MACRO_PARAMS(X), loadConfig(), config_loaded logging, gameInit, and newAIModule intact.
   - Anti-cheat gate: strictly own-side legal vision; no enableFlag/CompleteMapInformation/setRevealAll/setVision/
     setAlliance/leaveGame (outside loadConfig guard)/system/Win32/raw file paths/##/#pragma/\\u.
   - Use read_policy("MacroBot.cpp") first to get the function table of contents with line numbers, then
     read_policy("MacroBot.cpp", start_line=..., end_line=...) and edit_policy("MacroBot.cpp", old_text=..., new_text=...).
   - check_candidate() compiles MacroBot.cpp with MSVC (15-30s) and returns exact compiler errors if any so you can fix them!
2. build_config.json (37 integer thresholds, up to 8 changed keys per round; may be combined with other files).
3. strategy.md (<= 6,000 chars), rules.py, tools.py, observer.py (<= 12,000 chars each).
   - situation.meta.opponent gives the opponent name to rules.py and observer.py; situation.enemy.recent_peak_army
     gives a 3600-frame rolling enemy army peak (unlike permanent peak_army).
   - You also have read_engine_file() to inspect CommanderLayer's C++ headers (CommanderDefence.h, CommanderBases.h, etc.).

=== HOW L0 JUDGES YOUR CHALLENGER (CHAMPION vs CHALLENGER SELECTION) ===
- Your proposal becomes a Challenger tested on the SAME fixed core opponent suite as the Champion.
- ALL scoring and promotion decisions use STRICTLY L0 telemetry (commander-layer.jsonl, ResultsWatcher, L0 cutoff).
  MacroBot's self-reported astra-trace.jsonl (build_log) is ONLY diagnostic for you and NEVER enters scoring.
- Your Challenger is REJECTED automatically if:
  * Any match crashes / fails config_loaded / stalls (>240s no frames);
  * Mean FPS drops below 70% of Champion's FPS (keep onFrame() fast!);
  * Anti-regression guard triggers: Champion has >= 2 games and >= 50% win rate on a core opponent and Challenger loses it
    without strictly raising overall win rate.
- Your Challenger is PROMOTED if:
  * Net win rate across the core suite strictly improves, OR
  * Tied win rate and mean L0 anti-turtling match_score improves by >= +5.0 pts with no single opponent dropping > 12 pts.
  * Note: inconclusive timeouts get 0 bonus (and do NOT count as a tie-break win), survival score is capped at frame 14400, and high lock_defense fraction is penalized;
    the main score rewards real L0 army strength, combat engagement/exchange ratio, forward map presence, and tech/economy milestones.

=== INVESTIGATION DISCIPLINE ===
- Inspect the cross-version ledger carefully so you NEVER repeat failed experiments or regress earlier gains.
- When a previous Challenger was Rejected, inspect BOTH the Champion baseline matches and the Rejected Challenger matches.
- Run check_candidate() before submit() and verify compilation + shadow replay both pass.
- Finish by calling submit() with evidence as 1 to 5 specific frame/match reference strings (e.g. "match-4@7200").
- Do NOT choose keep or revert while Champion has not won 100% of core opponents; always propose a well-grounded Challenger.

build_config.json schema:
""" + build_config.describe()

V2_TOOLS = [
    {"name": "list_evidence",
     "description": "List all mounted matches (Champion baseline and Challenger trial), their roles, L0 scores, and trace sizes.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "get_frames",
     "description": f"L0 observation rows of one match between two frames, at most {FRAME_ROWS} evenly spaced rows.",
     "parameters": {"type": "object", "required": ["match_number", "start_frame", "end_frame"], "properties": {
         "match_number": {"type": "integer"}, "start_frame": {"type": "integer"}, "end_frame": {"type": "integer"},
         "fields": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "get_timeline",
     "description": f"L0 decision timeline rows of one match between two frames, at most {TIMELINE_ROWS} rows.",
     "parameters": {"type": "object", "required": ["match_number", "start_frame", "end_frame"], "properties": {
         "match_number": {"type": "integer"}, "start_frame": {"type": "integer"}, "end_frame": {"type": "integer"}}}},
    {"name": "get_build_log",
     "description": f"Self-reported MacroBot trace rows (diagnostic only, NOT used for scoring), at most {BUILD_LOG_ROWS} rows.",
     "parameters": {"type": "object", "required": ["match_number", "start_frame", "end_frame"], "properties": {
         "match_number": {"type": "integer"}, "start_frame": {"type": "integer"}, "end_frame": {"type": "integer"},
         "events": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "run_analysis",
     "description": "Run restricted Python analyze(evidence) over mounted matches and ledger. Imports: math, collections, statistics, itertools, functools, copy, typing, dataclasses.",
     "parameters": {"type": "object", "required": ["code"], "properties": {"code": {"type": "string"}}}},
    {"name": "read_policy",
     "description": "Read a policy file (staged if modified, else Champion). For MacroBot.cpp without start_line/end_line, returns function TOC with line numbers; pass start_line and end_line for numbered source lines.",
     "parameters": {"type": "object", "required": ["file"], "properties": {
         "file": {"type": "string", "enum": list(V2_VERSION_FILES)},
         "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}}},
    {"name": "edit_policy",
     "description": "Replace one unique exact substring old_text with new_text in a whitelist file (at most 3 files staged; MacroBot.cpp <= 150 changed lines total).",
     "parameters": {"type": "object", "required": ["file", "old_text", "new_text"], "properties": {
         "file": {"type": "string", "enum": list(V2_VERSION_FILES)},
         "old_text": {"type": "string"}, "new_text": {"type": "string"}}}},
    {"name": "write_policy",
     "description": "Stage the complete text of one whitelist file (at most 3 files staged). Prefer edit_policy for MacroBot.cpp.",
     "parameters": {"type": "object", "required": ["file", "content"], "properties": {
         "file": {"type": "string", "enum": list(V2_VERSION_FILES)},
         "content": {"type": "string"}}}},
    {"name": "discard_policy", "description": "Drop a staged file.",
     "parameters": {"type": "object", "required": ["file"], "properties": {"file": {"type": "string"}}}},
    {"name": "read_engine_file",
     "description": "Read-only access to CommanderLayer C++ reference headers (e.g. CommanderDefence.h, CommanderBases.h).",
     "parameters": {"type": "object", "required": ["file"], "properties": {
         "file": {"type": "string", "enum": sorted(ENGINE_REFERENCE_FILES)},
         "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}}},
    {"name": "check_candidate",
     "description": "Run L0 security, diff-size, MSVC C++ compilation, build_config, and Python shadow-replay gates on staged files.",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "submit",
     "description": "Submit the Challenger proposal after passing check_candidate(). Staged files become the edits.",
     "parameters": {"type": "object", "required": [
         "prediction_check", "evidence", "diagnosis", "hypothesis", "expectation", "decision", "distillation_type"],
         "properties": {
             "prediction_check": {"type": "string"},
             "evidence": {"type": "array", "items": {"type": "string"},
                          "description": "1 to 5 specific match/frame references like 'match-3@7200'"},
             "diagnosis": {"type": "string"}, "hypothesis": {"type": "string"}, "expectation": {"type": "string"},
             "decision": {"type": "string", "enum": ["modify", "keep"]},
             "distillation_type": {"type": "string", "enum": sorted(V2_DISTILLATION_TYPES)}}}},
]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_v2_sources(path: Path) -> dict[str, str]:
    return {name: (path / name).read_text(encoding="utf-8") for name in V2_VERSION_FILES}


def _next_candidate_id(batch: Path) -> str:
    cands = batch / "candidates"
    if not cands.is_dir():
        return "c1"
    nums = [int(p.name[1:]) for p in cands.iterdir()
            if p.is_dir() and p.name.startswith("c") and p.name[1:].isdigit()]
    return f"c{max(nums, default=0) + 1}"


def initialize_v2_batch(batch: Path, repo_root: Path, parameters: dict | None = None, *,
                        compile_fn=None) -> Path:
    """Create a self-contained v2 batch with cleaned v1/MacroBot.cpp and compiled v1/MacroBot.dll."""
    batch = Path(batch)
    repo_root = Path(repo_root).resolve()
    if batch.exists():
        raise FileExistsError(f"batch already exists: {batch}")
    base_cpp = (repo_root / "app/MacroBot.cpp").read_text(encoding="utf-8")
    seed_cpp = cpp_gate.seed_v2_macrobot(base_cpp)
    compiler = compile_fn or cpp_gate.compile_macrobot

    batch.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".batch-v2-", dir=batch.parent) as temporary:
        staging = Path(temporary) / batch.name
        v1 = staging / "versions/v1"
        v1.mkdir(parents=True, exist_ok=True)
        for name, text in SEED_SOURCES.items():
            (v1 / name).write_text(text, encoding="utf-8", newline="\n")
        (v1 / cpp_gate.CPP_FILENAME).write_text(seed_cpp, encoding="utf-8", newline="\n")
        build_info = compiler(seed_cpp, v1 / cpp_gate.DLL_FILENAME, repo_root=repo_root)
        write_json(v1 / "manifest.json", {
            "version": "v1", "parent": None, "created_after_match": 0,
            "summary": "v2 seed (fixed proactive cannon priority + expansion cooldown)",
            "dll_sha256": build_info["dll_sha256"],
            "macrobot_source_sha256": build_info["source_sha256"],
            "compile_seconds": build_info.get("seconds"),
        })
        pointer = {"version": "v1", "parent": None, "path": "versions/v1"}
        write_json(staging / "active.json", pointer)
        write_json(staging / "batch.json", {
            "batch_id": batch.name,
            "evolution_mode": "v2",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "parameters": parameters or {},
            "matches": [],
            "rounds": [],
            "active": pointer,
        })
        core = (parameters or {}).get("core_opponents") or []
        led = ledger_mod.load_ledger(staging, core_opponents=core)
        ledger_mod.save_ledger(staging, led)
        staging.rename(batch)
    return batch / "versions/v1"


class V2ReviewWorkspace:
    """Mounted evidence (Champion baseline + Challenger trial) and staged files with live MSVC compile."""

    def __init__(self, batch: Path, repo_root: Path, champion_sources: dict[str, str],
                 mounted_matches: list[dict], compact_ledger: dict, *,
                 compile_fn=None, scratch_dir: Path | None = None):
        self.batch = Path(batch)
        self.repo_root = Path(repo_root).resolve()
        self.sources = dict(champion_sources)
        self.staged: dict[str, str] = {}
        self.compact_ledger = compact_ledger
        self.compile_fn = compile_fn or cpp_gate.compile_macrobot
        self.scratch_dir = Path(scratch_dir) if scratch_dir else self.batch / ".v2-review-scratch"
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        self._compiled_cache: dict | None = None  # {"source_sha256", "dll_path", "info"}
        self.matches: dict[int, dict] = {}
        for item in mounted_matches:
            number = item["match_number"]
            run = self.batch / f"match-{number}"
            timeline = evolution.read_json(run / "timeline.json", [])
            receipts = evolution.read_json(run / "native-receipts.json")
            obs_raw = evolution.read_json(run / "observations.json", [])
            self.matches[number] = {
                "role": item.get("role", "current_round"),
                "result": {name: item[name] for name in (*RESULT_FIELDS, "l0_score", "l0_fps", "l0_outcome") if name in item},
                "observations": situation.attach(obs_raw) if obs_raw else [],
                "timeline": timeline,
                "receipts": receipts if receipts is not None else [r["receipt"] for r in timeline if r.get("receipt")],
                "runtime_errors": evolution.read_json(run / "runtime-errors.json", []),
                "self_reported_build_log": _build_log(run / BUILD_LOG),
            }

    def match(self, number: int) -> dict:
        if number not in self.matches:
            raise ValueError(f"match {number} is not mounted; use one of {sorted(self.matches)}")
        return self.matches[number]

    def list_evidence(self) -> dict:
        return {str(num): {
            "role": data["role"],
            "result": data["result"],
            "observations": len(data["observations"]),
            "first_frame": data["observations"][0]["frame"] if data["observations"] else None,
            "last_frame": data["observations"][-1]["frame"] if data["observations"] else None,
            "timeline_rows": len(data["timeline"]),
            "runtime_errors": len(data["runtime_errors"]),
            "self_reported_build_log_rows": len(data["self_reported_build_log"]),
        } for num, data in self.matches.items()}

    def get_frames(self, match_number: int, start_frame: int, end_frame: int, fields=None) -> dict:
        fields = tuple(fields or FRAME_FIELDS)
        rows = [r for r in self.match(match_number)["observations"] if start_frame <= r["frame"] <= end_frame]
        return {"rows_in_range": len(rows),
                "rows": [{name: r[name] for name in ("frame", *fields) if name in r}
                         for r in _sample(rows, FRAME_ROWS)]}

    def get_timeline(self, match_number: int, start_frame: int, end_frame: int) -> dict:
        rows = [r for r in self.match(match_number)["timeline"]
                if type(r.get("frame")) is int and start_frame <= r["frame"] <= end_frame]
        projected = []
        for r in rows[:TIMELINE_ROWS]:
            item = {name: r[name] for name in TIMELINE_FIELDS if name in r}
            if isinstance(r.get("receipt"), dict):
                item["receipt"] = {name: r["receipt"].get(name) for name in ("frame", "key", "status", "stance")}
            projected.append(item)
        return {"rows_in_range": len(rows), "rows": projected}

    def get_build_log(self, match_number: int, start_frame: int, end_frame: int, events=None) -> dict:
        rows = [r for r in self.match(match_number)["self_reported_build_log"]
                if start_frame <= r["frame"] <= end_frame and (not events or r.get("event") in events)]
        return {
            "source_note": "self_reported_diagnostic_only_not_used_for_L0_scoring",
            "rows_in_range": len(rows),
            "rows": rows[:BUILD_LOG_ROWS],
        }

    def read_engine_file(self, file: str, start_line: int = 1, end_line: int = 120) -> dict:
        if file not in ENGINE_REFERENCE_FILES:
            raise ValueError(f"unknown engine reference file {file!r}; allowed: {sorted(ENGINE_REFERENCE_FILES)}")
        path = self.repo_root / "app" / file
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        s = max(1, int(start_line))
        e = min(len(lines), max(s, int(end_line)), s + 219)
        numbered = "\n".join(f"{idx}: {lines[idx - 1]}" for idx in range(s, e + 1))
        return {"file": file, "total_lines": len(lines), "start_line": s, "end_line": e, "content": numbered}

    def read_policy(self, file: str, start_line: int | None = None, end_line: int | None = None) -> dict:
        if file not in self.sources:
            raise ValueError(f"unknown policy file {file!r}")
        text = self.staged.get(file, self.sources[file])
        if file == cpp_gate.CPP_FILENAME and start_line is None and end_line is None:
            return {"staged": file in self.staged, **cpp_gate.outline(text)}
        if start_line is not None or end_line is not None:
            lines = text.splitlines()
            s = max(1, int(start_line or 1))
            e = min(len(lines), max(s, int(end_line or (s + 140))), s + 219)
            numbered = "\n".join(f"{idx}: {lines[idx - 1]}" for idx in range(s, e + 1))
            return {"file": file, "staged": file in self.staged, "total_lines": len(lines),
                    "start_line": s, "end_line": e, "content": numbered}
        return {"file": file, "staged": file in self.staged, "content": text}

    def write_policy(self, file: str, content: str) -> dict:
        if file not in self.sources:
            raise ValueError(f"unknown policy file {file!r}")
        if file not in self.staged and len(self.staged) >= V2_MAX_EDITED_FILES:
            raise ValueError(f"at most {V2_MAX_EDITED_FILES} files may change; discard one first")
        limit = V2_SOURCE_LIMITS[file]
        if type(content) is not str or len(content) > limit:
            raise ValueError(f"{file} must be text of at most {limit} characters")
        self.staged[file] = content
        res = {"staged": sorted(self.staged), "characters": len(content)}
        if file == cpp_gate.CPP_FILENAME:
            res["changed_lines"] = cpp_gate.count_diff_lines(self.sources[file], content)
            res["max_changed_lines"] = cpp_gate.MAX_CPP_DIFF_LINES
        return res

    def edit_policy(self, file: str, old_text: str, new_text: str) -> dict:
        if file not in self.sources:
            raise ValueError(f"unknown policy file {file!r}")
        if file not in self.staged and len(self.staged) >= V2_MAX_EDITED_FILES:
            raise ValueError(f"at most {V2_MAX_EDITED_FILES} files may change; discard one first")
        if type(old_text) is not str or not old_text or type(new_text) is not str:
            raise ValueError("old_text must be nonempty text and new_text must be text")
        current = self.staged.get(file, self.sources[file])
        hits = current.count(old_text)
        if hits == 0:
            raise ValueError(f"{file}: old_text not found; read exact lines with read_policy first")
        if hits > 1:
            raise ValueError(f"{file}: old_text matched {hits} times; include more surrounding context")
        updated = current.replace(old_text, new_text, 1)
        limit = V2_SOURCE_LIMITS[file]
        if len(updated) > limit:
            raise ValueError(f"{file} exceeds limit of {limit} characters")
        self.staged[file] = updated
        res = {
            "staged": sorted(self.staged),
            "characters": len(updated),
            "changed_lines": cpp_gate.count_diff_lines(self.sources[file], updated),
        }
        if file == cpp_gate.CPP_FILENAME:
            res["max_changed_lines"] = cpp_gate.MAX_CPP_DIFF_LINES
        return res

    def discard_policy(self, file: str) -> dict:
        self.staged.pop(file, None)
        return {"staged": sorted(self.staged)}

    def run_analysis(self, code: str):
        evidence = {
            "matches": {str(num): data for num, data in self.matches.items()},
            "ledger": self.compact_ledger,
        }
        request = json.dumps({"source": code, "evidence": evidence}, ensure_ascii=False)
        worker = Path(__file__).with_name("agentic_review.py").resolve()
        try:
            result = subprocess.run(
                [sys.executable, "-I", "-B", str(worker), "--analysis-worker"],
                input=request, encoding="utf-8", capture_output=True,
                timeout=ANALYSIS_TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired:
            raise ValueError(f"analysis exceeded {ANALYSIS_TIMEOUT_SECONDS}s") from None
        if result.returncode:
            raise ValueError(f"analysis worker exited with code {result.returncode}: {result.stderr.strip()[-800:]}")
        reply = json.loads(result.stdout)
        if "error" in reply:
            raise ValueError(reply["error"])
        return reply["result"]

    def check_candidate(self) -> dict:
        if not self.staged:
            raise ValueError("no staged files")
        if unchanged := [name for name, text in self.staged.items() if text == self.sources[name]]:
            raise ValueError(f"staged files are unchanged: {', '.join(unchanged)}")
        if len(self.staged) > V2_MAX_EDITED_FILES:
            raise ValueError(f"at most {V2_MAX_EDITED_FILES} files may change")

        checked: dict = {"passed": True, "staged": sorted(self.staged)}
        for name, text in self.staged.items():
            if name.endswith(".py"):
                sandbox.check_source(name, text)
            elif name == build_config.FILENAME:
                checked["build_config_changes"] = build_config.check_edit(
                    self.sources[name], text, max_changed_keys=V2_MAX_CONFIG_KEYS
                )
            elif name == cpp_gate.CPP_FILENAME:
                cpp_info = cpp_gate.check_source(
                    text, parent_source=self.sources[name], max_diff_lines=cpp_gate.MAX_CPP_DIFF_LINES
                )
                src_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if self._compiled_cache and self._compiled_cache["source_sha256"] == src_hash and self._compiled_cache["dll_path"].is_file():
                    comp = self._compiled_cache["info"]
                else:
                    out_dll = self.scratch_dir / f"candidate-{src_hash[:10]}.dll"
                    comp = self.compile_fn(text, out_dll, repo_root=self.repo_root)
                    self._compiled_cache = {"source_sha256": src_hash, "dll_path": out_dll, "info": comp}
                checked["cpp_check"] = {**cpp_info, "dll_sha256": comp["dll_sha256"],
                                        "compile_seconds": comp.get("seconds"), "exports": comp.get("exports")}

        # Python shadow replay: use any mounted matches with observations (fallback to Champion baseline if Challenger crashed).
        py_before = {k: self.sources[k] for k in VERSION_FILES}
        py_after = {k: self.staged.get(k, self.sources[k]) for k in VERSION_FILES}
        report = {}
        for num, data in self.matches.items():
            if not data["observations"]:
                continue
            compared = validator._compare_versions(py_before, py_after, data["observations"], validator.SHADOW_MAX_FRAMES)
            cand = compared["versions"]["candidate"]
            report[str(num)] = {
                "role": data["role"],
                "candidate_wakes": cand["wake_llm_count"],
                "candidate_reflexes": cand["reflex_trigger_count"],
                "wake_frame_diffs": (compared["wake_frame_diffs"] or [])[:8],
                "reflex_frame_diffs": (compared["reflex_frame_diffs"] or [])[:8],
                "tool_smoke": cand["tool_smoke"],
            }
        if not report and any(name.endswith(".py") for name in self.staged):
            loaded = sandbox.load_sources(py_after)
            for module, names in ((loaded.observer, ("summarize", "should_wake_llm")),
                                  (loaded.rules, ("decide_reflex",))):
                for fn_name in names:
                    if not callable(getattr(module, fn_name, None)):
                        raise ValueError(f"{fn_name} export must be callable")
            validator.check_tools(loaded.tools.TOOLS)
        checked["shadow_replay"] = report
        return checked


def _validate_v2_submission(proposal: dict, workspace: V2ReviewWorkspace, *, allow_keep: bool) -> str | None:
    required = ("prediction_check", "evidence", "diagnosis", "hypothesis", "expectation", "decision", "distillation_type")
    for field in ("prediction_check", "diagnosis", "hypothesis", "expectation"):
        if type(proposal.get(field)) is not str or not proposal[field].strip():
            return f"{field} must be nonempty text"
    decision = proposal.get("decision")
    if decision not in ("modify", "keep"):
        return "decision must be modify or keep"
    if decision == "keep" and not allow_keep:
        return "keep is disabled while Champion win rate < 100%; propose a concrete Challenger modification"
    dist = proposal.get("distillation_type")
    if dist not in V2_DISTILLATION_TYPES:
        return f"unknown distillation_type {dist!r}"
    if decision == "keep" and (workspace.staged or dist != "none"):
        return "keep requires no staged files and distillation_type none"
    evidence = proposal.get("evidence")
    if (type(evidence) is not list or not (0 if decision == "keep" else 1) <= len(evidence) <= 5
            or any(type(item) is not str or not item.strip() for item in evidence)):
        return "evidence must be a list of 1 to 5 nonempty frame/match reference strings (e.g. 'match-3@7200')"
    if decision == "modify":
        try:
            workspace.check_candidate()
        except Exception as error:
            return f"check_candidate failed: {type(error).__name__}: {error}"
    return None


class V2AgenticReviewClient:
    """Bounded tool-calling reviewer for RSI v2 with MSVC compile feedback and thinking traces."""

    backend = "agentic_v2"

    def __init__(self, model: str, reasoning_effort: str, endpoint: str, timeout: int, *,
                 api_key: str, circuit: ModelCircuit | None = None,
                 max_turns: int = V2_MAX_TURNS, wall_seconds: int = V2_WALL_SECONDS, sleep=time.sleep):
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

    def review(self, overview: dict, workspace: V2ReviewWorkspace, archive: Path, *,
               allow_keep: bool = False) -> dict:
        archive = Path(archive)
        trace_path = archive.with_name(archive.name.replace("review-call", "review-trace")).with_suffix(".jsonl")
        report = dict.fromkeys(USAGE_FIELDS, 0)
        report.update(backend=self.backend, model=self.model, reasoning_effort=self.reasoning_effort,
                      decoded=None, error=None, failure_kind=None, invoked=True, turns=0,
                      tool_calls={}, usage_missing_calls=0, trace=trace_path.name)
        started = time.monotonic()
        if self.circuit.open:
            report.update(error=self.circuit.reason, failure_kind="circuit_open", invoked=False)
        else:
            trace_path.unlink(missing_ok=True)
            try:
                report["decoded"] = self._loop(overview, workspace, report, trace_path, started, allow_keep)
            except AgentFailure as error:
                report.update(error=str(error), failure_kind=error.kind)
            except Exception as error:
                report.update(error=f"{type(error).__name__}: {error}", failure_kind="backend_error")
        report["seconds"] = round(time.monotonic() - started, 3)
        report["circuit"] = {"open": self.circuit.open, "reason": self.circuit.reason, "failures": self.circuit.failures}
        write_json(archive, report)
        return report

    def _loop(self, overview, workspace, report, trace_path, started, allow_keep):
        messages = [
            {"role": "system", "content": V2_REVIEW_PROMPT},
            {"role": "user", "content": json.dumps(overview, ensure_ascii=False)},
        ]
        rejections = 0
        warned_wall = False
        for turn in range(1, self.max_turns + 3):
            elapsed_wall = time.monotonic() - started
            if elapsed_wall > self.wall_seconds:
                raise AgentFailure(f"v2 agentic review exceeded {self.wall_seconds}s", "invalid_response")
            if not warned_wall and elapsed_wall >= self.wall_seconds * 0.80:
                warned_wall = True
                messages.append({"role": "user", "content": "Time budget is 80% used. Finalize edits, run check_candidate, and call submit soon."})
            if turn == self.max_turns + 1:
                messages.append({"role": "user", "content": "Tool budget spent. Call check_candidate if needed and submit now."})
            message = self._chat(messages, report)
            report["turns"] = turn
            calls = message.get("tool_calls") or []
            messages.append({k: message[k] for k in ("role", "content", "tool_calls") if k in message})
            if not calls:
                messages.append({"role": "user", "content": "Continue using tools and finish by calling submit."})
                self._trace(trace_path, turn, message, [])
                continue
            results = []
            for call in calls:
                name = call["function"]["name"]
                report["tool_calls"][name] = report["tool_calls"].get(name, 0) + 1
                try:
                    arguments = json.loads(call["function"].get("arguments") or "{}")
                    if name == "submit":
                        proposal = {k: arguments.get(k) for k in (
                            "prediction_check", "evidence", "diagnosis", "hypothesis",
                            "expectation", "decision", "distillation_type",
                        )}
                        problem = _validate_v2_submission(proposal, workspace, allow_keep=allow_keep)
                        if problem is None:
                            proposal["edits"] = dict(workspace.staged) if proposal["decision"] == "modify" else {}
                            self._trace(trace_path, turn, message, [{"tool": name, "accepted": True}])
                            return proposal
                        rejections += 1
                        if rejections >= SUBMIT_REJECTIONS:
                            proposal["edits"] = dict(workspace.staged) if proposal.get("decision") == "modify" else {}
                            self._trace(trace_path, turn, message, [{"tool": name, "accepted": False, "error": problem}])
                            raise AgentFailure(f"submit rejected {rejections} times: {problem}", "invalid_response")
                        output = {"error": problem, "rejections_left": SUBMIT_REJECTIONS - rejections}
                    elif name in {"list_evidence", "get_frames", "get_timeline", "get_build_log", "run_analysis",
                                  "read_policy", "edit_policy", "write_policy", "discard_policy",
                                  "read_engine_file", "check_candidate"}:
                        output = getattr(workspace, name)(**arguments)
                    else:
                        output = {"error": f"unknown tool {name!r}"}
                except AgentFailure:
                    raise
                except Exception as error:
                    output = {"error": f"{type(error).__name__}: {error}"}
                text = _text(output)
                results.append({"tool": name, "arguments": call["function"].get("arguments"), "output": text[:2000]})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": text})
            self._trace(trace_path, turn, message, results)
        raise AgentFailure("v2 agentic review ended without an accepted submit", "invalid_response")

    def _chat(self, messages, report):
        body = {"model": self.model, "messages": _cache_breakpoints(messages),
                "tools": [{"type": "function", "function": tool} for tool in V2_TOOLS]}
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
            except (URLError, TimeoutError, OSError, http.client.HTTPException, json.JSONDecodeError) as error:
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
        thinking = message.get("reasoning_content")
        if thinking is None and isinstance(message.get("provider_specific_fields"), dict):
            thinking = message["provider_specific_fields"].get("thinking_blocks")
        row = {"turn": turn, "content": message.get("content"),
               "tool_calls": [{"name": call["function"]["name"], "arguments": call["function"].get("arguments")}
                              for call in message.get("tool_calls") or []],
               "results": results}
        if thinking:
            row["thinking"] = thinking
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def stage_v2_candidate(batch: Path, repo_root: Path, champion_id: str, proposal: dict,
                       workspace: V2ReviewWorkspace, review_dir: Path, *,
                       compile_fn=None, review_usage=None) -> dict:
    """Validate and stage a Challenger into candidates/c{K}/ without mutating active.json."""
    batch = Path(batch)
    champ_dir = batch / "versions" / champion_id
    before = _read_v2_sources(champ_dir)
    edits = proposal.get("edits") or {}
    if not 1 <= len(edits) <= V2_MAX_EDITED_FILES or not edits.keys() <= set(V2_VERSION_FILES):
        raise ValueError(f"edits must modify 1 to {V2_MAX_EDITED_FILES} whitelist files")
    after = {**before, **edits}

    changed_functions = {}
    diff = {}
    build_config_changes = None
    cpp_changed_lines = 0
    for name, text in edits.items():
        if text == before[name]:
            raise ValueError(f"unchanged file {name}")
        if len(text) > V2_SOURCE_LIMITS[name]:
            raise ValueError(f"{name} exceeds size limit")
        diff[name] = validator._source_diff(name, before[name], text)
        if name.endswith(".py"):
            sandbox.check_source(name, text)
            changed_functions[name] = validator._file_changes(before, after, {name})[name]["functions"]
        elif name == build_config.FILENAME:
            build_config_changes = build_config.check_edit(before[name], text, max_changed_keys=V2_MAX_CONFIG_KEYS)
        elif name == cpp_gate.CPP_FILENAME:
            info = cpp_gate.check_source(text, parent_source=before[name], max_diff_lines=cpp_gate.MAX_CPP_DIFF_LINES)
            cpp_changed_lines = info["changed_lines"]

    cand_id = _next_candidate_id(batch)
    cands_root = batch / "candidates"
    cands_root.mkdir(parents=True, exist_ok=True)
    dest = cands_root / cand_id
    compiler = compile_fn or workspace.compile_fn

    with TemporaryDirectory(prefix=".cand-", dir=cands_root) as tmp:
        staged = Path(tmp) / cand_id
        staged.mkdir()
        for name, text in after.items():
            (staged / name).write_text(text, encoding="utf-8", newline="\n")
        target_dll = staged / cpp_gate.DLL_FILENAME
        if cpp_gate.CPP_FILENAME in edits:
            src_hash = hashlib.sha256(after[cpp_gate.CPP_FILENAME].encode("utf-8")).hexdigest()
            if (workspace._compiled_cache and workspace._compiled_cache["source_sha256"] == src_hash
                    and workspace._compiled_cache["dll_path"].is_file()):
                shutil.copy2(workspace._compiled_cache["dll_path"], target_dll)
                dll_sha = _sha256_file(target_dll)
            else:
                comp = compiler(after[cpp_gate.CPP_FILENAME], target_dll, repo_root=repo_root)
                dll_sha = comp["dll_sha256"]
        else:
            shutil.copy2(champ_dir / cpp_gate.DLL_FILENAME, target_dll)
            dll_sha = _sha256_file(target_dll)

        manifest = {
            "candidate_id": cand_id,
            "parent": champion_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "changed_files": sorted(edits),
            "changed_functions": changed_functions,
            "build_config_changes": build_config_changes,
            "cpp_changed_lines": cpp_changed_lines,
            "dll_sha256": dll_sha,
            "macrobot_source_sha256": hashlib.sha256(after[cpp_gate.CPP_FILENAME].encode("utf-8")).hexdigest(),
            "diagnosis": proposal.get("diagnosis", ""),
            "hypothesis": proposal.get("hypothesis", ""),
            "expectation": proposal.get("expectation", ""),
        }
        write_json(staged / "manifest.json", manifest)
        staged.rename(dest)

    result = {
        **proposal,
        "validation": "staged_challenger",
        "from_version": champion_id,
        "challenger_id": cand_id,
        "challenger_path": f"candidates/{cand_id}",
        "diff": diff,
        "changed_functions": changed_functions,
        "build_config_changes": build_config_changes,
        "cpp_changed_lines": cpp_changed_lines,
        "review_usage": review_usage,
    }
    write_json(review_dir / "review.json", result)
    evolution._log_event(batch, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "challenger_staged",
        "parent": champion_id,
        "challenger_id": cand_id,
        "changed_files": sorted(edits),
        "changed_functions": changed_functions,
        "build_config_changes": build_config_changes,
        "cpp_changed_lines": cpp_changed_lines,
        "distillation_type": proposal.get("distillation_type"),
    })
    return result


def promote_v2_candidate(batch: Path, candidate_id: str, verdict: dict) -> str:
    """Atomically promote candidates/c{K}/ -> versions/v{N+1}/ and update active.json."""
    batch = Path(batch)
    cand_dir = batch / "candidates" / candidate_id
    manifest = evolution.read_json(cand_dir / "manifest.json")
    parent = manifest["parent"]
    new_version = validator._next_version(batch / "versions")
    with TemporaryDirectory(prefix=".promote-", dir=batch) as tmp:
        staged = Path(tmp) / new_version
        shutil.copytree(cand_dir, staged)
        write_json(staged / "manifest.json", {
            **manifest, "version": new_version, "promoted_from": candidate_id,
            "promoted_at": datetime.now(timezone.utc).isoformat(), "verdict": verdict,
        })
        pointer = Path(tmp) / "active.json"
        write_json(pointer, {"version": new_version, "parent": parent, "path": f"versions/{new_version}"})
        staged.rename(batch / "versions" / new_version)
        pointer.replace(batch / "active.json")
    evolution._log_event(batch, {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "champion_promoted",
        "parent": parent,
        "challenger_id": candidate_id,
        "version": new_version,
        "verdict": verdict,
    })
    return new_version


def _play_single_v2(args, batch: Path, version_dir: Path, version_label: str, model,
                    match_number: int, opponent: str, stop_event, *, play_fn=None) -> dict:
    """Play one match with pre-game LAN flake retry (once) and v2 frame-stall/strategy limits."""
    run = batch / f"match-{match_number}"
    for attempt in (1, 2):
        if attempt == 2 and run.exists():
            flake_backup = batch / f"match-{match_number}-flake-1"
            if flake_backup.exists():
                shutil.rmtree(flake_backup)
            run.rename(flake_backup)
        started = time.monotonic()
        build_cfg_text = (version_dir / build_config.FILENAME).read_text(encoding="utf-8")
        build_cfg_obj = build_config.parse(build_cfg_text)
        dll_path = version_dir / cpp_gate.DLL_FILENAME
        if play_fn is not None:
            result = play_fn(run, version_dir, version_label, opponent, match_number, build_cfg_text, dll_path)
        else:
            adapter = NativeV17Adapter(
                run, args.map, opponent, args.speed,
                match_timeout=args.match_timeout, hold_lock=False,
                build_config=build_cfg_text, delegate_dll=dll_path,
            )
            runner = LiveRunner(
                adapter, model, version_dir, run,
                max_frames=args.max_frames, stalemate_zero_frames=args.stalemate_zero_frames,
                stop_requested=stop_event.is_set,
                max_strategy_chars=V2_SOURCE_LIMITS["strategy.md"],
                stamp_opponent=True,
                build_config_summary={
                    k: build_cfg_obj[k] for k in (
                        "proactive_cannon_frame", "proactive_observer_frame", "cannons_per_base",
                        "expand2_frame", "expand2_army", "dragoons_per_zealot", "robo_frame",
                    )
                },
                frame_stall_seconds=V2_FRAME_STALL_SECONDS,
            )
            try:
                result = runner.run()
            except Exception as error:
                result = {
                    "state": "error", "winner": None, "natural_outcome": "unknown",
                    "frame": runner.frame, "seconds": round(time.monotonic() - started, 3),
                    "policy_version": version_label, "decision_sources": runner.decision_sources,
                    "usage_missing": runner.usage_missing, "tokens": runner.tokens,
                    "runtime_error_count": len(runner.runtime_errors),
                    "error": f"{type(error).__name__}: {error}",
                }
            result = {**result, "live_calls": runner.call_number}

        result = evolution.normalized_match({
            **result, "map": args.map, "opponent": opponent,
            "match_number": match_number, "policy_version": version_label,
        })
        filename = "match-error.json" if result["state"] == "error" else "match.json"
        write_json(run / filename, result)
        if attempt == 1 and scoring.is_pre_game_flake(result) and not stop_event.is_set() and not model.circuit.open:
            continue
        return result
    return result


def _play_wave_v2(args, batch: Path, jobs: list[tuple[Path, str, int, str]], model, stop_event, *, play_fn=None) -> list[dict]:
    if not jobs:
        return []
    with ThreadPoolExecutor(len(jobs), thread_name_prefix="rsi-v2-match") as pool:
        futures = [
            pool.submit(_play_single_v2, args, batch, vdir, vlabel, model, num, opp, stop_event, play_fn=play_fn)
            for vdir, vlabel, num, opp in jobs
        ]
        while wait(futures, timeout=0.5).not_done:
            pass
    return [f.result() for f in futures]


def _mounted_for_review(batch: Path, led: dict, round_matches: list[dict], champion_id: str) -> list[dict]:
    """Mount current round matches PLUS Champion's latest eval matches per opponent (eliminates blind spots)."""
    mounted = []
    seen = set()
    for m in round_matches:
        num = m["match_number"]
        seen.add(num)
        mounted.append({**m, "role": m.get("_eval_role", "current_round")})
    champ_entry = led["versions"].get(champion_id, {})
    for opp, rows in champ_entry.get("matches_by_opponent", {}).items():
        if not rows:
            continue
        valid_rows = [r for r in rows if r.get("outcome") in ("win", "loss", "inconclusive")]
        latest = valid_rows[-1] if valid_rows else rows[-1]
        num = latest.get("match_number")
        if num is None or num in seen:
            continue
        seen.add(num)
        run = batch / f"match-{num}"
        mj = evolution.read_json(run / "match-error.json") or evolution.read_json(run / "match.json") or {}
        mounted.append({
            **mj,
            "match_number": num,
            "opponent": opp,
            "policy_version": champion_id,
            "l0_score": latest.get("score"),
            "l0_fps": latest.get("fps"),
            "l0_outcome": latest.get("outcome"),
            "role": "champion_baseline",
        })
    return mounted


def run_v2_live(args, batch: Path, repo_root: Path, live_model, review_client: V2AgenticReviewClient,
                stop_event, *, play_fn=None, compile_fn=None) -> int:
    """Main RSI v2 Champion/Challenger evolution loop."""
    batch = Path(batch)
    repo_root = Path(repo_root).resolve()
    core_opponents = [o.strip() for o in args.opponent.split(",") if o.strip()]
    if "PurpleWave" in core_opponents:
        raise ValueError("RSI v2 core opponents must not include PurpleWave (Java single-wave)")

    led = ledger_mod.load_ledger(batch, core_opponents=core_opponents)
    pending_challenger: str | None = None
    champ_infra_errors = 0
    last_prediction: dict | None = None

    def next_match_num() -> int:
        idx = evolution.read_json(batch / "batch.json")
        return len(idx["matches"]) + 1

    def append_matches_to_batch(matches: list[dict], round_entry: dict) -> None:
        idx = evolution.read_json(batch / "batch.json")
        idx["matches"].extend(matches)
        idx["rounds"].append(round_entry)
        idx["active"] = evolution.active_pointer(batch)
        idx["statistics"] = evolution._stats(idx["matches"])
        write_json(batch / "batch.json", idx)

    for round_number in range(1, args.rounds + 1):
        if stop_event.is_set() or live_model.circuit.open or review_client.circuit.open:
            break
        champ_id = evolution.active_pointer(batch)["version"]
        champ_dir = batch / "versions" / champ_id
        round_matches: list[dict] = []
        adjudication: dict | None = None
        evaluated_label = champ_id
        mnum = next_match_num()

        if pending_challenger is None:
            # Round 1 (or recovery round): evaluate Champion across all core opponents (+1 retest in Wave 2 idle slot).
            eval_opps = list(core_opponents)
            if args.parallel >= 2 and len(eval_opps) % args.parallel != 0:
                eval_opps.append(core_opponents[0])
            pending_jobs = [(champ_dir, champ_id, mnum + i, opp) for i, opp in enumerate(eval_opps)]
            while pending_jobs:
                if stop_event.is_set() or live_model.circuit.open:
                    break
                wave = pending_jobs[:args.parallel]
                pending_jobs = pending_jobs[args.parallel:]
                res = _play_wave_v2(args, batch, wave, live_model, stop_event, play_fn=play_fn)
                for m in res:
                    obs = evolution.read_json(batch / f"match-{m['match_number']}/observations.json", [])
                    sr = scoring.score_match(m, obs, max_frames=args.max_frames)
                    m.update(l0_score=sr["score"], l0_fps=sr["fps"], l0_outcome=sr["outcome"], _eval_role="champion_baseline")
                    ledger_mod.record_match_in_ledger(led, champ_id, m, sr, batch / f"match-{m['match_number']}")
                    round_matches.append(m)
                    champ_infra_errors = champ_infra_errors + 1 if m["state"] == "error" else 0
                if any(m["state"] in {"model_circuit_open", "manual_stop"} for m in res):
                    break
        else:
            # Challenger trial: Wave 1 tests core[0..parallel-1]; Wave 2 tests remaining core + Champion re-test in idle slot!
            chal_id = pending_challenger
            chal_dir = batch / "candidates" / chal_id
            evaluated_label = chal_id
            chal_records_by_opp: dict[str, list[dict]] = {o: [] for o in core_opponents}
            chal_crashed = False

            chal_queue = list(core_opponents)
            champ_recs = ledger_mod.champion_records_by_opp(led)
            champ_retest_opp = min(
                core_opponents,
                key=lambda o: (
                    scoring.aggregate_records(champ_recs.get(o, []))["games"],
                    (core_opponents.index(o) - (round_number - 1)) % len(core_opponents),
                ),
            )
            retest_done = False

            while chal_queue:
                if stop_event.is_set() or live_model.circuit.open or chal_crashed:
                    break
                wave_opps = chal_queue[:args.parallel]
                chal_queue = chal_queue[args.parallel:]
                wave_jobs = []
                for opp in wave_opps:
                    wave_jobs.append((chal_dir, chal_id, mnum, opp))
                    mnum += 1
                # Fill idle slot in final wave with 1 Champion re-test match (Claude opinion #1!).
                if not chal_queue and not retest_done and len(wave_jobs) < args.parallel:
                    wave_jobs.append((champ_dir, champ_id, mnum, champ_retest_opp))
                    mnum += 1
                    retest_done = True

                res = _play_wave_v2(args, batch, wave_jobs, live_model, stop_event, play_fn=play_fn)
                for m in res:
                    obs = evolution.read_json(batch / f"match-{m['match_number']}/observations.json", [])
                    sr = scoring.score_match(m, obs, max_frames=args.max_frames)
                    is_chal = (m["policy_version"] == chal_id)
                    m.update(
                        l0_score=sr["score"], l0_fps=sr["fps"], l0_outcome=sr["outcome"],
                        _eval_role="challenger_trial" if is_chal else "champion_retest",
                    )
                    rec = ledger_mod.record_match_in_ledger(
                        led, chal_id if is_chal else champ_id, m, sr, batch / f"match-{m['match_number']}"
                    )
                    round_matches.append(m)
                    if is_chal:
                        chal_records_by_opp.setdefault(m["opponent"], []).append(rec)
                        if scoring.is_challenger_crash(m) or m["state"] == "error":
                            chal_crashed = True
                    else:
                        champ_infra_errors = champ_infra_errors + 1 if m["state"] == "error" else 0
                if any(m["state"] in {"model_circuit_open", "manual_stop"} for m in res):
                    break

            if not stop_event.is_set() and not live_model.circuit.open:
                if chal_crashed:
                    adjudication = {"decision": "rejected", "reason": "crashed_reject", "details": {"fail_fast": True}}
                else:
                    adjudication = scoring.adjudicate_challenger(
                        ledger_mod.champion_records_by_opp(led),
                        chal_records_by_opp,
                        core_opponents,
                    )
                if adjudication["decision"] == "promoted":
                    new_champ = promote_v2_candidate(batch, chal_id, adjudication)
                    ledger_mod.record_promotion(led, chal_id, new_champ, adjudication)
                    champ_id = new_champ
                    champ_dir = batch / "versions" / champ_id
                    print(f"[RSI v2] Round {round_number}: PROMOTED {chal_id} -> {new_champ} ({adjudication['reason']})", flush=True)
                else:
                    ledger_mod.record_rejection(led, chal_id, adjudication)
                    print(f"[RSI v2] Round {round_number}: REJECTED {chal_id} ({adjudication['reason']}); Champion remains {champ_id}", flush=True)
                pending_challenger = None

        if not round_matches:
            break

        # Review & propose next Challenger (unless final round or interrupted/no_evolve).
        review_validation = "skipped"
        proposed_id = None
        review_dir = batch / f"match-{round_matches[-1]['match_number']}"
        review_usage = None

        if stop_event.is_set() or any(m["state"] == "manual_stop" for m in round_matches):
            review_validation = "manual_stop"
        elif live_model.circuit.open:
            review_validation = "circuit_open"
        elif args.no_evolve or round_number == args.rounds:
            review_validation = "disabled" if args.no_evolve else "final_round_no_review"
        else:
            champ_sources = _read_v2_sources(champ_dir)
            compact_led = ledger_mod.compact_for_review(led)
            mounted = _mounted_for_review(batch, led, round_matches, champ_id)
            ws = V2ReviewWorkspace(
                batch, repo_root, champ_sources, mounted, compact_led,
                compile_fn=compile_fn, scratch_dir=review_dir / "compile-scratch",
            )
            champ_wr_all = [
                scoring.aggregate_records(led["versions"][champ_id]["matches_by_opponent"].get(o, []))["win_rate"]
                for o in core_opponents
            ]
            allow_keep = all(wr >= 1.0 for wr in champ_wr_all) and all(
                len(led["versions"][champ_id]["matches_by_opponent"].get(o, [])) >= 1 for o in core_opponents
            )
            diversity_nudge = None
            if led.get("consecutive_rejects", 0) >= 2:
                diversity_nudge = (
                    f"WARNING: {led['consecutive_rejects']} consecutive Challengers were rejected on Champion {champ_id}. "
                    "Do NOT tweak the same small threshold again. Pivot to a structurally stronger MacroBot.cpp "
                    "improvement (e.g. build order, scout-reactive tech switch, army composition, or combat micro)."
                )
            overview = {
                "round": round_number,
                "active_champion": champ_id,
                "just_evaluated": evaluated_label,
                "adjudication": adjudication,
                "previous_prediction": last_prediction,
                "diversity_nudge": diversity_nudge,
                "this_round_matches": [
                    {
                        "match_number": m["match_number"], "role": m.get("_eval_role"),
                        "policy_version": m["policy_version"], "opponent": m["opponent"],
                        "state": m["state"], "natural_outcome": m.get("natural_outcome"),
                        "adjudicated_outcome": m.get("adjudicated_outcome"),
                        "frame": m.get("frame"), "l0_score": m.get("l0_score"),
                        "l0_fps": m.get("l0_fps"), "error": m.get("error"),
                    }
                    for m in round_matches
                ],
                "ledger": compact_led,
            }
            call_report = review_client.review(overview, ws, review_dir / "review-call-1.json", allow_keep=allow_keep)
            review_usage = {k: call_report.get(k) for k in (*USAGE_FIELDS, "seconds", "turns", "tool_calls")}
            if call_report.get("error") or not call_report.get("decoded"):
                review_validation = "circuit_open" if review_client.circuit.open else "review_failed"
                write_json(review_dir / "review.json", {
                    "round": round_number, "validation": review_validation,
                    "error": call_report.get("error"), "review_usage": review_usage,
                })
            else:
                proposal = call_report["decoded"]
                if proposal["decision"] == "keep":
                    review_validation = "kept"
                    write_json(review_dir / "review.json", {**proposal, "validation": "kept", "review_usage": review_usage})
                else:
                    try:
                        staged_res = stage_v2_candidate(
                            batch, repo_root, champ_id, proposal, ws, review_dir,
                            compile_fn=compile_fn, review_usage=review_usage,
                        )
                        proposed_id = staged_res["challenger_id"]
                        pending_challenger = proposed_id
                        review_validation = "staged_challenger"
                        ledger_mod.register_challenger(led, proposed_id, champ_id, staged_res)
                        last_prediction = {
                            "for_challenger": proposed_id,
                            "hypothesis": proposal["hypothesis"],
                            "expectation": proposal["expectation"],
                        }
                        print(f"[RSI v2] Round {round_number}: staged {proposed_id} from {champ_id} "
                              f"(files={sorted(proposal['edits'])}, cpp_delta={staged_res['cpp_changed_lines']}L)", flush=True)
                    except Exception as error:
                        review_validation = "stage_rejected"
                        write_json(review_dir / "review.json", {
                            **proposal, "validation": "stage_rejected",
                            "validation_error": str(error), "review_usage": review_usage,
                        })

        round_entry = {
            "round": round_number,
            "evaluated": evaluated_label,
            "match_numbers": [m["match_number"] for m in round_matches],
            "champion_after_round": champ_id,
            "adjudication_decision": (adjudication or {}).get("decision"),
            "adjudication_reason": (adjudication or {}).get("reason"),
            "proposed_challenger": proposed_id,
            "review_validation": review_validation,
            "review_usage": review_usage,
        }
        led["rounds"].append(round_entry)
        clean_matches = [{k: v for k, v in m.items() if not k.startswith("_")} for m in round_matches]
        append_matches_to_batch(clean_matches, round_entry)
        ledger_mod.save_ledger(batch, led)

        if live_model.circuit.open or review_client.circuit.open:
            open_circuit = live_model.circuit if live_model.circuit.open else review_client.circuit
            idx = evolution.read_json(batch / "batch.json")
            idx.update(state="model_circuit_open", model_circuit={
                "open": True, "reason": open_circuit.reason, "failures": open_circuit.failures,
            })
            write_json(batch / "batch.json", idx)
            return 1
        if stop_event.is_set() or any(m["state"] == "manual_stop" for m in round_matches):
            idx = evolution.read_json(batch / "batch.json")
            idx["state"] = "manual_stop"
            write_json(batch / "batch.json", idx)
            return 130
        if champ_infra_errors >= 3:
            idx = evolution.read_json(batch / "batch.json")
            idx.update(state="repeated_champion_errors", consecutive_champion_errors=champ_infra_errors)
            write_json(batch / "batch.json", idx)
            return 1

    idx = evolution.read_json(batch / "batch.json")
    idx["state"] = "completed"
    write_json(batch / "batch.json", idx)
    ledger_mod.save_ledger(batch, led)
    return 0
