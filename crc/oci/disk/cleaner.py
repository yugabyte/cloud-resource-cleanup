# Copyright (c) Yugabyte, Inc.

"""OCI block volume and boot volume cleaner (`DiskCleaner`).

`DiskCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the volumes,
boot volumes and their attachments of every region and decides on them, `apply()` calls
`delete_volume` / `delete_boot_volume`. The legacy class `Disk` that `crc.py` uses lives in
`adapter.py`.
"""

import datetime
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

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
from crc.oci import _logger
from crc.oci._base import Base
from crc.oci.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.oci.disk.api import (
    ApiFactory,
    RegionLister,
    SdkVolume,
    VolumeApi,
    api_for,
    regions_of,
)

# Attachment lifecycle states that mean the volume/boot volume is still in use and
# must not be deleted. ATTACHING is included alongside ATTACHED since an
# in-progress attachment is not yet reflected as a running instance's dependency
# but the volume is still claimed.
IN_USE_ATTACHMENT_STATES = ("ATTACHED", "ATTACHING")

KIND_DISK = "disk"
KIND_BOOT_DISK = "boot disk"

_AVAILABLE = "AVAILABLE"
_LIST_CONTEXT = "list volumes/boot volumes disk delete"
_ATTACHMENTS_CONTEXT = "list_volume_attachments"
_BOOT_ATTACHMENTS_CONTEXT = "list_boot_volume_attachments"
_KIND_KEY = "kind"

TagSpec = Mapping[str, Sequence[str]]
Clock = Callable[[], datetime.datetime]


@dataclass(frozen=True, slots=True)
class DiskRuntime:
    """Injected collaborators of `Disk`: any of them left as `None` is built from the OCI SDK
    (through `Base`), and the clock supplies `now` (the cleaner itself never reads the clock)."""

    api_factory: ApiFactory | None = None
    list_regions: RegionLister | None = None
    clock: Clock = datetime.datetime.now


@dataclass(frozen=True, slots=True)
class DiskSettings:
    """Filters and thresholds of one run."""

    filter_tags: TagSpec = field(default_factory=dict)
    exception_tags: TagSpec = field(default_factory=dict)
    notags: TagSpec = field(default_factory=dict)
    age: core_age.AgeSpec = field(default_factory=dict)
    custom_age_tag_key: str = ""


def _log_summary(deleted: Sequence[str], *, dry_run: bool) -> None:
    items = list(deleted)
    if not items:
        logging.warning("No OCI disk to delete.")

    if not dry_run:
        logging.warning(f"number of OCI disks deleted: {len(items)}")
        logging.warning(f"List of OCI disk deleted: {items}")
    else:
        logging.warning(f"List of OCI disk (Total: {len(items)}) which will be deleted: {items}")


SKIP_KEYS = {
    "state is not AVAILABLE": "state",
    "attached": "attached",
    "matches exception_tags or notags": "exception_tag",
    "does not match filter_tags": "filter_mismatch",
    "not older than age": "too_young",
}


