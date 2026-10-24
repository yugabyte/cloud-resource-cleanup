# Copyright (c) Yugabyte, Inc.

"""Which volumes are attached (so kept) and the dry-run ACTION lines of `DiskCleaner.plan()`."""

from collections.abc import Callable, Sequence

from crc.core.cleaner import Candidate, Operation
from crc.oci import _logger
from crc.oci.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.oci.disk.api import VolumeApi
from crc.oci.disk.decide import in_use
from crc.oci.disk.settings import (
    ATTACHMENTS_CONTEXT,
    BOOT_ATTACHMENTS_CONTEXT,
    KIND_BOOT_DISK,
    KIND_DISK,
)

KeepAll = Callable[[str, str, BaseException], None]


def attached_volume_ids(api: VolumeApi, region: str, keep_all: KeepAll) -> set[str] | None:
    """Ids of block volumes in use, or `None` when the attachments cannot be listed (a
    connectivity error). OCI has no "unattached" lifecycle state: a volume stays `AVAILABLE`
    whether or not it is attached, so attachments are listed separately. `None` means every
    block volume of the region is kept (fail closed, BUG-096)."""
    try:
        return {a.volume_id for a in in_use(api.list_volume_attachments())}
    except CONNECTIVITY_ERRORS as e:
        log_skipped_region(region, ATTACHMENTS_CONTEXT, e)
        keep_all(region, KIND_DISK, e)
        return None


def attached_boot_volume_ids(api: VolumeApi, region: str, keep_all: KeepAll) -> set[str] | None:
    """Ids of boot volumes in use, or `None` when they cannot be known: a connectivity
    error while listing availability domains or any domain's attachments (BUG-097), or a
    region without availability domains (BUG-098). `None` means every boot volume of the
    region is kept (fail closed). Boot volume attachments are scoped per availability
    domain, unlike regular volume attachments."""
    attached_ids: set[str] = set()
    try:
        domains = api.list_availability_domains()
        if not domains:
            keep_all(region, KIND_BOOT_DISK, ValueError("no availability domains"))
            return None
        for domain in domains:
            attached_ids.update(
                a.boot_volume_id for a in in_use(api.list_boot_volume_attachments(domain))
            )
    except CONNECTIVITY_ERRORS as e:
        log_skipped_region(region, BOOT_ATTACHMENTS_CONTEXT, e)
        keep_all(region, KIND_BOOT_DISK, e)
        return None
    return attached_ids


def note_dry_run(found: Sequence[Candidate], partial: list[str]) -> None:
    """The ACTION lines of a dry run, logged per region while `plan()` walks
    the region (the engine never calls `apply()` in a dry run); `partial` collects the names."""
    for candidate in found:
        partial.append(candidate.name)
        _logger.record_action(
            Operation.DELETE,
            dry_run=True,
            region=candidate.region,
            resource_id=candidate.id,
            name=candidate.name,
        )
