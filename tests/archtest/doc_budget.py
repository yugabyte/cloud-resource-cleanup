"""ARCH-010: the agent entry documents stay short enough to read in full."""

from pathlib import Path

from tests.archtest._common import relpath, violation

RULE = "ARCH-010"
AGENTS_PATH = "AGENTS.md"
MAX_AGENTS_LINES = 120
MAX_RULE_LINES = 40
MAX_SKILL_LINES = 150
_REMEDIATION = (
    "Agents read these files in full on every task, so their length is a budget. Cut text that "
    "a check already enforces (point to tests/archtest/README.md instead), text that can be "
    "inferred from the code, and anything an agent needs only for one task: move that into a "
    "linked file one hop away. Do not raise the limit and do not delete a rule that no check "
    "enforces."
)


def _budgeted(root: Path) -> list[tuple[Path, int]]:
    files: list[tuple[Path, int]] = [(root / AGENTS_PATH, MAX_AGENTS_LINES)]
    files += [(path, MAX_RULE_LINES) for path in sorted((root / ".cursor" / "rules").glob("*.mdc"))]
    skills = root / ".cursor" / "skills"
    files += [(path, MAX_SKILL_LINES) for path in sorted(skills.glob("*/SKILL.md"))]
    return files


def check_doc_budget(root: Path) -> list[str]:
    """Violations for `AGENTS.md`, `.cursor/rules/*.mdc` and `.cursor/skills/*/SKILL.md`."""
    problems: list[str] = []
    for path, limit in _budgeted(root):
        if not path.is_file():
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > limit:
            problems.append(
                violation(
                    RULE,
                    f"{relpath(root, path)} has {lines} lines (limit {limit})",
                    _REMEDIATION,
                )
            )
    return problems
