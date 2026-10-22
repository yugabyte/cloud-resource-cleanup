# Copyright (c) Yugabyte, Inc.

"""GCP Spot VM cleaner: `plan()` lists and filters, `apply()` deletes the VMs and their
leftover disks."""

import logging
from collections.abc import Iterable, Iterator
from typing import Any

from crc.core import age as core_age
from crc.core import logger
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext
from crc.core.registry import register
from crc.gcp._scan import ZoneScan, log_action
from crc.gcp.spot_vm.client import ComputeSpotClient, GcpSpotClient, InstanceView
from crc.gcp.spot_vm.disks import (
    disk_label,
    disk_ok_to_delete,
    non_autodelete_disks,
    remove_leftover_disk,
)
from crc.gcp.spot_vm.instances import Clock, reject_reason
from crc.gcp.spot_vm.operations import wait_operation
from crc.gcp.spot_vm.settings import (
    DEFAULT_POLL_INTERVAL_SEC,
    DEFAULT_WAIT_SEC,
    DiskRef,
    SpotSettings,
)

LEFTOVER_DISKS = "leftover_disks"
DISK = "Disk"


def log_summary(instances: list[str], disks: list[str], *, dry_run: bool) -> None:
    """The closing log lines of a run, as `main` writes them."""
    if not instances:
        logging.warning("No GCP Spot/preemptible VMs to delete.")
    label = "which will be deleted" if dry_run else "deleted"
    logging.warning("List of GCP Spot VMs (Total: %s) %s: %s", len(instances), label, instances)
    logging.warning(
        "List of GCP leftover disks (autoDelete=false) (Total: %s) %s: %s",
        len(disks),
        label,
        disks,
    )


def _log_failure(name: object, error: Exception, zone: str) -> None:
    logging.error("Error processing GCP Spot VM %s: %s", name, error)
    logger.current().fail(zone, str(name), error)


def _leftover(candidate: Candidate) -> tuple[DiskRef, ...]:
    refs: tuple[DiskRef, ...] = candidate.extra.get(LEFTOVER_DISKS, ())
    return refs


