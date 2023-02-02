# Copyright (c) Yugabyte, Inc.

import logging
from typing import List

from google.cloud import compute_v1

from crc.gcp._base import GCP_REGION_LIST
from crc.service import Service


class IP(Service):
    """
    The IP class is a subclass of the Service class and is used for managing IP addresses on GCP.

    Attributes:
        default_state (str): The default state of the IP address, set to "RESERVED" by default.
    """

    # The state of the IP address. Can be "RESERVED" or "IN_USE".
    default_state = "RESERVED"

    def __init__(
        self,
        dry_run: bool,
        project_id: str,
        filter_regex: List[str],
        exception_regex: List[str],
    ) -> None:
        """
        Initialize the IP class with the project id, filter regex, and exception regex.

        :param project_id: The GCP project id.
        :type project_id: str
        :param filter_regex: A list of regular expressions to filter IP addresses.
        :type filter_regex: List[str]
        :param exception_regex: A list of regular expressions for exceptions.
        :type exception_regex: List[str]
        """
        super().__init__()
        self.deleted_ips = []
        self.dry_run = dry_run
        self.project_id = project_id
        self.filter_regex = filter_regex
        self.exception_regex = exception_regex

    @property
    def get_deleted(self) -> str:
        """
        Returns the list of IPs that have been deleted.
        :return: The list of IPs that have been deleted.
        """
        return self.deleted_ips

    @property
    def count(self) -> int:
        """
        Returns the number of IPs that have been deleted.
        :return: The number of IPs that have been deleted.
        """
        count = len(self.deleted_ips)
        logging.info(f"count of items in deleted_ips: {count}")
        return count

    def delete(self):
        """
        Delete the IP addresses that match the filter regex and do not match the exception regex.
        It checks if the filter_regex attribute of the class is empty, if so, it returns True because there are no regex set to filter the IPs, so any ip should be considered.
        """
        for region in GCP_REGION_LIST:
            ips_to_delete = []

            # Get all addresses in the region
            addresses = compute_v1.AddressesClient().list(
                project=self.project_id, region=region
            )

            for address in addresses:
                name = address.name

                # Skip if address is not in the default state
                if address.status != self.default_state:
                    continue

                # Check if address name matches filter regex and does not match exception regex
                has_matching_filter_regex = not self.filter_regex or any(
                    regex in name for regex in self.filter_regex
                )

                has_matching_exception_regex = self.exception_regex and not any(
                    regex in name for regex in self.exception_regex
                )

                if has_matching_filter_regex and not has_matching_exception_regex:
                    ips_to_delete.append(name)

            if not self.dry_run:
                for ip in ips_to_delete:
                    # Delete IP addresses
                    compute_v1.AddressesClient().delete(
                        project=self.project_id,
                        region=region,
                        address=ip,
                    )
                    logging.info(f"Deleting IP address: {ip}")
            self.deleted_ips.extend(ips_to_delete)

        if not self.dry_run:
            logging.warning(f"number of GCP IPs deleted: {len(self.deleted_ips)}")
            logging.warning(f"List of GCP IPs deleted: {self.deleted_ips}")
        else:
            logging.warning(
                f"List of GCP IPs (Total: {len(self.deleted_ips)}) which will be deleted: {self.deleted_ips}"
            )
