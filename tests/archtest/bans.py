"""ARCH-003: banned constructs in cloud provider packages, crc/core, crc/cli and crc/notify."""

import ast
from collections.abc import Mapping
from pathlib import Path

from tests.archtest._common import non_legacy_files, parse_module, relpath, violation

RULE = "ARCH-003"
CLOUD_PACKAGES = ("crc/aws", "crc/azu", "crc/gcp", "crc/oci")
_ALL_KINDS = frozenset({"re", "clock", "clock_ref", "env", "print", "sys.exit"})
# `clock_ref`: the wall clock passed as a value (`default_factory=datetime.now`, `now=time.time`,
# `x = datetime.utcnow`) instead of called. crc/core, crc/cli and the cloud packages are checked.

# Banned kinds per package. crc/core is pure: no clock (calls or value references), no
# environment reads, no console output, no exits (`re` stays legal: crc/core/policy.py owns the
# regexes). crc/cli is the boundary and may read the environment, print, call the clock and
# exit; it must not compile regexes or pass the clock around as a value. crc/notify gets the
# configuration injected: no env, exit or `re`; console output and timestamps are legitimate.
PACKAGE_BANS: Mapping[str, frozenset[str]] = {
    **dict.fromkeys(CLOUD_PACKAGES, _ALL_KINDS),
    "crc/core": frozenset({"clock", "clock_ref", "env", "print", "sys.exit"}),
    "crc/cli": frozenset({"re", "clock_ref"}),
    "crc/notify": frozenset({"re", "env", "sys.exit"}),
}

# Explicit exceptions: repo-relative path -> banned kinds that file may use
# ("re", "clock", "clock_ref", "env", "print", "sys.exit"). Only the repository owner adds entries.
ARCH003_ALLOWLIST: Mapping[str, frozenset[str]] = {
    # Credential loading, owner-approved 2026-10-21: `Base` reads the credentials from the
    # environment when an Azure or OCI cleaner is built, exactly as on main. Reading them in
    # crc/cli instead would move the KeyError for a missing variable to startup and break
    # single-cloud Jenkins jobs that do not set the other clouds' variables.
    "crc/azu/_base.py": frozenset({"env"}),
    "crc/oci/_base.py": frozenset({"env"}),
}

_CLOCK_CALLS = frozenset(
    {"datetime.now", "datetime.utcnow", "datetime.datetime.now", "datetime.datetime.utcnow"}
)
_CLOCK_REFS = _CLOCK_CALLS | {"time.time"}
_FROM_IMPORT_BANS = {
    ("time", "time"): "clock",
    ("os", "environ"): "env",
    ("os", "getenv"): "env",
    ("sys", "exit"): "sys.exit",
}
_REMEDIATION = {
    "re": "Do not import `re` here. Compile and match regexes in "
    "crc/core/policy.py (pure functions on DTOs) and pass the result in.",
    "clock": "Do not read the wall clock for age decisions. Take `now` as a parameter and "
    "decide in crc/core/age.py or crc/core/policy.py. `time.monotonic` and `time.sleep` "
    "are fine for polling and waits.",
    "clock_ref": "Do not pass the wall clock around as a value (default argument, attribute, "
    "factory). Take `now` as a parameter; only the CLI boundary calls `datetime.now(UTC)`.",
    "env": "Do not read os.environ or os.getenv here. Parse env vars once at the "
    "CLI boundary and inject the value through the constructor.",
    "print": "Do not call print. Use the injected logger.",
    "sys.exit": "Do not call sys.exit here. Raise a specific exception with "
    "context and let the CLI boundary decide the exit code.",
}


def _dotted(node: ast.expr) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _import_kinds(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return ["re" for alias in node.names if alias.name == "re"]
    if node.module == "re":
        return ["re"]
    kinds = [_FROM_IMPORT_BANS.get((node.module or "", alias.name)) for alias in node.names]
    return [kind for kind in kinds if kind is not None]


def _expr_kind(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute):
        return "env" if _dotted(node) in {"os.environ", "os.getenv"} else None
    if not isinstance(node, ast.Call):
        return None
    if isinstance(node.func, ast.Name):
        return "print" if node.func.id == "print" else None
    dotted = _dotted(node.func)
    if dotted in _CLOCK_CALLS or dotted == "time.time":
        return "clock"
    return "sys.exit" if dotted == "sys.exit" else None


def _clock_aliases(tree: ast.Module) -> dict[str, str]:
    """Local name -> full name for the clock modules and names a module imports."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"datetime", "time"}:
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in {"datetime", "time"}:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _clock_refs(tree: ast.Module) -> list[int]:
    """Lines where a clock function is referenced without being called."""
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    aliases = _clock_aliases(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if id(node) in called or not isinstance(node, ast.Attribute | ast.Name):
            continue
        if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load):
            continue
        dotted = _dotted(node)
        if dotted is None:
            continue
        head, _, rest = dotted.partition(".")
        full = ".".join(filter(None, (aliases.get(head, head), rest)))
        if dotted in _CLOCK_REFS or full in _CLOCK_REFS:
            lines.append(node.lineno)
    return lines


def find_bans(tree: ast.Module) -> list[tuple[int, str]]:
    """Return `(line, kind)` for every banned construct in a module."""
    found: list[tuple[int, str]] = [(line, "clock_ref") for line in _clock_refs(tree)]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom):
            found.extend((node.lineno, kind) for kind in _import_kinds(node))
        else:
            kind = _expr_kind(node)
            if kind is not None:
                found.append((getattr(node, "lineno", 0), kind))
    return sorted(found)


def check_bans(
    root: Path,
    legacy: frozenset[str],
    allowlist: Mapping[str, frozenset[str]] = ARCH003_ALLOWLIST,
) -> list[str]:
    problems: list[str] = []
    for package, banned in PACKAGE_BANS.items():
        for path in non_legacy_files(root, legacy, package):
            rel = relpath(root, path)
            allowed = allowlist.get(rel, frozenset())
            for line, kind in find_bans(parse_module(path)):
                if kind in banned and kind not in allowed:
                    problems.append(
                        violation(RULE, f"{rel}:{line} uses banned `{kind}`", _REMEDIATION[kind])
                    )
    return problems
