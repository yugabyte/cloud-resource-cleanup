"""Shared helpers for the architecture tests (ARCH-xxx and SAF-LOCK)."""

import ast
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).resolve().parent / "legacy_baseline.txt"
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        "tests",
    }
)


def violation(rule: str, problem: str, remediation: str) -> str:
    """Format a failure: starts with the rule ID, ends with a REMEDIATION paragraph."""
    return f"{rule}: {problem}\n\nREMEDIATION: {remediation}"


def _pyproject(root: Path) -> dict[str, Any]:
    with (root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def ruff_exclude(root: Path) -> list[str]:
    """The ruff `extend-exclude` list, which is the legacy ratchet."""
    return _string_list(_pyproject(root).get("tool", {}).get("ruff", {}).get("extend-exclude"))


def mypy_override_modules(root: Path) -> list[str]:
    """Modules listed in `[[tool.mypy.overrides]]` blocks that set `ignore_errors`."""
    overrides = _pyproject(root).get("tool", {}).get("mypy", {}).get("overrides", [])
    modules: list[str] = []
    if isinstance(overrides, list):
        for block in overrides:
            if isinstance(block, dict) and block.get("ignore_errors") is True:
                modules.extend(_string_list(block.get("module")))
    return modules


def load_legacy(root: Path) -> frozenset[str]:
    """Posix paths (relative to `root`) of every legacy file."""
    return frozenset(ruff_exclude(root))


def relpath(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def iter_python_files(directory: Path) -> list[Path]:
    """All `.py` files under `directory`, skipping caches and virtualenvs."""
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.rglob("*.py")
        if not SKIP_DIRS.intersection(path.relative_to(directory).parts[:-1])
    )


def non_legacy_files(root: Path, legacy: frozenset[str], under: str = "") -> list[Path]:
    """Non-legacy `.py` files below `root/under`, excluding `tests/`."""
    base = root / under if under else root
    return [path for path in iter_python_files(base) if relpath(root, path) not in legacy]


def parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
