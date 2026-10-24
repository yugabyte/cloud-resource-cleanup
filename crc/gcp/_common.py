# Copyright (c) Yugabyte, Inc.

"""Helpers shared by the GCP cleaners of this package (disk and VM age gates)."""

import datetime
import logging
from collections.abc import Callable, Mapping
from typing import Protocol, cast

from crc.core import age as core_age
from crc.core import policy

TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"

Clock = Callable[[], datetime.datetime]


class _Labelled(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def labels(self) -> Mapping[str, str]: ...


def is_old_enough(
    item: _Labelled,
    timestamp: str,
    clock: Clock,
    custom_age_tag_key: str,
    default_age: object,
    kind: str,
) -> bool:
    """Whether `item` is at least as old as its custom age label, else `default_age`. `timestamp`
    is the creation (VM) or last detach (disk) time; an int `default_age` is a number of days."""
    stamp = datetime.datetime.strptime(timestamp, TIME_FORMAT)  # noqa: DTZ007 - TIME_FORMAT ends in %z, so the result is aware
    now = clock().astimezone(stamp.tzinfo)
    logging.debug(item.labels)
    retention_age = core_age.get_retention_age(
        cast(dict[str, str], item.labels), custom_age_tag_key
    )
    if retention_age:
        logging.info("Updating age for %s: %s", kind, item.name)
    age = policy.effective_age(retention_age, cast(core_age.AgeSpec, default_age))
    return core_age.is_old(age, now, stamp)
