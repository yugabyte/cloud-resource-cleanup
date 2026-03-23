# Copyright (c) Yugabyte, Inc.

import datetime
import logging
import time
from typing import Dict, List

from crc.azu._base import Base
from crc.service import Service


# Power states for stopped/deallocated VMs (from Azure instance_view display_status)
STOPPED_POWER_STATES = ("VM stopped", "VM deallocated")


class SpotVM(Service):
    """
    Deletes Azure Spot VMs that are in stopped or deallocated state.
    Uses the same tag/age filtering as the regular VM cleaner and also deletes
    associated NICs.
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
        self.base = Base(resource_group)
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = age
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

    def _is_spot_vm(self, vm) -> bool:
        """Return True if this VM is a Spot VM."""
        priority = getattr(vm, "priority", None)
        if priority is None:
            return False
        return str(priority).lower() == "spot"

    def _get_vm_power_status(self, vm_name: str) -> str:
        """Get power state display status for the VM (e.g. 'VM running', 'VM stopped')."""
        try:
            instance_view = (
                self.base.get_compute_client()
                .virtual_machines.instance_view(self.base.resource_group, vm_name)
            )
            # statuses[0] is ProvisioningState, statuses[1] is PowerState
            if instance_view.statuses and len(instance_view.statuses) > 1:
                return instance_view.statuses[1].display_status or ""
        except Exception as e:
            logging.error(f"Failed to get instance view for {vm_name}: {e}")
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
        if not vm.tags:
            return False
        if not self.filter_tags:
            return True
        return all(
            key in vm.tags and (not value or vm.tags[key] in value)
            for key, value in self.filter_tags.items()
        )

    def _delete_vm(self, vm_name: str):
        if not self.dry_run:
            self.base.get_compute_client().virtual_machines.begin_delete(
                self.base.resource_group, vm_name
            )
            logging.info("Deleting Azure Spot VM: %s", vm_name)
        self.instance_names_to_delete.append(vm_name)
        self._delete_nic(vm_name)

    def _delete_nic(self, vm_name: str):
        deleted_nic = False
        failure_count = 3
        nic_name = f"{vm_name}-NIC"
        while not deleted_nic and failure_count:
            try:
                if not self.dry_run:
                    logging.info(
                        f"Sleeping for {60 * failure_count} seconds before deleting NIC"
                    )
                    time.sleep(60 * failure_count)
                    self.base.get_network_client().network_interfaces.begin_delete(
                        self.base.resource_group, nic_name
                    )
                    logging.info(f"Deleted the NIC - {nic_name}")
                deleted_nic = True
                self.nics_names_to_delete.append(nic_name)
            except Exception as e:
                failure_count -= 1
                logging.error(f"Error occurred while processing {nic_name} NIC: {e}")
                if failure_count:
                    logging.info(f"Retrying deletion of NIC {nic_name}")
        if not failure_count:
            logging.error(f"Failed to delete the NIC - {nic_name}")

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
                        f"Skipping Spot VM {vm.name}: power state '{power_status}'"
                    )
                    continue
            except Exception as e:
                logging.error(f"Error getting status for Spot VM {vm.name}: {e}")
                continue

            time_created = getattr(vm, "time_created", None)
            if not time_created:
                logging.warning(
                    f"Skipping Spot VM {vm.name}: no creation time (age check not possible)"
                )
                continue
            dt = datetime.datetime.now().astimezone(time_created.tzinfo)
            retention_age = self.get_retention_age(
                vm.tags or {}, self.custom_age_tag_key
            )
            if retention_age:
                logging.info(f"Updating age for Spot VM: {vm.name}")
            if not self.is_old(retention_age or self.age, dt, time_created):
                continue

            try:
                self._delete_vm(vm.name)
            except Exception as e:
                logging.error(f"Error deleting Spot VM {vm.name}: {e}")

        if not self.instance_names_to_delete:
            logging.warning("No Azure Spot VMs (stopped/deallocated) to delete.")

        if self.dry_run:
            logging.warning(
                f"List of Azure Spot VMs (Total: {len(self.instance_names_to_delete)}) "
                f"which would be deleted: {self.instance_names_to_delete}"
            )
            logging.warning(
                f"List of Azure NICs (Total: {len(self.nics_names_to_delete)}) "
                f"which would be deleted: {self.nics_names_to_delete}"
            )
        else:
            logging.warning(
                f"Number of Azure Spot VMs deleted: {len(self.instance_names_to_delete)}"
            )
            logging.warning(
                f"List of Azure Spot VMs deleted: {self.instance_names_to_delete}"
            )
            logging.warning(
                f"Number of Azure NICs deleted: {len(self.nics_names_to_delete)}"
            )
            logging.warning(
                f"List of Azure NICs deleted: {self.nics_names_to_delete}"
            )
