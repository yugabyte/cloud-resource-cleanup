# Copyright (c) Yugabyte, Inc.

import logging

from crc.azu._base import Base
from crc.service import Service


class IP(Service):
    """
    Class for managing public IP addresses in Azure.
    """

    def __init__(
        self, dry_run: bool, filter_tags: dict, exception_tags: dict, notags: dict
    ) -> None:
        """
        Initialize the IP class with filter and exception tags.
        :param dry_run: A boolean variable that indicates whether the class should operate in dry_run mode or not.
        In dry_run mode, the class will only list the Resources that match the specified filter and exception tags,
        but will not perform any operations on them.
        :param filter_tags: dict containing key-value pairs to filter IP addresses by
        :param exception_tags: dict containing key-value pairs to exclude IP addresses by
        :param notags: dict containing key-value pairs to filter IP addresses that do not have the specified tags
        """
        super().__init__()
        self.deleted_ips = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.notags = notags

    @property
    def get_deleted(self):
        """
        Return the list of deleted IP addresses.
        """
        return self.deleted_ips

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
        base = Base()
        ips = base.get_network_client().public_ip_addresses.list_all()

        for ip in ips:
            filter_tags_match = not self.filter_tags or (
                ip.tags
                and any(
                    key in ip.tags and (not value or ip.tags[key] in value)
                    for key, value in self.filter_tags.items()
                )
            )

            exception_tags_match = self.exception_tags and any(
                key in ip.tags and (not value or ip.tags[key] in value)
                for key, value in self.exception_tags.items()
            )

            no_tags_match = self.notags and all(
                key in ip.tags and (not value or ip.tags[key] in value)
                for key, value in self.notags.items()
            )

            if (
                filter_tags_match
                and not exception_tags_match
                and not no_tags_match
                and ip.ip_configuration is None
            ):
                if not self.dry_run:
                    base.get_network_client().public_ip_addresses.begin_delete(
                        resource_group_name=base.resource_group,
                        public_ip_address_name=ip.name,
                    )
                    logging.info(f"Deleted IP address: {ip.name}")
                self.deleted_ips.append(ip.name)
        if not self.dry_run:
            logging.warning(f"number of Azure IPs deleted: {len(self.deleted_ips)}")
            logging.warning(f"List of Azure IPs deleted: {self.deleted_ips}")
        else:
            logging.warning(
                f"List of Azure IPs (Total: {len(self.deleted_ips)}) which will be deleted: {self.deleted_ips}"
            )
