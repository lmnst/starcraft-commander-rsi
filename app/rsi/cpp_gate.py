"""L0 C++ static safety gate, function outline, and isolated MSVC build for evolvable MacroBot.cpp."""

from difflib import unified_diff
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
from tempfile import NamedTemporaryFile
import time

from . import build_config


CPP_FILENAME = "MacroBot.cpp"
DLL_FILENAME = "MacroBot.dll"
MAX_CPP_CHARS = 60000
MAX_CPP_DIFF_LINES = 150
COMPILE_TIMEOUT_SECONDS = 90

ALLOWED_INCLUDES = frozenset({
    "BWAPI.h", "bwem.h",
    "algorithm", "array", "cctype", "climits", "cmath", "cstdint", "deque", "fstream",
    "functional", "iomanip", "limits", "list", "map", "memory", "numeric", "optional",
    "queue", "set", "sstream", "stack", "string", "tuple", "unordered_map",
    "unordered_set", "utility", "vector",
})
ALLOWED_FILE_PATHS = frozenset({
    "bwapi-data/AI/build_config.json",
    "bwapi-data/write/astra-trace.jsonl",
})
LOCKED_DEFINE_BODIES = {
    "MACRO_DECLARE": "(name) int name=0;",
    "MACRO_READ": '(name) {auto it=values.find(#name); if(it==values.end()) {error="missing " #name;return false;} cfg.name=it->second; values.erase(it);}',
    "MACRO_WRITE": '(name) trace<<separator<<"\\"" #name "\\":"<<cfg.name; separator=",";',
}
LOCKED_DEFINE_NAMES = frozenset({"MACRO_PARAMS", *LOCKED_DEFINE_BODIES})

BANNED_IDENTIFIERS = frozenset({
    # BWAPI cheat / engine-manipulation APIs
    "enableFlag", "CompleteMapInformation", "UserInput",
    "setRevealAll", "setVision", "setAlliance",
    "setLocalSpeed", "setFrameSkip", "setGUI", "setCommandOptimizationLevel",
    "pauseGame", "resumeGame", "restartGame", "setLatCom", "sendText",
    # Memory / OS / process / threading / raw C file I/O escapes
    "reinterpret_cast", "volatile", "__declspec", "DllMain", "_Pragma",
    "windows", "filesystem", "fstream", "wfstream", "wifstream", "wofstream", "filebuf",
    "basic_ifstream", "basic_ofstream", "basic_fstream", "basic_filebuf",
    "thread", "jthread", "mutex", "async",
    "system", "popen", "_popen",
    "CreateProcess", "CreateProcessA", "CreateProcessW",
    "CreateFile", "CreateFileA", "CreateFileW",
    "WinExec", "ShellExecute", "ShellExecuteA", "ShellExecuteW",
    "LoadLibrary", "LoadLibraryA", "LoadLibraryW", "GetProcAddress",
    "VirtualProtect", "VirtualAlloc", "ReadProcessMemory", "WriteProcessMemory",
    "GetModuleHandle", "GetModuleHandleA", "GetModuleHandleW",
    "asm", "__asm", "__asm__",
    "fopen", "fopen_s", "freopen", "freopen_s", "_wfopen", "_open", "_wopen",
    "FILE", "getenv", "_wgetenv", "Sleep",
    "ExitProcess", "TerminateProcess", "terminate",
    "PostMessage", "PostMessageA", "PostMessageW",
    "SendMessage", "SendMessageA", "SendMessageW",
    "SendInput", "keybd_event", "SetWindowsHookEx",
    "abort", "quick_exit", "_exit",
})
BANNED_WORD_RE = re.compile(r"\b(" + "|".join(sorted(BANNED_IDENTIFIERS)) + r")\b")
BARE_C_FILE_OP_RE = re.compile(r"(?:(?<![:.\w>])|(?<!\w)::)\b(remove|rename)\s*\(")
INCLUDE_RE = re.compile(r"^\s*#\s*include\s*(.*)$")
ANGLE_INCLUDE_RE = re.compile(r"^<([A-Za-z0-9_./]+)>\s*(?://.*)?$")
DEFINE_RE = re.compile(r"^\s*#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)(.*)$")
SIMPLE_NUM_DEFINE_RE = re.compile(r"^\s+-?(?:\d+(?:\.\d+)?|0x[0-9a-fA-F]+)\s*(?://.*)?$")
PARAM_LIST_RE = re.compile(r"^#define MACRO_PARAMS\(X\) (.*)$", re.M)
UNICODE_ESCAPE_RE = re.compile(r"\\u[0-9a-fA-F]{4}|\\U[0-9a-fA-F]{8}")
LINE_SPLICE_RE = re.compile(r"\\\r?\n")
PRAGMA_RE = re.compile(r"^\s*#\s*pragma\b", re.M)
LEAVE_GAME_RE = re.compile(r"\bleaveGame\b")
STREAM_TOKEN_RE = re.compile(r"\b(?:std\s*::\s*)?[io]fstream\b")
STREAM_DECL_ONLY_RE = re.compile(r"^\s+[A-Za-z_][A-Za-z0-9_]*\s*;")
STREAM_INIT_LITERAL_RE = re.compile(r'^(?:\s+[A-Za-z_][A-Za-z0-9_]*)?\s*[({]\s*"([^"]*)"\s*(?:,|[)}])')
STREAM_OPEN_RE = re.compile(r"(?:\.|->)\s*open\s*\(([^)]*)\)")
STRING_LITERAL_START_RE = re.compile(r'^\s*"([^"]*)"\s*(?:,|$)')
HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


