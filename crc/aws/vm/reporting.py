# Copyright (c) Yugabyte, Inc.

"""Logging of a VM run: region progress, dry-run actions and the closing summary."""

import logging
import time
from collections.abc import Sequence

from crc.aws.connectivity import log_skipped_region
from crc.core import logger
from crc.core.cleaner import Candidate, Operation, Skipped

ACTED = {
    (Operation.DELETE, False): "deleted",
    (Operation.DELETE, True): "would_delete",
    (Operation.STOP, False): "stopped",
    (Operation.STOP, True): "would_stop",
}


def skip_region(region: str, operation: Operation, error: BaseException) -> None:
    log_skipped_region(region, f"ec2 vm {operation.value}", error)
    logger.current().region_skipped(region, error)


def note_region(
    region: str, started: float, found: Sequence[Candidate], kept: Sequence[Skipped]
) -> None:
    """The REGION progress line of a region that was listed and filtered."""
    seconds = time.monotonic() - started
    logger.current().region(region, len(found) + len(kept), len(found), seconds)


def note_dry_run(operation: Operation, candidates: Sequence[Candidate]) -> None:
    """One ACTION would_* line per instance a dry run lists."""
    for candidate in candidates:
        logger.current().action(
            ACTED[operation, True], candidate.region, candidate.id, candidate.name
        )


def log_summary(
    operation: Operation, dry_run: bool, deleted: Sequence[str], stopped: Sequence[str]
) -> None:
    """The closing warnings of a run, as on main (`deleted` and `stopped` are both checked
    for the empty message, the one of `operation` is listed)."""
    if not deleted and not stopped:
        logging.warning("No AWS instances to %s.", operation.value)
    names = list(deleted if operation is Operation.DELETE else stopped)
    verb = "deleted" if operation is Operation.DELETE else "stopped"
    if not dry_run:
        logging.warning("number of AWS instances %s: %s", verb, len(names))
        logging.warning("List of AWS instances %s: %s", verb, names)
    else:
        logging.warning(
            "List of AWS instances (Total: %s) which will be %s: %s", len(names), verb, names
        )


def summarise(operation: Operation, dry_run: bool, names: Sequence[str]) -> None:
    """The closing summary of a run: `names` are the deleted or the stopped instances."""
    is_delete = operation is Operation.DELETE
    log_summary(operation, dry_run, names if is_delete else [], [] if is_delete else names)


def dry_run_names(operation: Operation, candidates: Sequence[Candidate]) -> list[str]:
    """What a dry run records: names for delete, but instance ids for stop (as on main)."""
    if operation is Operation.DELETE:
        return [c.name for c in candidates]
    return [c.id for c in candidates]
