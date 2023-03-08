# Copyright (c) Yugabyte, Inc.

import datetime
import logging
from typing import Dict, List, Tuple

import boto3

from crc.aws._base import get_all_regions
from crc.service import Service


class VPC(Service):
    """
    The VPC class provides an interface for managing AWS VPC vpcs.
    It inherits from the Service class and uses boto3 to interact with the AWS VPC service.
    By default, boto3 will clean up attached resources (Route Table, subnets etc.) when a VM is deleted.
    The class allows for filtering and excluding VPCs based on specified tags.
    The class also has properties for the number of VPCs that will be deleted.
    """

    service_name = "ec2"
    """
    The service_name variable specifies the AWS service that this class will interact with.
    """

    default_region_name = "us-west-2"
    """
    The default_region_name variable specifies the default region to be used when interacting with the AWS service.
    """

    def __init__(
        self,
        dry_run: bool,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        notags: Dict[str, List[str]],
    ) -> None:
        """
        Initializes the object with filter and exception tags to be used when searching for vpcs, as well as an age threshold for vpcs.

        :param dry_run: A boolean variable that indicates whether the class should operate in dry_run mode or not.
        In dry_run mode, the class will only list the Resources that match the specified filter and exception tags,
        but will not perform any operations on them.
        :param filter_tags: dictionary containing key-value pairs as filter tags
        :type filter_tags: Dict[str, List[str]]
        :param exception_tags: dictionary containing key-value pairs as exception tags
        :type exception_tags: Dict[str, List[str]]
        :param age: dictionary containing key-value pairs as age threshold, the key is either "days" or "hours" or both and value is the number of days or hours.
        :type age: Dict[str, int]
        :param notags: dictionary containing key-value pairs as filter tags to exclude vpcs which do not have these tags
        :type notags: Dict[str, List[str]]
        """
        super().__init__()
        self.vpc_names_to_delete = []
        self.vpc_names_to_stop = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.notags = notags

    @property
    def get_deleted(self):
        """
        This is a property decorator that returns the list of items in the vpc_names_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        return self.vpc_names_to_delete

    @property
    def delete_count(self):
        """
        This is a property decorator that returns the count of items in the vpc_names_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        count = len(self.vpc_names_to_delete)
        logging.info(f"count of items in vpc_names_to_delete: {count}")
        return count

    def _get_filter(self) -> List[Dict[str, List[str]]]:
        """
        Creates a filter to be used when searching for vpcs, based on the filter tags provided during initialization.

        :return: list of filters.
        :rtype: List[Dict[str, List[str]]]
        """
        filters = []
        if self.filter_tags:
            for key, value in self.filter_tags.items():
                filters.append({"Name": f"tag:{key}", "Values": value})

        logging.info(f"Filters created: {filters}")
        return filters

    def _get_filtered_vpcs(
        self, ec2: boto3.client, vpc_details: dict
    ) -> Tuple[List[str], List[str]]:
        """
        Retrieves a list of vpcs that match the filter and age threshold,
        checks for exception tags, and also checks for vpcs that do not have the specified notags.
        """
        vpc_ids = []
        vpc_names = []
        for reservation in vpc_details["Reservations"]:
            for i in reservation["vpcs"]:
                try:
                    vpc_name = None
                    if "Tags" not in i:
                        continue
                    tags = i["Tags"]
                    if self._should_skip_vpc(tags):
                        continue
                    vpc_name = self._get_vpc_name(tags)
                    if not vpc_name:
                        logging.error(
                            f"{vpc_name} vpc doesn't have Name Tag. Skipping it"
                        )
                        continue
                    vpc_id = i["vpcId"]
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
                        datetime.datetime.now().astimezone(network_interface_attached_time.tzinfo),
                        network_interface_attached_time,
                    ):
                        vpc_ids.append(vpc_id)
                        vpc_names.append(vpc_name)
                        logging.info(
                            f"vpc {vpc_name} with ID {vpc_id} added to list of vpcs to be cleaned up."
                        )
                except Exception as e:
                    logging.error(
                        f"Error occurred while processing {vpc_name} vpc: {e}"
                    )
        return vpc_ids, vpc_names

    def _should_skip_vpc(self, tags: List[Dict[str, str]]) -> bool:
        """
        Check if the vpc should be skipped based on the exception tags and vpcs that do not have the specified notags.
        :param tags: List of tags associated with the vpc
        :type tags: List[Dict[str,str]]
        :return: True if the vpc should be skipped, False otherwise
        :rtype: bool
        """
        if not self.exception_tags and not self.notags:
            logging.warning("Tags and notags not present")
            return False

        in_no_tags = False
        all_tags = {}

        for tag in tags:
            k = tag["Key"]
            v = tag["Value"]

            if self.exception_tags:
                if k in self.exception_tags.keys() and (
                    not self.exception_tags[k] or v in self.exception_tags[k]
                ):
                    return True

            all_tags[k] = v

        if self.notags:
            in_no_tags = all(
                key in all_tags and (not value or all_tags[key] in value)
                for key, value in self.notags.items()
            )

        return in_no_tags

    def _get_vpc_name(self, tags: List[Dict[str, str]]) -> str:
        """
        Retrieves the vpc name from its tags
        :param tags: List of tags associated with the vpc
        :type tags: List[Dict[str,str]]
        :return: the vpc name if found, None otherwise
        :rtype: str
        """
        for tag in tags:
            if tag["Key"] == "Name":
                return tag["Value"]
        return None

    def _perform_operation(
        self,
        operation_type: str
    ) -> None:
        """
        Perform the specified operation (delete or stop) on vpcs that match the specified filter labels and do not match exception and notags labels, and are older than the specified age.
        It checks if the filter_tags attribute of the class is empty, if so, it returns True because there are no tags set to filter the VMs, so any vm should be considered.

        :param operation_type: The type of operation to perform (delete or stop)
        :type operation_type: str
        :param vpc_state: List of valid statuses of vpcs to perform the operation on.
        :type vpc_state: List[str]
        """
        # Renaming filter to vpc_filter for better understanding
        vpc_filter = self._get_filter()
        client = boto3.client(self.service_name, region_name="eu-west-3")
            
            # Renaming vpc_details to describe_vpcs_response for better understanding
        describe_vpcs_response = client.describe_vpcs(
                Filters=vpc_filter
            )

        logging.info(describe_vpcs_response)

        import sys
        sys.exit(1)
        for region in get_all_regions(self.service_name, self.default_region_name):
            client = boto3.client(self.service_name, region_name=region)
            
            # Renaming vpc_details to describe_vpcs_response for better understanding
            describe_vpcs_response = client.describe_vpcs(
                Filters=vpc_filter
            )

            logging.info(describe_vpcs_response)

            import sys
            sys.exit(1)
            
            (
                vpcs_to_operate,
                vpc_names_to_operate,
            ) = self._get_filtered_vpcs(client, describe_vpcs_response)

            if vpcs_to_operate:
                try:
                    if operation_type == "delete":
                        if not self.dry_run:
                            client.terminate_vpcs(vpcIds=vpcs_to_operate)
                            for i in range(len(vpcs_to_operate)):
                                logging.info(
                                    f"vpc {vpc_names_to_operate[i]} with id {vpcs_to_operate[i]} deleted."
                                )
                        self.vpc_names_to_delete.extend(vpc_names_to_operate)
                    elif operation_type == "stop":
                        if not self.dry_run:
                            client.stop_vpcs(vpcIds=vpcs_to_operate)
                            for i in range(len(vpcs_to_operate)):
                                logging.info(
                                    f"vpc {vpc_names_to_operate[i]} with id {vpcs_to_operate[i]} stopped."
                                )
                        self.vpc_names_to_stop.extend(vpc_names_to_operate)
                except Exception as e:
                    logging.error(
                        f"Error occurred while {operation_type} vpcs: {e}"
                    )

        # Using more descriptive if conditions
        if not self.vpc_names_to_delete and not self.vpc_names_to_stop:
            logging.warning(f"No AWS vpcs to {operation_type}.")

        if operation_type == "delete":
            if not self.dry_run:
                logging.warning(
                    f"number of AWS vpcs deleted: {len(self.vpc_names_to_delete)}"
                )
                logging.warning(
                    f"List of AWS vpcs deleted: {self.vpc_names_to_delete}"
                )
            else:
                logging.warning(
                    f"List of AWS vpcs (Total: {len(self.vpc_names_to_delete)}) which will be deleted: {self.vpc_names_to_delete}"
                )

        if operation_type == "stop":
            if not self.dry_run:
                logging.warning(
                    f"number of AWS vpcs stopped: {len(self.vpc_names_to_stop)}"
                )
                logging.warning(
                    f"List of AWS vpcs stopped: {self.vpc_names_to_stop}"
                )
            else:
                logging.warning(
                    f"List of AWS vpcs (Total: {len(self.vpc_names_to_stop)}) which will be stopped: {self.vpc_names_to_stop}"
                )

    def delete(
        self
    ) -> None:
        """
        Deletes vpcs that match the filter and age threshold, and also checks for exception tags.
        It checks if the filter_tags and notags attribute of the class is empty,
        if so, it returns True because there are no tags set to filter the VMs, so any vm should be considered.
        The method will list the vpcs that match the specified filter and exception tags but will not perform
        any operations on them if dry_run mode is enabled.

        :param vpc_state: list of strings representing the state of vpcs.
        :type vpc_state: List[str]
        """
        self._perform_operation("delete")
