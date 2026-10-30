"""ARCH-004 check and self-tests."""

from pathlib import Path

from tests.archtest._common import REPO_ROOT, load_legacy
from tests.archtest.sizes import (
    MAX_FUNCTION_LINES,
    MAX_LINES,
    check_function_sizes,
    check_sizes,
)

EXPECTED_LIMIT = 250
EXPECTED_FUNCTION_LIMIT = 60


def _write_lines(root: Path, name: str, count: int) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x = 1\n" * count, encoding="utf-8")


def test_arch004_files_are_at_most_250_lines() -> None:
    assert MAX_LINES == EXPECTED_LIMIT
    problems = check_sizes(REPO_ROOT, load_legacy(REPO_ROOT))
    assert not problems, "\n\n".join(problems)


def test_arch004_selftest_fires_on_long_file(tmp_path: Path) -> None:
    _write_lines(tmp_path, "crc/core/big.py", MAX_LINES + 1)
    messages = check_sizes(tmp_path, frozenset())
    assert len(messages) == 1
    assert messages[0].startswith(f"ARCH-004: crc/core/big.py has {MAX_LINES + 1} lines")
    assert "\n\nREMEDIATION: " in messages[0]


def test_arch004_selftest_allows_exactly_the_limit(tmp_path: Path) -> None:
    _write_lines(tmp_path, "crc/core/ok.py", MAX_LINES)
    assert check_sizes(tmp_path, frozenset()) == []


def test_arch004_selftest_skips_legacy_and_tests(tmp_path: Path) -> None:
    _write_lines(tmp_path, "crc/old.py", MAX_LINES + 50)
    _write_lines(tmp_path, "crc/core/test_big.py", MAX_LINES + 50)
    _write_lines(tmp_path, "tests/helper.py", MAX_LINES + 50)
    assert check_sizes(tmp_path, frozenset({"crc/old.py"})) == []


def _write_function(root: Path, name: str, body_lines: int) -> None:
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    body = "    x = 1\n" * body_lines
    target.write_text(f"@staticmethod\ndef f() -> None:\n{body}", encoding="utf-8")


def test_arch004_functions_are_at_most_60_lines() -> None:
    assert MAX_FUNCTION_LINES == EXPECTED_FUNCTION_LIMIT
    problems = check_function_sizes(REPO_ROOT, load_legacy(REPO_ROOT))
    assert not problems, "\n\n".join(problems)


def test_arch004_selftest_fires_on_long_function(tmp_path: Path) -> None:
    _write_function(tmp_path, "crc/core/long.py", MAX_FUNCTION_LINES)
    messages = check_function_sizes(tmp_path, frozenset())
    assert len(messages) == 1
    assert messages[0].startswith(
        f"ARCH-004: crc/core/long.py:2 f() has {MAX_FUNCTION_LINES + 1} lines"
    )
    assert "\n\nREMEDIATION: " in messages[0]


def test_arch004_selftest_allows_function_at_the_limit(tmp_path: Path) -> None:
    _write_function(tmp_path, "crc/core/ok.py", MAX_FUNCTION_LINES - 1)
    assert check_function_sizes(tmp_path, frozenset()) == []
