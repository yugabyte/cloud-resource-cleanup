# Copyright (c) Yugabyte, Inc.

import logging

import boto3

from crc.aws._base import get_all_regions
from crc.service import Service


class ElasticIPs(Service):
    """
    The ElasticIPs class is a subclass of the Service class and is used to interact with the AWS EC2 service.
    """

    service_name = "ec2"
    """
    The service_name variable specifies the AWS service that this class will interact with.
    """

    default_region_name = "us-west-2"
    """
    The default_region_name variable specifies the default region to be used when interacting with the AWS service.
    """

    def __init__(self, filter_tags: dict, exception_tags: dict) -> None:
        """
        Initialize the ElasticIPs class.
        :param filter_tags: A dictionary of tags that should be filtered when searching for Elastic IPs to delete.
        :param exception_tags: A dictionary of tags that should be excluded when searching for Elastic IPs to delete.
        """
        super().__init__()
        self.deleted_ips = []
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags

    @property
    def count(self) -> int:
        """
        Returns the number of Elastic IPs that have been deleted.
        :return: The number of Elastic IPs that have been deleted.
        """
        count = len(self.deleted_ips)
        logging.info(f"count of items in deleted_ips: {count}")
        return count

    def delete(self):
        """
        Delete Elastic IPs that match the specified filter_tags and do not match the specified exception_tags.
        """
        eips_to_delete = {}
        regions = get_all_regions(self.service_name, self.default_region_name)
        for region in regions:
            client = boto3.client(self.service_name, region_name=region)
            addresses = client.describe_addresses()["Addresses"]
            for eip in addresses:
                if "NetworkInterfaceId" not in eip and "Tags" in eip:
                    tags = eip["Tags"]
                    is_exception = False
                    for tag in tags:
                        # check for exception_tags match
                        if any(
                            tag["Key"] == key and tag["Value"] in value
                            for key, value in self.exception_tags.items()
                        ):
                            is_exception = True
                            break
                    if not is_exception and self.filter_tags:
                        for tag in tags:
                            # check for filter_tags match
                            if any(
                                tag["Key"] == key and tag["Value"] in value
                                for key, value in self.filter_tags.items()
                            ):
                                eips_to_delete[eip["PublicIp"]] = eip[
                                    "AllocationId"
                                ]
                    elif not self.filter_tags:
                        eips_to_delete[eip["PublicIp"]] = eip["AllocationId"]
        # Release the Elastic IPs
        client = boto3.client(
            self.service_name, region_name=self.default_region_name
        )
        for ip in eips_to_delete:
            client.release_address(AllocationId=eips_to_delete[ip])
            logging.info(f"Deleted IP: {ip}")
        # Add deleted IPs to deleted_ips list
        self.deleted_ips.extend(list(eips_to_delete.keys()))
