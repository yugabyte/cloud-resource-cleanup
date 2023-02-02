# Copyright (c) Yugabyte, Inc.

import logging
from typing import Dict, List

import boto3

from crc.aws._base import get_all_regions
from crc.service import Service


class ElasticIPs(Service):
    """
    The ElasticIPs class is a subclass of the Service class and is used to interact with the AWS EC2 service.
    It allows for deletion of Elastic IPs based on specified filter and exception tags, and also can filter Elastic IPs based on absence of certain tags.

    Attributes:
        service_name (str): The name of the AWS service that the class interacts with.
        default_region_name (str): The default region to be used when interacting with the AWS service.
        deleted_ips (List[str]): A list of Elastic IPs that have been deleted.
        filter_tags (Dict[str, List[str]]): A dictionary of tags that should be filtered when searching for Elastic IPs to delete.
        exception_tags (Dict[str, List[str]]): A dictionary of tags that should be excluded when searching for Elastic IPs to delete.
        notags (Dict[str, List[str]]): A dictionary of tags that should be used to filter Elastic IPs that do not have the specified tag.
        dry_run (bool): A boolean variable that indicates whether the class should operate in dry_run mode or not. In dry_run mode, the class will only list the Resources that match the specified filter and exception tags, and also filters Elastic IPs based on absence of certain tags, but will not perform any operations on them.

    Methods:
        count: Returns the number of Elastic IPs that have been deleted.
        delete: Deletes Elastic IPs that match the specified filter_tags and do not match the specified exception_tags, and also filters Elastic IPs based on absence of certain tags.

    When initializing the class, the user can pass in a dry_run boolean value, as well as dictionaries for filter_tags, exception_tags, and notags. These parameters will be used to determine which Elastic IPs to delete.
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
        self, dry_run: bool, filter_tags: dict, exception_tags: dict, notags: dict
    ) -> None:
        """
        Initialize the ElasticIPs class.
        :param dry_run: A boolean variable that indicates whether the class should operate in dry_run mode or not.
        In dry_run mode, the class will only list the Resources that match the specified filter and exception tags,
        and also filters Elastic IPs based on absence of certain tags, but will not perform any operations on them.
        :param filter_tags: A dictionary of tags that should be filtered when searching for Elastic IPs to delete.
        :param exception_tags: A dictionary of tags that should be excluded when searching for Elastic IPs to delete.
        :param notags: A dictionary of tags that should be used to filter Elastic IPs that do not have the specified tag.
        """
        super().__init__()
        self.deleted_ips = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.notags = notags

    @property
    def get_deleted(self) -> str:
        """
        Returns the list of Elastic IPs that have been deleted.
        :return: The list of Elastic IPs that have been deleted.
        """
        return self.deleted_ips

    @property
    def count(self) -> int:
        """
        Returns the number of Elastic IPs that have been deleted.
        :return: The number of Elastic IPs that have been deleted.
        """
        count = len(self.deleted_ips)
        logging.info(f"count of items in deleted_ips: {count}")
        return count

    def _should_skip_instance(self, tags: List[Dict[str, str]]) -> bool:
        """
        Check if the Elastic IP instance should be skipped based on the exception and notags filter.
        :param tags: List of tags associated with the Elastic IP instance
        :type tags: List[Dict[str,str]]
        :return: True if the Elastic IP instance should be skipped, False otherwise
        :rtype: bool
        """
        if not self.exception_tags and not self.notags:
            return False
        in_exception_tags = False
        in_no_tags = False
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
                in_no_tags = all(
                    in_no_tags
                    and key in self.notags
                    and (not self.notags[key] or tag["Value"] in self.notags[key]),
                )

        return in_no_tags

    def delete(self):
        """
        Delete Elastic IPs that match the specified filter_tags and do not match the specified exception_tags and notags filter.
        In dry_run mode, this method will only list the Elastic IPs that match the specified filter and exception tags and notags filter,
        but will not perform any operations on them.
        """
        regions = get_all_regions(self.service_name, self.default_region_name)

        for region in regions:
            eips_to_delete = {}
            client = boto3.client(self.service_name, region_name=region)
            addresses = client.describe_addresses()["Addresses"]
            for eip in addresses:
                if "NetworkInterfaceId" not in eip and "Tags" in eip:
                    tags = eip["Tags"]
                    if self._should_skip_instance(tags):
                        continue
                    if not self.filter_tags:
                        eips_to_delete[eip["PublicIp"]] = eip["AllocationId"]
                        continue
                    for tag in tags:
                        key = tag["Key"]
                        # check for filter_tags match
                        if key in self.filter_tags and (
                            not self.filter_tags[key]
                            or tag["Value"] in self.filter_tags[key]
                        ):
                            eips_to_delete[eip["PublicIp"]] = eip["AllocationId"]

            if not self.dry_run:
                for ip in eips_to_delete:
                    client.release_address(AllocationId=eips_to_delete[ip])
                    logging.info(f"Deleted IP: {ip}")

            # Add deleted IPs to deleted_ips list
            self.deleted_ips.extend(list(eips_to_delete.keys()))

        if not self.dry_run:
            logging.warning(
                f"number of AWS Elastic IPs deleted: {len(self.deleted_ips)}"
            )
            logging.warning(f"List of AWS Elastic IPs deleted: {self.deleted_ips}")
        else:
            logging.warning(
                f"List of AWS Elastic IPs (Total: {len(self.deleted_ips)}) which will be deleted: {self.deleted_ips}"
            )
