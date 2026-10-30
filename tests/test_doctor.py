"""`scripts/doctor.py`: the environment check behind `make doctor`."""

from collections.abc import Callable
from pathlib import Path

import pytest

from scripts import doctor

PINS = {"ruff": "0.1", "mypy": "2.0", "pytest": "9.0"}


def _lookup(versions: dict[str, str]) -> Callable[[str], str | None]:
    return versions.get


def test_ok_when_python_and_tools_match() -> None:
    assert doctor.problems_of(PINS, (3, 11), (3, 11), _lookup(PINS)) == []


def test_old_python_is_reported() -> None:
    problems = doctor.problems_of(PINS, (3, 11), (3, 10), _lookup(PINS))
    assert problems == ["Python 3.10 is too old: pyproject.toml requires >= 3.11"]


def test_missing_and_wrong_versions_are_reported() -> None:
    problems = doctor.problems_of(PINS, (3, 11), (3, 12), _lookup({"ruff": "0.2", "pytest": "9.0"}))
    assert problems == [
        "ruff is 0.2, constraints.txt pins 0.1",
        "mypy is not installed (constraints.txt pins 2.0)",
    ]


def test_tool_without_a_pin_is_reported() -> None:
    problems = doctor.problems_of({"ruff": "0.1"}, (3, 11), (3, 12), _lookup(PINS))
    assert "mypy has no pin in constraints.txt" in problems
    assert "pytest has no pin in constraints.txt" in problems


def test_install_command_names_the_interpreter_or_creates_the_venv() -> None:
    inside = doctor.install_command("/x/python", in_venv=True)
    assert inside == (
        "/x/python -m pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt"
    )
    outside = doctor.install_command("/usr/bin/python3", in_venv=False)
    assert outside.startswith("python3 -m venv .venv && .venv/bin/python -m pip install -r ")


def test_pinned_versions_reads_exact_pins_only(tmp_path: Path) -> None:
    path = tmp_path / "constraints.txt"
    path.write_text("# c\nRuff==0.1\nloose>=1\n\nmypy==2.0\n", encoding="utf-8")
    assert doctor.pinned_versions(path) == {"ruff": "0.1", "mypy": "2.0"}


def test_minimum_python_reads_requires_python(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\nrequires-python = ">=3.12"\n', encoding="utf-8")
    assert doctor.minimum_python(path) == (3, 12)


def test_the_real_checkout_is_pinned_for_every_tool() -> None:
    pins = doctor.pinned_versions()
    assert all(tool in pins for tool in doctor.TOOLS)
    assert doctor.minimum_python() >= (3, 11)


def test_main_prints_the_install_command_when_a_tool_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(doctor, "installed_version", lambda name: None)
    monkeypatch.setattr(
        doctor,
        "problems_of",
        lambda pins, minimum: ["ruff is not installed (constraints.txt pins 1)"],
    )
    assert doctor.main() == 1
    out = capsys.readouterr().out
    assert "ruff is not installed" in out
    assert "doctor: fix with: " in out
    assert "-m pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt" in out


def test_main_is_ok_on_this_environment(capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor.main() == 0
    assert capsys.readouterr().out.startswith("doctor: ok")
