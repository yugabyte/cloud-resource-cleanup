# Copyright (c) Yugabyte, Inc.

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

from google.cloud import compute_v1
from googleapiclient import discovery

from crc.service import Service


class SpotVM(Service):
    """
    Delete GCP Spot / preemptible VMs that match tag and age filters.

    GCP has no separate spot-request object — deleting the instance is the
    cancel. Disks with autoDelete=false are deleted after the instance-delete
    zone operation completes (same idea as AWS DeleteOnTermination=false).
    """

    service_name = "compute"
    service_version = "v1"
    default_instance_state = ["RUNNING", "TERMINATED", "SUSPENDED"]
    time_format = "%Y-%m-%dT%H:%M:%S.%f%z"
    INSTANCE_DELETE_WAIT_SEC = 180
    INSTANCE_POLL_INTERVAL_SEC = 5

    def __init__(
        self,
        dry_run: bool,
        project_id: str,
        filter_labels: Dict[str, List[str]],
        exception_labels: Dict[str, List[str]],
        age: Dict[str, int],
        custom_age_tag_key: str,
        notags: Dict[str, List[str]],
    ) -> None:
        super().__init__()
        self.instance_names_to_delete: List[str] = []
        self.disks_to_delete: List[str] = []
        self.dry_run = dry_run
        self.project_id = project_id
        self.filter_labels = filter_labels or {}
        self.exception_labels = exception_labels or {}
        self.age = self.require_positive_age(age)
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags or {}

    @property
    def get_deleted(self):
        return self.instance_names_to_delete

    @property
    def delete_count(self):
        return len(self.instance_names_to_delete)

    @property
    def get_deleted_disks(self):
        return self.disks_to_delete

    def _is_spot_instance(self, instance) -> bool:
        scheduling = getattr(instance, "scheduling", None)
        if not scheduling:
            return False
        if getattr(scheduling, "preemptible", False):
            return True
        provisioning = str(getattr(scheduling, "provisioning_model", "") or "").upper()
        return provisioning == "SPOT"

    def _should_skip_instance(self, instance) -> bool:
        labels = dict(getattr(instance, "labels", None) or {})
        if not labels:
            return False
        if self.exception_labels:
            if any(
                key in labels and (not value or labels[key] in value)
                for key, value in self.exception_labels.items()
            ):
                return True
        if self.notags:
            if all(
                key in labels and (not value or labels[key] in value)
                for key, value in self.notags.items()
            ):
                return True
        return False

    def _has_matching_filter_label(self, instance) -> bool:
        """
        Match filter_labels. Empty filter means any instance is eligible
        (including unlabeled), same as crc.gcp.vm.VM.
        """
        if not self.filter_labels:
            return True
        labels = dict(getattr(instance, "labels", None) or {})
        return all(
            key in labels and (not value or labels[key] in value)
            for key, value in self.filter_labels.items()
        )

    def _is_old_instance(self, instance) -> bool:
        created = datetime.strptime(instance.creation_timestamp, self.time_format)
        now = datetime.now(tz=created.tzinfo)
        labels = dict(getattr(instance, "labels", None) or {})
        retention_age = self.validated_retention_age(
            labels, self.custom_age_tag_key
        )
        if retention_age:
            logging.info("Updating age for Spot VM: %s", instance.name)
        return self.is_old(retention_age or self.age, now, created)

    def _disk_name_from_source(self, source: Optional[str]) -> Optional[str]:
        if not source:
            return None
        # projects/.../zones/.../disks/NAME or full URL
        return source.rstrip("/").split("/")[-1]

    def _non_autodelete_disks(self, instance) -> List[str]:
        names = []
        for disk in getattr(instance, "disks", None) or []:
            if getattr(disk, "auto_delete", True):
                continue
            name = self._disk_name_from_source(getattr(disk, "source", None))
            if name:
                names.append(name)
                logging.info(
                    "Spot VM %s disk %s has autoDelete=false (will delete after VM)",
                    instance.name,
                    name,
                )
        return names

    def _wait_zone_operation(self, service, zone: str, operation_name: str) -> bool:
        """Wait until a zonal operation reaches DONE (instance delete)."""
        deadline = time.time() + self.INSTANCE_DELETE_WAIT_SEC
        while time.time() < deadline:
            try:
                op = (
                    service.zoneOperations()
                    .get(
                        project=self.project_id,
                        zone=zone,
                        operation=operation_name,
                    )
                    .execute()
                )
            except Exception as e:
                logging.error(
                    "Error polling GCP zone operation %s/%s: %s",
                    zone,
                    operation_name,
                    e,
                )
                return False
            status = op.get("status")
            if status == "DONE":
                if op.get("error"):
                    logging.error(
                        "GCP zone operation %s/%s failed: %s",
                        zone,
                        operation_name,
                        op.get("error"),
                    )
                    return False
                return True
            logging.info(
                "Waiting for GCP zone operation %s/%s (status=%s)",
                zone,
                operation_name,
                status,
            )
            time.sleep(self.INSTANCE_POLL_INTERVAL_SEC)
        logging.error(
            "Timed out waiting for GCP zone operation %s/%s", zone, operation_name
        )
        return False

    def _delete_disk(self, service, zone: str, disk_name: str) -> None:
        if self.dry_run:
            self.disks_to_delete.append(f"{zone}/{disk_name}")
            return
        try:
            op = (
                service.disks()
                .delete(project=self.project_id, zone=zone, disk=disk_name)
                .execute()
            )
            op_name = op.get("name")
            if op_name and not self._wait_zone_operation(service, zone, op_name):
                logging.error(
                    "Disk delete operation failed/incomplete for %s/%s; not counted",
                    zone,
                    disk_name,
                )
                return
            logging.info("Deleted leftover Spot VM disk %s/%s", zone, disk_name)
            self.disks_to_delete.append(f"{zone}/{disk_name}")
        except Exception as e:
            logging.error(
                "Failed to delete leftover disk %s/%s: %s", zone, disk_name, e
            )

    def delete(self) -> None:
        with discovery.build(self.service_name, self.service_version) as service:
            zones = service.zones().list(project=self.project_id).execute().get(
                "items", []
            )
            instance_client = compute_v1.InstancesClient()

            for zone_obj in zones:
                zone = zone_obj["name"]
                for instance in instance_client.list(
                    project=self.project_id, zone=zone
                ):
                    if instance.status not in self.default_instance_state:
                        continue
                    if not self._is_spot_instance(instance):
                        continue
                    if self._should_skip_instance(instance):
                        continue
                    if not self._has_matching_filter_label(instance):
                        continue
                    if not self._is_old_instance(instance):
                        continue

                    leftover = self._non_autodelete_disks(instance)
                    try:
                        if not self.dry_run:
                            op = (
                                service.instances()
                                .delete(
                                    project=self.project_id,
                                    zone=zone,
                                    instance=instance.name,
                                )
                                .execute()
                            )
                            logging.info("Deleting GCP Spot VM %s", instance.name)
                            op_name = op.get("name")
                            if not op_name or not self._wait_zone_operation(
                                service, zone, op_name
                            ):
                                logging.error(
                                    "Instance delete failed/incomplete for %s; "
                                    "not counting as deleted and skipping "
                                    "leftover disk deletes",
                                    instance.name,
                                )
                                continue
                        self.instance_names_to_delete.append(instance.name)
                        for disk_name in leftover:
                            self._delete_disk(service, zone, disk_name)
                    except Exception as e:
                        logging.error(
                            "Error deleting GCP Spot VM %s: %s", instance.name, e
                        )

        if not self.instance_names_to_delete:
            logging.warning("No GCP Spot/preemptible VMs to delete.")

        label = "which will be deleted" if self.dry_run else "deleted"
        logging.warning(
            "List of GCP Spot VMs (Total: %s) %s: %s",
            len(self.instance_names_to_delete),
            label,
            self.instance_names_to_delete,
        )
        logging.warning(
            "List of GCP leftover disks (autoDelete=false) (Total: %s) %s: %s",
            len(self.disks_to_delete),
            label,
            self.disks_to_delete,
        )
