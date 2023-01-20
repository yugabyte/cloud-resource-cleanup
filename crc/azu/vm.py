# Copyright (c) Yugabyte, Inc.

import datetime
import logging
import time
from typing import Dict, List

from crc.azu._base import Base
from crc.service import Service


class VM(Service):
    """
    This class provides an implementation of the Service class for managing virtual machines (VMs) on Azure Cloud.
    By Default this will clean NICs as well
    """

    default_instance_state = ["running"]
    """
    The default_instance_state variable specifies the default state of instances when querying for instances.
    """

    def __init__(
        self,
        dry_run: bool,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
        notags: Dict[str, List[str]],
    ) -> None:
        """
        Initializes the object with filter and exception tags to be used when searching for instances, as well as an age threshold for instances.

        :param dry_run: A boolean variable that indicates whether the class should operate in dry_run mode or not.
        In dry_run mode, the class will only list the Resources that match the specified filter and exception tags,
        but will not perform any operations on them.
        :param filter_tags: dictionary containing key-value pairs as filter tags
        :type filter_tags: Dict[str, List[str]]
        :param exception_tags: dictionary containing key-value pairs as exception tags
        :type exception_tags: Dict[str, List[str]]
        :param age: dictionary containing key-value pairs as age threshold, the key is either "days" or "hours" or both and value is the number of days or hours.
        :type age: Dict[str, int]
        :param notags: dictionary containing key-value pairs as filter tags to exclude instances which do not have these tags
        :type notags: Dict[str, List[str]]
        """
        super().__init__()
        self.instance_names_to_delete = []
        self.instance_names_to_stop = []
        self.nics_names_to_delete = []
        self.base = Base()
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = age
        self.notags = notags

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
        Perform the specified operation (delete or stop) on instances that match the specified filter labels and do not match exception and notags labels, and are older than the specified age.
        It checks if the filter_tags attribute of the class is empty, if so, it returns True because there are no tags set to filter the VMs, so any vm should be considered.

        :param operation_type: The type of operation to perform (delete or stop)
        :type operation_type: str
        :param instance_state: List of valid statuses of instances to perform the operation on.
        :type instance_state: List[str]
        """

        vms = self.base.get_compute_client().virtual_machines.list_all()

        for vm in vms:
            if self._should_perform_operation_on_vm(vm):
                dt = datetime.datetime.now().replace(tzinfo=vm.time_created.tzinfo)

                if self.is_old(self.age, dt, vm.time_created):
                    try:
                        status = self._get_vm_status(vm.name)

                        if any(status in state for state in instance_state):
                            if operation_type == "delete":
                                self._delete_vm(vm.name)
                            elif operation_type == "stop":
                                self._stop_vm(vm.name)
                    except Exception as e:
                        logging.error(
                            f"Error occurred while processing {vm.name} instance: {e}"
                        )

        # Using more descriptive if conditions
        if not self.instance_names_to_delete and not self.instance_names_to_stop:
            logging.warning(f"No Azure instances to {operation_type}.")

        if operation_type == "delete":
            if not self.dry_run:
                logging.warning(
                    f"number of Azure instances deleted: {len(self.instance_names_to_delete)}"
                )
                logging.warning(
                    f"number of Azure nics deleted: {len(self.nics_names_to_delete)}"
                )
            else:
                logging.warning(
                    f"List of Azure instances which will be deleted: {self.instance_names_to_delete}"
                )
                logging.warning(
                    f"List of Azure nics which will be deleted: {self.nics_names_to_delete}"
                )

        if operation_type == "stop":
            if not self.dry_run:
                logging.warning(
                    f"number of Azure instances stopped: {len(self.instance_names_to_stop)}"
                )
            else:
                logging.warning(
                    f"List of Azure instances which will be stopped: {self.instance_names_to_stop}"
                )

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
        if not vm.tags:
            return False

        if not self.filter_tags:
            return True

        if any(
            key in vm.tags and (not value or vm.tags[key] in value)
            for key, value in self.filter_tags.items()
        ):
            if self._should_skip_instance(vm):
                return False
            return True
        return False

    def _should_skip_instance(self, vm):
        """
        Check if the instance should be skipped based on the exception tags and instances that do not have the specified notags.
        :return: True if the instance should be skipped, False otherwise
        :rtype: bool
        """
        in_exception_tags = False
        in_no_tags = False
        if self.exception_tags:
            in_exception_tags = any(
                key in vm.tags and (not value or vm.tags[key] in value)
                for key, value in self.exception_tags.items()
            )
            if in_exception_tags:
                return True
        if self.notags:
            in_no_tags = all(
                key in vm.tags and (not value or vm.tags[key] in value)
                for key, value in self.notags.items()
            )
        return in_no_tags

    def _get_vm_status(self, vm_name: str) -> str:
        """
        Get the current status of the specified virtual machine.

        :param vm_name: The name of the virtual machine
        :type vm_name: str
        :return: The current status of the virtual machine
        :rtype: str
        """
        return (
            self.base.get_compute_client()
            .virtual_machines.instance_view(self.base.resource_group, vm_name)
            .statuses[1]
            .display_status
        )

    def _delete_vm(self, vm_name: str):
        """
        Delete the specified virtual machine.

        :param vm_name: The name of the virtual machine to delete
        :type vm_name: str
        """
        if not self.dry_run:
            self.base.get_compute_client().virtual_machines.begin_delete(
                self.base.resource_group, vm_name
            )
            logging.info("Deleting virtual machine: %s", vm_name)
        self.instance_names_to_delete.append(vm_name)
        self._delete_nic(vm_name)

    def _stop_vm(self, vm_name: str):
        """
        Stop the specified virtual machine.

        :param vm_name: The name of the virtual machine to stop
        :type vm_name: str
        """
        if not self.dry_run:
            self.base.get_compute_client().virtual_machines.begin_power_off(
                self.base.resource_group, vm_name
            )
            logging.info("Stopping virtual machine: %s", vm_name)
        self.instance_names_to_stop.append(vm_name)

    def delete(
        self,
        instance_state: List[str] = default_instance_state,
    ) -> None:
        """
        Delete instances that match the specified filter_tags and do not match the specified exception_tags and notags filter.
        In dry_run mode, this method will only list the instances that match the specified filter and exception tags and notags filter,
        but will not perform any operations on them.
        This method waits to delete attached NICs after instance is terminated.
        Expect 5 mins delay for Retry in deleting NIC

        :param instance_state: List of valid statuses of instances to delete.
        :type instance_state: List[str]
        """
        self._perform_operation("delete", instance_state)

    def stop(self) -> None:
        """
        Stop VMs that match the specified filter labels and are older than the specified age, and also checks for notags.
        It checks if the filter_tags and notags attribute of the class is empty,
        if so, it returns True because there are no tags set to filter the VMs, so any vm should be considered.
        The method will list the instances that match the specified filter and notags
        but will not perform any operations on them if dry_run mode is enabled.
        """
        self._perform_operation("stop", self.default_instance_state)

    def _delete_nic(self, vm_name):
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
                if not self.dry_run:
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
                    logging.info(f"Retrying Deletion of NIC {nic_name}")

        if not failure_count:
            logging.error(f"Failed to delete the NIC - {nic_name}")
