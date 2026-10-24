"""ARCH-012 check and self-tests."""

from pathlib import Path

import pytest

from tests.archtest._common import REPO_ROOT
from tests.archtest.todo_bugs import check_todo_bugs, tracked_bug_rows

MIN_OPEN = 5  # the register always holds open medium/low rows; 0 means the parser broke
HEADER = "| ID | Title | Loc | Main | Effect | Risk | Found | Status |\n|---|---|---|---|---|---|---|---|\n"  # noqa: E501
WORD = "TO" + "DO"
PREFIX = WORD + "(BUG-"


def _row(number: str, risk: str, status: str) -> str:
    return f"| BUG-{number} | title | `x.py:1` | yes | cosmetic | {risk} | R1 | {status} |\n"


def _project(root: Path, *rows: str) -> None:
    (root / "docs" / "refactor").mkdir(parents=True)
    (root / "docs" / "refactor" / "BUGS.md").write_text(HEADER + "".join(rows), encoding="utf-8")
    (root / "crc").mkdir()


def _write(root: Path, name: str, text: str) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _todo(number: str) -> str:
    return f"# {PREFIX}{number}): a title\n#  labels: bug, risk-high, crc\n"


def test_arch012_repository_todos_match_the_register() -> None:
    problems = check_todo_bugs(REPO_ROOT)
    assert not problems, "\n\n".join(problems)


def test_arch012_register_requires_the_open_critical_and_high_rows() -> None:
    open_ids, required = tracked_bug_rows(REPO_ROOT)
    # Every critical/high row may be fixed; the open medium/low rows prove the register parses.
    assert len(open_ids) >= MIN_OPEN
    assert required <= open_ids


def test_arch012_selftest_accepts_matching_todo(tmp_path: Path) -> None:
    _project(tmp_path, _row("001", "high", "open"), _row("002", "medium", "open"))
    _write(tmp_path, "crc/aws/mod.py", _todo("001"))
    assert check_todo_bugs(tmp_path) == []


@pytest.mark.parametrize("risk", ["critical", "high"])
@pytest.mark.parametrize("status", ["open", "partly fixed in FIX-X (rest open)"])
def test_arch012_selftest_fires_on_missing_todo(tmp_path: Path, risk: str, status: str) -> None:
    _project(tmp_path, _row("001", risk, status))
    messages = check_todo_bugs(tmp_path)
    assert len(messages) == 1
    assert messages[0].startswith("ARCH-012: BUG-001 is open or partly fixed")
    assert "\n\nREMEDIATION: " in messages[0]


@pytest.mark.parametrize("risk", ["medium", "low"])
def test_arch012_selftest_does_not_require_todo_for_medium_and_low(
    tmp_path: Path, risk: str
) -> None:
    _project(tmp_path, _row("001", risk, "open"))
    assert check_todo_bugs(tmp_path) == []


def test_arch012_selftest_does_not_require_todo_for_fixed_critical(tmp_path: Path) -> None:
    _project(tmp_path, _row("001", "critical", "fixed in FIX-X"))
    assert check_todo_bugs(tmp_path) == []


@pytest.mark.parametrize("status", ["fixed in FIX-X", "not a bug", "wontfix"])
def test_arch012_selftest_fires_on_todo_for_closed_row(tmp_path: Path, status: str) -> None:
    _project(tmp_path, _row("001", "high", status))
    _write(tmp_path, "crc/aws/mod.py", _todo("001"))
    messages = check_todo_bugs(tmp_path)
    assert len(messages) == 1
    assert messages[0].startswith("ARCH-012: crc/aws/mod.py:1 has TODO(BUG-001)")


def test_arch012_selftest_fires_on_todo_for_unknown_row(tmp_path: Path) -> None:
    _project(tmp_path, _row("001", "low", "open"))
    _write(tmp_path, "crc/aws/mod.py", _todo("999"))
    messages = check_todo_bugs(tmp_path)
    assert len(messages) == 1
    assert "BUG-" + "999 is not an open or partly fixed row" in messages[0]


@pytest.mark.parametrize("name", ["crc/aws/mod.py", "crc/aws/sub/mod.py", "crc/aws/README.md"])
@pytest.mark.parametrize(
    "text", [f"# {WORD} fix this\n", f"# {WORD}: fix this\n", f"# {PREFIX}1): x\n"]
)
def test_arch012_selftest_fires_on_todo_without_bug_id(
    tmp_path: Path, name: str, text: str
) -> None:
    _project(tmp_path, _row("001", "low", "open"))
    _write(tmp_path, name, text)
    messages = check_todo_bugs(tmp_path)
    assert len(messages) == 1
    assert messages[0].startswith(f"ARCH-012: {name}:1 has a TODO without a BUG id")
    assert "write the row first" in messages[0].lower()


def test_arch012_selftest_fires_on_duplicate_todo(tmp_path: Path) -> None:
    _project(tmp_path, _row("001", "high", "open"))
    _write(tmp_path, "crc/aws/a.py", _todo("001"))
    _write(tmp_path, "crc/aws/b.py", _todo("001"))
    messages = check_todo_bugs(tmp_path)
    assert len(messages) == 1
    assert "TODO(BUG-001) appears 2 times" in messages[0]


def test_arch012_selftest_ignores_files_outside_crc(tmp_path: Path) -> None:
    _project(tmp_path, _row("001", "low", "open"))
    _write(tmp_path, "tests/test_mod.py", f"# {WORD} later\n")
    _write(tmp_path, "docs/notes.md", f"{WORD} later\n")
    assert check_todo_bugs(tmp_path) == []
