# Copyright (c) Yugabyte, Inc.

"""GCP disk cleaner.

`DiskCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the zones of
the project and their disks and filters them, `apply()` deletes the planned disks.
"""

import logging
from collections.abc import Iterator, Mapping, Sequence
from typing import cast

from crc.core import age as core_age
from crc.core import logger, policy
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext
from crc.core.registry import register
from crc.gcp._common import Clock, is_old_enough
from crc.gcp._scan import ZoneScan, log_action
from crc.gcp.disk_client import ComputeDiskClient, DiskView, GcpDiskClient
from crc.gcp.disk_settings import (
    Deleted,
    DiskSettings,
    _frozen_groups,
    _log_summary,
    _name_patterns,
    _record,
)
from crc.gcp.spot_vm.client import GcpSpotClient
from crc.gcp.spot_vm.operations import wait_operation
from crc.gcp.spot_vm.settings import SpotSettings


class DiskCleaner:
    """Deletes unattached disks with a `last_detach_timestamp` that are at least `detach_age`
    (or the custom age label) old, match `filter_labels` (any key; empty matches all) and
    `name_regex` (`re.search` any; empty matches all) and are not excluded by `exception_regex`
    (`re.match` on the name), `exception_labels` (any key) or `notags` (all keys, as the GCP VM
    cleaner). Applies to every zone of the project. An error on one disk or one zone listing is
    recorded and the run goes on."""

    cloud = "gcp"
    kind = "disk"

    def __init__(self, client: GcpDiskClient, settings: DiskSettings) -> None:
        self._client = client
        self._settings = settings
        self._name_patterns = _name_patterns(settings.name_regex)
        self.partial_succeeded: list[str] = []

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def zones(self) -> Sequence[Mapping[str, str]]:
        """Zones of the project (read-only call)."""
        return self._client.list_zones(self._settings.project_id)

    def plan(self, context: RunContext) -> Plan:
        """List every zone and filter. Makes no mutating call."""
        settings = self._settings
        candidates: list[Candidate] = []
        self.partial_succeeded = []
        by_owner: dict[str, list[str]] | None = {} if settings.slack_notify_users else None
        deleted: Deleted = by_owner if by_owner is not None else []
        try:
            for zone_obj in self.zones():
                zone = zone_obj["name"]
                for candidate in self.plan_zone(zone, lambda: context.now):
                    candidates.append(candidate)
                    _record(deleted, settings, candidate.id, candidate.tags)
                    if context.dry_run:
                        self.partial_succeeded.append(candidate.id)
            if context.dry_run:
                for candidate in candidates:
                    log_action("delete", candidate, dry_run=True)
                _log_summary(deleted, dry_run=True)
        finally:
            self._client.close()
        return Plan(candidates=tuple(candidates), by_owner=_frozen_groups(by_owner))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned disks one by one; a failed delete is logged and skipped."""
        settings = self._settings
        by_owner: dict[str, list[str]] | None = {} if settings.slack_notify_users else None
        deleted: Deleted = by_owner if by_owner is not None else []
        succeeded: list[str] = []
        self.partial_succeeded = succeeded
        try:
            succeeded.extend(
                candidate.id
                for candidate in plan.candidates
                if self.apply_disk(candidate.region, candidate, deleted, dry_run=context.dry_run)
            )
            _log_summary(deleted, dry_run=context.dry_run)
        finally:
            self._client.close()
        return Result(succeeded=tuple(succeeded), by_owner=_frozen_groups(by_owner))

    def plan_zone(self, zone: str, clock: Clock) -> Iterator[Candidate]:
        """List one zone lazily and yield the disks to delete, in the order of `main`: attached,
        exception regex and labels, filter labels, detach timestamp, age; then the REGION line."""
        scan = ZoneScan(zone)
        try:
            for disk in self._client.list_disks(self._settings.project_id, zone):
                scan.scanned += 1
                candidate = self._evaluate(disk, zone, clock)
                if candidate is None:
                    continue
                scan.selected += 1
                yield candidate
        except Exception as e:  # noqa: BLE001 - per-zone isolation, listing failure logged and recorded, zone skipped
            logging.error("Error occurred while listing disks of zone %s: %s", zone, e)
            logger.current().fail(zone, f"zone {zone}", e)
        scan.finish()

    def _evaluate(self, disk: DiskView, zone: str, clock: Clock) -> Candidate | None:
        """The candidate for a disk to delete; `None` when it is kept, skipped, or an error
        while evaluating it was recorded (the disk is kept)."""
        try:
            reason = self._reject(disk, clock)
            if reason is not None:
                logger.current().skip(reason)
                return None
            return Candidate(
                id=disk.name, name=disk.name, region=zone, tags=policy.normalize_tags(disk.labels)
            )
        except Exception as e:  # noqa: BLE001 - per-disk isolation, failure logged and recorded, disk kept
            name = str(getattr(disk, "name", ""))
            logging.error("Error occurred while evaluating disk %s: %s", name, e)
            logger.current().fail(zone, name, e)
            return None

    def _reject(self, disk: DiskView, clock: Clock) -> str | None:
        if disk.users:
            return "attached"
        if reason := self._exception_reason(disk):
            return reason
        if self._name_patterns and not policy.name_regex_search_matches(
            self._name_patterns, disk.name
        ):
            return "name_regex_mismatch"
        if not self._has_matching_filter_label(disk):
            return "filter_mismatch"
        if not disk.last_detach_timestamp:
            return "never_detached"
        return None if self._older_than_detach_age(disk, clock) else "too_young"

    def apply_disk(
        self, zone: str, candidate: Candidate, deleted: Deleted, *, dry_run: bool
    ) -> bool:
        """Delete one disk (nothing in a dry run) and record it; an error is logged and the
        disk is not recorded. Returns whether it was recorded."""
        try:
            if not dry_run:
                operation = self._client.delete_disk(self._settings.project_id, zone, candidate.id)
                logging.info("Deleting disk %s", candidate.id)
                if not self._delete_completed(zone, candidate, operation):
                    return False
            _record(deleted, self._settings, candidate.id, candidate.tags)
        except Exception as e:  # noqa: BLE001 - per-resource isolation, failure logged and recorded, resource kept
            logging.error("Error occurred while deleting disk %s: %s", candidate.id, e)
            logger.current().fail(zone, candidate.id, e)
            return False
        log_action("delete", candidate, dry_run=dry_run)
        return True

    def _delete_completed(self, zone: str, candidate: Candidate, operation: object) -> bool:
        """Wait for the zone operation of a disk delete to be DONE without an error, as the GCP
        spot cleaner does; a missing operation name, an operation error or a timeout is logged
        and recorded as a failure."""
        settings = self._settings
        name = operation.get("name") if isinstance(operation, Mapping) else None
        spot = SpotSettings(
            project_id=settings.project_id,
            filter_labels={},
            exception_labels={},
            notags={},
            age={},
            custom_age_tag_key=None,
            wait_sec=settings.wait_sec,
            poll_sec=settings.poll_sec,
        )
        if name and wait_operation(cast(GcpSpotClient, self._client), spot, "zone", zone, name):
            return True
        logging.error("Disk delete failed or incomplete for %s", candidate.id)
        logger.current().fail(zone, candidate.id, "disk delete failed or incomplete")
        return False

    def _exception_reason(self, disk: DiskView) -> str | None:
        settings = self._settings
        if settings.exception_regex:
            patterns = [f"{p}" for p in settings.exception_regex]
            if policy.exception_regex_match_matches(patterns, disk.name):
                return "exception_regex"
        if settings.exception_labels or settings.notags:
            tags = policy.normalize_tags(disk.labels)
            if policy.should_skip_by_tags(
                tags, settings.exception_labels or {}, settings.notags or {}
            ):
                return "exception_tag"
        return None

    def _has_matching_filter_label(self, disk: DiskView) -> bool:
        if not self._settings.filter_labels:
            return True
        tags = policy.normalize_tags(disk.labels)
        return policy.filter_tags_any_key(tags, self._settings.filter_labels)

    def _older_than_detach_age(self, disk: DiskView, clock: Clock) -> bool:
        settings = self._settings
        return is_old_enough(
            disk,
            disk.last_detach_timestamp,
            clock,
            settings.custom_age_tag_key,
            settings.detach_age,
            "disk",
        )


@register("gcp", "disk")
def build_disk_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `project_id`, `filter_tags` (labels), `exception_tags`
    (labels), `exception_regex`, `notags`, `name_regex`, `custom_age_tag_key`, `detach_age`,
    `slack_notify_users` and `slack_user_label`."""
    options = context.options
    project_id = options.get("project_id")
    if not project_id:
        raise ValueError("project_id is required for GCP disk cleanup")
    detach_age = options.get("detach_age") or {}
    if not context.dry_run:
        detach_age = core_age.require_positive_age(detach_age, "detach_age")
    settings = DiskSettings(
        project_id=project_id,
        filter_labels=options.get("filter_tags"),
        exception_labels=options.get("exception_tags"),
        exception_regex=options.get("exception_regex"),
        custom_age_tag_key=options.get("custom_age_tag_key") or "",
        detach_age=detach_age,
        slack_notify_users=bool(options.get("slack_notify_users")),
        slack_user_label=options.get("slack_user_label") or "",
        notags=options.get("notags"),
        name_regex=options.get("name_regex"),
    )
    return DiskCleaner(ComputeDiskClient(), settings)
