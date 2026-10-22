# Copyright (c) Yugabyte, Inc.

"""Azure Spot VM cleaner and the legacy `SpotVM` adapter `crc.py` uses.

`AzureSpotVmCleaner` (registered as `azure`/`spot_instances`) holds the logic. `SpotVM` keeps the
constructor, attributes and methods of the original class and runs the cleaner VM by VM, in the
exact call order of the original.
"""

import datetime
import logging
from collections.abc import Callable
from typing import Any, cast

from crc.azu import _logger
from crc.azu._base import Base
from crc.azu.spot_vm.cleaner import (
    STOPPED_POWER_STATES,
    AzureSpotVmCleaner,
    SpotVmConfig,
    TagSpec,
    build_spot_vm_cleaner,
    log_summary,
)
from crc.azu.spot_vm.client import (
    ComputeFactory,
    DiskOperations,
    NetworkFactory,
    Poller,
    SpotComputeClient,
    SpotNetworkClient,
    VirtualMachineOperations,
)
from crc.azu.spot_vm.disks import (
    DiskRef,
    DiskSlot,
    detached_disk_refs,
    disk_name_from_id,
    disk_ref_from_id,
    disk_slots,
)
from crc.azu.spot_vm.removal import RemovalOps, Removed, Remover
from crc.azu.vm_delete_helpers import begin_delete_vm_and_wait, delete_vm_primary_nic
from crc.core import age as core_age
from crc.service import Service

__all__ = [
    "STOPPED_POWER_STATES",
    "AzureSpotVmCleaner",
    "ComputeFactory",
    "DiskOperations",
    "DiskRef",
    "DiskSlot",
    "NetworkFactory",
    "Poller",
    "RemovalOps",
    "Removed",
    "Remover",
    "SpotComputeClient",
    "SpotNetworkClient",
    "SpotVM",
    "SpotVmConfig",
    "VirtualMachineOperations",
    "build_spot_vm_cleaner",
    "detached_disk_refs",
    "disk_name_from_id",
    "disk_ref_from_id",
    "disk_slots",
    "log_summary",
]


def _delete_vm_via_helper(client: Any, resource_group: str, vm_name: str) -> None:
    begin_delete_vm_and_wait(client, resource_group, vm_name)


def _delete_nic_via_helper(
    client: Any, resource_group: str, vm_name: str, dry_run: bool, nics: list[str]
) -> None:
    delete_vm_primary_nic(client, resource_group, vm_name, dry_run, nics)


