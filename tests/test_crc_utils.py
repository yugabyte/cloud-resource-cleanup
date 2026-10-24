"""Focused unit tests for `crc.utils.init_logging` and `crc.service.Service` construction."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from crc import utils
from crc.service import Service


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    noisy = {name: logging.getLogger(name).level for name in utils._NOISY_LOGGERS}
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)
    for name, noisy_level in noisy.items():
        logging.getLogger(name).setLevel(noisy_level)


def test_init_logging_creates_directory_and_writes_file(
    capsys: pytest.CaptureFixture[str], restore_logging: None, tmp_path: Path
) -> None:
    target = tmp_path / "nested" / "logs" / "crc.log"
    utils.init_logging(str(target))
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert capsys.readouterr().out == "Setting loglevel to logging: DEBUG\n"
    assert logging.getLogger().level == logging.DEBUG
    content = target.read_text(encoding="utf-8")
    assert f"Logging initialized with file: {target} and level: DEBUG" in content


def test_init_logging_info_level_sets_file_handler_to_info(
    restore_logging: None, tmp_path: Path
) -> None:
    before = list(logging.getLogger().handlers)
    utils.init_logging(str(tmp_path / "crc.log"), "info")
    added = [h for h in logging.getLogger().handlers if h not in before]
    assert [type(h) for h in added] == [logging.FileHandler, logging.StreamHandler]
    assert [h.level for h in added] == [logging.INFO, logging.INFO]
    assert logging.getLogger().level == logging.INFO


def test_init_logging_debug_level_sets_file_handler_to_debug(
    restore_logging: None, tmp_path: Path
) -> None:
    before = list(logging.getLogger().handlers)
    utils.init_logging(str(tmp_path / "crc.log"), "Debug")
    added = [h for h in logging.getLogger().handlers if h not in before]
    assert [h.level for h in added] == [logging.DEBUG, logging.INFO]


def test_init_logging_quiets_noisy_libraries(restore_logging: None, tmp_path: Path) -> None:
    utils.init_logging(str(tmp_path / "crc.log"))
    for name in ("azure", "msrest", "urllib3"):
        assert logging.getLogger(name).level == logging.WARNING


def test_init_logging_rejects_unknown_level_before_touching_disk(
    capsys: pytest.CaptureFixture[str], restore_logging: None, tmp_path: Path
) -> None:
    target = tmp_path / "never" / "crc.log"
    with pytest.raises(AttributeError):
        utils.init_logging(str(target), "loud")
    assert capsys.readouterr().out == "Setting loglevel to logging: LOUD\n"
    assert not target.parent.exists()


def test_service_init_logs_to_logs_dir(
    monkeypatch: pytest.MonkeyPatch, restore_logging: None, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    Service()
    assert (tmp_path / "logs" / "crc.log").exists()


def test_service_class_attributes_and_delegation() -> None:
    assert Service.logs_dir == "logs"
    assert Service.logs_file == "crc.log"
    assert frozenset({"days", "hours"}) == Service.AGE_KEYS
