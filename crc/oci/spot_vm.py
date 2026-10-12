# Copyright (c) Yugabyte, Inc.

import logging
import time
from datetime import datetime
from typing import Dict, List

import oci
from oci.exceptions import ServiceError

from crc.oci._base import Base
from crc.oci.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.service import Service


class SpotVM(Service, Base):
    """
    Delete OCI preemptible (spot-like) instances that match tag and age filters.

    OCI has no separate spot-request object — terminate_instance is the cancel.
    Boot volume is deleted with the instance by default (preserve_boot_volume=False).
    Attached block volumes that are not deleted with the instance are detached
    and deleted afterwards (same idea as AWS DeleteOnTermination=false).
    """

    default_instance_state = ["RUNNING", "STOPPED"]
    # terminate_instance is async; volumes stay attached until TERMINATED.
    INSTANCE_TERMINATED_WAIT_SEC = 180
    INSTANCE_POLL_INTERVAL_SEC = 5

    def __init__(
        self,
        dry_run: bool,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
        custom_age_tag_key: str,
        notags: Dict[str, List[str]],
    ) -> None:
        Service.__init__(self)
        Base.__init__(self)
        self.instance_names_to_delete: List[str] = []
        self.volumes_to_delete: List[str] = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags or {}
        self.exception_tags = exception_tags or {}
        self.age = self.require_positive_age(age)
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags or {}

    @property
    def get_deleted(self) -> List[str]:
        return self.instance_names_to_delete

    @property
    def delete_count(self) -> int:
        return len(self.instance_names_to_delete)

    @property
    def get_deleted_volumes(self) -> List[str]:
        return self.volumes_to_delete

    def _is_preemptible(self, instance) -> bool:
        return getattr(instance, "preemptible_instance_config", None) is not None

    def _should_skip_instance(self, instance) -> bool:
        tags = instance.freeform_tags or {}
        if not tags:
            return False
        if self.exception_tags:
            if any(
                key in tags and (not value or tags[key] in value)
                for key, value in self.exception_tags.items()
            ):
                return True
        if self.notags:
            if all(
                key in tags and (not value or tags[key] in value)
                for key, value in self.notags.items()
            ):
                return True
        return False

    def _has_matching_filter_tags(self, instance) -> bool:
        """
        Match filter_tags. Empty filter means any instance is eligible
        (including untagged), same as crc.oci.vm.VM.
        """
        if not self.filter_tags:
            return True
        tags = instance.freeform_tags or {}
        return all(
            key in tags and (not value or tags[key] in value)
            for key, value in self.filter_tags.items()
        )

    def _is_old_instance(self, instance) -> bool:
        created = instance.time_created
        if created.tzinfo is None:
            created = created.replace(tzinfo=datetime.now().astimezone().tzinfo)
        now = datetime.now(tz=created.tzinfo)
        retention_age = self.get_retention_age(
            instance.freeform_tags or {}, self.custom_age_tag_key
        )
        if retention_age:
            logging.info("Updating age for preemptible instance: %s", instance.display_name)
        return self.is_old(retention_age or self.age, now, created)

    def _attached_block_volume_ids(self, compute_client, instance_id: str) -> List[str]:
        """
        Block volumes currently attached to the instance via
        ``list_volume_attachments`` (boot volumes are not returned by that API).
        Boot volume deletion is handled by
        ``terminate_instance(preserve_boot_volume=False)``.
        """
        volume_ids = []
        try:
            attachments = oci.pagination.list_call_get_all_results(
                compute_client.list_volume_attachments,
                compartment_id=self.compartment_id,
                instance_id=instance_id,
            ).data
        except Exception as e:
            logging.error(
                "Failed to list volume attachments for %s: %s", instance_id, e
            )
            return volume_ids

        for attachment in attachments:
            lifecycle = getattr(attachment, "lifecycle_state", "") or ""
            if lifecycle.upper() in {"DETACHED", "DETACHING"}:
                continue
            volume_id = getattr(attachment, "volume_id", None)
            if volume_id:
                volume_ids.append(volume_id)
                logging.info(
                    "Instance %s has attached block volume %s "
                    "(will delete after terminate)",
                    instance_id,
                    volume_id,
                )
        return volume_ids

    def _wait_instance_terminated(self, compute_client, instance_id: str) -> bool:
        """
        Block until the instance is TERMINATED (or gone). Volumes cannot be
        deleted while still attached during async terminate.
        """
        deadline = time.time() + self.INSTANCE_TERMINATED_WAIT_SEC
        while time.time() < deadline:
            try:
                state = (
                    compute_client.get_instance(instance_id).data.lifecycle_state or ""
                ).upper()
                if state == "TERMINATED":
                    return True
                logging.info(
                    "Waiting for OCI instance %s to terminate (state=%s)",
                    instance_id,
                    state,
                )
            except ServiceError as e:
                # 404: already gone
                if getattr(e, "status", None) == 404:
                    return True
                logging.error(
                    "Error polling OCI instance %s during terminate wait: %s",
                    instance_id,
                    e,
                )
                return False
            except Exception as e:
                logging.error(
                    "Error polling OCI instance %s during terminate wait: %s",
                    instance_id,
                    e,
                )
                return False
            time.sleep(self.INSTANCE_POLL_INTERVAL_SEC)

        logging.error(
            "Timed out waiting for OCI instance %s to reach TERMINATED; "
            "will still attempt leftover volume deletes",
            instance_id,
        )
        return False

    def _delete_volume(self, blockstorage_client, volume_id: str) -> None:
        if self.dry_run:
            self.volumes_to_delete.append(volume_id)
            return
        try:
            blockstorage_client.delete_volume(volume_id)
            logging.info("Deleted leftover block volume %s", volume_id)
            self.volumes_to_delete.append(volume_id)
        except Exception as e:
            logging.error("Failed to delete leftover volume %s: %s", volume_id, e)

    def delete(self) -> None:
        for region in self.get_all_regions():
            try:
                compute_client = self.get_compute_client(region)
                blockstorage_client = self.get_blockstorage_client(region)
                instances = oci.pagination.list_call_get_all_results(
                    compute_client.list_instances,
                    compartment_id=self.compartment_id,
                ).data
            except CONNECTIVITY_ERRORS as e:
                log_skipped_region(region, "list_instances spot/preemptible cleanup", e)
                continue
            except Exception as e:
                logging.error("Region %s: failed listing instances: %s", region, e)
                continue

            for instance in instances:
                if instance.lifecycle_state not in self.default_instance_state:
                    continue
                if not self._is_preemptible(instance):
                    continue
                if self._should_skip_instance(instance):
                    continue
                if not self._has_matching_filter_tags(instance):
                    continue
                if not self._is_old_instance(instance):
                    continue

                leftover = self._attached_block_volume_ids(
                    compute_client, instance.id
                )
                try:
                    if not self.dry_run:
                        # preserve_boot_volume=False deletes the boot volume with
                        # the instance (DeleteOnTermination=true equivalent).
                        compute_client.terminate_instance(
                            instance.id, preserve_boot_volume=False
                        )
                        logging.info(
                            "Terminating OCI preemptible instance %s",
                            instance.display_name,
                        )
                        if leftover and not self._wait_instance_terminated(
                            compute_client, instance.id
                        ):
                            logging.error(
                                "Instance %s not TERMINATED yet; attempting "
                                "leftover volume deletes anyway (may fail if "
                                "still attached)",
                                instance.display_name,
                            )
                    self.instance_names_to_delete.append(instance.display_name)
                    for volume_id in leftover:
                        self._delete_volume(blockstorage_client, volume_id)
                except Exception as e:
                    logging.error(
                        "Error terminating OCI preemptible instance %s: %s",
                        instance.display_name,
                        e,
                    )

        if not self.instance_names_to_delete:
            logging.warning("No OCI preemptible instances to delete.")

        label = "which will be deleted" if self.dry_run else "deleted"
        logging.warning(
            "List of OCI preemptible instances (Total: %s) %s: %s",
            len(self.instance_names_to_delete),
            label,
            self.instance_names_to_delete,
        )
        logging.warning(
            "List of OCI leftover block volumes (Total: %s) %s: %s",
            len(self.volumes_to_delete),
            label,
            self.volumes_to_delete,
        )
