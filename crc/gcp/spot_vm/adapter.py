# Copyright (c) Yugabyte, Inc.

"""`SpotVM`: the legacy class `crc.py` uses, a thin adapter over `SpotVMCleaner`."""

import datetime
from typing import Any, ClassVar

from crc.gcp.spot_vm import disks as leftover_disks
from crc.gcp.spot_vm import instances as spot_instances
from crc.gcp.spot_vm.cleaner import SpotVMCleaner, log_summary
from crc.gcp.spot_vm.client import ComputeSpotClient, GcpSpotClient, InstanceView
from crc.gcp.spot_vm.instances import Clock
from crc.gcp.spot_vm.operations import wait_operation
from crc.gcp.spot_vm.settings import (
    DEFAULT_INSTANCE_STATES,
    DEFAULT_POLL_INTERVAL_SEC,
    DEFAULT_WAIT_SEC,
    TIME_FORMAT,
    DiskRef,
    SpotSettings,
)
from crc.service import Service


class SpotVM(Service):
    """
    Delete GCP Spot / preemptible VMs that match tag and age filters.

    GCP has no separate spot-request object: deleting the instance is the
    cancel. Disks with autoDelete=false are deleted after the instance-delete
    zone operation completes (same idea as AWS DeleteOnTermination=false).
    """

    service_name = "compute"
    service_version = "v1"
    default_instance_state: ClassVar[list[str]] = list(DEFAULT_INSTANCE_STATES)
    time_format = TIME_FORMAT
    INSTANCE_DELETE_WAIT_SEC = DEFAULT_WAIT_SEC
    INSTANCE_POLL_INTERVAL_SEC = DEFAULT_POLL_INTERVAL_SEC

    def __init__(  # noqa: PLR0913, PLR0917 - the seven parameters are the public signature
        self,
        dry_run: bool,
        project_id: str,
        filter_labels: dict[str, list[str]],
        exception_labels: dict[str, list[str]],
        age: dict[str, int],
        custom_age_tag_key: str | None,
        notags: dict[str, list[str]],
        client: GcpSpotClient | None = None,
        clock: Clock = datetime.datetime.now,
    ) -> None:
        super().__init__()
        self.instance_names_to_delete: list[str] = []
        self.disks_to_delete: list[str] = []
        self.dry_run = dry_run
        self.project_id = project_id
        self.filter_labels = filter_labels or {}
        self.exception_labels = exception_labels or {}
        self.age = self.require_positive_age(age)
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags or {}
        self._client: GcpSpotClient = client or ComputeSpotClient()
        self._clock = clock

    @property
    def get_deleted(self) -> list[str]:
        """Names of the VMs deleted (listed in a dry run)."""
        return self.instance_names_to_delete

    @property
    def delete_count(self) -> int:
        """Number of VMs deleted."""
        return len(self.instance_names_to_delete)

    @property
    def get_deleted_disks(self) -> list[str]:
        """`project/location/name` of the leftover disks deleted (listed in a dry run)."""
        return self.disks_to_delete

    def _settings(self) -> SpotSettings:
        return SpotSettings(
            project_id=self.project_id,
            filter_labels=self.filter_labels,
            exception_labels=self.exception_labels,
            notags=self.notags,
            age=self.age,
            custom_age_tag_key=self.custom_age_tag_key,
            instance_states=self.default_instance_state,
            wait_sec=self.INSTANCE_DELETE_WAIT_SEC,
            poll_sec=self.INSTANCE_POLL_INTERVAL_SEC,
        )

    def _cleaner(self, client: GcpSpotClient | None = None) -> SpotVMCleaner:
        return SpotVMCleaner(client or self._client, self._settings())

    def _is_spot_instance(self, instance: InstanceView) -> bool:
        return spot_instances.is_spot_instance(instance)

    def _labels_protect(self, labels: dict[str, str]) -> bool:
        return spot_instances.labels_protect(labels, self._settings())

    def _should_skip_instance(self, instance: InstanceView) -> bool:
        return spot_instances.should_skip_instance(instance, self._settings())

    def _is_managed_or_gke_node(self, instance: InstanceView) -> bool:
        return spot_instances.is_managed_or_gke_node(instance)

    def _has_matching_filter_label(self, instance: InstanceView) -> bool:
        return spot_instances.has_matching_filter_label(instance, self._settings())

    def _is_old_instance(self, instance: InstanceView) -> bool:
        return spot_instances.is_old_instance(instance, self._settings(), self._clock)

    def _disk_name_from_source(self, source: str | None) -> str | None:
        ref = self._disk_ref_from_source(source)
        return ref["name"] if ref else None

    def _disk_ref_from_source(self, source: str | None) -> DiskRef | None:
        return leftover_disks.disk_ref_from_source(source, self.project_id)

    def _non_autodelete_disks(self, instance: InstanceView) -> list[DiskRef]:
        return leftover_disks.non_autodelete_disks(instance, self.project_id)

    def _wait_compute_operation(
        self,
        service: Any,
        scope: str,
        location: str,
        operation_name: str,
        project: str | None = None,
    ) -> bool:
        return wait_operation(
            ComputeSpotClient(service), self._settings(), scope, location, operation_name, project
        )

    def _wait_zone_operation(self, service: Any, zone: str, operation_name: str) -> bool:
        return self._wait_compute_operation(
            service, "zone", zone, operation_name, project=self.project_id
        )

    def _disk_ok_to_delete(self, service: Any, disk_ref: DiskRef, fallback_zone: str) -> bool:
        return leftover_disks.disk_ok_to_delete(
            ComputeSpotClient(service), self._settings(), disk_ref, fallback_zone
        )

    def _delete_disk(self, service: Any, disk_ref: DiskRef, fallback_zone: str) -> None:
        label = leftover_disks.remove_leftover_disk(
            ComputeSpotClient(service),
            self._settings(),
            disk_ref,
            fallback_zone,
            dry_run=self.dry_run,
        )
        if label is not None:
            self.disks_to_delete.append(label)

    def delete(self) -> None:
        """Delete (or, in a dry run, only list) the matching VMs of every zone, VM by VM,
        each followed by its leftover disks."""
        cleaner = self._cleaner()
        try:
            for zone_obj in cleaner.zones():
                zone = zone_obj["name"]
                for candidate in cleaner.scan_zone(zone, self._clock):
                    cleaner.apply_instance(
                        candidate,
                        self.instance_names_to_delete,
                        self.disks_to_delete,
                        dry_run=self.dry_run,
                    )
        finally:
            self._client.close()
        log_summary(self.instance_names_to_delete, self.disks_to_delete, dry_run=self.dry_run)