class SpotVM(Service):
    """
    Deletes Azure Spot VMs that are in stopped or deallocated state.

    Azure has no separate "spot request" object: deleting the VM is the cancel.
    Disks whose delete_option is Detach, or unset (Azure's Detach default), are
    removed after the VM so they are not left orphaned (same idea as AWS
    DeleteOnTermination=false). Explicit Delete is left to Azure.

    Attributes: `instance_names_to_delete`, `nics_names_to_delete`, `disks_names_to_delete`,
    `base`, `dry_run`, `filter_tags`, `exception_tags`, `age`, `custom_age_tag_key`, `notags`.
    """

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        resource_group: str,
        dry_run: bool,
        filter_tags: TagSpec | None,
        exception_tags: TagSpec | None,
        age: core_age.AgeSpec | int,
        custom_age_tag_key: str | None,
        notags: TagSpec | None,
        clock: Callable[[], datetime.datetime] = datetime.datetime.now,
    ) -> None:
        super().__init__()
        self._clock = clock
        self.instance_names_to_delete: list[str] = []
        self.nics_names_to_delete: list[str] = []
        self.disks_names_to_delete: list[str] = []
        self.base = Base(resource_group)
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = self.require_positive_age(age)
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags

    @property
    def get_deleted(self) -> list[str]:
        return self.instance_names_to_delete

    @property
    def delete_count(self) -> int:
        return len(self.instance_names_to_delete)

    @property
    def get_deleted_nic(self) -> list[str]:
        return self.nics_names_to_delete

    @property
    def nic_delete_count(self) -> int:
        return len(self.nics_names_to_delete)

    @property
    def get_deleted_disks(self) -> list[str]:
        return self.disks_names_to_delete

    def _cleaner(self) -> AzureSpotVmCleaner:
        config = SpotVmConfig(
            resource_group=self.base.resource_group,
            filter_tags=self.filter_tags,
            exception_tags=self.exception_tags,
            notags=self.notags,
            age=self.age,
            custom_age_tag_key=self.custom_age_tag_key,
        )
        return AzureSpotVmCleaner(
            cast(ComputeFactory, self.base.get_compute_client),
            cast(NetworkFactory, self.base.get_network_client),
            config,
            RemovalOps(delete_vm=_delete_vm_via_helper, delete_nic=_delete_nic_via_helper),
        )

    def _is_spot_vm(self, vm: Any) -> bool:
        priority = getattr(vm, "priority", None)
        if priority is None:
            return False
        return str(priority).lower() == "spot"

    def _get_vm_power_status(self, vm_name: str) -> str:
        return self._cleaner().power_status(vm_name)

    def _should_skip_instance(self, vm: Any) -> bool:
        return self._cleaner().tags_skip(vm)

    def _should_include_by_tags(self, vm: Any) -> bool:
        return self._cleaner().tags_include(vm)

    def _disk_name_from_id(self, disk_id: str | None) -> str | None:
        return disk_name_from_id(disk_id)

    def _disk_ref_from_id(
        self, disk_id: str | None, fallback_name: str | None = None
    ) -> DiskRef | None:
        return disk_ref_from_id(disk_id, self.base.resource_group, fallback_name)

    def _detached_disk_refs(self, vm: Any) -> list[DiskRef]:
        return detached_disk_refs(vm.name, disk_slots(vm), self.base.resource_group)

    def _detached_disk_names(self, vm: Any) -> list[str]:
        return [name for _, name, _ in self._detached_disk_refs(vm)]

    def _disk_should_skip_delete(self, resource_group: str, disk_name: str) -> bool:
        return self._cleaner().remover.disk_protected(resource_group, disk_name)

    def _delete_disk(self, resource_group: str, disk_name: str, rg_from_id: bool = False) -> None:
        self._cleaner().remover.remove_disk(
            resource_group, disk_name, rg_from_id, self.dry_run, self.disks_names_to_delete
        )

    def _delete_vm(self, vm: Any) -> None:
        leftover_disks = self._detached_disk_refs(vm)
        vm_name = vm.name
        remover = self._cleaner().remover

        if not self.dry_run:
            remover.remove_vm_instance(vm_name)
        else:
            _logger.acted("would_delete", self.base.resource_group, vm_name)
        self.instance_names_to_delete.append(vm_name)

        for resource_group, disk_name, rg_from_id in leftover_disks:
            if self._disk_should_skip_delete(resource_group, disk_name):
                continue
            self._delete_disk(resource_group, disk_name, rg_from_id)

        remover.remove_nic(vm_name, self.dry_run, self.nics_names_to_delete)

    def delete(self) -> None:
        """
        Delete Azure Spot VMs that are stopped or deallocated and match
        filter/exception tags and age. In dry_run mode only lists candidates.
        """
        cleaner = self._cleaner()
        started = _logger.started()
        scanned = selected = 0
        for vm in cleaner.list_vms():
            scanned += 1
            candidate, _ = cleaner.decide(vm, self._clock())
            if candidate is None:
                continue
            selected += 1
            try:
                self._delete_vm(vm)
            except Exception as e:
                logging.error("Error deleting Spot VM %s: %s", vm.name, e)
                _logger.failed(self.base.resource_group, vm.name, e)
        _logger.region_done(self.base.resource_group, scanned, selected, started)

        log_summary(
            Removed(
                self.instance_names_to_delete,
                self.nics_names_to_delete,
                self.disks_names_to_delete,
            ),
            dry_run=self.dry_run,
        )
