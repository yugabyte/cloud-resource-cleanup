"""Parity: `init_logging` of `main` (frozen oracle) against the refactored `crc.utils`.

`tests/legacy/crc_utils.txt` is the exact output of `git show main:crc/utils.py`. Both versions
run against a temporary log directory with the root logger saved and restored; the printed text,
root logger level, handlers, formatters, noisy-logger levels, created directories, file content
and raised errors must be equal.
"""

import logging
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from crc import utils

ROOT = Path(__file__).resolve().parent.parent
NOISY = ("azure", "msrest", "urllib3")
Snapshot = dict[str, Any]
InitLogging = Callable[..., None]


def _load_oracle() -> ModuleType:
    source = (ROOT / "tests" / "legacy" / "crc_utils.txt").read_text(encoding="utf-8")
    module = ModuleType("legacy_crc_utils")
    exec(compile(source, "crc_utils.txt", "exec"), module.__dict__)
    return module


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    noisy = {name: logging.getLogger(name).level for name in NOISY}
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)
    for name, noisy_level in noisy.items():
        logging.getLogger(name).setLevel(noisy_level)


def _handler_info(handler: logging.Handler) -> tuple[str, int, str | None]:
    formatter = handler.formatter
    return type(handler).__name__, handler.level, formatter._fmt if formatter else None


def _file_lines(path: Path, base: Path) -> list[str] | None:
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    # "<date> <time> <file>:<line> <LEVEL> <thread> <message>": drop what differs per module.
    return [re.sub(r"^\S+ \S+ \S+ ", "", line).replace(str(base), "<base>") for line in lines]


def _run(
    init: InitLogging,
    capsys: pytest.CaptureFixture[str],
    base: Path,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Snapshot:
    root = logging.getLogger()
    for name in NOISY:
        logging.getLogger(name).setLevel(logging.NOTSET)
    before = list(root.handlers)
    error: tuple[str, str] | None = None
    capsys.readouterr()
    try:
        init(*args, **kwargs)
    except Exception as e:
        error = (type(e).__name__, str(e).replace(str(base), "<base>"))
    out = capsys.readouterr().out
    added = [h for h in root.handlers if h not in before]
    snapshot: Snapshot = {
        "error": error,
        "stdout": out.replace(str(base), "<base>"),
        "level": root.level,
        "kept": [h for h in before if h in root.handlers] == before,
        "added": [_handler_info(h) for h in added],
        "noisy": {name: logging.getLogger(name).level for name in NOISY},
        "tree": sorted(str(p.relative_to(base)) for p in base.rglob("*")),
    }
    for handler in added:
        root.removeHandler(handler)
        handler.close()
    return snapshot


CASES: list[tuple[str, dict[str, Any], list[str] | None]] = [
    ("logs/crc.log", {}, None),
    ("logs/crc.log", {"log_level": "debug"}, None),
    ("logs/crc.log", {"log_level": "DEBUG"}, None),
    ("logs/crc.log", {"log_level": "Debug"}, None),
    ("logs/crc.log", {"log_level": "info"}, None),
    ("logs/crc.log", {"log_level": "WARNING"}, None),
    ("logs/crc.log", {"log_level": "error"}, None),
    ("logs/crc.log", {"log_level": "critical"}, None),
    ("logs/crc.log", {"log_level": "notset"}, None),
    ("logs/crc.log", {"log_level": "loud"}, None),
    ("logs/crc.log", {"log_level": ""}, None),
    ("logs/crc.log", {"log_level": "basicConfig"}, None),
    ("logs/crc.log", {"log_level": "basic_format"}, None),
    ("logs/crc.log", {"log_level": "warn"}, None),
    ("a/b/c/deep.log", {}, None),
    ("flat.log", {}, None),
    ("logs/crc.log", {}, ["old line 1", "old line 2"]),
    ("logs/crc.log", {"log_level": "info"}, ["x" * 500]),
]


@pytest.mark.parametrize(("name", "kwargs", "existing"), CASES)
def test_init_logging_parity(
    capsys: pytest.CaptureFixture[str],
    restore_logging: None,
    tmp_path: Path,
    name: str,
    kwargs: dict[str, Any],
    existing: list[str] | None,
) -> None:
    oracle = _load_oracle()
    results: list[tuple[Snapshot, list[str] | None]] = []
    for label, init in (("old", oracle.init_logging), ("new", utils.init_logging)):
        base = tmp_path / label
        base.mkdir()
        target = base / name
        if existing is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(existing), encoding="utf-8")
        snapshot = _run(init, capsys, base, (str(target),), kwargs)
        for handler in logging.getLogger().handlers:
            handler.flush()
        results.append((snapshot, _file_lines(target, base)))
    (old_snap, old_lines), (new_snap, new_lines) = results
    assert new_snap == old_snap
    assert new_lines == old_lines


