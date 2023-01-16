# Copyright (c) Yugabyte, Inc.

import logging
from typing import List

from google.cloud import compute_v1

from crc.gcp._base import GCP_REGION_LIST
from crc.service import Service


class IP(Service):
    """
    The IP class is a subclass of the Service class and is used for managing IP addresses on GCP.
    """

    default_state = "RESERVED"

    def __init__(
        self,
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
        self.project_id = project_id
        self.filter_regex = filter_regex
        self.exception_regex = exception_regex

    def delete(self):
        """
        Delete the IP addresses that match the filter regex and do not match the exception regex.
        """
        for region in GCP_REGION_LIST:
            ips_to_delete = []
            addresses = compute_v1.AddressesClient().list(
                project=self.project_id, region=region
            )
            for address in addresses:
                name = address.name
                if address.status != self.default_state:
                    continue
                if any(
                    regex in name for regex in self.filter_regex
                ) and not any(regex in name for regex in self.exception_regex):
                    ips_to_delete.append(name)

            for ip in ips_to_delete:
                compute_v1.AddressesClient().delete(
                    project=self.project_id, region=region, address=ip
                )
                logging.info(f"Deleting IP address: {ip}")
            self.deleted_ips.extend(ips_to_delete)
