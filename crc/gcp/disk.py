# Copyright (c) Yugabyte, Inc.

"""GCP disk cleaner.

`DiskCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the zones of
the project and their disks and filters them, `apply()` deletes the planned disks.
`Disk` is the legacy class `crc.py` uses; it keeps its constructor, attributes and result
shapes and runs the cleaner disk by disk, in the exact call order of the original implementation.
"""

import datetime
import logging
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, cast

from google.cloud import compute_v1
from googleapiclient import discovery

from crc.core import age as core_age
from crc.core import logger, policy
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext
from crc.core.registry import register
from crc.gcp._scan import ZoneScan, log_action
from crc.service import Service

_SERVICE_NAME = "compute"
_SERVICE_VERSION = "v1"
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"
_NOT_TAGGED = "not_tagged"

Clock = Callable[[], datetime.datetime]
Deleted = list[str] | dict[str, list[str]]


class DiskView(Protocol):
    """The four fields of a GCP disk this cleaner reads."""

    @property
    def users(self) -> Sequence[str]:
        """Instances the disk is attached to; empty when unattached."""
        ...

    @property
    def name(self) -> str:
        """Name of the disk."""
        ...

    @property
    def labels(self) -> Mapping[str, str]:
        """Labels of the disk; empty when unlabelled."""
        ...

    @property
    def last_detach_timestamp(self) -> str:
        """RFC 3339 time the disk was last detached; empty when never attached."""
        ...


class GcpDiskClient(Protocol):
    """The Compute Engine calls this cleaner uses. `close()` ends the session the first
    `list_zones` or `delete_disk` call opened."""

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        """The `items` of the zones listing (one page, as dictionaries with a `name`)."""
        ...

    def list_disks(self, project_id: str, zone: str) -> Iterable[DiskView]:
        """Disks of one zone."""
        ...

    def delete_disk(self, project_id: str, zone: str, disk: str) -> object:
        """Delete one disk of a zone."""
        ...

    def close(self) -> None:
        """Close the session; a no-op when none is open."""
        ...


class ComputeDiskClient:
    """`GcpDiskClient` on google-api-python-client (zones, deletes) and google-cloud-compute
    (disk listing); as on `main` one discovery service serves a run."""

    def __init__(self) -> None:
        self._service: Any = None
        self._disks: compute_v1.DisksClient | None = None

    def _compute(self) -> Any:
        if self._service is None:
            self._service = discovery.build(_SERVICE_NAME, _SERVICE_VERSION)
        return self._service

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        request = self._compute().zones().list(project=project_id)
        zones: Sequence[Mapping[str, str]] = request.execute()["items"]
        self._disks = compute_v1.DisksClient()
        return zones

    def list_disks(self, project_id: str, zone: str) -> Iterable[DiskView]:
        if self._disks is None:
            self._disks = compute_v1.DisksClient()
        disks: Iterable[DiskView] = self._disks.list(project=project_id, zone=zone)
        return disks

    def delete_disk(self, project_id: str, zone: str, disk: str) -> object:
        return self._compute().disks().delete(project=project_id, zone=zone, disk=disk).execute()

    def close(self) -> None:
        service, self._service = self._service, None
        if service is not None:
            service.close()


@dataclass(frozen=True, slots=True)
class DiskSettings:
    """Filters and options of one run: only the fields `main` reads."""

    project_id: str
    filter_labels: Mapping[str, Sequence[str]] | None
    exception_labels: Mapping[str, Sequence[str]] | None
    exception_regex: Sequence[str] | None
    custom_age_tag_key: str
    detach_age: core_age.AgeSpec | int
    slack_notify_users: bool = False
    slack_user_label: str = ""


def _frozen_groups(groups: dict[str, list[str]] | None) -> dict[str, tuple[str, ...]] | None:
    if groups is None:
        return None
    return {owner: tuple(names) for owner, names in groups.items()}


def _record(deleted: Deleted, settings: DiskSettings, name: str, labels: Mapping[str, str]) -> None:
    """Record a deleted (or listed) disk by name, or by `slack_user_label` (`not_tagged`)."""
    if not settings.slack_notify_users:
        cast(list[str], deleted).append(name)
        return
    groups = cast(dict[str, list[str]], deleted)
    label = settings.slack_user_label
    owner = labels.get(label, _NOT_TAGGED)
    groups.setdefault(owner, []).append(name)


