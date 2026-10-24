# Copyright (c) Yugabyte, Inc.

"""Small helpers of the Azure VM cleaner: constants, name tally, power state, error log."""

import logging
from collections.abc import Iterable, Sequence

from crc.azu.vm.client import InstanceStatus
from crc.azu.vm.settings import Tally
from crc.core.cleaner import Operation

NIC_NAME_SUFFIX = "-NIC"
NIC_LABEL = "NIC"
POWER_STATE_PREFIX = "PowerState/"
SKIP_NO_POWER_STATE = "no_power_state"
SKIP_STATE_NOT_ALLOWED = "status does not contain an allowed state"


def tally_of(operation: Operation, candidates: Iterable[str]) -> Tally:
    names = list(candidates)
    if operation is Operation.DELETE:
        return Tally(deleted=names, nics=[f"{name}{NIC_NAME_SUFFIX}" for name in names])
    return Tally(stopped=names)


def power_state(statuses: Sequence[InstanceStatus] | None) -> str | None:
    """The power state of an instance view: the code after `PowerState/` of the first status
    with such a code, lower-cased, or None when there is none."""
    for status in statuses or ():
        code = getattr(status, "code", None)
        if isinstance(code, str) and code.startswith(POWER_STATE_PREFIX):
            state = code[len(POWER_STATE_PREFIX) :].casefold()
            if state:
                return state
    return None


def log_processing_error(name: str, error: Exception) -> None:
    logging.error("Error occurred while processing %s instance: %s", name, error)
