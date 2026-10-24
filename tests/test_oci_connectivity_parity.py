"""Parity: `crc.oci.connectivity` against main's version (`tests/legacy/oci_connectivity.txt`).

The exception tuple catches the same errors and `log_skipped_region` writes the same records
(logger, level, message, arguments) and nothing to stdout.
"""

import logging
import types
from pathlib import Path
from typing import Any

import pytest
from oci.exceptions import ConnectTimeout, RequestException, ServiceError

import crc.oci.connectivity as new_module

ORACLE = Path(__file__).parent / "legacy" / "oci_connectivity.txt"


class CustomError(Exception):
    pass


def legacy_module() -> Any:
    module = types.ModuleType("legacy_oci_connectivity")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    return module


def errors() -> list[tuple[str, BaseException]]:
    return [
        ("connect-timeout", ConnectTimeout(Exception("timed out"))),
        ("request-exception", RequestException(Exception("no route"))),
        ("service-503", ServiceError(503, "ServiceUnavailable", {}, "unavailable")),
        ("service-401", ServiceError(401, "NotAuthenticated", {}, "bad key")),
        ("service-404", ServiceError(404, "NotAuthorizedOrNotFound", {}, "gone")),
        ("value-error", ValueError("boom")),
        ("custom", CustomError("")),
        ("keyboard-interrupt", KeyboardInterrupt()),
        ("os-error-unicode", OSError("réseau — down")),
    ]


ERROR_IDS = [name for name, _ in errors()]


@pytest.mark.parametrize("index", range(len(ERROR_IDS)), ids=ERROR_IDS)
def test_tuple_catches_the_same_errors(index: int) -> None:
    exc = errors()[index][1]
    assert isinstance(exc, new_module.CONNECTIVITY_ERRORS) == isinstance(
        exc, legacy_module().CONNECTIVITY_ERRORS
    )


def test_tuple_is_identical() -> None:
    assert new_module.CONNECTIVITY_ERRORS == legacy_module().CONNECTIVITY_ERRORS
    assert (ConnectTimeout, RequestException, ServiceError) == new_module.CONNECTIVITY_ERRORS


def _records(
    function: Any, region: str, context: str, exc: BaseException, caplog: pytest.LogCaptureFixture
) -> list[tuple[str, int, str, Any]]:
    caplog.clear()
    function(region, context, exc)
    return [(r.name, r.levelno, r.getMessage(), r.args) for r in caplog.records]


@pytest.mark.parametrize("region", ["us-sanjose-1", "", "r2"])
@pytest.mark.parametrize("context", ["list_region_subscriptions", "list_instances", ""])
@pytest.mark.parametrize("index", range(len(ERROR_IDS)), ids=ERROR_IDS)
def test_log_skipped_region_matches_main(
    index: int,
    context: str,
    region: str,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    exc = errors()[index][1]
    old = _records(legacy_module().log_skipped_region, region, context, exc, caplog)
    capsys.readouterr()
    new = _records(new_module.log_skipped_region, region, context, exc, caplog)
    assert new == old
    assert len(new) == 1
    assert new[0][1] == logging.WARNING
    assert capsys.readouterr().out == ""