def _log_summary(deleted: Deleted, *, dry_run: bool) -> None:
    if not deleted:
        logging.warning("No GCP disk to delete.")
    if not dry_run:
        logging.warning(f"number of GCP disks deleted: {len(deleted)}")
        logging.warning(f"List of GCP disk deleted: {deleted}")
    else:
        logging.warning(
            f"List of GCP disk (Total: {len(deleted)}) which will be deleted: {deleted}"
        )


class DiskCleaner:
    """Deletes unattached disks with a `last_detach_timestamp` that are at least `detach_age`
    (or the custom age label) old, match `filter_labels` (any key; empty matches all) and are
    not excluded by `exception_regex` (`re.match` on the name) or `exception_labels`.
    Applies to every zone of the project."""

    cloud = "gcp"
    kind = "disk"

    def __init__(self, client: GcpDiskClient, settings: DiskSettings) -> None:
        self._client = client
        self._settings = settings

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def zones(self) -> Sequence[Mapping[str, str]]:
        """Zones of the project (read-only call)."""
        return self._client.list_zones(self._settings.project_id)

    def plan(self, context: RunContext) -> Plan:
        """List every zone and filter. Makes no mutating call."""
        settings = self._settings
        candidates: list[Candidate] = []
        try:
            for zone_obj in self.zones():
                zone = zone_obj["name"]
                candidates.extend(self.plan_zone(zone, lambda: context.now))
        finally:
            self._client.close()
        by_owner: dict[str, list[str]] | None = {} if settings.slack_notify_users else None
        deleted: Deleted = by_owner if by_owner is not None else []
        for candidate in candidates:
            _record(deleted, settings, candidate.id, candidate.tags)
        if context.dry_run:
            for candidate in candidates:
                log_action("delete", candidate, dry_run=True)
            _log_summary(deleted, dry_run=True)
        return Plan(candidates=tuple(candidates), by_owner=_frozen_groups(by_owner))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned disks one by one; a failed delete is logged and skipped."""
        settings = self._settings
        by_owner: dict[str, list[str]] | None = {} if settings.slack_notify_users else None
        deleted: Deleted = by_owner if by_owner is not None else []
        succeeded: list[str] = []
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
        for disk in self._client.list_disks(self._settings.project_id, zone):
            scan.scanned += 1
            reason = self._reject(disk, clock)
            if reason is not None:
                logger.current().skip(reason)
                continue
            scan.selected += 1
            yield Candidate(
                id=disk.name, name=disk.name, region=zone, tags=policy.normalize_tags(disk.labels)
            )
        scan.finish()

    def _reject(self, disk: DiskView, clock: Clock) -> str | None:
        if disk.users:
            return "attached"
        if reason := self._exception_reason(disk):
            return reason
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
                self._client.delete_disk(self._settings.project_id, zone, candidate.id)
                logging.info(f"Deleting disk {candidate.id}")
            _record(deleted, self._settings, candidate.id, candidate.tags)
        except Exception as e:
            logging.error(f"Error occurred while deleting disk {candidate.id}: {e}")
            logger.current().fail(zone, candidate.id, e)
            return False
        log_action("delete", candidate, dry_run=dry_run)
        return True

    def _exception_reason(self, disk: DiskView) -> str | None:
        settings = self._settings
        if settings.exception_regex:
            patterns = [f"{p}" for p in settings.exception_regex]
            if policy.exception_regex_match_matches(patterns, disk.name):
                return "exception_regex"
        if settings.exception_labels:
            tags = policy.normalize_tags(disk.labels)
            if policy.exception_tags_any(tags, settings.exception_labels):
                return "exception_tag"
        return None

    def _has_matching_filter_label(self, disk: DiskView) -> bool:
        if not self._settings.filter_labels:
            return True
        tags = policy.normalize_tags(disk.labels)
        return policy.filter_tags_any_key(tags, self._settings.filter_labels)

    def _older_than_detach_age(self, disk: DiskView, clock: Clock) -> bool:
        detached = datetime.datetime.strptime(disk.last_detach_timestamp, _TIME_FORMAT)
        now = clock().astimezone(detached.tzinfo)
        logging.debug(disk.labels)
        settings = self._settings
        retention_age = core_age.get_retention_age(
            cast(dict[str, str], disk.labels), settings.custom_age_tag_key
        )
        if retention_age:
            logging.info(f"Updating age for disk: {disk.name}")
        # An int is a number of days, which `is_old` accepts.
        default_age = cast(core_age.AgeSpec, settings.detach_age)
        return core_age.is_old(policy.effective_age(retention_age, default_age), now, detached)


@register("gcp", "disk")
def build_disk_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `project_id`, `filter_tags` (labels), `exception_tags`
    (labels), `exception_regex`, `custom_age_tag_key`, `detach_age`, `slack_notify_users`
    and `slack_user_label`."""
    options = context.options
    project_id = options.get("project_id")
    if not project_id:
        raise ValueError("project_id is required for GCP disk cleanup")
    settings = DiskSettings(
        project_id=project_id,
        filter_labels=options.get("filter_tags"),
        exception_labels=options.get("exception_tags"),
        exception_regex=options.get("exception_regex"),
        custom_age_tag_key=options.get("custom_age_tag_key") or "",
        detach_age=options.get("detach_age") or {},
        slack_notify_users=bool(options.get("slack_notify_users")),
        slack_user_label=options.get("slack_user_label") or "",
    )
    return DiskCleaner(ComputeDiskClient(), settings)


