# Copyright (c) Yugabyte, Inc.

import logging
from datetime import datetime
from typing import Dict, List

import oci

from crc.oci._base import Base
from crc.oci.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.service import Service


class VM(Service, Base):
    """
    This class provides an implementation of the Service class for managing virtual machines (VMs)
    on Oracle Cloud Infrastructure (OCI). Instances are matched via freeform_tags, the same
    dict-shaped tags used by GCP labels / Azure tags.
    """

    default_instance_state = ["RUNNING"]
    """
    The default state of instances that will be deleted.
    """

    def __init__(
        self,
        dry_run: bool,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
        custom_age_tag_key: str,
        notags: Dict[str, List[str]],
    ) -> None:
        """
        :param dry_run: If True, only list matching instances without deleting/stopping them.
        :param filter_tags: Dictionary of freeform tags and their values used to filter VMs for deletion.
        :param exception_tags: Dictionary of freeform tags and their values used to exclude VMs from deletion.
        :param age: Age (days/hours) of VMs that will be deleted.
        :param custom_age_tag_key: Tag name to overwrite the age condition.
        :param notags: Dictionary of tags used to exclude instances which do not have these tags.
        """
        Service.__init__(self)
        Base.__init__(self)
        self.instance_names_to_delete = []
        self.instance_names_to_stop = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = age
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags

    @property
    def get_deleted(self) -> List[str]:
        return self.instance_names_to_delete

    @property
    def delete_count(self) -> int:
        count = len(self.instance_names_to_delete)
        logging.info(f"count of items in instance_names_to_delete: {count}")
        return count

    @property
    def get_stopped(self) -> List[str]:
        return self.instance_names_to_stop

    @property
    def stopped_count(self) -> int:
        count = len(self.instance_names_to_stop)
        logging.info(f"count of items in instance_names_to_stop: {count}")
        return count

    def _should_skip_instance(self, instance) -> bool:
        """
        Check if the instance should be skipped based on the exception tags and instances
        that do not have the specified notags.
        """
        tags = instance.freeform_tags or {}
        in_exception_tags = False
        if self.exception_tags:
            in_exception_tags = any(
                key in tags and (not value or tags[key] in value)
                for key, value in self.exception_tags.items()
            )
            if in_exception_tags:
                return True
        in_no_tags = False
        if self.notags:
            in_no_tags = all(
                key in tags and (not value or tags[key] in value)
                for key, value in self.notags.items()
            )
        return in_no_tags

    def _has_matching_filter_tags(self, instance) -> bool:
        """
        Check if the instance has freeform tags matching the filter. Returns True if
        filter_tags is empty (no filter set, so any instance should be considered).
        """
        if not self.filter_tags:
            return True
        tags = instance.freeform_tags or {}
        return all(
            key in tags and (not value or tags[key] in value)
            for key, value in self.filter_tags.items()
        )

    def _is_old_instance(self, instance) -> bool:
        """
        Check if the instance is older than the specified age (or its own
        custom_age_tag_key override, capped by MAX_AGE).
        """
        created_time = instance.time_created  # timezone-aware datetime
        now = datetime.now().astimezone(created_time.tzinfo)
        tags = instance.freeform_tags or {}
        logging.info(tags)
        retention_age = self.get_retention_age(tags, self.custom_age_tag_key)
        if retention_age:
            logging.info(f"Updating age for instance: {instance.display_name}")
        return self.is_old(retention_age or self.age, now, created_time)

    def _perform_operation(
        self,
        operation_type: str,
        instance_state: List[str] = default_instance_state,
    ) -> None:
        """
        Perform the specified operation (delete or stop) on instances that match the
        specified filter tags and do not match exception and notags tags, and are older
        than the specified age. Iterates every region the tenancy is subscribed to.
        """
        for region in self.get_all_regions():
            try:
                compute_client = self.get_compute_client(region)
                instances = oci.pagination.list_call_get_all_results(
                    compute_client.list_instances,
                    compartment_id=self.compartment_id,
                ).data
            except CONNECTIVITY_ERRORS as e:
                log_skipped_region(region, f"list_instances vm {operation_type}", e)
                continue

            for instance in instances:
                if instance.lifecycle_state not in instance_state:
                    continue
                if self._should_skip_instance(instance):
                    continue
                if not self._has_matching_filter_tags(instance):
                    continue
                if not self._is_old_instance(instance):
                    continue
                try:
                    if operation_type == "delete":
                        if not self.dry_run:
                            compute_client.terminate_instance(instance.id)
                            logging.info(f"Deleting instance {instance.display_name}")
                        self.instance_names_to_delete.append(instance.display_name)
                    elif operation_type == "stop":
                        if not self.dry_run:
                            compute_client.instance_action(instance.id, "STOP")
                            logging.info(f"Stopping instance {instance.display_name}")
                        self.instance_names_to_stop.append(instance.display_name)
                except Exception as e:
                    logging.error(
                        f"Error occurred while {operation_type} instance {instance.display_name}: {e}"
                    )

        if not self.instance_names_to_delete and not self.instance_names_to_stop:
            logging.warning(f"No OCI instances to {operation_type}.")

        if operation_type == "delete":
            if not self.dry_run:
                logging.info(
                    f"number of OCI instances deleted: {len(self.instance_names_to_delete)}"
                )
                logging.warning(
                    f"List of OCI instances deleted: {self.instance_names_to_delete}"
                )
            else:
                logging.warning(
                    f"List of OCI instances (Total: {len(self.instance_names_to_delete)}) which will be deleted: {self.instance_names_to_delete}"
                )

        if operation_type == "stop":
            if not self.dry_run:
                logging.info(
                    f"number of OCI instances stopped: {len(self.instance_names_to_stop)}"
                )
                logging.warning(
                    f"List of OCI instances stopped: {self.instance_names_to_stop}"
                )
            else:
                logging.warning(
                    f"List of OCI instances (Total: {len(self.instance_names_to_stop)}) which will be stopped: {self.instance_names_to_stop}"
                )

    def delete(
        self,
        instance_state: List[str] = default_instance_state,
    ) -> None:
        """
        Delete instances that match the specified filter_tags and are older than the
        specified age, excluding exception_tags/notags matches.
        """
        self._perform_operation("delete", instance_state)

    def stop(self) -> None:
        """
        Stop instances that match the specified filter_tags and are older than the
        specified age, excluding exception_tags/notags matches.
        """
        self._perform_operation("stop", self.default_instance_state)