def test_init_logging_relative_filename_parity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restore_logging: None,
    tmp_path: Path,
) -> None:
    oracle = _load_oracle()
    results: list[tuple[Snapshot, list[str] | None]] = []
    for label, init in (("old", oracle.init_logging), ("new", utils.init_logging)):
        base = tmp_path / label
        base.mkdir()
        monkeypatch.chdir(base)
        snapshot = _run(init, capsys, base, ("rel/dir/out.log",), {})
        for handler in logging.getLogger().handlers:
            handler.flush()
        results.append((snapshot, _file_lines(base / "rel/dir/out.log", base)))
    assert results[1] == results[0]


@pytest.mark.parametrize("kind", ["parent-is-file", "target-is-dir"])
def test_init_logging_filesystem_errors_parity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restore_logging: None,
    tmp_path: Path,
    kind: str,
) -> None:
    oracle = _load_oracle()
    snapshots: list[Snapshot] = []
    for label, init in (("old", oracle.init_logging), ("new", utils.init_logging)):
        base = tmp_path / label
        base.mkdir()
        monkeypatch.chdir(base)
        if kind == "parent-is-file":
            (base / "blocker").write_text("x", encoding="utf-8")
            target = base / "blocker" / "crc.log"
        else:
            target = base / "adir"
            target.mkdir()
        snapshots.append(_run(init, capsys, base, (str(target),), {}))
    assert snapshots[1] == snapshots[0]
    assert snapshots[1]["error"] is not None


def test_init_logging_keeps_existing_handlers_parity(
    capsys: pytest.CaptureFixture[str],
    restore_logging: None,
    tmp_path: Path,
) -> None:
    root = logging.getLogger()
    sentinel = logging.NullHandler()
    root.addHandler(sentinel)
    oracle = _load_oracle()
    snapshots = []
    for label, init in (("old", oracle.init_logging), ("new", utils.init_logging)):
        base = tmp_path / label
        base.mkdir()
        snapshots.append(_run(init, capsys, base, (str(base / "l" / "c.log"),), {}))
    assert snapshots[1] == snapshots[0]
    assert snapshots[1]["kept"] is True


def test_init_logging_repeated_calls_accumulate_handlers_parity(
    capsys: pytest.CaptureFixture[str],
    restore_logging: None,
    tmp_path: Path,
) -> None:
    oracle = _load_oracle()
    outcomes: list[tuple[list[tuple[str, int, str | None]], str, list[str] | None]] = []
    for label, init in (("old", oracle.init_logging), ("new", utils.init_logging)):
        base = tmp_path / label
        base.mkdir()
        target = base / "logs" / "crc.log"
        root = logging.getLogger()
        before = list(root.handlers)
        capsys.readouterr()
        init(str(target))
        init(str(target))
        out = capsys.readouterr().out.replace(str(base), "<base>")
        added = [h for h in root.handlers if h not in before]
        for handler in added:
            handler.flush()
        info = [_handler_info(h) for h in added]
        for handler in added:
            root.removeHandler(handler)
            handler.close()
        outcomes.append((info, out, _file_lines(target, base)))
    expected_handlers_after_two_calls = 4
    assert outcomes[1] == outcomes[0]
    assert len(outcomes[1][0]) == expected_handlers_after_two_calls


@pytest.mark.parametrize(
    ("bad_level", "expected_exc"),
    [
        ("loud", "AttributeError"),
        ("", "AttributeError"),
        ("basicConfig", "AttributeError"),
        ("basic_format", "ValueError"),
    ],
)
def test_init_logging_invalid_levels_raise_expected_error_type(
    capsys: pytest.CaptureFixture[str],
    restore_logging: None,
    tmp_path: Path,
    bad_level: str,
    expected_exc: str,
) -> None:
    oracle = _load_oracle()
    old_snap = _run(
        oracle.init_logging,
        capsys,
        tmp_path / "old",
        (str(tmp_path / "old" / "c.log"),),
        {"log_level": bad_level},
    )
    new_snap = _run(
        utils.init_logging,
        capsys,
        tmp_path / "new",
        (str(tmp_path / "new" / "c.log"),),
        {"log_level": bad_level},
    )
    assert new_snap == old_snap
    assert new_snap["error"] is not None
    assert new_snap["error"][0] == expected_exc


def test_log_formatter_is_unchanged() -> None:
    assert utils.LOG_FORMATTER == _load_oracle().LOG_FORMATTER
