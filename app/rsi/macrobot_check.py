"""Prove app/MacroBot.cpp with the default build_config.json is baseline/Bot.cpp.

1. Drop every //@config-begin ... //@config-end block (config loading, logging, includes).
2. Drop every /*+*/ ... /*-*/ span. Each span must have one of the inert shapes below.
3. Replace every cfg.<name> with its default from build_config.SCHEMA.
The result must equal baseline/Bot.cpp byte for byte.

    py -3.12 -m app.rsi.macrobot_check
"""

from difflib import unified_diff
import hashlib
import json
from pathlib import Path
import re
import sys

from .build_config import SCHEMA, defaults


ROOT = Path(__file__).resolve().parents[2]
SPAN = re.compile(r"/\*\+\*/(.*?)/\*-\*/")
PARAM = re.compile(r"\bcfg\.([a-z][a-z0-9_]*)\b")
PARAM_LIST = re.compile(r"^#define MACRO_PARAMS\(X\) (.*)$", re.M)
# Inert at defaults: a guarded early return whose conjunction contains early(cfg.X) with X off (< 0),
# an extra "|| early(cfg.X)" with X off, a factor "*cfg.X" with X == 1, or the config-load guard.
GUARDED_RETURN = re.compile(r"if\((.*)\) return Protoss_[A-Za-z_]+;")
EARLY = re.compile(r"early\(cfg\.([a-z0-9_]+)\)")
OR_EARLY = re.compile(r" \|\| early\(cfg\.([a-z0-9_]+)\)")
FACTOR = re.compile(r"\*cfg\.([a-z0-9_]+)")
LOAD_GUARD = "if(!configOk) return;"


def _conjuncts(condition):
    terms, depth, start = [], 0, 0
    for index, char in enumerate(condition):
        depth += char == "("
        depth -= char == ")"
        if depth == 0 and condition.startswith("&&", index):
            terms.append(condition[start:index].strip())
            start = index + 2
    return terms + [condition[start:].strip()]


def _inert(span, values):
    if span == LOAD_GUARD:
        return True
    if match := OR_EARLY.fullmatch(span):
        return values.get(match.group(1), 0) < 0
    if match := FACTOR.fullmatch(span):
        return values.get(match.group(1)) == 1
    if match := GUARDED_RETURN.fullmatch(span):
        for term in _conjuncts(match.group(1)):
            early = EARLY.fullmatch(term)
            if early and values.get(early.group(1), 0) < 0:
                return True
    return False


def reduce_source(text, values):
    """Return (reduced text, spans, referenced parameter names) or raise ValueError."""
    lines, spans, referenced, skipping = [], [], set(), False
    for number, line in enumerate(text.splitlines(keepends=True), 1):
        marker = line.strip()
        if marker == "//@config-begin":
            if skipping:
                raise ValueError(f"line {number}: nested //@config-begin")
            skipping = True
            continue
        if marker == "//@config-end":
            if not skipping:
                raise ValueError(f"line {number}: //@config-end without begin")
            skipping = False
            continue
        if skipping:
            continue
        found = SPAN.findall(line)
        for span in found:
            if not _inert(span, values):
                raise ValueError(f"line {number}: span is not inert at defaults: {span}")
            spans.append({"line": number, "span": span})
            referenced.update(PARAM.findall(span))
        if found and not SPAN.sub("", line).strip():
            continue  # A whole added line.
        line = SPAN.sub("", line)
        if "/*+*/" in line or "/*-*/" in line:
            raise ValueError(f"line {number}: unbalanced /*+*/ marker")
        for name in PARAM.findall(line):
            if name not in values:
                raise ValueError(f"line {number}: unknown parameter cfg.{name}")
            referenced.add(name)
        lines.append(PARAM.sub(lambda match: str(values[match.group(1)]), line))
    if skipping:
        raise ValueError("unterminated //@config-begin")
    return "".join(lines), spans, referenced


def check(macro_path=ROOT / "app/MacroBot.cpp", baseline_path=ROOT / "baseline/Bot.cpp"):
    macro = Path(macro_path).read_text(encoding="utf-8")
    baseline = Path(baseline_path).read_text(encoding="utf-8")
    declared = PARAM_LIST.search(macro)
    names = re.findall(r"X\(([a-z0-9_]+)\)", declared.group(1)) if declared else []
    if names != list(SCHEMA):
        raise ValueError("MACRO_PARAMS in MacroBot.cpp does not list the schema keys in schema order")
    reduced, spans, referenced = reduce_source(macro, defaults())
    if unused := [name for name in SCHEMA if name not in referenced]:
        raise ValueError(f"schema keys never used by MacroBot.cpp: {', '.join(unused)}")
    if reduced != baseline:
        diff = "".join(unified_diff(baseline.splitlines(keepends=True), reduced.splitlines(keepends=True),
                                    fromfile="baseline/Bot.cpp", tofile="MacroBot.cpp (reduced)", n=1))
        raise ValueError("reduced MacroBot.cpp differs from baseline/Bot.cpp:\n" + diff)
    return {
        "equivalent": True,
        "baseline_sha256": hashlib.sha256(baseline.encode()).hexdigest(),
        "macrobot_sha256": hashlib.sha256(macro.encode()).hexdigest(),
        "parameters": len(SCHEMA),
        "inert_spans": spans,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(check(), indent=2))
    except ValueError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
