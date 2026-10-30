"""ARCH-004: file and function size limits for non-legacy, non-test sources."""

import ast
from pathlib import Path

from tests.archtest._common import non_legacy_files, relpath, violation

RULE = "ARCH-004"
MAX_LINES = 250
MAX_FUNCTION_LINES = 60


def check_sizes(root: Path, legacy: frozenset[str], max_lines: int = MAX_LINES) -> list[str]:
    problems: list[str] = []
    for path in non_legacy_files(root, legacy):
        if path.name == "conftest.py" or path.name.startswith("test_"):
            continue
        count = len(path.read_text(encoding="utf-8").splitlines())
        if count > max_lines:
            problems.append(
                violation(
                    RULE,
                    f"{relpath(root, path)} has {count} lines (limit {max_lines})",
                    "Split the module along a real seam: move cohesive helpers or DTOs into "
                    "their own module in the same package, update the package README "
                    "`## Public API`, and keep every behaviour and test unchanged. Do not "
                    "compress code or raise the limit.",
                )
            )
    return problems


def check_function_sizes(
    root: Path, legacy: frozenset[str], max_lines: int = MAX_FUNCTION_LINES
) -> list[str]:
    """Every function or method, decorators excluded, spans at most `max_lines` lines."""
    problems: list[str] = []
    for path in non_legacy_files(root, legacy):
        if path.name == "conftest.py" or path.name.startswith("test_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            count = (node.end_lineno or node.lineno) - node.lineno + 1
            if count > max_lines:
                problems.append(
                    violation(
                        RULE,
                        f"{relpath(root, path)}:{node.lineno} {node.name}() has {count} lines "
                        f"(limit {max_lines})",
                        "Extract cohesive steps into named helpers in the same module or "
                        "package, keep every behaviour and test unchanged. Do not compress "
                        "code or raise the limit.",
                    )
                )
    return problems
