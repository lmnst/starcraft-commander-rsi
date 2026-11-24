"""Restricted policy loader. Python checks limit capabilities, not OS permissions."""

import ast
import builtins
import importlib
import sys
from types import ModuleType, SimpleNamespace
from uuid import uuid4


# Import names follow §6.2. Expose only the pure parts needed by policy code.
MODULE_EXPORTS = {
    "math": tuple(name for name in dir(importlib.import_module("math")) if not name.startswith("_")),
    "collections": ("Counter", "defaultdict", "deque", "namedtuple", "OrderedDict"),
    "statistics": ("mean", "fmean", "median", "median_low", "median_high", "median_grouped",
                   "mode", "multimode", "quantiles", "variance", "pvariance", "stdev", "pstdev",
                   "covariance", "correlation", "linear_regression"),
    "itertools": ("accumulate", "chain", "combinations", "combinations_with_replacement",
                  "compress", "count", "cycle", "dropwhile", "filterfalse", "groupby",
                  "islice", "pairwise", "permutations", "product", "repeat", "starmap",
                  "takewhile", "tee", "zip_longest"),
    "functools": ("cmp_to_key", "partial", "reduce"),
    "copy": ("copy", "deepcopy"),
    "typing": ("Any", "Callable", "Dict", "Iterable", "Iterator", "List", "Literal", "Mapping",
               "Optional", "Sequence", "Set", "Tuple", "Union", "cast"),
    "dataclasses": ("dataclass", "field", "fields", "asdict", "astuple", "replace", "is_dataclass"),
}
FORBIDDEN_NAMES = {
    "open", "eval", "exec", "compile", "getattr", "setattr", "delattr", "globals", "locals",
    "vars", "input", "breakpoint", "dir",
}
FORBIDDEN_ATTRIBUTES = FORBIDDEN_NAMES | {
    "mro", "format", "format_map", "with_traceback", "f_globals", "f_locals", "f_builtins",
    "f_back", "f_code", "f_trace", "gi_frame", "gi_code", "ag_frame", "ag_code", "cr_frame",
    "cr_code", "tb_frame", "tb_next",
}
REQUIRED_EXPORTS = {
    "observer.py": {"summarize", "should_wake_llm"},
    "tools.py": {"TOOLS"},
    "rules.py": {"decide_reflex"},
}
SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in (
        "abs", "all", "any", "bool", "bytes", "dict", "divmod", "enumerate", "filter", "float",
        "frozenset", "hash", "int", "isinstance", "issubclass", "iter", "len", "list", "map",
        "max", "min", "next", "pow", "range", "repr", "reversed", "round", "set", "slice",
        "sorted", "str", "sum", "tuple", "zip", "classmethod", "staticmethod", "property",
        "super", "object", "Exception", "ArithmeticError", "IndexError", "KeyError",
        "RuntimeError", "StopIteration", "TypeError", "ValueError", "ZeroDivisionError",
        "__build_class__",
    )
}


def check_source(filename, source, required=None):
    """Parse policy source, reject forbidden capabilities and check named exports."""
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in MODULE_EXPORTS:
                    raise ValueError(f"{filename}:{node.lineno}: forbidden import {alias.name}")
                names.append(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in MODULE_EXPORTS:
                raise ValueError(f"{filename}:{node.lineno}: forbidden import {node.module!r}")
            for alias in node.names:
                if alias.name not in MODULE_EXPORTS[node.module]:
                    raise ValueError(f"{filename}:{node.lineno}: unavailable import {alias.name}")
                names.append(alias.asname or alias.name)
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_ATTRIBUTES:
                raise ValueError(f"{filename}:{node.lineno}: forbidden attribute {node.attr}")
            names.append(node.attr)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Global):
            raise ValueError(f"{filename}:{node.lineno}: global mutation is not allowed")
        for name in names:
            if name.startswith("__") or name in FORBIDDEN_NAMES:
                raise ValueError(f"{filename}:{node.lineno}: forbidden name {name}")

    exports = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            exports.add(node.name)
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        exports.update(target.id for target in targets if isinstance(target, ast.Name))
    missing = (REQUIRED_EXPORTS[filename] if required is None else required) - exports
    if missing:
        raise ValueError(f"{filename}: missing exports {', '.join(sorted(missing))}")
    # Compilation catches contexts that parse as AST but are invalid Python.
    return compile(tree, filename, "exec", dont_inherit=True)


def _restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name not in MODULE_EXPORTS:
        raise ImportError(f"unavailable import {name!r}")
    module = importlib.import_module(name)
    return SimpleNamespace(**{item: getattr(module, item) for item in MODULE_EXPORTS[name]})


def _module(code, stem):
    module = ModuleType(f"_rsi_{stem}_{uuid4().hex}")
    module.__dict__["__builtins__"] = {**SAFE_BUILTINS, "__import__": _restricted_import}
    # dataclass annotation resolution looks up the defining module during decoration.
    sys.modules[module.__name__] = module
    try:
        exec(code, module.__dict__)
    finally:
        del sys.modules[module.__name__]
    return module


def load_analysis(source):
    """Load review analysis code under the policy restrictions and return its analyze function."""
    module = _module(check_source("analysis.py", source, {"analyze"}), "analysis")
    if not callable(module.__dict__.get("analyze")):
        raise ValueError("analysis.py: analyze must be a function")
    return module.__dict__["analyze"]


def load_sources(sources):
    """Load scanned modules. Use a killable process for untrusted execution deadlines."""
    modules = {}
    for filename in REQUIRED_EXPORTS:
        code = check_source(filename, sources[filename])
        stem = filename.removesuffix(".py")
        module = ModuleType(f"_rsi_{stem}_{uuid4().hex}")
        module.__dict__["__builtins__"] = {**SAFE_BUILTINS, "__import__": _restricted_import}
        # dataclass annotation resolution looks up the defining module during decoration.
        sys.modules[module.__name__] = module
        try:
            exec(code, module.__dict__)
        except Exception as error:
            raise ValueError(f"{filename} load: {type(error).__name__}: {error}") from error
        finally:
            del sys.modules[module.__name__]
        modules[stem] = module
    return SimpleNamespace(**modules)


def load_version(path):
    """Load observer, tools and rules for a version installed by L0."""
    return load_sources({
        filename: (path / filename).read_text(encoding="utf-8") for filename in REQUIRED_EXPORTS
    })
