"""ARCH-004 check and self-tests."""

from pathlib import Path

from tests.archtest._common import REPO_ROOT, load_legacy
from tests.archtest.sizes import MAX_LINES, check_sizes

EXPECTED_LIMIT = 400


def _write_lines(root: Path, name: str, count: int) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n" * count, encoding="utf-8")


def test_arch004_files_are_at_most_400_lines() -> None:
    assert MAX_LINES == EXPECTED_LIMIT
    problems = check_sizes(REPO_ROOT, load_legacy(REPO_ROOT))
    assert not problems, "\n\n".join(problems)


def test_arch004_selftest_fires_on_long_file(tmp_path: Path) -> None:
    _write_lines(tmp_path, "crc/core/big.py", MAX_LINES + 1)
    messages = check_sizes(tmp_path, frozenset())
    assert len(messages) == 1
    assert messages[0].startswith("ARCH-004: crc/core/big.py has 401 lines")
    assert "\n\nREMEDIATION: " in messages[0]


def test_arch004_selftest_allows_exactly_the_limit(tmp_path: Path) -> None:
    _write_lines(tmp_path, "crc/core/ok.py", MAX_LINES)
    assert check_sizes(tmp_path, frozenset()) == []


def test_arch004_selftest_skips_legacy_and_tests(tmp_path: Path) -> None:
    _write_lines(tmp_path, "crc/old.py", MAX_LINES + 50)
    _write_lines(tmp_path, "crc/core/test_big.py", MAX_LINES + 50)
    _write_lines(tmp_path, "tests/helper.py", MAX_LINES + 50)
    assert check_sizes(tmp_path, frozenset({"crc/old.py"})) == []
