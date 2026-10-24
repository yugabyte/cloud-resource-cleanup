"""Cleaner contract: protocol and the frozen DTOs that cross the cleaner boundary.

`plan()` is read-only and backs `--dry_run`; `apply()` performs the mutations for a plan.
Nothing here talks to a cloud, reads the clock or reads the environment.
"""

import datetime
import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class Operation(enum.StrEnum):
    """What a run does to the selected resources (the `--operation_type` values)."""

    DELETE = "delete"
    STOP = "stop"


def _frozen_map(value: Mapping[K, V]) -> Mapping[K, V]:
    """Read-only copy: later changes to the caller's mapping do not leak in."""
    return MappingProxyType(dict(value))


def _empty_map() -> Mapping[str, Any]:
    return MappingProxyType({})


def _freeze_id_groups(groups: Mapping[str, Sequence[str]]) -> Mapping[str, tuple[str, ...]]:
    return _frozen_map({key: tuple(ids) for key, ids in groups.items()})


@dataclass(frozen=True, slots=True)
class Candidate:
    """One cloud resource, normalised by the cleaner. No SDK objects."""

    id: str
    name: str = ""
    region: str = ""
    state: str = ""
    instance_type: str = ""
    launch_time: datetime.datetime | None = None
    console_url: str = ""
    tags: Mapping[str, str] = field(default_factory=_empty_map)
    extra: Mapping[str, Any] = field(default_factory=_empty_map)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tags", _frozen_map(self.tags))
        object.__setattr__(self, "extra", _frozen_map(self.extra))


@dataclass(frozen=True, slots=True)
class Skipped:
    """A resource the policy kept, with the reason (age, tag, state, telemetry, ...)."""

    id: str
    reason: str
    name: str = ""


@dataclass(frozen=True, slots=True)
class Plan:
    """What `apply()` will act on, and what was deliberately left alone."""

    candidates: tuple[Candidate, ...] = ()
    skipped: tuple[Skipped, ...] = ()
    related: Mapping[str, tuple[str, ...]] = field(default_factory=_empty_map)
    by_owner: Mapping[str, tuple[str, ...]] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "skipped", tuple(self.skipped))
        object.__setattr__(self, "related", _freeze_id_groups(self.related))
        if self.by_owner is not None:
            object.__setattr__(self, "by_owner", _freeze_id_groups(self.by_owner))


@dataclass(frozen=True, slots=True)
class Failure:
    """A resource that could not be processed, with the error text."""

    id: str
    error: str


@dataclass(frozen=True, slots=True)
class Result:
    """Outcome of `apply()`.

    `related` holds dependent resources (NICs, leftover disks, volumes) keyed by the
    resource label used in reports, for example `"NIC"` or `"Disk"`. `by_owner` holds
    owner-grouped resource ids when `--slack_notify_users` is active (`None` otherwise).
    """

    succeeded: tuple[str, ...] = ()
    failed: tuple[Failure, ...] = ()
    related: Mapping[str, tuple[str, ...]] = field(default_factory=_empty_map)
    by_owner: Mapping[str, tuple[str, ...]] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "succeeded", tuple(self.succeeded))
        object.__setattr__(self, "failed", tuple(self.failed))
        object.__setattr__(self, "related", _freeze_id_groups(self.related))
        if self.by_owner is not None:
            object.__setattr__(self, "by_owner", _freeze_id_groups(self.by_owner))


@dataclass(frozen=True, slots=True)
class RunContext:
    """Inputs of one run. `now` comes from the caller: cleaners never read the clock."""

    dry_run: bool
    operation: Operation
    options: Mapping[str, Any]
    now: datetime.datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", _frozen_map(self.options))


class Cleaner(Protocol):
    """One cleaner handles one resource kind of one cloud."""

    cloud: str
    kind: str

    def supports(self, operation: Operation) -> bool:
        """True when this cleaner can run `operation` (most only support delete)."""
        ...

    def plan(self, context: RunContext) -> Plan:
        """Discover and filter. Read-only: must never mutate cloud state."""
        ...

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Perform the mutating calls for `plan`, in the order CLEANUP_SAFETY.md requires."""
        ...


def candidate_ids(items: Sequence[Candidate]) -> tuple[str, ...]:
    """Ids of `items`, in order."""
    return tuple(item.id for item in items)
