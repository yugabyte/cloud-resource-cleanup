# Copyright (c) Yugabyte, Inc.

"""Settings and constants of the GCP Spot VM cleaner."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypedDict

from crc.core import age as core_age

TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"
DEFAULT_INSTANCE_STATES: tuple[str, ...] = ("RUNNING", "TERMINATED", "SUSPENDED")
DEFAULT_WAIT_SEC = 180
DEFAULT_POLL_INTERVAL_SEC = 5


class DiskRef(TypedDict):
    """A disk source URL split into project, scope (`zone` or `region`), location and name."""

    project: str
    scope: str
    location: str
    name: str


@dataclass(frozen=True, slots=True)
class SpotSettings:
    """Filters and options of one run: the fields `main` reads.

    `age` is already validated (positive). `wait_sec` and `poll_sec` bound the wait for a
    delete operation to reach DONE.
    """

    project_id: str
    filter_labels: Mapping[str, Sequence[str]]
    exception_labels: Mapping[str, Sequence[str]]
    notags: Mapping[str, Sequence[str]]
    age: core_age.AgeSpec
    custom_age_tag_key: str | None
    instance_states: Sequence[str] = DEFAULT_INSTANCE_STATES
    wait_sec: float = DEFAULT_WAIT_SEC
    poll_sec: float = DEFAULT_POLL_INTERVAL_SEC
