# Copyright (c) Yugabyte, Inc.

"""Settings and closing log lines of the GCP VM cleaner, used by `VmCleaner`."""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from crc.core import age as core_age
from crc.core.cleaner import Operation

_DEFAULT_STATE = ("RUNNING",)
_DEFAULT_WAIT_SEC = 180.0
_DEFAULT_POLL_SEC = 5.0


@dataclass(frozen=True, slots=True)
class VmSettings:
    """Filters and options of one run, as the registered factory builds them."""

    project_id: str
    filter_labels: Mapping[str, Sequence[str]] | None
    exception_labels: Mapping[str, Sequence[str]] | None
    notags: Mapping[str, Sequence[str]] | None
    age: core_age.AgeSpec | int
    custom_age_tag_key: str
    instance_state: Sequence[str] = _DEFAULT_STATE
    wait_sec: float = _DEFAULT_WAIT_SEC
    poll_sec: float = _DEFAULT_POLL_SEC


def _log_summary(
    operation: Operation, deleted: Sequence[str], stopped: Sequence[str], *, dry_run: bool
) -> None:
    """The closing log lines of `main`; the empty check looks at both lists."""
    if not deleted and not stopped:
        logging.warning("No GCP instances to %s.", operation.value)
    past = "deleted" if operation is Operation.DELETE else "stopped"
    names = deleted if operation is Operation.DELETE else stopped
    if not dry_run:
        logging.info("number of GCP instances %s: %s", past, len(names))
        logging.warning("List of GCP instances %s: %s", past, names)
    else:
        logging.warning(
            "List of GCP instances (Total: %s) which will be %s: %s", len(names), past, names
        )
