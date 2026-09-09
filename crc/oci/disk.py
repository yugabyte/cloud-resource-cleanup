# Copyright (c) Yugabyte, Inc.

import logging
from datetime import datetime
from typing import Dict, List

import oci

from crc.oci._base import Base
from crc.oci.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.service import Service


class Disk(Service, Base):
    """
    This class provides an implementation of the Service class for managing OCI block
    volumes. Only standalone (non-boot) block volumes are handled; boot volumes are a
    separate OCI resource type and are left untouched.
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
        :param dry_run: If True, only list matching volumes without deleting them.
        :param filter_tags: Dictionary of freeform tags and their values used to filter volumes for deletion.
        :param exception_tags: Dictionary of freeform tags and their values used to exclude volumes from deletion.
        :param age: Age (days/hours) since volume creation, used to decide deletion eligibility.
        :param custom_age_tag_key: Tag name to overwrite the age condition.
        :param notags: Dictionary of tags used to exclude volumes which do not have these tags.
        """
        Service.__init__(self)
        Base.__init__(self)
        self.disk_names_to_delete = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = age
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags

    @property
    def get_deleted(self) -> List[str]:
        return self.disk_names_to_delete

    @property
    def count(self) -> int:
        count = len(self.disk_names_to_delete)
        logging.info(f"count of items in disk_names_to_delete: {count}")
        return count

    def _get_attached_volume_ids(self, compute_client, region: str) -> set:
        """
        OCI does not expose "unattached" as a volume lifecycle_state (unlike GCP/Azure
        disks) — a volume stays AVAILABLE whether or not it's attached. Attachment has
        to be determined separately via VolumeAttachments.
        """
        attached_ids = set()
        try:
            attachments = oci.pagination.list_call_get_all_results(
                compute_client.list_volume_attachments,
                compartment_id=self.compartment_id,
            ).data
            for attachment in attachments:
                if attachment.lifecycle_state == "ATTACHED":
                    attached_ids.add(attachment.volume_id)
        except CONNECTIVITY_ERRORS as e:
            log_skipped_region(region, "list_volume_attachments", e)
        return attached_ids

    def _should_skip_volume(self, volume) -> bool:
        """
        Check if the volume should be skipped based on the exception tags and volumes
        that do not have the specified notags.
        """
        tags = volume.freeform_tags or {}
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

    def _has_matching_filter_tags(self, volume) -> bool:
        """
        Check if the volume has freeform tags matching the filter. Returns True if
        filter_tags is empty (no filter set, so any volume should be considered).
        """
        if not self.filter_tags:
            return True
        tags = volume.freeform_tags or {}
        return any(
            key in tags and (not value or tags[key] in value)
            for key, value in self.filter_tags.items()
        )

    def _is_old_volume(self, volume) -> bool:
        """
        Check if the volume is older than the specified age (or its own
        custom_age_tag_key override, capped by MAX_AGE).
        """
        created_time = volume.time_created
        now = datetime.now().astimezone(created_time.tzinfo)
        tags = volume.freeform_tags or {}
        logging.info(tags)
        retention_age = self.get_retention_age(tags, self.custom_age_tag_key)
        if retention_age:
            logging.info(f"Updating age for volume: {volume.display_name}")
        return self.is_old(retention_age or self.age, now, created_time)

    def delete(self) -> None:
        """
        Delete unattached block volumes that match the specified filter_tags and are
        older than the specified age, excluding exception_tags/notags matches. In
        dry_run mode, only lists matching volumes without deleting them.
        """
        for region in self.get_all_regions():
            try:
                compute_client = self.get_compute_client(region)
                blockstorage_client = self.get_blockstorage_client(region)
                volumes = oci.pagination.list_call_get_all_results(
                    blockstorage_client.list_volumes,
                    compartment_id=self.compartment_id,
                ).data
            except CONNECTIVITY_ERRORS as e:
                log_skipped_region(region, "list_volumes disk delete", e)
                continue

            attached_volume_ids = self._get_attached_volume_ids(compute_client, region)

            for volume in volumes:
                if volume.lifecycle_state != "AVAILABLE":
                    continue
                if volume.id in attached_volume_ids:
                    continue
                if self._should_skip_volume(volume):
                    continue
                if not self._has_matching_filter_tags(volume):
                    continue
                if not self._is_old_volume(volume):
                    continue
                try:
                    if not self.dry_run:
                        blockstorage_client.delete_volume(volume.id)
                        logging.info(f"Deleting disk {volume.display_name}")
                    self.disk_names_to_delete.append(volume.display_name)
                except Exception as e:
                    logging.error(
                        f"Error occurred while deleting disk {volume.display_name}: {e}"
                    )

        if not self.disk_names_to_delete:
            logging.warning("No OCI disk to delete.")

        if not self.dry_run:
            logging.warning(
                f"number of OCI disks deleted: {len(self.disk_names_to_delete)}"
            )
            logging.warning(f"List of OCI disk deleted: {self.disk_names_to_delete}")
        else:
            logging.warning(
                f"List of OCI disk (Total: {len(self.disk_names_to_delete)}) which will be deleted: {self.disk_names_to_delete}"
            )
