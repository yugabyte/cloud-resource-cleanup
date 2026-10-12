# Copyright (c) Yugabyte, Inc.

import datetime
import logging
from typing import Dict, List, Optional

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from crc.azu._base import Base
from crc.azu.vm_delete_helpers import (
    begin_delete_vm_and_wait,
    delete_vm_primary_nic,
)
from crc.service import Service


# Power states for stopped/deallocated VMs (from Azure instance_view display_status)
STOPPED_POWER_STATES = ("VM stopped", "VM deallocated")


class SpotVM(Service):
    """
    Deletes Azure Spot VMs that are in stopped or deallocated state.

    Azure has no separate "spot request" object — deleting the VM is the cancel.
    Disks whose delete_option is Detach (not Delete) are removed after the VM
    so they are not left orphaned (same idea as AWS DeleteOnTermination=false).
    """

    def __init__(
        self,
        resource_group: str,
        dry_run: bool,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
        custom_age_tag_key: str,
        notags: Dict[str, List[str]],
    ) -> None:
        super().__init__()
        self.instance_names_to_delete = []
        self.nics_names_to_delete = []
        self.disks_names_to_delete = []
        self.base = Base(resource_group)
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = self.require_positive_age(age)
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags

    @property
    def get_deleted(self):
        return self.instance_names_to_delete

    @property
    def delete_count(self):
        return len(self.instance_names_to_delete)

    @property
    def get_deleted_nic(self):
        return self.nics_names_to_delete

    @property
    def nic_delete_count(self):
        return len(self.nics_names_to_delete)

    @property
    def get_deleted_disks(self):
        return self.disks_names_to_delete

    def _is_spot_vm(self, vm) -> bool:
        priority = getattr(vm, "priority", None)
        if priority is None:
            return False
        return str(priority).lower() == "spot"

    def _get_vm_power_status(self, vm_name: str) -> str:
        try:
            instance_view = (
                self.base.get_compute_client()
                .virtual_machines.instance_view(self.base.resource_group, vm_name)
            )
            if instance_view.statuses and len(instance_view.statuses) > 1:
                return instance_view.statuses[1].display_status or ""
        except Exception as e:
            logging.error("Failed to get instance view for %s: %s", vm_name, e)
        return ""

    def _should_skip_instance(self, vm) -> bool:
        if not vm.tags:
            return False
        if self.exception_tags:
            if any(
                key in vm.tags and (not value or vm.tags[key] in value)
                for key, value in self.exception_tags.items()
            ):
                return True
        if self.notags:
            if all(
                key in vm.tags and (not value or vm.tags[key] in value)
                for key, value in self.notags.items()
            ):
                return True
        return False

    def _should_include_by_tags(self, vm) -> bool:
        """
        Match filter_tags. Untagged VMs are never deleted (same as
        ``crc.azu.vm.VM``). Empty filter_tags means any *tagged* Spot VM may
        match subject to age / exception / notags.
        """
        if not vm.tags:
            return False
        if not self.filter_tags:
            return True
        return all(
            key in vm.tags and (not value or vm.tags[key] in value)
            for key, value in self.filter_tags.items()
        )

    def _disk_name_from_id(self, disk_id: Optional[str]) -> Optional[str]:
        if not disk_id:
            return None
        return disk_id.rstrip("/").split("/")[-1]

    def _disk_ref_from_id(
        self, disk_id: Optional[str], fallback_name: Optional[str] = None
    ) -> Optional[tuple]:
        """
        Return (resource_group, disk_name) from a managed disk ARM id.
        Falls back to the VM resource group when the id has no RG segment.
        """
        name = self._disk_name_from_id(disk_id) or fallback_name
        if not name:
            return None
        rg = self.base.resource_group
        if disk_id:
            parts = disk_id.strip("/").split("/")
            for i, part in enumerate(parts):
                if part.lower() == "resourcegroups" and i + 1 < len(parts):
                    rg = parts[i + 1]
                    break
        return rg, name

    def _detached_disk_refs(self, vm) -> List[tuple]:
        """
        Disks that will survive VM delete (delete_option=Detach / not Delete).
        Each entry is (resource_group, disk_name).
        """
        refs = []
        profile = getattr(vm, "storage_profile", None)
        if not profile:
            return refs

        os_disk = getattr(profile, "os_disk", None)
        if os_disk:
            delete_option = str(getattr(os_disk, "delete_option", "") or "").lower()
            managed = getattr(os_disk, "managed_disk", None)
            disk_id = getattr(managed, "id", None) if managed else None
            ref = self._disk_ref_from_id(disk_id, getattr(os_disk, "name", None))
            if ref and delete_option not in ("", "delete"):
                refs.append(ref)
                logging.info(
                    "Spot VM %s OS disk %s/%s has delete_option=%s "
                    "(will delete after VM)",
                    vm.name,
                    ref[0],
                    ref[1],
                    delete_option or "unset",
                )

        for data_disk in getattr(profile, "data_disks", None) or []:
            delete_option = str(getattr(data_disk, "delete_option", "") or "").lower()
            managed = getattr(data_disk, "managed_disk", None)
            disk_id = getattr(managed, "id", None) if managed else None
            ref = self._disk_ref_from_id(disk_id, getattr(data_disk, "name", None))
            if ref and delete_option not in ("", "delete"):
                refs.append(ref)
                logging.info(
                    "Spot VM %s data disk %s/%s has delete_option=%s "
                    "(will delete after VM)",
                    vm.name,
                    ref[0],
                    ref[1],
                    delete_option or "unset",
                )
        return refs

    # Back-compat alias used by unit tests
    def _detached_disk_names(self, vm) -> List[str]:
        return [name for _, name in self._detached_disk_refs(vm)]

    def _delete_disk(self, resource_group: str, disk_name: str) -> None:
        label = f"{resource_group}/{disk_name}"
        if self.dry_run:
            self.disks_names_to_delete.append(label)
            return
        try:
            poller = self.base.get_compute_client().disks.begin_delete(
                resource_group, disk_name
            )
            poller.result(timeout=600)
            logging.info("Deleted leftover Spot VM disk %s", label)
            self.disks_names_to_delete.append(label)
        except ResourceNotFoundError:
            # Only treat as gone when we targeted the RG from the disk id.
            logging.info("Disk %s already removed", label)
            self.disks_names_to_delete.append(label)
        except HttpResponseError as e:
            if getattr(e, "status_code", None) == 404:
                logging.info("Disk %s already removed", label)
                self.disks_names_to_delete.append(label)
                return
            logging.error("Failed to delete disk %s: %s", label, e)
        except Exception as e:
            logging.error("Failed to delete disk %s: %s", label, e)

    def _delete_vm(self, vm) -> None:
        leftover_disks = self._detached_disk_refs(vm)
        vm_name = vm.name

        if not self.dry_run:
            logging.info("Deleting Azure Spot VM: %s", vm_name)
            try:
                begin_delete_vm_and_wait(
                    self.base.get_compute_client(),
                    self.base.resource_group,
                    vm_name,
                )
            except Exception as e:
                logging.error("Error deleting Spot VM %s: %s", vm_name, e)
                raise
        self.instance_names_to_delete.append(vm_name)

        for resource_group, disk_name in leftover_disks:
            self._delete_disk(resource_group, disk_name)

        delete_vm_primary_nic(
            self.base.get_network_client(),
            self.base.resource_group,
            vm_name,
            self.dry_run,
            self.nics_names_to_delete,
        )

    def delete(self) -> None:
        """
        Delete Azure Spot VMs that are stopped or deallocated and match
        filter/exception tags and age. In dry_run mode only lists candidates.
        """
        try:
            compute = self.base.get_compute_client()
            vms = list(compute.virtual_machines.list(self.base.resource_group))
        except Exception as e:
            logging.error(
                "Failed to list Azure VMs in resource group %s: %s",
                self.base.resource_group,
                e,
            )
            raise

        for vm in vms:
            if not self._is_spot_vm(vm):
                continue
            if self._should_skip_instance(vm):
                continue
            if not self._should_include_by_tags(vm):
                continue

            try:
                power_status = self._get_vm_power_status(vm.name)
                if power_status not in STOPPED_POWER_STATES:
                    logging.debug(
                        "Skipping Spot VM %s: power state '%s'",
                        vm.name,
                        power_status,
                    )
                    continue
            except Exception as e:
                logging.error("Error getting status for Spot VM %s: %s", vm.name, e)
                continue

            time_created = getattr(vm, "time_created", None)
            if not time_created:
                logging.warning(
                    "Skipping Spot VM %s: no creation time (age check not possible)",
                    vm.name,
                )
                continue
            dt = datetime.datetime.now().astimezone(time_created.tzinfo)
            retention_age = self.validated_retention_age(
                vm.tags or {}, self.custom_age_tag_key
            )
            if retention_age:
                logging.info("Updating age for Spot VM: %s", vm.name)
            if not self.is_old(retention_age or self.age, dt, time_created):
                continue

            try:
                self._delete_vm(vm)
            except Exception as e:
                logging.error("Error deleting Spot VM %s: %s", vm.name, e)

        if not self.instance_names_to_delete:
            logging.warning("No Azure Spot VMs (stopped/deallocated) to delete.")

        label = "which would be deleted" if self.dry_run else "deleted"
        logging.warning(
            "List of Azure Spot VMs (Total: %s) %s: %s",
            len(self.instance_names_to_delete),
            label,
            self.instance_names_to_delete,
        )
        logging.warning(
            "List of Azure NICs (Total: %s) %s: %s",
            len(self.nics_names_to_delete),
            label,
            self.nics_names_to_delete,
        )
        logging.warning(
            "List of Azure leftover disks (delete_option!=Delete) (Total: %s) %s: %s",
            len(self.disks_names_to_delete),
            label,
            self.disks_names_to_delete,
        )