class DiskCleaner:
    """Deletes `AVAILABLE` block volumes and boot volumes that are not attached, match the
    filter tags (any key), are not excluded by exception tags or notags, and are old enough.
    Applies to every region the injected lister returns."""

    cloud = "oci"
    kind = "disk"

    def __init__(
        self,
        api_factory: ApiFactory,
        list_regions: RegionLister,
        settings: DiskSettings,
    ) -> None:
        self._api_factory = api_factory
        self._list_regions = list_regions
        self._settings = settings

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List every region and decide on each volume. Makes no mutating call."""
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        for region in self._list_regions():
            started = _logger.region_started()
            before = len(candidates)
            try:
                api = self._api_factory(region)
                volumes, boot_volumes = self.list_region(api)
            except CONNECTIVITY_ERRORS as e:
                log_skipped_region(region, _LIST_CONTEXT, e)
                _logger.region_skipped(region, e)
                continue
            attached = self.attached_volume_ids(api, region)
            self._decide_all(volumes, attached, KIND_DISK, region, context.now, candidates, skipped)
            attached_boot = self.attached_boot_volume_ids(api, region)
            self._decide_all(
                boot_volumes,
                attached_boot,
                KIND_BOOT_DISK,
                region,
                context.now,
                candidates,
                skipped,
            )
            _logger.region_done(
                region, len(volumes) + len(boot_volumes), len(candidates) - before, started
            )
        if context.dry_run:
            _log_summary([c.name for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned volumes region by region (nothing in a dry run). A failed delete
        is logged and left out of the result, as on main."""
        by_region: dict[str, list[Candidate]] = {}
        for candidate in plan.candidates:
            by_region.setdefault(candidate.region, []).append(candidate)
        deleted: list[str] = []
        failed: list[Failure] = []
        for region, items in by_region.items():
            api = self._api_factory(region)
            for candidate in items:
                error = self.remove(api, candidate, deleted, dry_run=context.dry_run)
                if error is not None:
                    failed.append(error)
        _log_summary(deleted, dry_run=context.dry_run)
        return Result(succeeded=tuple(deleted), failed=tuple(failed))

    def list_region(self, api: VolumeApi) -> tuple[Sequence[SdkVolume], Sequence[SdkVolume]]:
        """Block volumes then boot volumes of one region."""
        volumes = api.list_volumes()
        boot_volumes = api.list_boot_volumes()
        return volumes, boot_volumes

    def attached_volume_ids(self, api: VolumeApi, region: str) -> set[str]:
        """Ids of block volumes in use. OCI has no "unattached" lifecycle state: a volume stays
        `AVAILABLE` whether or not it is attached, so attachments are listed separately. A
        connectivity error is logged and leaves the set as far as it got."""
        attached_ids: set[str] = set()
        try:
            for attachment in api.list_volume_attachments():
                if attachment.lifecycle_state in IN_USE_ATTACHMENT_STATES:
                    attached_ids.add(attachment.volume_id)
        except CONNECTIVITY_ERRORS as e:
            log_skipped_region(region, _ATTACHMENTS_CONTEXT, e)
        return attached_ids

    def attached_boot_volume_ids(self, api: VolumeApi, region: str) -> set[str]:
        """Ids of boot volumes in use. Boot volume attachments are scoped per availability
        domain, unlike regular volume attachments."""
        attached_ids: set[str] = set()
        try:
            for domain in api.list_availability_domains():
                for attachment in api.list_boot_volume_attachments(domain):
                    if attachment.lifecycle_state in IN_USE_ATTACHMENT_STATES:
                        attached_ids.add(attachment.boot_volume_id)
        except CONNECTIVITY_ERRORS as e:
            log_skipped_region(region, _BOOT_ATTACHMENTS_CONTEXT, e)
        return attached_ids

    def decide(
        self,
        volume: SdkVolume,
        attached_ids: set[str],
        kind: str,
        region: str,
        now: datetime.datetime,
    ) -> Candidate | Skipped:
        """State, attachment, tag and age checks common to block and boot volumes."""
        reason = self._skip_reason(volume, attached_ids, now)
        if reason is not None:
            _logger.record_skip(SKIP_KEYS[reason])
            return Skipped(id=volume.id, reason=reason, name=volume.display_name)
        return Candidate(
            id=volume.id,
            name=volume.display_name,
            region=region,
            state=volume.lifecycle_state,
            launch_time=volume.time_created,
            tags=policy.normalize_tags(volume.freeform_tags),
            extra={_KIND_KEY: kind},
        )

    def remove(
        self, api: VolumeApi, candidate: Candidate, deleted: list[str], *, dry_run: bool
    ) -> Failure | None:
        """Delete one planned volume and record its name in `deleted` (also in a dry run, where
        nothing is deleted). Any error is logged and returned, not raised."""
        kind = candidate.extra[_KIND_KEY]
        try:
            if not dry_run:
                if kind == KIND_BOOT_DISK:
                    api.delete_boot_volume(candidate.id)
                else:
                    api.delete_volume(candidate.id)
                logging.info(f"Deleting {kind} {candidate.name}")
            deleted.append(candidate.name)
        except Exception as e:
            logging.error(f"Error occurred while deleting {kind} {candidate.name}: {e}")
            _logger.record_failure(candidate.region, candidate.id, e)
            return Failure(id=candidate.name, error=str(e))
        _logger.record_action(
            Operation.DELETE,
            dry_run=dry_run,
            region=candidate.region,
            resource_id=candidate.id,
            name=candidate.name,
        )
        return None

    def _decide_all(  # noqa: PLR0913, PLR0917 - accumulators of one plan() pass
        self,
        volumes: Sequence[SdkVolume],
        attached_ids: set[str],
        kind: str,
        region: str,
        now: datetime.datetime,
        candidates: list[Candidate],
        skipped: list[Skipped],
    ) -> None:
        for volume in volumes:
            decision = self.decide(volume, attached_ids, kind, region, now)
            if isinstance(decision, Candidate):
                candidates.append(decision)
            else:
                skipped.append(decision)

    def _skip_reason(
        self, volume: SdkVolume, attached_ids: set[str], now: datetime.datetime
    ) -> str | None:
        if volume.lifecycle_state != _AVAILABLE:
            return "state is not AVAILABLE"
        if volume.id in attached_ids:
            return "attached"
        settings = self._settings
        tags = policy.normalize_tags(volume.freeform_tags)
        if policy.should_skip_by_tags(tags, settings.exception_tags, settings.notags):
            return "matches exception_tags or notags"
        if not policy.filter_tags_any_key(tags, settings.filter_tags):
            return "does not match filter_tags"
        if not self._is_old(volume, now):
            return "not older than age"
        return None

    def _is_old(self, volume: SdkVolume, now: datetime.datetime) -> bool:
        """Older than `age`, or than the volume's own custom age tag (capped by MAX_AGE)."""
        created_time = volume.time_created
        current = now.astimezone(created_time.tzinfo)
        tags = volume.freeform_tags or {}
        logging.debug(tags)
        retention_age = core_age.get_retention_age(
            cast(dict[str, str], tags), self._settings.custom_age_tag_key
        )
        if retention_age:
            logging.info(f"Updating age for volume: {volume.display_name}")
        return core_age.is_old(
            policy.effective_age(retention_age, self._settings.age), current, created_time
        )


@register("oci", "disk")
def build_disk_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `filter_tags`, `exception_tags`, `notags` (dicts), `age`
    (dict) and `custom_age_tag_key`."""
    options = context.options
    base = Base()
    settings = DiskSettings(
        filter_tags=options.get("filter_tags") or {},
        exception_tags=options.get("exception_tags") or {},
        notags=options.get("notags") or {},
        age=options.get("age") or {},
        custom_age_tag_key=options.get("custom_age_tag_key") or "",
    )
    return DiskCleaner(api_for(base), regions_of(base), settings)
