"""ARCH-001: the legacy ratchet only shrinks."""

from pathlib import Path

from tests.archtest._common import (
    BASELINE_PATH,
    mypy_override_modules,
    ruff_exclude,
    violation,
)

RULE = "ARCH-001"
MYPY_PREFIXES = ("crc/", "tests/")
GLOB_CHARS = frozenset("*?[]")


def read_baseline(path: Path) -> frozenset[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return frozenset(line.strip() for line in lines if line.strip() and not line.startswith("#"))


def module_name(path: str) -> str:
    """Map `crc/aws/disk.py` to `crc.aws.disk` and `crc/aws/__init__.py` to `crc.aws`."""
    dotted = path.removesuffix(".py").replace("/", ".")
    return dotted.removesuffix(".__init__")


def check_ratchet(root: Path, baseline_path: Path = BASELINE_PATH) -> list[str]:
    baseline = read_baseline(baseline_path)
    excluded = ruff_exclude(root)
    problems: list[str] = []

    added = sorted(set(excluded) - baseline)
    if added:
        problems.append(
            violation(
                RULE,
                f"ruff extend-exclude gained entries not in legacy_baseline.txt: {added}",
                "Delete these entries from `extend-exclude` and from the mypy override list in "
                "pyproject.toml, then make the files pass ruff and mypy strict. The ratchet "
                "never grows. Never edit tests/archtest/legacy_baseline.txt to admit a file.",
            )
        )

    malformed = sorted(e for e in excluded if not e.endswith(".py") or GLOB_CHARS & set(e))
    if malformed:
        problems.append(
            violation(
                RULE,
                f"extend-exclude entries must be explicit .py file paths, got: {malformed}",
                "Replace each directory or glob entry with nothing: refactor the files so they "
                "pass ruff and mypy, and remove them from the ratchet. Do not exempt trees.",
            )
        )

    expected = {module_name(e) for e in excluded if e.startswith(MYPY_PREFIXES)}
    actual = set(mypy_override_modules(root))
    if expected != actual:
        problems.append(
            violation(
                RULE,
                "mypy override modules differ from ruff extend-exclude: "
                f"only in ruff {sorted(expected - actual)}, "
                f"only in mypy {sorted(actual - expected)}",
                "Keep both lists identical file for file. When refactoring a module remove it "
                "from ruff `extend-exclude` and from the `[[tool.mypy.overrides]]` module list "
                "in the same change (crc.py is never type-checked, so it has no mypy entry).",
            )
        )
    return problems
