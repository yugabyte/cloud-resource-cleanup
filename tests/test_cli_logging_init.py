"""BUG-363: the run-log lines of the CLI path never add a root handler of their own.

`logger.begin()` runs in `crc.cli.dispatch` before any cleaner exists, and logging is only
initialised when a cleaner is built (`Service.__init__` calls `crc.utils.init_logging`). A
module-level `logging.*` call on a root logger without handlers runs `basicConfig()`, which adds
a stderr handler that prints every record a second time.
"""

import logging
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from crc.cli import dispatch
from crc.cli.inputs import Options, Targets
from crc.core import logger
from crc.utils import init_logging

_FIELDS = {name: None for name in Options.__dataclass_fields__}


def _options(**values: Any) -> Options:
    return Options(**{**_FIELDS, "operation_type": "delete", "dry_run": False, **values})


def _targets(cloud: str, resource: str) -> Targets:
    return Targets(cloud_arg=cloud, aws_disk_opt_in=False, clouds=[cloud], resources=[resource])


class BareRoot:
    """The root logger without handlers, as in a fresh process (pytest's capture lifted)."""

    def __init__(self) -> None:
        self.root = logging.getLogger()
        self._level = self.root.level

    def strip(self) -> logging.Logger:
        """Call inside the test: pytest adds its capture handlers when the test phase starts."""
        self.root.handlers = []
        self.root.setLevel(logging.WARNING)
        logger.finish()
        return self.root

    def restore(self) -> None:
        for handler in self.root.handlers[:]:
            if not type(handler).__module__.startswith("_pytest"):
                handler.close()
                self.root.removeHandler(handler)
        self.root.setLevel(self._level)
        logger.finish()


@pytest.fixture
def bare() -> Iterator[BareRoot]:
    helper = BareRoot()
    yield helper
    helper.restore()


def test_cli_path_with_init_logging_in_cleaner_adds_no_stray_handler(
    bare: BareRoot,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    bare_root = bare.strip()
    crc = Mock()
    monkeypatch.setattr(dispatch, "make_crc", lambda context, cloud: crc)
    log_file = tmp_path / "crc.log"

    def cleaner(*args: Any) -> None:
        init_logging(str(log_file))
        run = logger.current()
        logging.getLogger("botocore").debug("retrying request")
        run.region("us-east-2", 2, 0, 0.1)
        run.region("us-east-1", 3, 1, 0.2)
        run.action("deleted", "us-east-1", "kp-1", "kp")

    crc.delete_keypairs.side_effect = cleaner
    assert bare_root.handlers == []

    dispatch.run_dispatch(Mock(), _options(age={"days": 1}), _targets("aws", "keypair"))

    kinds = sorted(type(h).__name__ for h in bare_root.handlers)
    assert kinds == ["FileHandler", "StreamHandler"]
    stream = next(h for h in bare_root.handlers if not isinstance(h, logging.FileHandler))
    assert stream.level == logging.INFO

    lines = [line for line in capsys.readouterr().err.splitlines() if line]
    assert all(count == 1 for count in Counter(lines).values()), lines
    assert not [line for line in lines if re.match(r"^(DEBUG|INFO|WARNING|ERROR):", line)]
    assert not [line for line in lines if "retrying request" in line]
    assert not [line for line in lines if "region=us-east-2" in line]
    starts = [i for i, line in enumerate(lines) if "RUN START" in line]
    results = [i for i, line in enumerate(lines) if "RESULT cloud=" in line]
    assert len(starts) == 1 and len(results) == 1 and starts[0] < results[0]
    first_region = next(i for i, line in enumerate(lines) if "REGION cloud=" in line)
    assert starts[0] < first_region

    file_text = log_file.read_text()
    assert file_text.count("RUN START") == 1
    assert file_text.count("RESULT cloud=") == 1
    assert "retrying request" in file_text
    assert "region=us-east-2 scanned=2 selected=0" in file_text


def test_run_without_root_handlers_still_emits_header_then_result(
    bare: BareRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare_root = bare.strip()
    monkeypatch.setattr(dispatch, "make_crc", lambda context, cloud: Mock())
    records: list[str] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    run_logger = logging.getLogger("crc.run")
    collector = Collect(level=logging.DEBUG)
    run_logger.addHandler(collector)
    run_logger.setLevel(logging.DEBUG)
    try:
        dispatch.run_dispatch(Mock(), _options(), _targets("aws", "keypair"))
    finally:
        run_logger.removeHandler(collector)
        run_logger.setLevel(logging.NOTSET)
    assert [m.split()[0] for m in records] == ["RUN", "RESULT"]
    assert records[0].startswith("RUN START cloud=aws resource=keypair")
    assert bare_root.handlers == []


def test_dispatch_warning_before_init_logging_does_not_run_basic_config(
    bare: BareRoot, monkeypatch: pytest.MonkeyPatch
) -> None:
    bare_root = bare.strip()
    crc = Mock(cloud="aws")
    monkeypatch.setattr(dispatch, "make_crc", lambda context, cloud: crc)
    dispatch.run_dispatch(Mock(), _options(), _targets("aws", "spot_instances"))
    assert bare_root.handlers == []
    crc.delete_spot_instances.assert_not_called()


def test_no_module_level_logging_call_in_run_log_or_dispatch() -> None:
    pattern = re.compile(r"(?<![\w.])logging\.(log|debug|info|warning|error|exception|critical)\(")
    for name in ("crc/core/logger.py", "crc/cli/dispatch.py", "crc/cli/scan.py"):
        assert not pattern.search(Path(name).read_text()), name
