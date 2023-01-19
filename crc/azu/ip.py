# Copyright (c) Yugabyte, Inc.

import logging

from crc.azu._base import network_client, resourceGroup
from crc.service import Service


class IP(Service):
    """
    Class for managing public IP addresses in Azure.
    """

    def __init__(self, monitor: bool, filter_tags: dict, exception_tags: dict) -> None:
        """
        Initialize the IP class with filter and exception tags.
        :param monitor: A boolean variable that indicates whether the class should operate in monitor mode or not.
        In monitor mode, the class will only list the Resources that match the specified filter and exception tags,
        but will not perform any operations on them.
        :param filter_tags: dict containing key-value pairs to filter IP addresses by
        :param exception_tags: dict containing key-value pairs to exclude IP addresses by
        """
        super().__init__()
        self.deleted_ips = []
        self.monitor = monitor
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

            exception_tags_match = self.exception_tags and any(
                key in ip.tags and ip.tags[key] in value
                for key, value in self.exception_tags.items()
            )

            if (
                filter_tags_match
                and not exception_tags_match
                and ip.ip_configuration is None
            ):
                if not self.monitor:
                    network_client.public_ip_addresses.begin_delete(
                        resource_group_name=resourceGroup,
                        public_ip_address_name=ip.name,
                    )
                    logging.info(f"Deleted IP address: {ip.name}")
                self.deleted_ips.append(ip.name)
        if not self.monitor:
            logging.warning(f"number of Azure IPs deleted: {len(self.deleted_ips)}")
        else:
            logging.warning(
                f"List of Azure IPs which will be deleted: {self.deleted_ips}"
            )
