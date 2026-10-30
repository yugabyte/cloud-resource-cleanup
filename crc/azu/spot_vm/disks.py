# Copyright (c) Yugabyte, Inc.

"""Leftover disks of a Spot VM: which managed disks survive the VM delete, and where they are.

Pure functions apart from the INFO line logged for every leftover disk found. Disks whose
`delete_option` is `Delete` go with the VM; Detach, or unset (Azure's Detach default), stay.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

DiskRef = tuple[str, str, bool]
"""(resource group, disk name, resource group was parsed from the disk id)."""


@dataclass(frozen=True, slots=True)
class DiskSlot:
    """One disk attached to a VM as the SDK reports it, reduced to plain values."""

    kind: str
    delete_option: str
    disk_id: str | None


def disk_name_from_id(disk_id: str | None) -> str | None:
    """Last path segment of a managed disk ARM id; None for an empty id."""
    if not disk_id:
        return None
    return disk_id.rstrip("/").split("/")[-1]


def disk_ref_from_id(
    disk_id: str | None, resource_group: str, fallback_name: str | None = None
) -> DiskRef | None:
    """(resource group, disk name, rg_from_id) of a managed disk ARM id.

    `rg_from_id` is True only when the resource group was parsed from the id and not taken
    from `resource_group` (the VM's own group).
    """
    name = disk_name_from_id(disk_id) or fallback_name
    if not name:
        return None
    rg = resource_group
    rg_from_id = False
    if disk_id:
        parts = disk_id.strip("/").split("/")
        for i, part in enumerate(parts):
            if part.lower() == "resourcegroups" and i + 1 < len(parts):
                rg = parts[i + 1]
                rg_from_id = True
                break
    return rg, name, rg_from_id


def _slot(kind: str, disk: Any) -> DiskSlot:
    delete_option = str(getattr(disk, "delete_option", "") or "").lower()
    managed = getattr(disk, "managed_disk", None)
    disk_id = getattr(managed, "id", None) if managed else None
    return DiskSlot(kind, delete_option, disk_id)


def disk_slots(vm: Any) -> tuple[DiskSlot, ...]:
    """The OS disk (if any) then the data disks of an SDK VM, as plain values."""
    profile = getattr(vm, "storage_profile", None)
    if not profile:
        return ()
    slots: list[DiskSlot] = []
    os_disk = getattr(profile, "os_disk", None)
    if os_disk:
        slots.append(_slot("OS", os_disk))
    slots.extend(_slot("data", disk) for disk in getattr(profile, "data_disks", None) or [])
    return tuple(slots)


def detached_disk_refs(
    vm_name: str, slots: Sequence[DiskSlot], resource_group: str
) -> list[DiskRef]:
    """Disks that survive the VM delete, in slot order, logging each one.

    Only managed disks with an ARM id count: ephemeral OS disks and name-only disks are never
    guessed at (a name could match an unrelated disk in the group).
    """
    refs: list[DiskRef] = []
    for slot in slots:
        if not slot.disk_id or slot.delete_option == "delete":
            continue
        ref = disk_ref_from_id(slot.disk_id, resource_group)
        if not ref:
            continue
        refs.append(ref)
        logging.info(
            "Spot VM %s %s disk %s/%s has delete_option=%s (will delete after VM)",
            vm_name,
            slot.kind,
            ref[0],
            ref[1],
            slot.delete_option or "Detach(default)",
        )
    return refs
