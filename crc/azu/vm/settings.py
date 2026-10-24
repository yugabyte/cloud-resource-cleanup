# Copyright (c) Yugabyte, Inc.

"""Settings, name tally and summary logging of the Azure VM cleaner."""

import datetime
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from crc.core import age as core_age
from crc.core.cleaner import Operation

DEFAULT_INSTANCE_STATES = ("running",)

TagSpec = Mapping[str, Sequence[str]]
Clock = Callable[[], datetime.datetime]


@dataclass(frozen=True, slots=True)
class VmSettings:
    """Filters and thresholds of one run."""

    filter_tags: TagSpec = field(default_factory=dict)
    exception_tags: TagSpec = field(default_factory=dict)
    notags: TagSpec = field(default_factory=dict)
    age: core_age.AgeSpec = field(default_factory=dict)
    custom_age_tag_key: str = ""
    instance_states: Sequence[str] = DEFAULT_INSTANCE_STATES


@dataclass(slots=True)
class Tally:
    """Names recorded so far: VMs deleted, VMs stopped and NICs deleted (also in a dry run)."""

    deleted: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    nics: list[str] = field(default_factory=list)


def _log_deleted(tally: Tally, *, dry_run: bool) -> None:
    if not dry_run:
        logging.warning("number of Azure instances deleted: %s", len(tally.deleted))
        logging.warning("List of Azure instances deleted: %s", tally.deleted)
        logging.warning("number of Azure nics deleted: %s", len(tally.nics))
        logging.warning("List of Azure nics deleted: %s", tally.nics)
    else:
        logging.warning(
            "List of Azure instances (Total: %s) which will be deleted: %s",
            len(tally.deleted),
            tally.deleted,
        )
        logging.warning(
            "List of Azure nics (Total: %s) which will be deleted: %s", len(tally.nics), tally.nics
        )


def _log_stopped(tally: Tally, *, dry_run: bool) -> None:
    if not dry_run:
        logging.warning("number of Azure instances stopped: %s", len(tally.stopped))
        logging.warning("List of Azure instances stopped: %s", tally.stopped)
    else:
        logging.warning(
            "List of Azure instances (Total: %s) which will be stopped: %s",
            len(tally.stopped),
            tally.stopped,
        )


def log_summary(operation: Operation, tally: Tally, *, dry_run: bool) -> None:
    """The closing lines of main. The "nothing to do" warning looks at both name lists,
    whichever operation runs."""
    if not tally.deleted and not tally.stopped:
        logging.warning("No Azure instances to %s.", operation)
    if operation is Operation.DELETE:
        _log_deleted(tally, dry_run=dry_run)
    if operation is Operation.STOP:
        _log_stopped(tally, dry_run=dry_run)
