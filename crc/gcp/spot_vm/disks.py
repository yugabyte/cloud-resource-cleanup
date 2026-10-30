# Copyright (c) Yugabyte, Inc.

"""Leftover disks of a Spot VM: disks with autoDelete=false are deleted after the VM."""

import datetime
import logging
from collections.abc import Mapping
from typing import Any

from crc.core import age as core_age
from crc.core import logger
from crc.gcp.spot_vm.client import GcpSpotClient, InstanceView
from crc.gcp.spot_vm.instances import Clock, labels_protect
from crc.gcp.spot_vm.operations import wait_operation
from crc.gcp.spot_vm.settings import TIME_FORMAT, DiskRef, SpotSettings


def disk_ref_from_source(source: str | None, project_id: str) -> DiskRef | None:
    """Parse a disk source URL into project / scope / location / name.

    Supports `projects/P/zones/Z/disks/NAME` and `projects/P/regions/R/disks/NAME`; any other
    string is a name only, in the caller's project and zone (empty location).
    """
    if not source:
        return None
    parts = source.rstrip("/").split("/")
    try:
        project = parts[parts.index("projects") + 1]
        name = parts[-1]
        if "zones" in parts:
            return {
                "project": project,
                "scope": "zone",
                "location": parts[parts.index("zones") + 1],
                "name": name,
            }
        if "regions" in parts:
            return {
                "project": project,
                "scope": "region",
                "location": parts[parts.index("regions") + 1],
                "name": name,
            }
    except (ValueError, IndexError):
        pass
    last = parts[-1] if parts else None
    if not last:
        return None
    return {"project": project_id, "scope": "zone", "location": "", "name": last}


def non_autodelete_disks(instance: InstanceView, project_id: str) -> list[DiskRef]:
    """Disks of the instance with `auto_delete` false (logged), as parsed references."""
    refs: list[DiskRef] = []
    for disk in getattr(instance, "disks", None) or []:
        if getattr(disk, "auto_delete", True):
            continue
        ref = disk_ref_from_source(getattr(disk, "source", None), project_id)
        if ref:
            refs.append(ref)
            logging.info(
                "Spot VM %s disk %s/%s/%s has autoDelete=false (will delete after VM)",
                instance.name,
                ref["project"],
                ref["location"] or "?",
                ref["name"],
            )
    return refs


def _target(
    settings: SpotSettings, ref: DiskRef, fallback_zone: str
) -> tuple[str, str, str, str, str]:
    project = ref.get("project") or settings.project_id
    scope = ref.get("scope") or "zone"
    location = ref.get("location") or fallback_zone
    name = ref["name"]
    return project, scope, location, name, f"{project}/{location}/{name}"


def disk_label(settings: SpotSettings, ref: DiskRef, fallback_zone: str) -> str:
    """`project/location/name`, as recorded in the deleted-disk list."""
    return _target(settings, ref, fallback_zone)[4]


def _custom_age_keeps(
    disk: Mapping[str, Any], labels: dict[str, str], settings: SpotSettings, clock: Clock | None
) -> str | None:
    """BUG-242: the keep reason when the disk carries the custom age label and is younger than
    it, or when the label is invalid, the disk age is unreadable or no clock was injected (fail
    closed). None when the disk has no custom age label, or is old enough for it."""
    try:
        retention_age = core_age.validated_retention_age(labels, settings.custom_age_tag_key or "")
    except ValueError:
        return "disk_invalid_age_tag"
    if not retention_age:
        return None
    if clock is None:
        return "disk_age_unknown"
    try:
        created = datetime.datetime.strptime(str(disk.get("creationTimestamp")), TIME_FORMAT)  # noqa: DTZ007 - TIME_FORMAT ends in %z, so the result is aware
        now = clock().astimezone(created.tzinfo)
        old = core_age.is_old(retention_age, now, created)
    except (ValueError, TypeError):
        return "disk_age_unknown"
    return None if old else "disk_too_young"


def disk_ok_to_delete(
    client: GcpSpotClient,
    settings: SpotSettings,
    ref: DiskRef,
    fallback_zone: str,
    clock: Clock | None = None,
) -> bool:
    """Read the disk and keep it when its own labels match `exception_labels` or `notags`, or
    (BUG-242) when it carries the custom age label and is younger than it or the label is
    invalid. A failed read keeps the disk."""
    project, scope, location, name, label = _target(settings, ref, fallback_zone)
    try:
        disk = client.get_disk(project, scope, location, name)
    except Exception as e:  # noqa: BLE001 - SAF-039 a failed disk read keeps the disk
        logging.error("Failed to get leftover disk %s for label check (skipping): %s", label, e)
        logger.current().fail(location, label, e, logged=True)
        return False
    disk_labels = disk.get("labels") or {}
    if labels_protect(disk_labels, settings):
        logging.debug("Skipping leftover Spot disk %s: exception_labels/notags", label)
        logger.current().skip("disk_exception_tag")
        return False
    reason = _custom_age_keeps(disk, disk_labels, settings, clock)
    if reason is not None:
        logging.debug("Skipping leftover Spot disk %s: %s", label, reason)
        logger.current().skip(reason)
        return False
    return True


def remove_leftover_disk(
    client: GcpSpotClient,
    settings: SpotSettings,
    ref: DiskRef,
    fallback_zone: str,
    *,
    dry_run: bool,
) -> str | None:
    """Delete one disk and wait for the operation; the label when it counts as deleted
    (always in a dry run, which calls nothing), else None (the error is logged)."""
    project, scope, location, name, label = _target(settings, ref, fallback_zone)
    if dry_run:
        return label
    try:
        operation = client.delete_disk(project, scope, location, name)
        op_name = operation.get("name")
        if op_name and not wait_operation(client, settings, scope, location, op_name, project):
            logging.error("Disk delete operation failed/incomplete for %s; not counted", label)
            logger.current().fail(
                location, label, "disk delete operation failed or incomplete", logged=True
            )
            return None
        logging.info("Deleted leftover Spot VM disk %s", label)
    except Exception as e:  # noqa: BLE001 - per-disk isolation, failure logged and recorded
        logging.error("Failed to delete leftover disk %s: %s", label, e)
        logger.current().fail(location, label, e, logged=True)
        return None
    return label
