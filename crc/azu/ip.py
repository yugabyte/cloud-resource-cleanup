# Copyright (c) Yugabyte, Inc.

import logging

from crc.azu._base import network_client, resourceGroup
from crc.service import Service

logging.basicConfig(level=logging.INFO)


class IP(Service):
    """
    Class for managing public IP addresses in Azure.
    """

    def __init__(self, filter_tags: dict, exception_tags: dict) -> None:
        """
        Initialize the IP class with filter and exception tags.
        :param filter_tags: dict containing key-value pairs to filter IP addresses by
        :param exception_tags: dict containing key-value pairs to exclude IP addresses by
        """
        super().__init__()
        self.deleted_ips = []
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags

    @property
    def count(self):
        """
        Return the count of deleted IP addresses.
        """
        count = len(self.deleted_ips)
        logging.info(f"count of items in deleted_ips: {count}")
        return count

    def delete(self):
        """
        Delete public IP addresses that match the filter and exception tags.
        """
        ips = network_client.public_ip_addresses.list_all()

        for ip in ips:
            filter_tags_match = not self.filter_tags or (
                ip.tags
                and any(
                    key in ip.tags and ip.tags[key] in value
                    for key, value in self.filter_tags.items()
                )
            )

            exception_tags_match = not any(
                key in ip.tags and ip.tags[key] in value
                for key, value in self.exception_tags.items()
            )

            if (
                filter_tags_match
                and exception_tags_match
                and ip.ip_configuration is None
            ):
                network_client.public_ip_addresses.begin_delete(
                    resource_group_name=resourceGroup,
                    public_ip_address_name=ip.name,
                )
                self.deleted_ips.append(ip.name)
                logging.info(f"Deleted IP address: {ip.name}")
