# Copyright (c) Yugabyte, Inc.

import logging
from datetime import datetime
from typing import Dict, List
import re

from google.cloud import compute_v1
from googleapiclient import discovery

from crc.service import Service


class Disk(Service):
    """
    This class provides an implementation of the Service class for managing Disk on Google Cloud.
    By Default this will clean Disks.
    """

    # Initialize class variables
    service_name = "compute"
    """
    The service name used for the Google Cloud API.
    """

    service_version = "v1"
    """
    The version of the service used for the Google Cloud API.
    """

    time_format = "%Y-%m-%dT%H:%M:%S.%f%z"
    """
    The format of timestamps used for comparison.
    """

    default_disk_state = ["Unattached"]
    """
    The default_disk_state variable specifies the default state of disks when querying for disks.
    """

    def __init__(
        self,
        dry_run: bool,
        project_id: str,
        filter_labels: Dict[str, List[str]],
        exception_labels: Dict[str, List[str]],
        age: int,
        detach_age: int,
        notags: Dict[str, List[str]],
        name_regex: List[str],
        exception_regex: List[str],
    ) -> None:
        """
        Initialize the Disk management class.

        :param dry_run: A boolean variable that indicates whether the class should operate in dry_run mode or not.
        In dry_run mode, the class will only list the Resources that match the specified filter and exception tags,
        and also filters Elastic IPs based on absence of certain tags, but will not perform any operations on them.
        :param project_id: ID of the Google Cloud project.
        :type project_id: str
        :param filter_labels: Dictionary of labels and their values used to filter Disks for deletion.
        :type filter_labels: Dict[str, List[str]]
        :param exception_labels: Dictionary of labels and their values used to exclude Disks from deletion.
        :type exception_labels: Dict[str, List[str]]
        :param age: Age in days of Disks that will be deleted.
        :type age: int
        :param detach_age: Age in days the Disks last got detached .
        :type detach_age: int
        :param notags: dictionary containing key-value pairs as filter tags to exclude disks which do not have these tags
        :type notags: Dict[str, List[str]]
        :param name_regex: A list of regular expressions for incuding disks.
        :type name_regex: List[str]
        :param exception_regex: A list of regular expressions for excluding disks.
        :type exception_regex: List[str]
        """
        super().__init__()
        self.disk_names_to_delete = []
        self.dry_run = dry_run
        self.project_id = project_id
        self.filter_tags = filter_labels
        self.exception_labels = exception_labels
        self.age = age
        self.detach_age = detach_age
        self.notags = notags
        self.name_regex = name_regex
        self.exception_regex = exception_regex

    @property
    def get_deleted(self):
        """
        This is a property decorator that returns the list of items in the disk_names_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        return self.disk_names_to_delete

    @property
    def count(self):
        """
        This is a property decorator that returns the count of items in the disk_names_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        count = len(self.disk_names_to_delete)
        logging.info(f"count of items in disk_names_to_delete: {count}")
        return count

    def _is_old_disk_detach(self, disk):
        """
        Check if the disk is older than the specified age

        :param disk: The disk to check age
        :type disk: dict
        :return: True if the disk is older than the specified age, False otherwise
        :rtype: bool
        """
        timestamp = disk.last_detach_timestamp
        detached_timestamp = datetime.strptime(timestamp, self.time_format)
        dt = datetime.now().astimezone(detached_timestamp.tzinfo)
        return self.is_old(self.detach_age, dt, detached_timestamp)

    def _should_skip_disk(self, disk):
        """
        Check if the instance should be skipped based on the exception tags and disks that do not have the specified notags.
        :return: True if the instance should be skipped, False otherwise
        :rtype: bool
        """
        in_exception_labels = False
        if self.exception_regex:
            for ex_reg in self.exception_regex:
                if re.match(r"{}".format(ex_reg), disk.name):
                    return True

        if self.exception_labels:
            in_exception_labels = any(
                key in disk.labels and (not value or disk.labels[key] in value)
                for key, value in self.exception_labels.items()
            )
            if in_exception_labels:
                return True

    def delete(
        self,
        disk_state: List[str] = default_disk_state,
    ) -> None:
        """
        Delete disks that match the specified filter_tags and do not match the specified exception_tags and notags filter.
        In dry_run mode, this method will only list the disks that match the specified filter and exception tags and notags filter,
        but will not perform any operations on them.

        :param disk_state: List of valid statuses of disks to delete.
        :type disk_state: List[str]
        """
        with discovery.build(self.service_name, self.service_version) as service:
            request = service.zones().list(project=self.project_id)
            zones = request.execute()["items"]
            disks_client = compute_v1.DisksClient()

            for zone_obj in zones:
                zone = zone_obj["name"]
                disks = disks_client.list(project=self.project_id, zone=zone)
                for disk in disks:
                    if disk.users:
                        continue
                    if self._should_skip_disk(disk):
                        continue
                    if not disk.last_detach_timestamp:
                        continue
                    if self._is_old_disk_detach(disk):
                        #print(disk.name)
                        try:
                            if not self.dry_run:
                                service.disks().delete(
                                    project=self.project_id,
                                    zone=zone,
                                    disk=disk.name,
                                ).execute()
                                logging.info(f"Deleting disk {disk.name}")
                            self.disk_names_to_delete.append(disk.name)
                        except Exception as e:
                            # log the error message if an exception occurs
                            logging.error(
                                f"Error occurred while deleting disk {disk.name}: {e}"
                            )
            if not self.disk_names_to_delete:
                logging.warning(f"No GCP disk to delete.")

            if not self.dry_run:
                logging.info(
                    f"number of GCP disks deleted: {len(self.disk_names_to_delete)}"
                )
                logging.warning(
                    f"List of GCP disk deleted: {self.disk_names_to_delete}"
                )
            else:
                logging.warning(
                    f"List of GCP disk (Total: {len(self.disk_names_to_delete)}) which will be deleted: {self.disk_names_to_delete}"
                )