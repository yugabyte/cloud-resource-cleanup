"""ARCH-010 check and self-tests."""

from pathlib import Path

import pytest

from tests.archtest._common import REPO_ROOT
from tests.archtest.doc_budget import (
    MAX_AGENTS_LINES,
    MAX_RULE_LINES,
    MAX_SKILL_LINES,
    check_doc_budget,
)

CASES = [
    ("AGENTS.md", MAX_AGENTS_LINES),
    (".cursor/rules/10-x.mdc", MAX_RULE_LINES),
    (".cursor/skills/crc-x/SKILL.md", MAX_SKILL_LINES),
]


def _write_lines(root: Path, name: str, count: int) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("line\n" * count, encoding="utf-8")


def test_arch010_limits_are_the_agreed_budget() -> None:
    assert (MAX_AGENTS_LINES, MAX_RULE_LINES, MAX_SKILL_LINES) == (120, 40, 150)


def test_arch010_entry_documents_are_within_budget() -> None:
    assert (REPO_ROOT / "AGENTS.md").is_file()
    problems = check_doc_budget(REPO_ROOT)
    assert not problems, "\n\n".join(problems)


@pytest.mark.parametrize(("name", "limit"), CASES)
def test_arch010_selftest_fires_over_the_limit(tmp_path: Path, name: str, limit: int) -> None:
    _write_lines(tmp_path, name, limit + 1)
    messages = check_doc_budget(tmp_path)
    assert len(messages) == 1
    assert messages[0].startswith(f"ARCH-010: {name} has {limit + 1} lines (limit {limit})")
    assert "\n\nREMEDIATION: " in messages[0]


@pytest.mark.parametrize(("name", "limit"), CASES)
def test_arch010_selftest_allows_exactly_the_limit(tmp_path: Path, name: str, limit: int) -> None:
    _write_lines(tmp_path, name, limit)
    assert check_doc_budget(tmp_path) == []


def test_arch010_selftest_other_files_are_not_budgeted(tmp_path: Path) -> None:
    _write_lines(tmp_path, ".cursor/skills/crc-x/review.md", MAX_SKILL_LINES * 3)
    _write_lines(tmp_path, "ARCHITECTURE.md", MAX_AGENTS_LINES * 3)
    assert check_doc_budget(tmp_path) == []
