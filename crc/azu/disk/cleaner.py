# Copyright (c) Yugabyte, Inc.

"""Azure managed disk cleaner.

`AzureDiskCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the disks of
the configured resource group (`disks.list_by_resource_group`) and filters them, `apply()` calls
`disks.begin_delete` and waits for its poller, disk by disk (an error on one disk is recorded and
the next disk is still processed).
"""

import datetime
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from crc.azu import _logger
from crc.azu._base import Base
from crc.azu._common import log_delete_summary, log_would_delete, real_run_age
from crc.azu.disk.client import AgeSpec, ClientFactory, SdkDisk, TagSpec
from crc.core import age as core_age
from crc.core import policy
from crc.core.cleaner import (
    Candidate,
    Cleaner,
    Failure,
    Operation,
    Plan,
    Result,
    RunContext,
    Skipped,
)
from crc.core.registry import register

DEFAULT_DISK_STATES: tuple[str, ...] = ("Unattached",)


@dataclass(frozen=True, slots=True)
class DiskSettings:
    """Filters and age gate of one run."""

    filter_tags: TagSpec | None = None
    exception_tags: TagSpec | None = None
    notags: TagSpec | None = None
    age: AgeSpec | None = None
    custom_age_tag_key: str | None = None
    disk_states: Sequence[str] = field(default=DEFAULT_DISK_STATES)


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
        """List every disk of the configured resource group and filter. Makes no mutating call."""
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
            log_would_delete(self._resource_group, candidates)
            log_delete_summary("Disks", [c.id for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned disks in plan order (nothing is deleted in a dry run)."""
        deleted: list[str] = []
        failed: list[Failure] = []
        for candidate in plan.candidates:
            error = self.delete_disk(
                candidate.id, dry_run=context.dry_run, record=not context.dry_run
            )
            if error is None:
                deleted.append(candidate.id)
            else:
                failed.append(Failure(id=candidate.id, error=error))
        log_delete_summary("Disks", deleted, dry_run=context.dry_run)
        return Result(succeeded=tuple(deleted), failed=tuple(failed))

    def list_disks(self) -> Iterable[SdkDisk]:
        """The (lazy) listing of the configured resource group only (BUG-172)."""
        return self._client_factory().disks.list_by_resource_group(self._resource_group)

    def delete_disk(self, disk_name: str, *, dry_run: bool, record: bool = True) -> str | None:
        """Delete one disk (start the deletion, wait for it) and log it; a dry run does nothing.

        Returns None once the disk is deleted (always in a dry run), else the error text. An
        error is logged and recorded for the end-of-run list, not raised (BUG-174), so the caller
        goes on with the next disk. `record` False leaves the ACTION line to the caller
        (`plan()` logs it in a dry run).
        """
        try:
            if not dry_run:
                self._client_factory().disks.begin_delete(self._resource_group, disk_name).result()
                logging.info("Deleted disk: " + disk_name)  # noqa: G003 - BUG-174: a None name must raise TypeError here, as before
            if record:
                kind = "would_delete" if dry_run else "deleted"
                _logger.acted(kind, self._resource_group, disk_name)
        except Exception as e:  # noqa: BLE001 - per-resource isolation, failure logged and recorded, resource kept
            logging.error("Error occurred while deleting disk %s: %s", disk_name, e)
            _logger.failed(self._resource_group, str(disk_name), e)
            return str(e)
        return None

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
            logging.info("Updating age for disk: %s", disk.name)
        current = now.astimezone(disk.time_created.tzinfo)
        age = policy.effective_age(retention_age, settings.age or {})
        return core_age.is_old(age, current, disk.time_created)


@register("azure", "disk")
def build_disk_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `resource_group`, `filter_tags`, `exception_tags`, `notags`,
    `age` and `custom_age_tag_key`."""
    options = context.options
    age = real_run_age(context, "disk")
    base = Base(options.get("resource_group") or "")
    settings = DiskSettings(
        filter_tags=options.get("filter_tags"),
        exception_tags=options.get("exception_tags"),
        notags=options.get("notags"),
        age=age,
        custom_age_tag_key=options.get("custom_age_tag_key"),
    )
    return AzureDiskCleaner(base.get_compute_client, base.resource_group, settings)
