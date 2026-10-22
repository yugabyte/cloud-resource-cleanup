"""ARCH-001 check and self-tests."""

from pathlib import Path

import pytest

from tests.archtest._common import REPO_ROOT, ruff_exclude
from tests.archtest.ratchet import check_ratchet, module_name, read_baseline


def _write_project(root: Path, ruff: list[str], mypy: list[str]) -> None:
    ruff_items = ", ".join(f'"{item}"' for item in ruff)
    mypy_items = ", ".join(f'"{item}"' for item in mypy)
    (root / "pyproject.toml").write_text(
        f"[tool.ruff]\nextend-exclude = [{ruff_items}]\n"
        f"[[tool.mypy.overrides]]\nmodule = [{mypy_items}]\nignore_errors = true\n",
        encoding="utf-8",
    )


def _write_baseline(path: Path, entries: list[str]) -> Path:
    path.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return path


def _assert_formatted(messages: list[str]) -> None:
    assert messages
    for message in messages:
        assert message.startswith("ARCH-001: ")
        assert "\n\nREMEDIATION: " in message


def test_arch001_ratchet_only_shrinks() -> None:
    problems = check_ratchet(REPO_ROOT)
    assert not problems, "\n\n".join(problems)


def test_arch001_baseline_is_not_empty_and_covers_pyproject() -> None:
    baseline = read_baseline(REPO_ROOT / "tests" / "archtest" / "legacy_baseline.txt")
    assert baseline
    assert set(ruff_exclude(REPO_ROOT)) <= baseline


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("crc/aws/disk.py", "crc.aws.disk"),
        ("crc/aws/__init__.py", "crc.aws"),
        ("crc/__init__.py", "crc"),
        ("tests/__init__.py", "tests"),
    ],
)
def test_arch001_module_name(path: str, expected: str) -> None:
    assert module_name(path) == expected


def test_arch001_selftest_passes_on_consistent_lists(tmp_path: Path) -> None:
    _write_project(tmp_path, ["crc.py", "crc/a.py"], ["crc.a"])
    baseline = _write_baseline(tmp_path / "base.txt", ["crc.py", "crc/a.py", "crc/b.py"])
    assert check_ratchet(tmp_path, baseline) == []


def test_arch001_selftest_fires_when_list_grows(tmp_path: Path) -> None:
    _write_project(tmp_path, ["crc/a.py", "crc/new.py"], ["crc.a", "crc.new"])
    baseline = _write_baseline(tmp_path / "base.txt", ["crc/a.py"])
    messages = check_ratchet(tmp_path, baseline)
    _assert_formatted(messages)
    assert "crc/new.py" in messages[0]


def test_arch001_selftest_fires_on_directory_or_glob_entries(tmp_path: Path) -> None:
    _write_project(tmp_path, ["crc", "tests/*.py"], [])
    baseline = _write_baseline(tmp_path / "base.txt", ["crc", "tests/*.py"])
    messages = check_ratchet(tmp_path, baseline)
    _assert_formatted(messages)
    assert any("explicit .py file paths" in message for message in messages)


def test_arch001_selftest_fires_when_mypy_list_diverges(tmp_path: Path) -> None:
    _write_project(tmp_path, ["crc/a.py"], ["crc.a", "crc.extra"])
    baseline = _write_baseline(tmp_path / "base.txt", ["crc/a.py"])
    messages = check_ratchet(tmp_path, baseline)
    _assert_formatted(messages)
    assert "crc.extra" in messages[0]
