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

    def _labels_protect(self, labels: dict) -> bool:
        if not labels:
            return False
        if self.exception_labels and any(
            key in labels and (not value or labels[key] in value)
            for key, value in self.exception_labels.items()
        ):
            return True
        if self.notags and all(
            key in labels and (not value or labels[key] in value)
            for key, value in self.notags.items()
        ):
            return True
        return False

    def _should_skip_instance(self, instance) -> bool:
        return self._labels_protect(dict(getattr(instance, "labels", None) or {}))

    def _is_managed_or_gke_node(self, instance) -> bool:
        labels = dict(getattr(instance, "labels", None) or {})
        if "goog-gke-node" in labels:
            return True
        metadata = getattr(instance, "metadata", None)
        # Iterate any sequence (proto-plus RepeatedComposite is not list/tuple).
        for item in getattr(metadata, "items", None) or []:
            if getattr(item, "key", None) != "created-by":
                continue
            value = str(getattr(item, "value", "") or "")
            if "instanceGroupManagers" in value:
                return True
        return False

    def _has_matching_filter_label(self, instance) -> bool:
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
        try:
            retention_age = self.validated_retention_age(
                labels, self.custom_age_tag_key
            )
        except ValueError:
            # Present but invalid custom age tag — fail closed (keep).
            return False
        if retention_age:
            logging.info("Updating age for Spot VM: %s", instance.name)
        return self.is_old(retention_age or self.age, now, created)

    def _disk_name_from_source(self, source: Optional[str]) -> Optional[str]:
        ref = self._disk_ref_from_source(source)
        return ref["name"] if ref else None

    def _disk_ref_from_source(self, source: Optional[str]) -> Optional[Dict[str, str]]:
        """
        Parse a disk source URL into project / scope / location / name.

        Supports:
          projects/P/zones/Z/disks/NAME
          projects/P/regions/R/disks/NAME
        """
        if not source:
            return None
        parts = source.rstrip("/").split("/")
        try:
            p_idx = parts.index("projects")
            project = parts[p_idx + 1]
            name = parts[-1]
            if "zones" in parts:
                z_idx = parts.index("zones")
                return {
                    "project": project,
                    "scope": "zone",
                    "location": parts[z_idx + 1],
                    "name": name,
                }
            if "regions" in parts:
                r_idx = parts.index("regions")
                return {
                    "project": project,
                    "scope": "region",
                    "location": parts[r_idx + 1],
                    "name": name,
                }
        except (ValueError, IndexError):
            pass
        # Fallback: name only in the caller's project/zone
        name = parts[-1] if parts else None
        if not name:
            return None
        return {
            "project": self.project_id,
            "scope": "zone",
            "location": "",
            "name": name,
        }

    def _non_autodelete_disks(self, instance) -> List[Dict[str, str]]:
        refs = []
        for disk in getattr(instance, "disks", None) or []:
            if getattr(disk, "auto_delete", True):
                continue
            ref = self._disk_ref_from_source(getattr(disk, "source", None))
            if ref:
                refs.append(ref)
                logging.info(
                    "Spot VM %s disk %s/%s/%s has autoDelete=false "
                    "(will delete after VM)",
                    instance.name,
                    ref["project"],
                    ref["location"] or "?",
                    ref["name"],
                )
        return refs

    def _wait_compute_operation(
        self,
        service,
        scope: str,
        location: str,
        operation_name: str,
        project: Optional[str] = None,
    ) -> bool:
        """Wait until a zone or region operation reaches DONE."""
        project = project or self.project_id
        deadline = time.time() + self.INSTANCE_DELETE_WAIT_SEC
        while time.time() < deadline:
            try:
                if scope == "region":
                    op = (
                        service.regionOperations()
                        .get(
                            project=project,
                            region=location,
                            operation=operation_name,
                        )
                        .execute()
                    )
                else:
                    op = (
                        service.zoneOperations()
                        .get(
                            project=project,
                            zone=location,
                            operation=operation_name,
                        )
                        .execute()
                    )
            except Exception as e:
                logging.error(
                    "Error polling GCP %s operation %s/%s/%s: %s",
                    scope,
                    project,
                    location,
                    operation_name,
                    e,
                )
                return False
            status = op.get("status")
            if status == "DONE":
                if op.get("error"):
                    logging.error(
                        "GCP %s operation %s/%s/%s failed: %s",
                        scope,
                        project,
                        location,
                        operation_name,
                        op.get("error"),
                    )
                    return False
                return True
            logging.info(
                "Waiting for GCP %s operation %s/%s/%s (status=%s)",
                scope,
                project,
                location,
                operation_name,
                status,
            )
            time.sleep(self.INSTANCE_POLL_INTERVAL_SEC)
        logging.error(
            "Timed out waiting for GCP %s operation %s/%s/%s",
            scope,
            project,
            location,
            operation_name,
        )
        return False

    def _wait_zone_operation(self, service, zone: str, operation_name: str) -> bool:
        return self._wait_compute_operation(
            service, "zone", zone, operation_name, project=self.project_id
        )

    def _disk_ok_to_delete(
        self, service, disk_ref: Dict[str, str], fallback_zone: str
    ) -> bool:
        project = disk_ref.get("project") or self.project_id
        scope = disk_ref.get("scope") or "zone"
        location = disk_ref.get("location") or fallback_zone
        disk_name = disk_ref["name"]
        label = f"{project}/{location}/{disk_name}"
        try:
            if scope == "region":
                disk = (
                    service.regionDisks()
                    .get(project=project, region=location, disk=disk_name)
                    .execute()
                )
            else:
                disk = (
                    service.disks()
                    .get(project=project, zone=location, disk=disk_name)
                    .execute()
                )
        except Exception as e:
            logging.error(
                "Failed to get leftover disk %s for label check (skipping): %s",
                label,
                e,
            )
            return False
        if self._labels_protect(disk.get("labels") or {}):
            logging.info(
                "Skipping leftover Spot disk %s: exception_labels/notags", label
            )
            return False
        return True

    def _delete_disk(
        self, service, disk_ref: Dict[str, str], fallback_zone: str
    ) -> None:
        project = disk_ref.get("project") or self.project_id
        scope = disk_ref.get("scope") or "zone"
        location = disk_ref.get("location") or fallback_zone
        disk_name = disk_ref["name"]
        label = f"{project}/{location}/{disk_name}"
        if self.dry_run:
            self.disks_to_delete.append(label)
            return
        try:
            if scope == "region":
                op = (
                    service.regionDisks()
                    .delete(project=project, region=location, disk=disk_name)
                    .execute()
                )
            else:
                op = (
                    service.disks()
                    .delete(project=project, zone=location, disk=disk_name)
                    .execute()
                )
            op_name = op.get("name")
            if op_name and not self._wait_compute_operation(
                service, scope, location, op_name, project=project
            ):
                logging.error(
                    "Disk delete operation failed/incomplete for %s; not counted",
                    label,
                )
                return
            logging.info("Deleted leftover Spot VM disk %s", label)
            self.disks_to_delete.append(label)
        except Exception as e:
            logging.error("Failed to delete leftover disk %s: %s", label, e)

    def delete(self) -> None:
        with discovery.build(self.service_name, self.service_version) as service:
            zones = service.zones().list(project=self.project_id).execute().get(
                "items", []
            )
            instance_client = compute_v1.InstancesClient()

            for zone_obj in zones:
                zone = zone_obj["name"]
                try:
                    instances = list(
                        instance_client.list(project=self.project_id, zone=zone)
                    )
                except Exception as e:
                    logging.error(
                        "Zone %s: failed listing GCP instances: %s", zone, e
                    )
                    continue
                for instance in instances:
                    try:
                        if instance.status not in self.default_instance_state:
                            continue
                        if not self._is_spot_instance(instance):
                            continue
                        if self._is_managed_or_gke_node(instance):
                            continue
                        if self._should_skip_instance(instance):
                            continue
                        if not self._has_matching_filter_label(instance):
                            continue
                        if not self._is_old_instance(instance):
                            continue

                        leftover = self._non_autodelete_disks(instance)
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
                                    "skipping leftover disk deletes",
                                    instance.name,
                                )
                                continue
                        self.instance_names_to_delete.append(instance.name)
                        for disk_ref in leftover:
                            if not self._disk_ok_to_delete(
                                service, disk_ref, zone
                            ):
                                continue
                            self._delete_disk(service, disk_ref, zone)
                    except Exception as e:
                        logging.error(
                            "Error processing GCP Spot VM %s: %s",
                            getattr(instance, "name", "?"),
                            e,
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