class CompileError(ValueError):
    """Raised when candidate MacroBot.cpp fails to compile or export required symbols."""


def _strip_comments_and_strings(source: str):
    """Return (code_only, code_with_strings, string_literals).

    Handles // line comments, /* block comments */, "..." strings, '...' chars,
    and C++11 raw string literals R"delim(...)delim".
    """
    n = len(source)
    i = 0
    code_only = []
    code_with_strings = []
    strings = []
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        # Line comment
        if ch == "/" and nxt == "/":
            start = i
            i += 2
            while i < n and source[i] != "\n":
                i += 1
            blank = " " * (i - start)
            code_only.append(blank)
            code_with_strings.append(blank)
            continue
        # Block comment
        if ch == "/" and nxt == "*":
            start = i
            end = source.find("*/", i + 2)
            if end == -1:
                raise ValueError("unterminated block comment in MacroBot.cpp")
            i = end + 2
            replaced = "".join("\n" if c == "\n" else " " for c in source[start:i])
            code_only.append(replaced)
            code_with_strings.append(replaced)
            continue
        # C++11 raw string literal: R"delim(...)delim" or prefixed LR / uR / UR / u8R
        if ch == "R" and nxt == '"':
            raw_prefix_ok = (i == 0 or not (source[i - 1].isalnum() or source[i - 1] == "_"))
            if not raw_prefix_ok and i >= 1 and source[i - 1] in "LuU":
                raw_prefix_ok = (i == 1 or not (source[i - 2].isalnum() or source[i - 2] == "_"))
            elif not raw_prefix_ok and i >= 2 and source[i - 2:i] == "u8":
                raw_prefix_ok = (i == 2 or not (source[i - 3].isalnum() or source[i - 3] == "_"))
            if raw_prefix_ok:
                paren = source.find("(", i + 2)
                if paren != -1 and paren - (i + 2) <= 16:
                    delim = source[i + 2:paren]
                    if not any(c in delim for c in ' \t\r\n()\\'):
                        close = ")" + delim + '"'
                        end = source.find(close, paren + 1)
                        if end == -1:
                            raise ValueError("unterminated raw string literal in MacroBot.cpp")
                        content = source[paren + 1:end]
                        strings.append(content)
                        raw_span = source[i:end + len(close)]
                        code_only.append("".join("\n" if c == "\n" else " " for c in raw_span))
                        code_with_strings.append(raw_span)
                        i = end + len(close)
                        continue
        # Normal string literal
        if ch == '"':
            start = i
            i += 1
            chars = []
            while i < n and source[i] != '"':
                if source[i] == "\\" and i + 1 < n:
                    chars.append(source[i:i + 2])
                    i += 2
                elif source[i] == "\n":
                    raise ValueError("unterminated string literal in MacroBot.cpp")
                else:
                    chars.append(source[i])
                    i += 1
            if i >= n:
                raise ValueError("unterminated string literal in MacroBot.cpp")
            i += 1
            literal = "".join(chars)
            strings.append(literal)
            span = source[start:i]
            code_only.append(" " * len(span))
            code_with_strings.append(span)
            continue
        # Char literal (distinguish C++17 numeric digit separator like 10'000)
        if ch == "'":
            if i > 0 and i + 1 < n and source[i - 1] in HEX_DIGITS and source[i + 1] in HEX_DIGITS:
                code_only.append(ch)
                code_with_strings.append(ch)
                i += 1
                continue
            start = i
            i += 1
            while i < n and source[i] != "'":
                if source[i] == "\\" and i + 1 < n:
                    i += 2
                elif source[i] == "\n":
                    raise ValueError("unterminated char literal in MacroBot.cpp")
                else:
                    i += 1
            if i >= n:
                raise ValueError("unterminated char literal in MacroBot.cpp")
            i += 1
            span = source[start:i]
            code_only.append(" " * len(span))
            code_with_strings.append(span)
            continue
        code_only.append(ch)
        code_with_strings.append(ch)
        i += 1
    return "".join(code_only), "".join(code_with_strings), strings


