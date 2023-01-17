# Copyright (c) Yugabyte, Inc.

import datetime
import logging
import time
from typing import Dict, List, Tuple

from crc.azu._base import compute_client, network_client, resourceGroup
from crc.service import Service


class VM(Service):

    default_instance_state = ["running"]
    """
    The default_instance_state variable specifies the default state of instances when querying for instances.
    """

    def __init__(
        self,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
    ) -> None:
        """
        Initializes the object with filter and exception tags to be used when searching for instances, as well as an age threshold for instances.

        :param filter_tags: dictionary containing key-value pairs as filter tags
        :type filter_tags: Dict[str, List[str]]
        :param exception_tags: dictionary containing key-value pairs as exception tags
        :type exception_tags: Dict[str, List[str]]
        :param age: dictionary containing key-value pairs as age threshold, the key is either "days" or "hours" or both and value is the number of days or hours.
        :type age: Dict[str, int]
        """
        super().__init__()
        self.instance_names_to_delete = []
        self.instance_names_to_stop = []
        self.nics_names_to_delete = []
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = age

    @property
    def delete_count(self):
        """
        This is a property decorator that returns the count of items in the instance_names_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        count = len(self.instance_names_to_delete)
        logging.info(f"count of items in instance_names_to_delete: {count}")
        return count

    @property
    def nic_delete_count(self):
        """
        This is a property decorator that returns the count of items in the nics_names_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        count = len(self.nics_names_to_delete)
        logging.info(f"count of items in nics_names_to_delete: {count}")
        return count

    @property
    def stopped_count(self):
        """
        This is a property decorator that returns the count of items in the instance_names_to_stop list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        count = len(self.instance_names_to_stop)
        logging.info(f"count of items in instance_names_to_stop: {count}")
        return count

    def _perform_operation(
        self,
        operation_type: str,
        instance_state: List[str] = default_instance_state,
    ) -> None:
        """
        Perform the specified operation (delete or stop) on VMs that match the specified filter tags and are older than the specified age.
        It checks if the filter_tags attribute of the class is empty, if so, it returns True because there are no tags set to filter the VMs, so any vm should be considered.

        :param operation_type: The type of operation to perform (delete or stop)
        :type operation_type: str
        :param instance_state: List of valid statuses of instances to perform the operation on.
        :type instance_state: List[str]
        """

        vms = compute_client.virtual_machines.list_all()

        for vm in vms:
            if self._should_perform_operation_on_vm(vm):
                dt = datetime.datetime.now().replace(
                    tzinfo=vm.time_created.tzinfo
                )

                if self.is_old(self.age, dt, vm.time_created):
                    status = self._get_vm_status(vm.name)

                    if any(status in state for state in instance_state):
                        if operation_type == "delete":
                            self._delete_vm(vm.name)
                        elif operation_type == "stop":
                            self._stop_vm(vm.name)

    def _should_perform_operation_on_vm(self, vm) -> bool:
        """
        Check if the specified operation should be performed on the given virtual machine.

        It checks if the filter_tags attribute of the class is empty, if so, it returns True because there are no tags set to filter the VMs, so any vm should be considered.
        If the filter_tags attribute is not empty, the method checks if the vm has any tags. If the vm doesn't have any tags, it returns False
        It also checks if any of the filter tags match any of the vm's tags, If a match is found, it then checks if any of the exception tags match any of the vm's tags, if so, it returns False otherwise it returns True.

        :param vm: The virtual machine to check
        :type vm: Azure Virtual Machine object
        :return: True if the operation should be performed, False otherwise.
        :rtype: bool
        """
        if not self.filter_tags:
            return True

        if not vm.tags:
            return False

        if any(
            key in vm.tags and vm.tags[key] in value
            for key, value in self.filter_tags.items()
        ):
            if any(
                key in vm.tags and vm.tags[key] in value
                for key, value in self.exception_tags.items()
            ):
                return False
            return True
        return False

    def _get_vm_status(self, vm_name: str) -> str:
        """
        Get the current status of the specified virtual machine.

        :param vm_name: The name of the virtual machine
        :type vm_name: str
        :return: The current status of the virtual machine
        :rtype: str
        """
        return (
            compute_client.virtual_machines.instance_view(
                resourceGroup, vm_name
            )
            .statuses[1]
            .display_status
        )

    def _delete_vm(self, vm_name: str):
        """
        Delete the specified virtual machine.

        :param vm_name: The name of the virtual machine to delete
        :type vm_name: str
        """
        compute_client.virtual_machines.begin_delete(resourceGroup, vm_name)
        logging.info("Deleting virtual machine: %s", vm_name)
        self.instance_names_to_delete.append(vm_name)
        self.delete_nics(vm_name)

    def _stop_vm(self, vm_name: str):
        """
        Stop the specified virtual machine.

        :param vm_name: The name of the virtual machine to stop
        :type vm_name: str
        """
        compute_client.virtual_machines.begin_power_off(resourceGroup, vm_name)
        logging.info("Stopping virtual machine: %s", vm_name)

    def delete(
        self, instance_state: List[str] = default_instance_state
    ) -> None:
        """
        Deletes instances and NICs that match the filter and age threshold, and also checks for exception tags.
        This method waits to delete attached NICs after instance is terminated.
        Expect 5 mins delay for Retry in deleting NIC
        It checks if the filter_tags attribute of the class is empty, if so, it returns True because there are no tags set to filter the VMs, so any vm should be considered.

        :param instance_state: List of valid statuses of instances to delete.
        :type instance_state: List[str]
        """
        self._perform_operation("delete", instance_state)

    def stop(self) -> None:
        """
        Stop VMs that match the specified filter tags and are older than the specified age.
        It checks if the filter_tags attribute of the class is empty, if so, it returns True because there are no tags set to filter the VMs, so any vm should be considered.
        """
        self._perform_operation("stop", self.default_instance_state)

    def delete_nics(self, vm_name):
        """
        Deletes the network interface (NIC) associated with a virtual machine.

        Parameters:
            vm_name (str): The name of the virtual machine
        """
        deleted_nic = False
        failure_count = 10
        nic_name = f"{vm_name}-NIC"
        while not deleted_nic and failure_count:
            try:
                time.sleep(60)
                network_client.network_interfaces.begin_delete(
                    resourceGroup, nic_name
                )
                deleted_nic = True
                logging.info(f"Deleted the NIC - {nic_name}")
                self.nics_names_to_delete.append(nic_name)
            except Exception as e:
                failure_count -= 1

        if not failure_count:
            logging.error(f"Failed to delete the NIC - {nic_name}")
