"""ARCH-003: `MAX_AGE` reaches `crc.core.age` through a source that `crc/cli` installs."""

import datetime
from collections.abc import Iterator
from pathlib import Path

import pytest

from crc.cli import main as cli_main
from crc.cli.context import install_max_age_source, read_max_age_env
from crc.core import age

NOW = datetime.datetime(2026, 1, 10, tzinfo=datetime.UTC)
CREATED = NOW - datetime.timedelta(hours=3)


@pytest.fixture(autouse=True)
def _restore_source() -> Iterator[None]:
    yield
    install_max_age_source()


def test_core_age_applies_no_override_without_a_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_AGE", "{'hours': 1}")
    age.set_max_age_source(None)
    assert age.max_age_override() is None
    assert age.is_old({"days": 5}, NOW, CREATED) is False


def test_core_age_uses_the_injected_source_not_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAX_AGE", raising=False)
    age.set_max_age_source(lambda: "{'hours': 1}")
    assert age.is_old({"days": 5}, NOW, CREATED) is True
    monkeypatch.setenv("MAX_AGE", "{'hours': 1}")
    age.set_max_age_source(lambda: None)
    assert age.is_old({"days": 5}, NOW, CREATED) is False


def test_cli_source_reads_the_variable_at_each_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    install_max_age_source()
    monkeypatch.delenv("MAX_AGE", raising=False)
    assert read_max_age_env() is None
    assert age.is_old({"days": 5}, NOW, CREATED) is False
    monkeypatch.setenv("MAX_AGE", "{'hours': 1}")
    assert read_max_age_env() == "{'hours': 1}"
    assert age.is_old({"days": 5}, NOW, CREATED) is True


def test_main_installs_the_source_before_anything_else(monkeypatch: pytest.MonkeyPatch) -> None:
    age.set_max_age_source(None)
    monkeypatch.setattr("sys.argv", ["crc.py", "--cloud", "bad"])
    with pytest.raises(SystemExit):
        cli_main.main()
    assert age.max_age_override() == read_max_age_env()
    monkeypatch.setenv("MAX_AGE", "{'days': 1}")
    assert age.max_age_override() == "{'days': 1}"


def test_core_package_reads_no_environment() -> None:
    root = Path(__file__).resolve().parents[1] / "crc" / "core"
    for path in sorted(root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "os.environ" not in text, path
        assert "getenv" not in text, path