def count_diff_lines(before: str, after: str) -> int:
    """Count total added + removed content lines in a unified diff."""
    changed = 0
    for line in unified_diff(before.splitlines(), after.splitlines(), n=0):
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")):
            changed += 1
    return changed


def check_source(source: str, *, parent_source: str | None = None,
                 max_diff_lines: int | None = MAX_CPP_DIFF_LINES) -> dict:
    """Static anti-cheat, contract, and diff-size gate for MacroBot.cpp."""
    if type(source) is not str or not source.strip():
        raise ValueError(f"{CPP_FILENAME}: expected nonempty source text")
    if "\x00" in source:
        raise ValueError(f"{CPP_FILENAME}: NUL byte is not allowed")
    if len(source) > MAX_CPP_CHARS:
        raise ValueError(f"{CPP_FILENAME}: {len(source)} characters exceeds limit {MAX_CPP_CHARS}")

    # Raw preprocessor hardening before stripping comments/strings.
    if LINE_SPLICE_RE.search(source):
        raise ValueError(f"{CPP_FILENAME}: backslash line splicing (\\<newline>) is not allowed")
    if UNICODE_ESCAPE_RE.search(source):
        raise ValueError(f"{CPP_FILENAME}: universal character names (\\u / \\U) are not allowed")
    if PRAGMA_RE.search(source):
        raise ValueError(f"{CPP_FILENAME}: #pragma directives are not allowed")

    code_only, code_with_strings, strings = _strip_comments_and_strings(source)

    if "##" in code_only or "%:" in code_only:
        raise ValueError(f"{CPP_FILENAME}: preprocessor token pasting (##) or digraph (%:) is not allowed")

    # Check #include and #define lines from code_with_strings (comments already removed).
    locked_define_seen: set[str] = set()
    filtered_code_lines = []
    for lineno, line in enumerate(code_with_strings.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            inc = INCLUDE_RE.match(line)
            if inc:
                target = inc.group(1).strip()
                angle = ANGLE_INCLUDE_RE.match(target)
                if not angle or angle.group(1) not in ALLOWED_INCLUDES:
                    raise ValueError(f"{CPP_FILENAME}:{lineno}: forbidden #include {target!r}")
                filtered_code_lines.append("")
                continue
            dfn = DEFINE_RE.match(line)
            if dfn:
                name, rest = dfn.group(1), dfn.group(2)
                if name in LOCKED_DEFINE_NAMES:
                    if name in locked_define_seen:
                        raise ValueError(f"{CPP_FILENAME}:{lineno}: duplicate locked #define {name}")
                    locked_define_seen.add(name)
                    if name in LOCKED_DEFINE_BODIES and rest.strip() != LOCKED_DEFINE_BODIES[name]:
                        raise ValueError(f"{CPP_FILENAME}:{lineno}: locked #define {name} body may not be modified")
                elif not SIMPLE_NUM_DEFINE_RE.match(rest):
                    raise ValueError(f"{CPP_FILENAME}:{lineno}: forbidden #define {name} (use constexpr/functions)")
                filtered_code_lines.append("")
                continue
            if re.match(r"^\s*#\s*(?:undef\s+(?:MACRO_DECLARE|MACRO_READ|MACRO_WRITE)\b|if|ifdef|ifndef|elif|else|endif)\b", line):
                filtered_code_lines.append("")
                continue
            raise ValueError(f"{CPP_FILENAME}:{lineno}: forbidden preprocessor directive")
        filtered_code_lines.append(line)

    # Scan stripped code (no comments, no strings, no #include/#define lines) for banned identifiers.
    code_only_no_pp_lines = []
    for raw_line, code_line in zip(code_with_strings.splitlines(), code_only.splitlines(), strict=True):
        if raw_line.lstrip().startswith("#"):
            code_only_no_pp_lines.append("")
        else:
            code_only_no_pp_lines.append(code_line)
    scan_text = "\n".join(code_only_no_pp_lines)

    if match := BANNED_WORD_RE.search(scan_text):
        raise ValueError(f"{CPP_FILENAME}: forbidden identifier {match.group(1)!r}")
    if match := BARE_C_FILE_OP_RE.search(scan_text):
        raise ValueError(f"{CPP_FILENAME}: forbidden bare C file operation {match.group(1)}()")

    # leaveGame is only allowed once inside the locked loadConfig failure guard.
    leave_lines = [idx for idx, line in enumerate(code_only_no_pp_lines, 1) if LEAVE_GAME_RE.search(line)]
    if len(leave_lines) > 1:
        raise ValueError(f"{CPP_FILENAME}: leaveGame() is forbidden outside the config-load guard")
    if len(leave_lines) == 1:
        ln = leave_lines[0]
        context = "\n".join(code_only_no_pp_lines[max(0, ln - 5):ln + 1])
        if "loadConfig" not in context:
            raise ValueError(f"{CPP_FILENAME}:{ln}: leaveGame() is only permitted inside the loadConfig guard")

    # String literal safety: forbid directory traversal or opponent references in literal paths.
    for lit in strings:
        low = lit.lower()
        if ".." in lit:
            raise ValueError(f"{CPP_FILENAME}: parent path '..' is forbidden in string literals")
        if "opponent" in low:
            raise ValueError(f"{CPP_FILENAME}: 'opponent' is forbidden in string literals")

    # File-stream path whitelist: every ifstream/ofstream must be bare declaration OR literal path;
    # every .open() / ->open() must use an immediate literal path in ALLOWED_FILE_PATHS.
    body_with_strings = "\n".join(filtered_code_lines)
    for match in STREAM_TOKEN_RE.finditer(body_with_strings):
        tail = body_with_strings[match.end():match.end() + 200]
        if STREAM_DECL_ONLY_RE.match(tail):
            continue
        init = STREAM_INIT_LITERAL_RE.match(tail)
        if not init or init.group(1) not in ALLOWED_FILE_PATHS:
            raise ValueError(f"{CPP_FILENAME}: file stream path must be a literal in {sorted(ALLOWED_FILE_PATHS)}")
    for match in STREAM_OPEN_RE.finditer(body_with_strings):
        lit = STRING_LITERAL_START_RE.match(match.group(1))
        if not lit or lit.group(1) not in ALLOWED_FILE_PATHS:
            raise ValueError(f"{CPP_FILENAME}: file stream open() path must be a literal in {sorted(ALLOWED_FILE_PATHS)}")

    # Locked L0 contract checks (prevents confirm_build_config & export failures).
    declared = PARAM_LIST_RE.search(source)
    names = re.findall(r"X\(([a-z0-9_]+)\)", declared.group(1)) if declared else []
    if names != list(build_config.SCHEMA):
        raise ValueError(f"{CPP_FILENAME}: MACRO_PARAMS(X) must match build_config.SCHEMA in exact order")
    for required in ("loadConfig", "config_loaded", "gameInit", "newAIModule", "onStart", "onFrame", "onEnd"):
        if required not in source:
            raise ValueError(f"{CPP_FILENAME}: missing required contract symbol {required!r}")

    changed_lines = 0
    if parent_source is not None:
        changed_lines = count_diff_lines(parent_source, source)
        if max_diff_lines is not None and changed_lines > max_diff_lines:
            raise ValueError(
                f"{CPP_FILENAME}: changed {changed_lines} lines, limit is {max_diff_lines} per round"
            )

    return {"chars": len(source), "lines": len(source.splitlines()), "changed_lines": changed_lines}


def outline(source: str) -> dict:
    """Return a function table of contents with 1-based line ranges for paginated reading."""
    code_only, _, _ = _strip_comments_and_strings(source)
    raw_lines = source.splitlines()
    code_lines = code_only.splitlines()
    funcs = []
    # Match class methods (indented 2 spaces) or top-level functions (0 spaces).
    header_re = re.compile(r"^(  |)(?:extern\s+\"C\"\s+)?[A-Za-z_][\w:<>,\s\*&]*?\b([A-Za-z_~]\w*)\s*\([^;]*\)\s*(?:const\s*)?(?:override\s*)?\{")
    skip_names = {"if", "for", "while", "switch", "catch", "else", "struct", "class", "namespace"}
    i = 0
    total = len(code_lines)
    while i < total:
        line = code_lines[i]
        m = header_re.match(line)
        if m and m.group(2) not in skip_names:
            name = m.group(2)
            depth = line.count("{") - line.count("}")
            start = i + 1
            j = i
            while depth > 0 and j + 1 < total:
                j += 1
                depth += code_lines[j].count("{") - code_lines[j].count("}")
            end = j + 1
            funcs.append({
                "name": name,
                "start_line": start,
                "end_line": end,
                "lines": end - start + 1,
                "signature": raw_lines[i].strip()[:120],
            })
            i = j + 1
            continue
        i += 1
    return {
        "file": CPP_FILENAME,
        "total_lines": total,
        "characters": len(source),
        "functions": funcs,
        "hint": "Call read_policy(file='MacroBot.cpp', start_line=..., end_line=...) to read specific line ranges, "
                "and edit_policy(file='MacroBot.cpp', old_text=..., new_text=...) for targeted edits (<=150 changed lines).",
    }


def verify_pe_exports(dll_path: Path, required=("gameInit", "newAIModule")) -> list[str]:
    """Parse a Win32 PE32 DLL export table using stdlib struct and verify required exports."""
    data = Path(dll_path).read_bytes()
    if len(data) < 0x100 or data[:2] != b"MZ":
        raise CompileError(f"{dll_path.name}: not a valid PE binary")
    pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe_offset:pe_offset + 4] != b"PE\x00\x00":
        raise CompileError(f"{dll_path.name}: missing PE signature")
    num_sections = struct.unpack_from("<H", data, pe_offset + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe_offset + 20)[0]
    opt_offset = pe_offset + 24
    magic = struct.unpack_from("<H", data, opt_offset)[0]
    if magic != 0x10B:  # PE32 (Win32)
        raise CompileError(f"{dll_path.name}: expected Win32 PE32 binary (magic=0x{magic:x})")
    export_rva, export_size = struct.unpack_from("<II", data, opt_offset + 96)
    if not export_rva or not export_size:
        raise CompileError(f"{dll_path.name}: DLL has no export directory")

    sec_offset = opt_offset + opt_size
    sections = []
    for idx in range(num_sections):
        off = sec_offset + idx * 40
        vsize, vaddr, rsize, rptr = struct.unpack_from("<IIII", data, off + 8)
        sections.append((vaddr, max(vsize, rsize), rptr))

    def rva_to_offset(rva):
        for vaddr, span, rptr in sections:
            if vaddr <= rva < vaddr + span:
                return rptr + (rva - vaddr)
        raise CompileError(f"{dll_path.name}: export RVA 0x{rva:x} outside sections")

    exp_off = rva_to_offset(export_rva)
    num_names = struct.unpack_from("<I", data, exp_off + 24)[0]
    names_rva = struct.unpack_from("<I", data, exp_off + 32)[0]
    names_off = rva_to_offset(names_rva)
    exports = []
    for idx in range(num_names):
        name_rva = struct.unpack_from("<I", data, names_off + idx * 4)[0]
        str_off = rva_to_offset(name_rva)
        end = data.index(b"\x00", str_off)
        exports.append(data[str_off:end].decode("ascii", errors="replace"))
    missing = [name for name in required if name not in exports]
    if missing:
        raise CompileError(f"{dll_path.name}: missing required exports {missing} (found {exports})")
    return exports


