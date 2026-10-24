"""ARCH-009 check and self-tests."""

from pathlib import Path

import pytest

from tests.archtest._common import REPO_ROOT
from tests.archtest.bug_refs import check_bug_refs, known_bug_ids

MIN_ROWS = 100
KNOWN = "BUG-" + "001"
UNKNOWN = "BUG-" + "999"
BUGS_TABLE = f"| ID | Title |\n|----|-------|\n| {KNOWN} | something | x |\n"


def _project(root: Path) -> None:
    (root / "docs" / "refactor").mkdir(parents=True)
    (root / "docs" / "refactor" / "BUGS.md").write_text(BUGS_TABLE, encoding="utf-8")


def _write(root: Path, name: str, text: str) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def test_arch009_every_bug_reference_has_a_row() -> None:
    problems = check_bug_refs(REPO_ROOT)
    assert not problems, "\n\n".join(problems)


def test_arch009_bugs_register_is_parsed() -> None:
    assert len(known_bug_ids(REPO_ROOT)) > MIN_ROWS


@pytest.mark.parametrize(
    "name", ["crc/aws/mod.py", "tests/test_mod.py", "crc/aws/README.md", "crc/cli/sub/mod.py"]
)
def test_arch009_selftest_fires_on_unknown_bug(tmp_path: Path, name: str) -> None:
    _project(tmp_path)
    _write(tmp_path, name, f"# see {UNKNOWN}\n")
    messages = check_bug_refs(tmp_path)
    assert len(messages) == 1
    assert messages[0].startswith(f"ARCH-009: {name}:1 mentions {UNKNOWN}")
    assert "\n\nREMEDIATION: " in messages[0]


@pytest.mark.parametrize("short", ["BUG-" + "1", "BUG-" + "04"])
def test_arch009_selftest_fires_on_short_id_typo(tmp_path: Path, short: str) -> None:
    _project(tmp_path)
    _write(tmp_path, "crc/aws/mod.py", f"# see {short}\n")
    messages = check_bug_refs(tmp_path)
    assert len(messages) == 1
    assert f"mentions {short}," in messages[0]


def test_arch009_selftest_accepts_known_bug(tmp_path: Path) -> None:
    _project(tmp_path)
    _write(tmp_path, "crc/aws/mod.py", f'"""Fixes {KNOWN} and mentions {KNOWN}."""\n')
    _write(tmp_path, "tests/test_mod.py", f"def test_x() -> None:  # {KNOWN}\n    pass\n")
    assert check_bug_refs(tmp_path) == []


def test_arch009_selftest_ignores_files_outside_crc_and_tests(tmp_path: Path) -> None:
    _project(tmp_path)
    _write(tmp_path, "docs/other.md", f"{UNKNOWN}\n")
    assert check_bug_refs(tmp_path) == []
