# Copyright (c) Yugabyte, Inc.

"""The mutating steps of a Spot VM removal: the VM, its leftover disks and its primary NIC.

`Remover` holds no state of its own; results go into the `Removed` lists it is handed. The
disk tag check (`disk_protected`) only reads and is also used by the dry-run preview.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from crc.azu import _logger
from crc.azu.spot_vm.client import (
    ComputeFactory,
    NetworkFactory,
    SpotComputeClient,
    SpotNetworkClient,
)
from crc.azu.spot_vm.disks import DiskRef
from crc.azu.vm_delete_helpers import begin_delete_vm_and_wait, delete_vm_primary_nic
from crc.core import policy

HTTP_NOT_FOUND = 404

TagSpec = Mapping[str, Sequence[str]]
VmDeleter = Callable[[SpotComputeClient, str, str], None]
NicDeleter = Callable[[SpotNetworkClient, str, str, bool, list[str]], None]


@dataclass(frozen=True, slots=True)
class RemovalOps:
    """The two shared delete helpers; tests can swap them."""

    delete_vm: VmDeleter = begin_delete_vm_and_wait
    delete_nic: NicDeleter = delete_vm_primary_nic


@dataclass(slots=True)
class Removed:
    """What a run removed (or, in a dry run, would remove), in order."""

    vms: list[str] = field(default_factory=list)
    nics: list[str] = field(default_factory=list)
    disks: list[str] = field(default_factory=list)


class Remover:
    """Deletes one Spot VM, its leftover disks and its primary NIC."""

    def __init__(
        self,
        compute_factory: ComputeFactory,
        network_factory: NetworkFactory,
        resource_group: str,
        exception_tags: TagSpec | None,
        notags: TagSpec | None,
        ops: RemovalOps,
    ) -> None:
        self._compute_factory = compute_factory
        self._network_factory = network_factory
        self._resource_group = resource_group
        self._exception_tags = exception_tags or {}
        self._notags = notags or {}
        self._ops = ops

    def disk_protected(self, resource_group: str, disk_name: str) -> bool:
        """True when a leftover disk must be kept: foreign resource group, tags, or a failed read.

        Fails closed when `disks.get` fails for a reason other than 404.
        """
        if resource_group.casefold() != self._resource_group.casefold():
            logging.debug(
                "Skipping leftover Spot disk %s/%s: outside --resource_group %s",
                resource_group,
                disk_name,
                self._resource_group,
            )
            _logger.skip("leftover disk outside resource group")
            return True
        try:
            disk = self._compute_factory().disks.get(resource_group, disk_name)
        except ResourceNotFoundError:
            return False
        except HttpResponseError as e:
            if getattr(e, "status_code", None) == HTTP_NOT_FOUND:
                return False
            self._log_tag_read_failure(resource_group, disk_name, e)
            return True
        except Exception as e:  # noqa: BLE001 - a tag read failure protects the disk (fail closed)
            self._log_tag_read_failure(resource_group, disk_name, e)
            return True
        return self._disk_tags_protect(disk, resource_group, disk_name)

    @staticmethod
    def _log_tag_read_failure(resource_group: str, disk_name: str, error: Exception) -> None:
        logging.error(
            "Failed to get disk %s/%s for tag check (skipping): %s",
            resource_group,
            disk_name,
            error,
        )

    def _disk_tags_protect(self, disk: Any, resource_group: str, disk_name: str) -> bool:
        tags = disk.tags or {}
        if not tags:
            return False
        if not (
            policy.exception_tags_any(tags, self._exception_tags)
            or policy.notags_all(tags, self._notags)
        ):
            return False
        logging.debug(
            "Skipping leftover Spot disk %s/%s: exception_tags/notags on the disk",
            resource_group,
            disk_name,
        )
        _logger.skip("leftover disk exception tag")
        return True

    def remove_disk(
        self,
        resource_group: str,
        disk_name: str,
        rg_from_id: bool,
        dry_run: bool,
        disks: list[str],
    ) -> None:
        """Delete one leftover disk and record it; errors are logged and swallowed."""
        label = f"{resource_group}/{disk_name}"
        if dry_run:
            disks.append(label)
            return
        try:
            poller = self._compute_factory().disks.begin_delete(resource_group, disk_name)
            poller.result(timeout=600)
            logging.info("Deleted leftover Spot VM disk %s", label)
            disks.append(label)
        except ResourceNotFoundError:
            self._disk_gone(label, rg_from_id, disks)
        except HttpResponseError as e:
            if getattr(e, "status_code", None) == HTTP_NOT_FOUND:
                self._disk_gone(label, rg_from_id, disks)
                return
            logging.error("Failed to delete disk %s: %s", label, e)
            _logger.failed(resource_group, label, e)
        except Exception as e:  # noqa: BLE001 - per-disk isolation, failure logged and recorded
            logging.error("Failed to delete disk %s: %s", label, e)
            _logger.failed(resource_group, label, e)

    @staticmethod
    def _disk_gone(label: str, rg_from_id: bool, disks: list[str]) -> None:
        if rg_from_id:
            logging.info("Disk %s already removed", label)
            disks.append(label)
        else:
            logging.error(
                "Disk %s not found (RG was not taken from disk id; not counting as deleted)",
                label,
            )

    def remove_vm_instance(self, vm_name: str) -> None:
        """Delete the VM and wait for it; logs and re-raises any error."""
        logging.info("Deleting Azure Spot VM: %s", vm_name)
        try:
            self._ops.delete_vm(self._compute_factory(), self._resource_group, vm_name)
        except Exception as e:
            logging.error("Error deleting Spot VM %s: %s", vm_name, e)
            raise
        _logger.acted("deleted", self._resource_group, vm_name)

    def remove_nic(self, vm_name: str, dry_run: bool, nics: list[str]) -> None:
        """Delete the NIC `<vm>-NIC` (the helper retries and records it)."""
        self._ops.delete_nic(self._network_factory(), self._resource_group, vm_name, dry_run, nics)

    def remove_vm(self, vm_name: str, leftovers: Sequence[DiskRef], removed: Removed) -> None:
        """Real removal in main's order: VM, then each unprotected leftover disk, then the NIC."""
        self.remove_vm_instance(vm_name)
        removed.vms.append(vm_name)
        for resource_group, disk_name, rg_from_id in leftovers:
            if self.disk_protected(resource_group, disk_name):
                continue
            self.remove_disk(resource_group, disk_name, rg_from_id, False, removed.disks)
        self.remove_nic(vm_name, False, removed.nics)
