# Copyright (c) Yugabyte, Inc.

import boto3
import datetime
import logging
from typing import Dict, List, Tuple
from crc.aws._base import get_all_regions
from crc.service import Service


class VM(Service):
    """
    The VM class provides an interface for managing AWS EC2 instances. It inherits from the Service class.
    By Default boto3 will clean attached resources with the VM like NICs, Disks.
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
        filters = [{"Name": "instance-state-name", "Values": instance_state}]
        for key, value in self.filter_tags.items():
            filters.append({"Name": f"tag:{key}", "Values": value})

        logging.info(f"Filters created: {filters}")
        return filters

    def _get_filtered_instances(
        self, ec2: boto3.client, instance_details: dict
    ) -> Tuple[List[str], List[str]]:
        """
        Retrieves a list of instances that match the filter and age threshold, and also checks for exception tags.

        :param ec2: EC2 service client
        :type ec2: boto3.client
        :param instance_details: dictionary containing instance details
        :type instance_details: dict
        :return: tuple containing a list of instance ids and a list of instance names
        :rtype: Tuple[List[str], List[str]]
        """
        instance_ids = []
        instance_names = []
        for i in instance_details["Reservations"]:
            try:
                instance_name = None
                autoclean = True
                i = i["Instances"][0]
                if "Tags" not in i:
                    continue

                for tag in i["Tags"]:
                    if (
                        tag["Key"] in self.exception_tags
                        and tag["Value"] in self.exception_tags.values()
                    ):
                        autoclean = False
                        break
                    if tag["Key"] == "Name":
                        instance_name = tag["Value"]

                if not autoclean:
                    continue

                instance_id = i["InstanceId"]
                network_interface_id = i["NetworkInterfaces"][0]["NetworkInterfaceId"]
                network_interface_details = ec2.describe_network_interfaces(
                    NetworkInterfaceIds=[network_interface_id]
                )
                network_interface_id_attached_time = network_interface_details[
                    "NetworkInterfaces"
                ][0]["Attachment"]["AttachTime"]
                dt = datetime.datetime.now().replace(
                    tzinfo=network_interface_id_attached_time.tzinfo
                )

                if self.is_old(self.age, dt, network_interface_id_attached_time):
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

    def _perform_operation(
        self, operation_type: str, instance_state: List[str] = default_instance_state
    ) -> None:
        """
        Perform the specified operation (delete or stop) on instances that match the specified filter labels and are older than the specified age.
        :param operation_type: The type of operation to perform (delete or stop)
        :type operation_type: str
        :param instance_state: List of valid statuses of instances to perform the operation on.
        :type instance_state: List[str]
        """
        filter = self._get_filter(instance_state)
        for region in get_all_regions(self.service_name, self.default_region_name):
            with boto3.client(self.service_name, region_name=region) as client:
                instance_details = client.describe_instances(Filters=filter)

                (
                    instances_to_operate,
                    instance_names_to_operate,
                ) = self._get_filtered_instances(client, instance_details)

                if instances_to_operate:
                    try:
                        if operation_type == "delete":
                            client.terminate_instances(InstanceIds=instances_to_operate)
                            for i in range(len(instances_to_operate)):
                                logging.info(
                                    f"Instance {instance_names_to_operate[i]} with id {instances_to_operate[i]} deleted."
                                )
                            self.instance_names_to_delete.extend(
                                instance_names_to_operate
                            )
                        elif operation_type == "stop":
                            client.stop_instances(InstanceIds=instances_to_operate)
                            for i in range(len(instances_to_operate)):
                                logging.info(
                                    f"Instance {instance_names_to_operate[i]} with id {instances_to_operate[i]} stopped."
                                )
                            self.instance_names_to_stop.extend(
                                instance_names_to_operate
                            )
                    except Exception as e:
                        logging.error(
                            f"Error occurred while {operation_type} instances: {e}"
                        )

        if not self.instance_names_to_delete and not self.instance_names_to_stop:
            logging.info(f"No instances to {operation_type}.")

        if operation_type == "delete":
            logging.info(
                f"number of instances deleted: {len(self.instance_names_to_delete)}"
            )

        if operation_type == "stop":
            logging.info(
                f"number of instances stopped: {len(self.instance_names_to_stop)}"
            )

    def delete(self, instance_state: List[str] = default_instance_state) -> None:
        """
        Deletes instances that match the filter and age threshold, and also checks for exception tags.

        :param instance_state: list of strings representing the state of instances.
        :type instance_state: List[str]
        """
        self._perform_operation("delete", instance_state)

    def stop(self):
        """
        Stop VMs that match the specified filter labels and are older than the specified age.
        """
        self._perform_operation("stop", self.default_instance_state)
