# Copyright (c) Yugabyte, Inc.

import logging

import boto3

from crc.aws._base import get_all_regions
from crc.service import Service


class ElasticIPs(Service):
    """
    The ElasticIPs class is a subclass of the Service class and is used to interact with the AWS EC2 service.
    It allows for deletion of Elastic IPs based on specified filter and exception tags.

    Attributes:
        service_name (str): The name of the AWS service that the class interacts with.
        default_region_name (str): The default region to be used when interacting with the AWS service.
        deleted_ips (List[str]): A list of Elastic IPs that have been deleted.
        filter_tags (Dict[str, List[str]]): A dictionary of tags that should be filtered when searching for Elastic IPs to delete.
        exception_tags (Dict[str, List[str]]): A dictionary of tags that should be excluded when searching for Elastic IPs to delete.

    Methods:
        count: Returns the number of Elastic IPs that have been deleted.
        delete: Deletes Elastic IPs that match the specified filter_tags and do not match the specified exception_tags.
    """

    service_name = "ec2"
    """
    The service_name variable specifies the AWS service that this class will interact with.
    """

    default_region_name = "us-west-2"
    """
    The default_region_name variable specifies the default region to be used when interacting with the AWS service.
    """

    def __init__(self, monitor: bool, filter_tags: dict, exception_tags: dict) -> None:
        """
        Initialize the ElasticIPs class.
        :param monitor: A boolean variable that indicates whether the class should operate in monitor mode or not.
        In monitor mode, the class will only list the Resources that match the specified filter and exception tags,
        but will not perform any operations on them.
        :param filter_tags: A dictionary of tags that should be filtered when searching for Elastic IPs to delete.
        :param exception_tags: A dictionary of tags that should be excluded when searching for Elastic IPs to delete.
        """
        super().__init__()
        self.deleted_ips = []
        self.monitor = monitor
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
        regions = get_all_regions(self.service_name, self.default_region_name)

        for region in regions:
            eips_to_delete = {}
            client = boto3.client(self.service_name, region_name=region)
            addresses = client.describe_addresses()["Addresses"]
            for eip in addresses:
                if "NetworkInterfaceId" not in eip and "Tags" in eip:
                    tags = eip["Tags"]
                    for tag in tags:
                        # check for exception_tags match
                        key = tag["Key"]
                        if (
                            self.exception_tags
                            and key in self.exception_tags
                            and tag["Value"] in self.exception_tags[key]
                        ):
                            continue
                    if not self.filter_tags:
                        eips_to_delete[eip["PublicIp"]] = eip["AllocationId"]
                        continue
                    for tag in tags:
                        key = tag["Key"]
                        # check for filter_tags match
                        if (
                            key in self.filter_tags
                            and tag["Value"] in self.filter_tags[key]
                        ):
                            eips_to_delete[eip["PublicIp"]] = eip["AllocationId"]

            if not self.monitor:
                for ip in eips_to_delete:
                    client.release_address(AllocationId=eips_to_delete[ip])
                    logging.info(f"Deleted IP: {ip}")

            # Add deleted IPs to deleted_ips list
            self.deleted_ips.extend(list(eips_to_delete.keys()))

        if not self.monitor:
            logging.warning(
                f"number of AWS Elastic IPs deleted: {len(self.deleted_ips)}"
            )
        else:
            logging.warning(
                f"List of AWS Elastic IPs which will be deleted: {self.deleted_ips}"
            )
