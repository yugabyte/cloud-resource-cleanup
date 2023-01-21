# Copyright (c) Yugabyte, Inc.

import datetime
import logging
from typing import Dict, List, Tuple

import boto3

from crc.aws._base import get_all_regions
from crc.service import Service


class VM(Service):
    """
    The VM class provides an interface for managing AWS EC2 instances.
    It inherits from the Service class and uses boto3 to interact with the AWS EC2 service.
    By default, boto3 will clean up any attached resources (NICs, disks, etc.) when a VM is deleted.
    The class allows for filtering and excluding instances based on specified tags, as well as an age threshold for instances.
    The class also has properties for the number of instances that will be deleted and stopped.
    """

    service_name = "ec2"
    """
    The service_name variable specifies the AWS service that this class will interact with.
    """

    default_region_name = "us-west-2"
    """
    The default_region_name variable specifies the default region to be used when interacting with the AWS service.
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
    def stopped_count(self):
        """
        This is a property decorator that returns the count of items in the instance_names_to_stop list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        count = len(self.instance_names_to_stop)
        logging.info(f"count of items in instance_names_to_stop: {count}")
        return count

    def _get_filter(self, instance_state: List[str]) -> List[Dict[str, List[str]]]:
        """
        Creates a filter to be used when searching for instances, based on the provided instance state and the filter tags provided during initialization.

        :param instance_state: list of strings representing the state of instances.
        :type instance_state: List[str]
        :return: list of filters.
        :rtype: List[Dict[str, List[str]]]
        """
        filters = [
            {
                "Name": "instance-state-name",
                "Values": instance_state,
            }
        ]
        if self.filter_tags:
            for key, value in self.filter_tags.items():
                filters.append({"Name": f"tag:{key}", "Values": value})

        logging.info(f"Filters created: {filters}")
        return filters

    def _get_filtered_instances(
        self, ec2: boto3.client, instance_details: dict
    ) -> Tuple[List[str], List[str]]:
        """
        Retrieves a list of instances that match the filter and age threshold,
        checks for exception tags, and also checks for instances that do not have the specified notags.
        """
        instance_ids = []
        instance_names = []
        for reservation in instance_details["Reservations"]:
            for i in reservation["Instances"]:
                try:
                    instance_name = None
                    if "Tags" not in i:
                        continue
                    tags = i["Tags"]
                    if self._should_skip_instance(tags):
                        continue
                    instance_name = self._get_instance_name(tags)
                    if not instance_name:
                        logging.error(
                            f"{instance_name} instance doesn't have Name Tag. Skipping it"
                        )
                        continue
                    instance_id = i["InstanceId"]
                    network_interface_id = i["NetworkInterfaces"][0][
                        "NetworkInterfaceId"
                    ]
                    network_interface_details = ec2.describe_network_interfaces(
                        NetworkInterfaceIds=[network_interface_id]
                    )
                    network_interface_attached_time = network_interface_details[
                        "NetworkInterfaces"
                    ][0]["Attachment"]["AttachTime"]
                    if self.is_old(
                        self.age,
                        datetime.datetime.now().replace(
                            tzinfo=network_interface_attached_time.tzinfo
                        ),
                        network_interface_attached_time,
                    ):
                        instance_ids.append(instance_id)
                        instance_names.append(instance_name)
                        logging.info(
                            f"Instance {instance_name} with ID {instance_id} added to list of instances to be cleaned up."
                        )
                except Exception as e:
                    logging.error(
                        f"Error occurred while processing {instance_name} instance: {e}"
                    )
        return instance_ids, instance_names

    def _should_skip_instance(self, tags: List[Dict[str, str]]) -> bool:
        """
        Check if the instance should be skipped based on the exception tags and instances that do not have the specified notags.
        :param tags: List of tags associated with the instance
        :type tags: List[Dict[str,str]]
        :return: True if the instance should be skipped, False otherwise
        :rtype: bool
        """
        if not self.exception_tags and not self.notags:
            return False
        in_exception_tags = False
        in_no_tags = True
        for tag in tags:
            key = tag["Key"]
            if self.exception_tags:
                in_exception_tags = key in self.exception_tags and (
                    not self.exception_tags[key]
                    or tag["Value"] in self.exception_tags[key]
                )
                if in_exception_tags:
                    return True
            if self.notags:
                if not in_no_tags:
                    continue
                in_no_tags = (
                    in_no_tags
                    and key in self.notags
                    and (not self.notags[key] or tag["Value"] in self.notags[key])
                )
        return in_no_tags

    def _get_instance_name(self, tags: List[Dict[str, str]]) -> str:
        """
        Retrieves the instance name from its tags
        :param tags: List of tags associated with the instance
        :type tags: List[Dict[str,str]]
        :return: the instance name if found, None otherwise
        :rtype: str
        """
        for tag in tags:
            if tag["Key"] == "Name":
                return tag["Value"]
        return None

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
        # Renaming filter to instance_filter for better understanding
        instance_filter = self._get_filter(instance_state)
        for region in get_all_regions(self.service_name, self.default_region_name):
            client = boto3.client(self.service_name, region_name=region)
            # Renaming instance_details to describe_instances_response for better understanding
            describe_instances_response = client.describe_instances(
                Filters=instance_filter
            )

            (
                instances_to_operate,
                instance_names_to_operate,
            ) = self._get_filtered_instances(client, describe_instances_response)

            if instances_to_operate:
                try:
                    if operation_type == "delete":
                        if not self.dry_run:
                            client.terminate_instances(InstanceIds=instances_to_operate)
                            for i in range(len(instances_to_operate)):
                                logging.info(
                                    f"Instance {instance_names_to_operate[i]} with id {instances_to_operate[i]} deleted."
                                )
                        self.instance_names_to_delete.extend(instance_names_to_operate)
                    elif operation_type == "stop":
                        if not self.dry_run:
                            client.stop_instances(InstanceIds=instances_to_operate)
                            for i in range(len(instances_to_operate)):
                                logging.info(
                                    f"Instance {instance_names_to_operate[i]} with id {instances_to_operate[i]} stopped."
                                )
                        self.instance_names_to_stop.extend(instance_names_to_operate)
                except Exception as e:
                    logging.error(
                        f"Error occurred while {operation_type} instances: {e}"
                    )

        # Using more descriptive if conditions
        if not self.instance_names_to_delete and not self.instance_names_to_stop:
            logging.warning(f"No AWS instances to {operation_type}.")

        if operation_type == "delete":
            if not self.dry_run:
                logging.warning(
                    f"number of AWS instances deleted: {len(self.instance_names_to_delete)}"
                )
            else:
                logging.warning(
                    f"List of AWS instances which will be deleted: {self.instance_names_to_delete}"
                )

        if operation_type == "stop":
            if not self.dry_run:
                logging.warning(
                    f"number of AWS instances stopped: {len(self.instance_names_to_stop)}"
                )
            else:
                logging.warning(
                    f"List of AWS instances which will be stopped: {self.instance_names_to_stop}"
                )

    def delete(
        self,
        instance_state: List[str] = default_instance_state,
    ) -> None:
        """
        Deletes instances that match the filter and age threshold, and also checks for exception tags.
        It checks if the filter_tags and notags attribute of the class is empty,
        if so, it returns True because there are no tags set to filter the VMs, so any vm should be considered.
        The method will list the instances that match the specified filter and exception tags but will not perform
        any operations on them if dry_run mode is enabled.

        :param instance_state: list of strings representing the state of instances.
        :type instance_state: List[str]
        """
        self._perform_operation("delete", instance_state)

    def stop(self):
        """
        Stop VMs that match the specified filter labels and are older than the specified age, and also checks for notags.
        It checks if the filter_tags and notags attribute of the class is empty,
        if so, it returns True because there are no tags set to filter the VMs, so any vm should be considered.
        The method will list the instances that match the specified filter and notags
        but will not perform any operations on them if dry_run mode is enabled.
        """
        self._perform_operation("stop", self.default_instance_state)
