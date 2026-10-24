"""Shared helpers of the `crc.service` parity tests (oracle loader and outcome comparison)."""

import logging
import re
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from crc.service import Service

ROOT = Path(__file__).resolve().parent.parent
Outcome = tuple[str, Any, list[tuple[str, str]]]


def load_oracle() -> ModuleType:
    source = (ROOT / "tests" / "legacy" / "crc_service.txt").read_text(encoding="utf-8")
    module = ModuleType("legacy_crc_service")
    exec(compile(source, "crc_service.txt", "exec"), module.__dict__)
    return module


def make_oracle_cls(monkeypatch: pytest.MonkeyPatch) -> type:
    module = load_oracle()
    monkeypatch.setattr(module, "init_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    cls: type = module.Service
    return cls


def _norm(text: str) -> str:
    return re.sub(r"0x[0-9a-f]+", "0x?", text)


def _outcome(caplog: pytest.LogCaptureFixture, call: Callable[[], Any]) -> Outcome:
    caplog.clear()
    try:
        value = call()
        kind, result = "return", value
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        kind, result = "raise", (type(e).__name__, _norm(str(e)), type(e.__cause__).__name__)
    records = [(r.levelname, _norm(r.getMessage())) for r in caplog.records]
    return kind, result, records


def both(
    oracle_cls: type,
    caplog: pytest.LogCaptureFixture,
    method: str,
    *args: Any,
) -> tuple[Outcome, Outcome]:
    caplog.set_level(logging.DEBUG)
    old = _outcome(caplog, lambda: getattr(oracle_cls(), method)(*args))
    new = _outcome(caplog, lambda: getattr(Service(), method)(*args))
    return old, new


def set_max_age(monkeypatch: pytest.MonkeyPatch, max_age: str | None) -> None:
    if max_age is None:
        monkeypatch.delenv("MAX_AGE", raising=False)
    else:
        monkeypatch.setenv("MAX_AGE", max_age)
