"""ARCH-008 self-tests (the real check is `make check`: `python -m tests.archtest.test_count`)."""

from pathlib import Path

from tests.archtest.test_count import (
    FLOOR_PATH,
    check_floor,
    parse_collected,
    raise_floor,
    read_floor,
)

TWO_TESTS = 2
FLOOR = 10
RAISED = 12
OUTPUT = "tests/test_a.py::test_one\ntests/test_a.py::test_two[x-1]\n\n2 tests collected in 0.01s\n"


def test_arch008_floor_file_holds_a_positive_integer() -> None:
    assert read_floor(FLOOR_PATH) > 0


def test_arch008_parses_collect_only_output() -> None:
    assert parse_collected(OUTPUT) == TWO_TESTS
    assert parse_collected("no tests collected in 0.01s\n") is None


def test_arch008_selftest_passes_at_or_above_floor() -> None:
    assert check_floor(10, 10) == []
    assert check_floor(11, 10) == []


def test_arch008_selftest_fires_below_floor() -> None:
    messages = check_floor(9, 10)
    assert len(messages) == 1
    assert messages[0].startswith("ARCH-008: 9 tests collected, below the committed floor of 10")
    assert "\n\nREMEDIATION: " in messages[0]


def test_arch008_selftest_floor_only_rises(tmp_path: Path) -> None:
    floor = tmp_path / "floor.txt"
    floor.write_text("10\n", encoding="utf-8")
    assert raise_floor(9, floor) is False
    assert raise_floor(10, floor) is False
    assert read_floor(floor) == FLOOR
    assert raise_floor(12, floor) is True
    assert read_floor(floor) == RAISED
