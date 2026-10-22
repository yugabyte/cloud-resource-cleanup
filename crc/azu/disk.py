# Copyright (c) Yugabyte, Inc.

"""Azure managed disk cleaner.

`AzureDiskCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the disks
(`disks.list`) and filters them, `apply()` calls `disks.begin_delete`. `Disk` is the legacy class
`crc.py` uses; it keeps its constructor and attributes and runs the cleaner disk by disk, in the
exact call order of the original (listing is lazy, so paging and deletes interleave).
"""

import datetime
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from crc.azu import _logger
from crc.azu._base import Base
from crc.core import age as core_age
from crc.core import policy
from crc.core.cleaner import (
    Candidate,
    Cleaner,
    Operation,
    Plan,
    Result,
    RunContext,
    Skipped,
)
from crc.core.registry import register
from crc.service import Service

DEFAULT_DISK_STATES: tuple[str, ...] = ("Unattached",)

TagSpec = Mapping[str, Sequence[str]]
AgeSpec = dict[str, int]


class SdkDisk(Protocol):
    """The fields of an Azure `Disk` model this cleaner reads."""

    @property
    def name(self) -> str:
        """Disk name."""
        ...

    @property
    def tags(self) -> dict[str, str] | None:
        """Resource tags, `None` when the disk has none."""
        ...

    @property
    def time_created(self) -> datetime.datetime:
        """Creation time (timezone aware in the SDK)."""
        ...

    @property
    def disk_state(self) -> str:
        """Disk state, for example `Unattached`."""
        ...


class DiskOperations(Protocol):
    """The two `disks` calls this cleaner uses."""

    def list(self) -> Iterable[SdkDisk]:
        """List the disks of the whole subscription (lazy paging)."""
        ...

    def begin_delete(self, resource_group_name: str, disk_name: str, /) -> object:
        """Start the deletion of one disk (positional arguments, result is not awaited)."""
        ...


class DiskComputeClient(Protocol):
    """The part of the Azure compute client this cleaner uses."""

    @property
    def disks(self) -> DiskOperations:
        """Operations on managed disks."""
        ...


ClientFactory = Callable[[], DiskComputeClient]
Clock = Callable[[], datetime.datetime]


@dataclass(frozen=True, slots=True)
class DiskSettings:
    """Filters and age gate of one run, as `crc.py` passes them."""

    filter_tags: TagSpec | None = None
    exception_tags: TagSpec | None = None
    notags: TagSpec | None = None
    age: AgeSpec | None = None
    custom_age_tag_key: str | None = None
    disk_states: Sequence[str] = field(default=DEFAULT_DISK_STATES)


def _log_summary(deleted: Sequence[str], *, dry_run: bool) -> None:
    items = list(deleted)
    if dry_run:
        logging.warning(f"List of Azure Disks (Total: {len(items)}) which will be deleted: {items}")
    else:
        logging.warning(f"number of Azure Disks deleted: {len(items)}")
        logging.warning(f"List of Azure Disks deleted: {items}")


class AzureDiskCleaner:
    """Deletes unattached managed disks that pass the tag filters and the age gate."""

    cloud = "azure"
    kind = "disk"

    def __init__(
        self,
        client_factory: ClientFactory,
        resource_group: str,
        settings: DiskSettings,
    ) -> None:
        self._client_factory = client_factory
        self._resource_group = resource_group
        self._settings = settings

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List every disk of the subscription and filter. Makes no mutating call."""
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        started = _logger.started()
        scanned = 0
        for disk in self.list_disks():
            scanned += 1
            candidate, reason = self.decide(disk, context.now)
            if candidate is not None:
                candidates.append(candidate)
            else:
                skipped.append(Skipped(id=disk.name, reason=reason, name=disk.name))
        _logger.region_done(self._resource_group, scanned, len(candidates), started)
        if context.dry_run:
            for candidate in candidates:
                _logger.acted("would_delete", self._resource_group, candidate.id)
            _log_summary([c.id for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned disks in plan order (nothing is deleted in a dry run)."""
        deleted: list[str] = []
        for candidate in plan.candidates:
            self.delete_disk(candidate.id, dry_run=context.dry_run, record=not context.dry_run)
            deleted.append(candidate.id)
        _log_summary(deleted, dry_run=context.dry_run)
        return Result(succeeded=tuple(deleted))

    def list_disks(self) -> Iterable[SdkDisk]:
        """The (lazy) subscription-wide listing."""
        return self._client_factory().disks.list()

    def delete_disk(self, disk_name: str, *, dry_run: bool, record: bool = True) -> None:
        """Start the deletion of one disk and log it; a dry run does nothing.

        `record` False leaves the ACTION line to the caller (`plan()` logs it in a dry run).
        """
        if not dry_run:
            self._client_factory().disks.begin_delete(self._resource_group, disk_name)
            logging.info("Deleted disk: " + disk_name)
        if record:
            kind = "would_delete" if dry_run else "deleted"
            _logger.acted(kind, self._resource_group, disk_name)

    def decide(self, disk: SdkDisk, now: datetime.datetime) -> tuple[Candidate | None, str]:
        """Select one SDK disk: the candidate, or None with the reason it was kept."""
        tags = dict(disk.tags) if disk.tags else {}
        reason = self._tag_skip_reason(tags)
        if reason:
            _logger.skip(reason)
            return None, reason
        logging.debug(disk.tags)
        if not self._is_old_enough(disk, now):
            _logger.skip("not older than the age threshold")
            return None, "not older than the age threshold"
        if not policy.state_is_substring_of_any(disk.disk_state, self._settings.disk_states):
            _logger.skip("disk state not allowed")
            return None, "disk state not allowed"
        return Candidate(id=disk.name, name=disk.name, state=disk.disk_state, tags=tags), ""

    def _tag_skip_reason(self, tags: dict[str, str]) -> str:
        settings = self._settings
        included = policy.filter_tags_any_key(tags, settings.filter_tags or {})
        excluded = policy.exception_tags_any(tags, settings.exception_tags or {})
        no_tags = policy.notags_all(tags, settings.notags or {})
        if not included:
            return "no filter_tags match"
        if excluded:
            return "matches exception_tags"
        if no_tags:
            return "matches notags"
        return ""

    def _is_old_enough(self, disk: SdkDisk, now: datetime.datetime) -> bool:
        settings = self._settings
        retention_age: Any = core_age.get_retention_age(
            disk.tags or {}, settings.custom_age_tag_key or ""
        )
        if retention_age:
            logging.info(f"Updating age for disk: {disk.name}")
        current = now.astimezone(disk.time_created.tzinfo)
        age = policy.effective_age(retention_age, settings.age or {})
        return core_age.is_old(age, current, disk.time_created)