def _decode_process_output(raw: bytes) -> str:
    if not raw:
        return ""
    for enc in ("mbcs", "utf-8"):
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_compiler_errors(text: str, limit: int = 3500) -> str:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    relevant = [line for line in lines if any(tok in line.lower() for tok in ("error", "fatal", "macrobot.cpp", "lnk"))]
    chosen = relevant if relevant else lines[-40:]
    joined = "\n".join(chosen)
    return joined[:limit] + ("... [truncated]" if len(joined) > limit else "")


def _isolated_cmake_text() -> str:
    return """cmake_minimum_required(VERSION 3.12)
project(MacroBot LANGUAGES CXX)
if(NOT MSVC OR NOT CMAKE_SIZEOF_VOID_P EQUAL 4)
  message(FATAL_ERROR "Configure with an x86 MSVC generator, e.g. -G \\"Visual Studio 15 2017\\"")
endif()
set(CMAKE_CXX_STANDARD 17)
set(BWAPI_ROOT "" CACHE PATH "BWAPI 4.4.0 SDK")
set(BWAPI_LIB "" CACHE FILEPATH "BWAPI.lib")
set(BWEM_ROOT "" CACHE PATH "BWEM source root")
file(GLOB BWEM_SOURCES "${BWEM_ROOT}/src/*.cpp")
if(NOT EXISTS "${BWAPI_ROOT}/include/BWAPI.h" OR NOT EXISTS "${BWAPI_LIB}" OR NOT BWEM_SOURCES)
  message(FATAL_ERROR "Set BWAPI_ROOT, BWAPI_LIB and BWEM_ROOT")
endif()
add_library(MacroBot SHARED MacroBot.cpp Candidate.def ${BWEM_SOURCES})
target_compile_definitions(MacroBot PRIVATE NOMINMAX WIN32 NDEBUG _CRT_SECURE_NO_WARNINGS)
target_compile_options(MacroBot PRIVATE /bigobj /utf-8)
target_include_directories(MacroBot PRIVATE "${BWAPI_ROOT}/include" "${BWEM_ROOT}/include")
target_link_libraries(MacroBot "${BWAPI_LIB}")
"""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def compile_macrobot(source: str, output_dll: Path, *, repo_root: Path,
                     build_dir: Path | None = None, timeout: int = COMPILE_TIMEOUT_SECONDS) -> dict:
    """Compile MacroBot.cpp in an isolated incremental CMake/MSVC directory and emit output_dll."""
    repo_root = Path(repo_root).resolve()
    settings_path = repo_root / "local.settings.json"
    if not settings_path.is_file():
        raise CompileError(f"missing {settings_path}")
    settings = json.loads(settings_path.read_text(encoding="utf-8-sig")).get("macrobot_build")
    if not settings:
        raise CompileError("local.settings.json has no macrobot_build entry")

    work = Path(build_dir) if build_dir is not None else repo_root / "build/macrobot_rsi"
    src_dir = work / "src"
    out_dir = work / "out"
    src_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    cmake_file = src_dir / "CMakeLists.txt"
    desired_cmake = _isolated_cmake_text()
    if not cmake_file.is_file() or cmake_file.read_text(encoding="utf-8") != desired_cmake:
        cmake_file.write_text(desired_cmake, encoding="utf-8", newline="\n")
    def_file = src_dir / "Candidate.def"
    if not def_file.is_file():
        shutil.copy2(repo_root / "app/Candidate.def", def_file)

    cpp_file = src_dir / "MacroBot.cpp"
    cpp_file.write_text(source, encoding="utf-8", newline="\n")

    started = time.monotonic()
    cmake = settings["cmake"]

    # Configure only once so BWEM .obj files remain cached for fast incremental builds.
    if not (out_dir / "MacroBot.sln").is_file():
        cfg_cmd = [
            cmake, str(src_dir),
            "-G", settings["generator"], "-A", "Win32",
            f"-DBWAPI_ROOT={settings['bwapi_root']}",
            f"-DBWAPI_LIB={settings['bwapi_lib']}",
            f"-DBWEM_ROOT={settings['bwem_root']}",
        ]
        if settings.get("generator_instance"):
            cfg_cmd.append(f"-DCMAKE_GENERATOR_INSTANCE={settings['generator_instance']}")
        try:
            proc = subprocess.run(cfg_cmd, cwd=out_dir, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise CompileError(f"CMake configure exceeded {timeout}s") from None
        if proc.returncode:
            err = _extract_compiler_errors(_decode_process_output(proc.stdout + b"\n" + proc.stderr))
            raise CompileError(f"CMake configure failed (rc={proc.returncode}):\n{err}")

    # Always remove MacroBot.obj and previous Release/MacroBot.dll to guarantee MSBuild never
    # skips recompiling due to coarse filesystem timestamps, while keeping BWEM objects cached.
    for stale in out_dir.rglob("MacroBot.obj"):
        stale.unlink(missing_ok=True)
    built_dll = out_dir / "Release/MacroBot.dll"
    built_dll.unlink(missing_ok=True)

    build_cmd = [cmake, "--build", str(out_dir), "--config", "Release"]
    try:
        proc = subprocess.run(build_cmd, cwd=out_dir, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise CompileError(f"MSVC build exceeded {timeout}s") from None
    if proc.returncode or not built_dll.is_file():
        err = _extract_compiler_errors(_decode_process_output(proc.stdout + b"\n" + proc.stderr))
        raise CompileError(f"MSVC build failed (rc={proc.returncode}):\n{err}")

    exports = verify_pe_exports(built_dll)
    output_dll = Path(output_dll)
    output_dll.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=output_dll.parent, delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        shutil.copy2(built_dll, tmp_path)
        tmp_path.replace(output_dll)
    finally:
        tmp_path.unlink(missing_ok=True)

    return {
        "dll": str(output_dll),
        "dll_sha256": _sha256_file(output_dll),
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "seconds": round(time.monotonic() - started, 3),
        "exports": exports,
    }


def seed_v2_macrobot(base_source: str) -> str:
    """Derive the cleaned RSI v2 seed MacroBot.cpp from app/MacroBot.cpp without mutating the repo template.

    Fixes two verified defects observed in Batch E:
    1. Proactive Forge/Cannon no longer preempts Assimilator + Cybernetics Core (only builds after core > 0;
       reactive detection on actual cloak/tech sightings remains top priority).
    2. Failed Nexus expansion tiles get a 1200-frame cooldown so Unbuildable_Location cannot deadlock build().
    """
    src = base_source

    # 1. Add failedExpansions member to AstraBot.
    marker_member = "  int cannonBlockedUntil = -1;\n"
    replacement_member = (
        "  int cannonBlockedUntil = -1;\n"
        "  std::map<std::pair<int,int>, int> failedExpansions;\n"
    )
    if marker_member not in src:
        raise ValueError("seed_v2_macrobot: expected cannonBlockedUntil member")
    src = src.replace(marker_member, replacement_member, 1)

    # 2. Skip cooled-down failed expansion tiles in expansion().
    marker_exp = "      if(occupied) continue;\n"
    replacement_exp = (
        "      auto failIt = failedExpansions.find({t.x, t.y});\n"
        "      if(failIt != failedExpansions.end() && frame < failIt->second) continue;\n"
        "      if(occupied) continue;\n"
    )
    if marker_exp not in src:
        raise ValueError("seed_v2_macrobot: expected occupied check in expansion()")
    src = src.replace(marker_exp, replacement_exp, 1)

    # 3. Record failed Nexus build attempts in build().
    marker_fail = (
        "        if(!started && planned==Protoss_Photon_Cannon) {\n"
        "          cannonBlockedUntil=frame+cannonRetryDelay;\n"
        "          trace<<\"{\\\"event\\\":\\\"cannon_skip\\\",\\\"frame\\\":\"<<frame<<\",\\\"reason\\\":\\\"plan_failed\\\",\\\"until\\\":\"<<cannonBlockedUntil<<\"}\\n\";\n"
        "        }\n"
    )
    replacement_fail = (
        marker_fail +
        "        if(!started && planned==Protoss_Nexus && buildTile.isValid()) {\n"
        "          failedExpansions[{buildTile.x, buildTile.y}] = frame + 1200;\n"
        "        }\n"
    )
    if marker_fail not in src:
        raise ValueError("seed_v2_macrobot: expected cannon_skip block in build()")
    src = src.replace(marker_fail, replacement_fail, 1)

    # 4. Move proactive Forge/Cannon checks below Cybernetics Core so proactive cannons never delay Dragoons.
    old_block = (
        "    bool detection=cloakSeen || cloakTechSeen;\n"
        "    /*+*/if(early(cfg.proactive_cannon_frame) && count(Protoss_Forge)==0) return Protoss_Forge;/*-*/\n"
        "    /*+*/if(early(cfg.proactive_cannon_frame) && count(Protoss_Forge,true) && count(Protoss_Photon_Cannon)<int(nexuses.size())*cfg.cannons_per_base && frame>=cannonBlockedUntil) return Protoss_Photon_Cannon;/*-*/\n"
        "    if(detection && count(Protoss_Forge)==0) return Protoss_Forge;\n"
        "    if(detection && count(Protoss_Forge,true) && count(Protoss_Photon_Cannon)<int(nexuses.size())/*+*/*cfg.cannons_per_base/*-*/ && frame>=cannonBlockedUntil) return Protoss_Photon_Cannon;\n"
        "    /*+*/if(count(Protoss_Assimilator)==0 && early(cfg.proactive_observer_frame)) return Protoss_Assimilator;/*-*/\n"
        "    if(count(Protoss_Assimilator)==0 && (count(Protoss_Zealot)>=cfg.gas1_zealots || frame>cfg.gas1_frame || detection)) return Protoss_Assimilator;\n"
        "    if(count(Protoss_Assimilator)>0 && core==0) return Protoss_Cybernetics_Core;\n"
    )
    new_block = (
        "    bool detection=cloakSeen || cloakTechSeen;\n"
        "    if(detection && count(Protoss_Forge)==0) return Protoss_Forge;\n"
        "    if(detection && count(Protoss_Forge,true) && count(Protoss_Photon_Cannon)<int(nexuses.size())*cfg.cannons_per_base && frame>=cannonBlockedUntil) return Protoss_Photon_Cannon;\n"
        "    if(count(Protoss_Assimilator)==0 && early(cfg.proactive_observer_frame)) return Protoss_Assimilator;\n"
        "    if(count(Protoss_Assimilator)==0 && (count(Protoss_Zealot)>=cfg.gas1_zealots || frame>cfg.gas1_frame || detection)) return Protoss_Assimilator;\n"
        "    if(count(Protoss_Assimilator)>0 && core==0) return Protoss_Cybernetics_Core;\n"
        "    if(core>0 && early(cfg.proactive_cannon_frame) && count(Protoss_Forge)==0) return Protoss_Forge;\n"
        "    if(core>0 && early(cfg.proactive_cannon_frame) && count(Protoss_Forge,true) && count(Protoss_Photon_Cannon)<int(nexuses.size())*cfg.cannons_per_base && frame>=cannonBlockedUntil) return Protoss_Photon_Cannon;\n"
    )
    if old_block not in src:
        raise ValueError("seed_v2_macrobot: expected nextBuilding priority block")
    src = src.replace(old_block, new_block, 1)

    check_source(src)
    return src
