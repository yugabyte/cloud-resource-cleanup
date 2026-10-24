"""ARCH-006: constructs the project forbids everywhere in `crc/` and `crc.py`."""

import ast
from pathlib import Path

from tests.archtest._common import iter_python_files, parse_module, relpath, violation

RULE = "ARCH-006"
BANNED_MODULES = frozenset({"asyncio", "pydantic", "httpx", "tenacity", "threading"})
_REMEDIATION = (
    "CRC is a synchronous CLI with no new dependencies (AGENTS.md hard rule 7). Rewrite the "
    "code with plain synchronous calls, dataclasses and the standard library: use a loop with "
    "`time.sleep` instead of `tenacity`, `requests` or the cloud SDK instead of `httpx`, a "
    "dataclass with explicit checks instead of `pydantic`, and pass state through constructors "
    "instead of a `threading` lock or singleton. Do not add the module to a skip list."
)


def _root_module(name: str) -> str:
    return name.split(".")[0]


def _construct(node: ast.AST) -> str | None:
    if isinstance(node, ast.AsyncFunctionDef):
        return "async def"
    if isinstance(node, ast.Await):
        return "await"
    if isinstance(node, ast.AsyncFor | ast.AsyncWith):
        return "async " + ("for" if isinstance(node, ast.AsyncFor) else "with")
    if isinstance(node, ast.Import):
        for alias in node.names:
            if _root_module(alias.name) in BANNED_MODULES:
                return f"import {_root_module(alias.name)}"
    if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
        root_name = _root_module(node.module)
        return f"import {root_name}" if root_name in BANNED_MODULES else None
    return None


def find_banned_constructs(tree: ast.Module) -> list[tuple[int, str]]:
    """Return `(line, construct)` for every banned construct in a module."""
    found = [(getattr(n, "lineno", 0), c) for n in ast.walk(tree) if (c := _construct(n))]
    return sorted(found)


def check_banned_constructs(root: Path) -> list[str]:
    files = iter_python_files(root / "crc")
    if (root / "crc.py").is_file():
        files.append(root / "crc.py")
    problems: list[str] = []
    for path in files:
        for line, construct in find_banned_constructs(parse_module(path)):
            problems.append(
                violation(RULE, f"{relpath(root, path)}:{line} uses `{construct}`", _REMEDIATION)
            )
    return problems