class Disk(Service):
    """Legacy entry point used by `crc.py`: deletes unattached GCP disks (see `DiskCleaner`);
    in a dry run it only lists them. `age`, `notags` and `name_regex` are stored, never used.

    Attributes: `service_name`, `service_version`, `time_format`, `default_disk_state`,
    `disk_names_to_delete` (a list of names, or with `slack_notify_users` a dict of owner to
    names), and the constructor arguments. `get_deleted` and `count` read the result.
    """

    service_name = _SERVICE_NAME
    service_version = _SERVICE_VERSION
    time_format = _TIME_FORMAT
    default_disk_state: ClassVar[list[str]] = ["Unattached"]

    def __init__(  # noqa: PLR0913, PLR0917 - the eleven parameters are the public signature
        self,
        dry_run: bool,
        project_id: str,
        filter_labels: dict[str, list[str]],
        exception_labels: dict[str, list[str]],
        age: int,
        custom_age_tag_key: str,
        detach_age: core_age.AgeSpec | int,
        notags: dict[str, list[str]],
        name_regex: list[str],
        exception_regex: list[str],
        slack_notify_users: bool,
        slack_user_label: str,
        client: GcpDiskClient | None = None,
        clock: Clock = datetime.datetime.now,
    ) -> None:
        super().__init__()
        self.dry_run = dry_run
        self.project_id = project_id
        self.filter_labels = filter_labels
        self.exception_labels = exception_labels
        self.age = age
        self.custom_age_tag_key = custom_age_tag_key
        self.detach_age = detach_age
        self.notags = notags
        self.name_regex = name_regex
        self.exception_regex = exception_regex
        self.slack_notify_users = slack_notify_users
        self.slack_user_label = slack_user_label
        self._client: GcpDiskClient = client or ComputeDiskClient()
        self._clock = clock
        self.disk_names_to_delete: Deleted = {} if self.slack_notify_users else []

    @property
    def get_deleted(self) -> Deleted:
        """Disks deleted (listed in a dry run): names, or owner to names."""
        return self.disk_names_to_delete

    @property
    def count(self) -> int:
        """Entries of `disk_names_to_delete` (owners with `slack_notify_users`); logs it."""
        count = len(self.disk_names_to_delete)
        logging.info(f"count of items in disk_names_to_delete: {count}")
        return count

    def _cleaner(self) -> DiskCleaner:
        settings = DiskSettings(
            project_id=self.project_id,
            filter_labels=self.filter_labels,
            exception_labels=self.exception_labels,
            exception_regex=self.exception_regex,
            custom_age_tag_key=self.custom_age_tag_key,
            detach_age=self.detach_age,
            slack_notify_users=self.slack_notify_users,
            slack_user_label=self.slack_user_label,
        )
        return DiskCleaner(self._client, settings)

    def delete(self, disk_state: list[str] = default_disk_state) -> None:
        """Delete (or, in a dry run, only list) the matching disks of every zone, disk by disk.
        `disk_state` is accepted and ignored, as on main."""
        cleaner = self._cleaner()
        try:
            for zone_obj in cleaner.zones():
                zone = zone_obj["name"]
                for candidate in cleaner.plan_zone(zone, self._clock):
                    cleaner.apply_disk(
                        zone, candidate, self.disk_names_to_delete, dry_run=self.dry_run
                    )
            _log_summary(self.disk_names_to_delete, dry_run=self.dry_run)
        finally:
            self._client.close()
