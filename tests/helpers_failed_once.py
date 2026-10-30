"""A failure whose error line was already logged is printed once: no end-of-run FAILED line."""

import logging

import pytest

_RUN_LOGGER = "crc.run"


def assert_failure_printed_once(caplog: pytest.LogCaptureFixture, resource: str) -> None:
    """`resource` (an id, name, zone or region) is named by a WARNING or ERROR line of the
    cleaner, and no FAILED line repeats it."""
    named = [
        r
        for r in caplog.records
        if r.name != _RUN_LOGGER and r.levelno >= logging.WARNING and resource in r.getMessage()
    ]
    assert named, f"no warning or error line names {resource!r}"
    repeated = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("FAILED ") and resource in r.getMessage()
    ]
    assert repeated == [], f"FAILED repeats {resource!r}: {repeated}"
