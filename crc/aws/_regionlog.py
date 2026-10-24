# Copyright (c) Yugabyte, Inc.

"""Run-log helpers shared by the AWS cleaners (rung LOG-AWS2, log text and levels only).

Thin wrappers over `crc.core.logger.current()` that give the cleaners one injectable clock for
the `REGION ... seconds=` field and one place for the skipped-region count. Nothing here
decides what is selected or deleted.
"""

import time
from collections.abc import Callable

from crc.core import logger

clock: Callable[[], float] = time.monotonic


def start() -> float:
    """Timestamp for `region_done`."""
    return clock()


def region_done(region: str, scanned: int, selected: int, started: float) -> None:
    """Report a walked region: how many resources were listed and selected, and how long."""
    logger.current().region(region, scanned, selected, clock() - started)


def region_skipped(region: str, error: BaseException) -> None:
    """Count a region skipped for a connectivity error (its own warning stays)."""
    logger.current().region_skipped(region, error)