@register("azure", "disk")
def build_disk_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `resource_group`, `filter_tags`, `exception_tags`, `notags`,
    `age` and `custom_age_tag_key`."""
    options = context.options
    base = Base(options.get("resource_group") or "")
    settings = DiskSettings(
        filter_tags=options.get("filter_tags"),
        exception_tags=options.get("exception_tags"),
        notags=options.get("notags"),
        age=options.get("age"),
        custom_age_tag_key=options.get("custom_age_tag_key"),
    )
    return AzureDiskCleaner(base.get_compute_client, base.resource_group, settings)


class Disk(Service):
    """
    A class for managing Disks on Azure.

    Attributes: `disks_names_to_delete` (names deleted, or listed in a dry run), `base`,
    `dry_run`, `filter_tags`, `exception_tags`, `age`, `custom_age_tag_key`, `notags`, `clock`.
    `get_deleted` and `count` read the result.
    """

    default_disk_state: ClassVar[list[str]] = ["Unattached"]
    """The default_disk_state variable specifies the default state of disks when querying."""

    def __init__(  # noqa: PLR0913, PLR0917 - the seven parameters are the public signature
        self,
        resource_group: str,
        dry_run: bool,
        filter_tags: TagSpec | None,
        exception_tags: TagSpec | None,
        age: AgeSpec | None,
        custom_age_tag_key: str | None,
        notags: TagSpec | None,
    ) -> None:
        """
        Initialize the Disk management class.

        :param dry_run: When True the class only lists the disks that match the filter and
        exception tags and performs no operation on them.
        :param filter_tags: A dictionary of tags to filter disks by.
        :param exception_tags: A dictionary of tags to exclude disks by.
        :param age: A dictionary specifying the age threshold for deleting disks.
        :param custom_age_tag_key: Tag name to overwrite the age condition.
        :param notags: A dictionary of tags to filter resources that do not have the specified tags.
        """
        super().__init__()
        self.disks_names_to_delete: list[str] = []
        self.base = Base(resource_group)
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = age
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags
        self.clock: Clock = datetime.datetime.now

    @property
    def get_deleted(self) -> list[str]:
        """
        Returns the list of items in the disks_names_to_delete list (read-only property).
        """
        return self.disks_names_to_delete

    @property
    def count(self) -> int:
        """
        Returns the count of items in the disks_names_to_delete list (read-only property).
        """
        count = len(self.disks_names_to_delete)
        logging.info(f"count of items in disks_names_to_delete: {count}")
        return count

    def _cleaner(self) -> AzureDiskCleaner:
        settings = DiskSettings(
            filter_tags=self.filter_tags,
            exception_tags=self.exception_tags,
            notags=self.notags,
            age=self.age,
            custom_age_tag_key=self.custom_age_tag_key,
            disk_states=self.default_disk_state,
        )
        return AzureDiskCleaner(self.base.get_compute_client, self.base.resource_group, settings)

    def delete(self) -> None:
        """
        Deletes disks that match filter_tags, do not match exception_tags or notags, and are
        older than the age. In dry_run mode it only lists the matching disks.
        """
        cleaner = self._cleaner()
        started = _logger.started()
        scanned = selected = 0
        for disk in cleaner.list_disks():
            scanned += 1
            candidate, _ = cleaner.decide(disk, self.clock())
            if candidate is None:
                continue
            selected += 1
            cleaner.delete_disk(candidate.id, dry_run=self.dry_run)
            self.disks_names_to_delete.append(candidate.id)
        _logger.region_done(self.base.resource_group, scanned, selected, started)
        _log_summary(self.disks_names_to_delete, dry_run=self.dry_run)
