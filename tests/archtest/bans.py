"""ARCH-003: banned constructs in cloud provider packages."""

import ast
from collections.abc import Mapping
from pathlib import Path

from tests.archtest._common import non_legacy_files, parse_module, relpath, violation

RULE = "ARCH-003"
CLOUD_PACKAGES = ("crc/aws", "crc/azu", "crc/gcp", "crc/oci")

# Explicit exceptions: repo-relative path -> banned kinds that file may use.
# Kinds: "re", "clock", "env", "print", "sys.exit". Only the repository owner adds entries.
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
_FROM_IMPORT_BANS = {
    ("time", "time"): "clock",
    ("os", "environ"): "env",
    ("os", "getenv"): "env",
    ("sys", "exit"): "sys.exit",
}
_REMEDIATION = {
    "re": "Do not import `re` in cloud modules. Compile and match regexes in "
    "crc/core/policy.py (pure functions on DTOs) and pass the result in.",
    "clock": "Do not read the wall clock for age decisions. Take `now` as a parameter and "
    "decide in crc/core/age.py or crc/core/policy.py. `time.monotonic` and `time.sleep` "
    "are fine for polling and waits.",
    "env": "Do not read os.environ or os.getenv in cloud modules. Parse env vars once at the "
    "CLI boundary and inject the value through the constructor.",
    "print": "Do not call print. Use the injected logger.",
    "sys.exit": "Do not call sys.exit in cloud modules. Raise a specific exception with "
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


def find_bans(tree: ast.Module) -> list[tuple[int, str]]:
    """Return `(line, kind)` for every banned construct in a module."""
    found: list[tuple[int, str]] = []
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
    for package in CLOUD_PACKAGES:
        for path in non_legacy_files(root, legacy, package):
            rel = relpath(root, path)
            allowed = allowlist.get(rel, frozenset())
            for line, kind in find_bans(parse_module(path)):
                if kind not in allowed:
                    problems.append(
                        violation(RULE, f"{rel}:{line} uses banned `{kind}`", _REMEDIATION[kind])
                    )
    return problems
