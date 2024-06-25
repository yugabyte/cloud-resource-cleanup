# Copyright (c) Yugabyte, Inc.

import logging
from typing import List

from crc.azu._base import Base
from crc.service import Service


class NIC(Service):
    """
    This class provides an implementation of the Service class for managing NICs on Azure Cloud.
    By default, it cleans all unattached NICs.
    """

    def __init__(
        self, dry_run: bool, name_regex: List[str], exception_regex: List[str]
    ) -> None:
        """
        Initializes the object with filter and exception tags to be used when searching for NICs.

        :param dry_run: Indicates whether to operate in dry_run mode. In dry_run mode, the class will only list
                        the resources that match the specified filter and not exception names but will not perform
                        any operations on them.
        :param name_regex: List containing filter regex.
        :type name_regex: List[str]
        :param exception_regex: List containing exception names.
        :type exception_regex: List[str]
        """
        super().__init__()
        self.nics_names_to_delete = []
        self.base = Base()
        self.dry_run = dry_run
        self.name_regex = name_regex
        self.exception_regex = exception_regex

    @property
    def get_deleted(self) -> List[str]:
        """
        Returns the list of items in the nics_names_to_delete list.
        """
        return self.nics_names_to_delete

    @property
    def delete_count(self) -> int:
        """
        Returns the count of items in the nics_names_to_delete list.
        """
        count = len(self.nics_names_to_delete)
        logging.info(f"Count of items in nics_names_to_delete: {count}")
        return count

    def delete(self) -> None:
        """
        Deletes NICs that match the specified name_regex and do not match the specified exception_regex.
        In dry_run mode, this method will only list the NICs that match the specified filter and exception names,
        but will not perform any operations on them.
        """
        if not self.nics_names_to_delete:
            logging.warning("No Azure NICs to delete.")
            return

        self._delete_nic()

        if self.dry_run:
            logging.warning(
                f"List of Azure NICs (Total: {len(self.nics_names_to_delete)}) which will be deleted: {self.nics_names_to_delete}"
            )
        else:
            logging.warning(
                f"Number of Azure NICs deleted: {len(self.nics_names_to_delete)}"
            )
            logging.warning(f"List of Azure NICs deleted: {self.nics_names_to_delete}")

    def _delete_nic(self) -> None:
        """
        Deletes the unattached network interface (NIC).
        """
        for nic in self.base.get_network_client().network_interfaces.list_all():
            if not nic.virtual_machine:
                if self._should_delete_nic(nic.name):
                    self._attempt_delete(nic.name)

    def _should_delete_nic(self, nic_name: str) -> bool:
        """
        Determines if a NIC should be deleted based on name_regex and exception_regex.

        :param nic_name: The name of the NIC to check.
        :return: True if the NIC should be deleted, False otherwise.
        """
        to_include = any(filter_name in nic_name for filter_name in self.name_regex)
        to_exclude = any(
            exception_name in nic_name for exception_name in self.exception_regex
        )
        return to_include and not to_exclude

    def _attempt_delete(self, nic_name: str) -> None:
        """
        Attempts to delete a NIC, with retries on failure.

        :param nic_name: The name of the NIC to delete.
        """
        deleted_nic = False
        failure_count = 3

        while not deleted_nic and failure_count:
            try:
                if not self.dry_run:
                    self.base.get_network_client().network_interfaces.begin_delete(
                        self.base.resource_group, nic_name
                    ).result()
                    logging.info(f"Deleted NIC: {nic_name}")
                deleted_nic = True
                self.nics_names_to_delete.append(nic_name)
            except Exception as e:
                failure_count -= 1
                logging.error(f"Error occurred while processing NIC {nic_name}: {e}")
                if failure_count:
                    logging.info(f"Retrying deletion of NIC {nic_name}")

        if not failure_count:
            logging.error(f"Failed to delete the NIC - {nic_name}")
