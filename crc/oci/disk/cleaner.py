# Copyright (c) Yugabyte, Inc.

"""OCI block volume and boot volume cleaner (`DiskCleaner`).

`plan()` lists the volumes, boot volumes and attachments of every region and decides on them;
`apply()` calls `delete_volume` / `delete_boot_volume`.
"""

import dataclasses
import datetime
import logging
from collections.abc import Sequence

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
from crc.oci._common import group_by_region
from crc.oci.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.oci.disk import attachments
from crc.oci.disk.api import ApiFactory, RegionLister, SdkVolume, VolumeApi, api_for, regions_of
from crc.oci.disk.decide import skip_reason, sort_decisions
from crc.oci.disk.settings import (
    IN_USE_ATTACHMENT_STATES,
    KIND_BOOT_DISK,
    KIND_DISK,
    KIND_KEY,
    LIST_CONTEXT,
    SKIP_KEYS,
    UNKNOWN_REASONS,
    DiskSettings,
    TagSpec,
    log_summary,
)

__all__ = [
    "IN_USE_ATTACHMENT_STATES",
    "KIND_BOOT_DISK",
    "KIND_DISK",
    "SKIP_KEYS",
    "DiskCleaner",
    "DiskSettings",
    "TagSpec",
    "build_disk_cleaner",
]


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
        *,
        engine: bool = False,
    ) -> None:
        """`engine` is True for the cleaner `crc.core.engine` builds: a dry-run `plan()` then
        logs the ACTION lines per region and names its candidates by display name (the engine
        never calls `apply()` in a dry run)."""
        self._engine = engine
        self._api_factory = api_factory
        self._list_regions = list_regions
        self._settings = settings
        self._unknown_regions: set[str] = set()
        self.partial_succeeded: list[str] = []

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List every region and decide on each volume. Makes no mutating call."""
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        self._unknown_regions = set()
        self.partial_succeeded = []
        for region in self._list_regions():
            started = _logger.region_started()
            before = len(candidates)
            try:
                api = self._api_factory(region)
                volumes, boot_volumes = self.list_region(api)
            except CONNECTIVITY_ERRORS as e:
                log_skipped_region(region, LIST_CONTEXT, e)
                _logger.region_skipped(region, e)
                continue
            attached = self.attached_volume_ids(api, region)
            mark = len(candidates)
            sort_decisions(
                (self.decide(v, attached, KIND_DISK, region, context.now) for v in volumes),
                candidates,
                skipped,
            )
            if context.dry_run:
                self._note_dry_run(candidates[mark:])
            attached_boot = self.attached_boot_volume_ids(api, region)
            mark = len(candidates)
            sort_decisions(
                (
                    self.decide(v, attached_boot, KIND_BOOT_DISK, region, context.now)
                    for v in boot_volumes
                ),
                candidates,
                skipped,
            )
            if context.dry_run:
                self._note_dry_run(candidates[mark:])
            _logger.region_done(
                region, len(volumes) + len(boot_volumes), len(candidates) - before, started
            )
        if context.dry_run:
            log_summary([c.name for c in candidates], dry_run=True)
            if self._engine:
                candidates = [dataclasses.replace(c, id=c.name) for c in candidates]
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def _note_dry_run(self, found: Sequence[Candidate]) -> None:
        if self._engine:
            attachments.note_dry_run(found, self.partial_succeeded)

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned volumes region by region (nothing in a dry run). A failed delete
        is logged and left out of the result, as on main."""
        if context.dry_run and self._engine:
            return Result(succeeded=tuple(c.name for c in plan.candidates))
        deleted: list[str] = []
        self.partial_succeeded = deleted
        failed: list[Failure] = []
        for region, items in group_by_region(plan.candidates).items():
            api = self._api_factory(region)
            for candidate in items:
                error = self.remove(api, candidate, deleted, dry_run=context.dry_run)
                if error is not None:
                    failed.append(error)
        log_summary(deleted, dry_run=context.dry_run)
        return Result(succeeded=tuple(deleted), failed=tuple(failed))

    def list_region(self, api: VolumeApi) -> tuple[Sequence[SdkVolume], Sequence[SdkVolume]]:
        """Block volumes then boot volumes of one region."""
        return api.list_volumes(), api.list_boot_volumes()

    def attached_volume_ids(self, api: VolumeApi, region: str) -> set[str] | None:
        """Ids of block volumes in use, or `None` (every volume of the region is kept)."""
        return attachments.attached_volume_ids(api, region, self._keep_all)

    def attached_boot_volume_ids(self, api: VolumeApi, region: str) -> set[str] | None:
        """Ids of boot volumes in use, or `None` (every boot volume of the region is kept)."""
        return attachments.attached_boot_volume_ids(api, region, self._keep_all)

    def _keep_all(self, region: str, kind: str, error: BaseException) -> None:
        logging.warning("Region %s: keeping every %s: %s", region, kind, UNKNOWN_REASONS[kind])
        if region not in self._unknown_regions:
            self._unknown_regions.add(region)
            _logger.region_skipped(region, error)

    def decide(
        self,
        volume: SdkVolume,
        attached_ids: set[str] | None,
        kind: str,
        region: str,
        now: datetime.datetime,
    ) -> Candidate | Skipped:
        """State, attachment, tag and age checks common to block and boot volumes.
        `attached_ids` is `None` when the attachments are unknown: the volume is kept."""
        reason = skip_reason(self._settings, volume, attached_ids, kind, now)
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
            extra={KIND_KEY: kind},
        )

    def remove(
        self, api: VolumeApi, candidate: Candidate, deleted: list[str], *, dry_run: bool
    ) -> Failure | None:
        """Delete one planned volume and record its name in `deleted` (also in a dry run, where
        nothing is deleted). Any error is logged and returned, not raised."""
        kind = candidate.extra[KIND_KEY]
        try:
            if not dry_run:
                if kind == KIND_BOOT_DISK:
                    api.delete_boot_volume(candidate.id)
                else:
                    api.delete_volume(candidate.id)
                logging.info("Deleting %s %s", kind, candidate.name)
            deleted.append(candidate.name)
        except Exception as e:  # noqa: BLE001 - per-resource isolation, failure logged and recorded, resource kept
            logging.error("Error occurred while deleting %s %s: %s", kind, candidate.name, e)
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
    return DiskCleaner(api_for(base), regions_of(base), settings, engine=True)
