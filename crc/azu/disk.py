# Copyright (c) Yugabyte, Inc.

import datetime
import logging
from typing import Dict, List

from crc.azu._base import compute_client, resourceGroup
from crc.service import Service


class Disk(Service):
    """
    A class for managing Disks on Azure.
    """

    default_disk_state = ["Unattached"]
    """
    The default_disk_state variable specifies the default state of disks when querying for disks.
    """

    def __init__(
        self,
        monitor: bool,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
    ) -> None:
        """
        Initialize the Disk management class.

        :param monitor: A boolean variable that indicates whether the class should operate in monitor mode or not.
        In monitor mode, the class will only list the Resources that match the specified filter and exception tags,
        but will not perform any operations on them.
        :param filter_tags: A dictionary of tags to filter virtual machines by.
        :param exception_tags: A dictionary of tags to exclude virtual machines by.
        :param age: A dictionary specifying the age threshold for stopping and deleting virtual machines.
        """
        super().__init__()
        self.disks_names_to_delete = []
        self.monitor = monitor
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = age

    @property
    def count(self):
        """
        This is a property decorator that returns the count of items in the disks_names_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        count = len(self.disks_names_to_delete)
        logging.info(f"count of items in disks_names_to_delete: {count}")
        return count

    def delete(self):
        """
        Deletes disks that match the specified filter tags and exception tags, and are older than the specified age.
        """
        # Get a list of all disks
        disks = compute_client().disks.list()

        # Iterate through each disk
        for disk in disks:
            # Check if the disk has the specified filter tags
            filter_tags_match = not self.filter_tags or (
                disk.tags
                and any(
                    key in disk.tags and disk.tags[key] in value
                    for key, value in self.filter_tags.items()
                )
            )

            # Check if the disk has the specified exception tags
            exception_tags_match = self.exception_tags and any(
                key in disk.tags and disk.tags[key] in value
                for key, value in self.exception_tags.items()
            )

            # Check if the disk matches the specified filter tags and not exception tags
            if filter_tags_match and not exception_tags_match:
                # Get the current time and compare it to the disk's time created
                dt = datetime.datetime.now().replace(tzinfo=disk.time_created.tzinfo)
                if self.is_old(self.age, dt, disk.time_created):
                    # Check if the disk is in a state that is allowed for deletion
                    if any(
                        disk.disk_state in state for state in self.default_disk_state
                    ):
                        if not self.monitor:
                            # Delete the disk
                            compute_client().disks.begin_delete(resourceGroup, disk.name)
                            # Log that the disk was deleted
                            logging.info("Deleted disk: " + disk.name)
                        self.disks_names_to_delete.append(disk.name)
        if not self.monitor:
            logging.warning(
                f"number of Azure Disks deleted: {len(self.disks_names_to_delete)}"
            )
        else:
            logging.warning(
                f"List of Azure Disks which will be deleted: {self.disks_names_to_delete}"
            )