class SpotVMCleaner:
    """Deletes Spot / preemptible VMs (`RUNNING`, `TERMINATED` or `SUSPENDED`) that match the
    label filters and are old enough, then their `autoDelete=false` disks. Skips GKE nodes and
    managed instance group members. Applies to every zone of the project."""

    cloud = "gcp"
    kind = "spot_instances"

    def __init__(self, client: GcpSpotClient, settings: SpotSettings) -> None:
        self._client = client
        self._settings = settings

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def zones(self) -> Iterable[Any]:
        """Zones of the project (read-only call)."""
        return self._client.list_zones(self._settings.project_id)

    def list_zone(self, zone: str) -> list[InstanceView]:
        """Instances of one zone; a listing error is logged and the zone skipped."""
        return self._list(zone) or []

    def _list(self, zone: str) -> list[InstanceView] | None:
        try:
            return list(self._client.list_instances(self._settings.project_id, zone))
        except Exception as e:
            logging.error("Zone %s: failed listing GCP instances: %s", zone, e)
            logger.current().region_skipped(zone, e)
            return None

    def scan_zone(self, zone: str, clock: Clock) -> Iterator[Candidate]:
        """List one zone and lazily yield the candidates, then log the REGION line (none for a
        zone whose listing failed)."""
        instances = self._list(zone)
        if instances is None:
            return
        scan = ZoneScan(zone)
        for instance in instances:
            scan.scanned += 1
            candidate = self.evaluate(instance, zone, clock)
            if candidate is not None:
                scan.selected += 1
                yield candidate
        scan.finish()

    def evaluate(self, instance: InstanceView, zone: str, clock: Clock) -> Candidate | None:
        """The candidate when the instance passes every filter. Any error is logged and the
        instance is kept."""
        try:
            reason = reject_reason(instance, self._settings, clock)
            if reason is not None:
                logger.current().skip(reason)
                return None
            leftover = non_autodelete_disks(instance, self._settings.project_id)
            return Candidate(
                id=instance.name,
                name=instance.name,
                region=zone,
                state=instance.status,
                tags=dict(getattr(instance, "labels", None) or {}),
                extra={LEFTOVER_DISKS: tuple(leftover)},
            )
        except Exception as e:
            _log_failure(getattr(instance, "name", "?"), e, zone)
            return None

    def plan(self, context: RunContext) -> Plan:
        """List every zone and filter. Makes no mutating call; in a dry run it also reads the
        leftover disks and logs the summary."""
        candidates: list[Candidate] = []
        instances: list[str] = []
        disks: list[str] = []
        try:
            for zone_obj in self.zones():
                zone = zone_obj["name"]
                candidates.extend(self.scan_zone(zone, lambda: context.now))
            if context.dry_run:
                for candidate in candidates:
                    self.preview_instance(candidate, instances, disks)
        finally:
            self._client.close()
        if context.dry_run:
            log_summary(instances, disks, dry_run=True)
        return Plan(candidates=tuple(candidates), related={DISK: tuple(disks)})

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned VMs one by one; a failed VM is logged and skipped."""
        instances: list[str] = []
        disks: list[str] = []
        try:
            for candidate in plan.candidates:
                self.apply_instance(candidate, instances, disks, dry_run=context.dry_run)
        finally:
            self._client.close()
        log_summary(instances, disks, dry_run=context.dry_run)
        return Result(succeeded=tuple(instances), related={DISK: tuple(disks)})

    def preview_instance(
        self, candidate: Candidate, instances: list[str], disks: list[str]
    ) -> None:
        """Dry run of one VM: record it, then record the leftover disks that pass the label
        check (a read of each disk). Any error is logged."""
        settings = self._settings
        try:
            instances.append(candidate.id)
            log_action("delete", candidate, dry_run=True)
            disks.extend(
                disk_label(settings, ref, candidate.region)
                for ref in _leftover(candidate)
                if disk_ok_to_delete(self._client, settings, ref, candidate.region)
            )
        except Exception as e:
            _log_failure(candidate.name, e, candidate.region)

    def apply_instance(
        self, candidate: Candidate, instances: list[str], disks: list[str], *, dry_run: bool
    ) -> None:
        """Delete one VM and wait for the operation, then its leftover disks, recording each
        as it is done. A VM whose delete fails or does not complete is not recorded and its
        disks are kept. Any error is logged."""
        settings = self._settings
        zone = candidate.region
        try:
            if not dry_run and not self._delete_and_wait(candidate):
                return
            instances.append(candidate.id)
            log_action("delete", candidate, dry_run=dry_run)
            for ref in _leftover(candidate):
                if not disk_ok_to_delete(self._client, settings, ref, zone):
                    continue
                label = remove_leftover_disk(self._client, settings, ref, zone, dry_run=dry_run)
                if label is not None:
                    disks.append(label)
        except Exception as e:
            _log_failure(candidate.name, e, zone)

    def _delete_and_wait(self, candidate: Candidate) -> bool:
        settings = self._settings
        zone = candidate.region
        operation = self._client.delete_instance(settings.project_id, zone, candidate.id)
        logging.info("Deleting GCP Spot VM %s", candidate.name)
        op_name = operation.get("name")
        if op_name and wait_operation(self._client, settings, "zone", zone, op_name):
            return True
        logging.error(
            "Instance delete failed/incomplete for %s; skipping leftover disk deletes",
            candidate.name,
        )
        logger.current().fail(zone, candidate.id, "instance delete failed or incomplete")
        return False


@register("gcp", "spot_instances")
def build_spot_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `project_id` (ValueError without it), `age` (a positive
    days/hours dict or int, ValueError otherwise), `filter_tags` (labels), `exception_tags`
    (labels), `notags`, `custom_age_tag_key`, and optionally `instance_delete_wait_sec` and
    `instance_poll_interval_sec`."""
    options = context.options
    project_id = options.get("project_id")
    if not project_id:
        raise ValueError("project_id is required for GCP Spot VM cleanup")
    settings = SpotSettings(
        project_id=project_id,
        filter_labels=options.get("filter_tags") or {},
        exception_labels=options.get("exception_tags") or {},
        notags=options.get("notags") or {},
        age=core_age.require_positive_age(options.get("age")),
        custom_age_tag_key=options.get("custom_age_tag_key"),
        wait_sec=options.get("instance_delete_wait_sec", DEFAULT_WAIT_SEC),
        poll_sec=options.get("instance_poll_interval_sec", DEFAULT_POLL_INTERVAL_SEC),
    )
    return SpotVMCleaner(ComputeSpotClient(), settings)
