# Copyright (c) Yugabyte, Inc.

"""Leftover disks of a Spot VM: disks with autoDelete=false are deleted after the VM."""

import logging

from crc.core import logger
from crc.gcp.spot_vm.client import GcpSpotClient, InstanceView
from crc.gcp.spot_vm.instances import labels_protect
from crc.gcp.spot_vm.operations import wait_operation
from crc.gcp.spot_vm.settings import DiskRef, SpotSettings


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


def disk_ok_to_delete(
    client: GcpSpotClient, settings: SpotSettings, ref: DiskRef, fallback_zone: str
) -> bool:
    """Read the disk and keep it when its own labels match `exception_labels` or `notags`.
    A failed read keeps the disk."""
    project, scope, location, name, label = _target(settings, ref, fallback_zone)
    try:
        disk = client.get_disk(project, scope, location, name)
    except Exception as e:
        logging.error("Failed to get leftover disk %s for label check (skipping): %s", label, e)
        logger.current().fail(location, label, e)
        return False
    if labels_protect(disk.get("labels") or {}, settings):
        logging.debug("Skipping leftover Spot disk %s: exception_labels/notags", label)
        logger.current().skip("disk_exception_tag")
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
            logger.current().fail(location, label, "disk delete operation failed or incomplete")
            return None
        logging.info("Deleted leftover Spot VM disk %s", label)
    except Exception as e:
        logging.error("Failed to delete leftover disk %s: %s", label, e)
        logger.current().fail(location, label, e)
        return None
    return label
