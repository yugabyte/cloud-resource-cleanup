# Copyright (c) Yugabyte, Inc.

"""Constants, settings and run state shared by the spot instance request modules."""

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from crc.core.age import AgeSpec

SERVICE_NAME = "ec2"
DEFAULT_REGION = "us-west-2"
SKIP_CONTEXT = "spot instance requests"

# Tagged onto each DoT=false volume before terminate so a later run finds orphans.
LEFTOVER_FROM_TAG = "crc_leftover_of"
# Written when LEFTOVER_FROM_TAG cannot be cleared; the sweep rejects this value.
LEFTOVER_INVALID_VALUE = "terminate-failed"
LEFTOVER_SCOPE_PREFIX = "crc_leftover_tag_"
VOLUME_AVAILABLE_WAIT_SEC = 180
VOLUME_POLL_INTERVAL_SEC = 5

Tag = Mapping[str, Any]
TagList = Sequence[Tag]
Spec = Mapping[str, Sequence[str]]
NOT_FOUND_INSTANCE = frozenset({"InvalidInstanceID.NotFound", "InvalidInstanceID.Malformed"})
NOT_FOUND_REQUEST = frozenset(
    {"InvalidSpotInstanceRequestID.NotFound", "InvalidSpotInstanceRequestID.Malformed"}
)
GONE_STATES = frozenset({None, "terminated", "shutting-down"})


@dataclass(frozen=True, slots=True)
class SpotSettings:
    """Filters and age gate of one run."""

    filter_tags: Spec
    exception_tags: Spec
    notags: Spec
    age: AgeSpec
    custom_age_tag_key: str | None
    poll_interval_sec: float = VOLUME_POLL_INTERVAL_SEC
    wait_sec: float = VOLUME_AVAILABLE_WAIT_SEC


@dataclass(frozen=True, slots=True)
class RequestMatch:
    """One spot request selected for cleanup."""

    request_id: str
    instance_id: str | None
    request_type: str
    already_cancelled: bool
    tags: TagList


@dataclass(slots=True)
class SpotState:
    """What a run reports: ids cancelled, terminated and leftover volumes deleted."""

    spot_requests_to_delete: list[str] = field(default_factory=list)
    instance_ids_to_delete: list[str] = field(default_factory=list)
    volumes_to_delete: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class Waiter:
    """Injected sleep and monotonic clock for the volume wait."""

    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic


@dataclass(frozen=True, slots=True)
class Inspection:
    """Read-only view of one request's instance before any mutation."""

    instance_state: str | None
    leftover_volumes: list[str]
    volumes_from_bdm: bool

    @property
    def instance_done(self) -> bool:
        return self.instance_state in GONE_STATES
