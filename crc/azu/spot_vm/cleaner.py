# Copyright (c) Yugabyte, Inc.

"""Azure Spot VM cleaner: stopped or deallocated Spot VMs, their leftover disks and NIC.

`plan()` lists the VMs and filters them (tags, power state, age) with read calls only. In a dry
run it also previews the leftover disks and the NIC, so a dry run needs `plan()` alone. `apply()`
deletes each planned VM, then its unprotected leftover disks, then its NIC, as main does.
"""

import datetime
import logging
from dataclasses import replace
from typing import Any

from crc.azu import _logger
from crc.azu._base import Base
from crc.azu.spot_vm.client import ComputeFactory, NetworkFactory
from crc.azu.spot_vm.config import (
    STOPPED_POWER_STATES,
    SpotVmConfig,
    TagSpec,
    log_summary,
    old_enough,
)
from crc.azu.spot_vm.disks import DiskSlot, detached_disk_refs, disk_slots
from crc.azu.spot_vm.removal import RemovalOps, Removed, Remover
from crc.core import age as core_age
from crc.core import policy
from crc.core.cleaner import (
    Candidate,
    Cleaner,
    Failure,
    Operation,
    Plan,
    Result,
    RunContext,
    Skipped,
)
from crc.core.registry import register


class AzureSpotVmCleaner:
    """Deletes stopped or deallocated Spot VMs that match the tag filters and are old enough."""

    cloud = "azure"
    kind = "spot_instances"

    def __init__(
        self,
        compute_factory: ComputeFactory,
        network_factory: NetworkFactory,
        config: SpotVmConfig,
        ops: RemovalOps | None = None,
    ) -> None:
        self._compute_factory = compute_factory
        self._network_factory = network_factory
        self._config = config
        self._filter_tags = config.filter_tags or {}
        self._exception_tags = config.exception_tags or {}
        self._notags = config.notags or {}
        self.remover = Remover(
            compute_factory,
            network_factory,
            config.resource_group,
            config.exception_tags,
            config.notags,
            ops or RemovalOps(),
        )

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List and filter the VMs. In a dry run also preview disks and NIC (reads only)."""
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        previewed = Removed()
        started = _logger.started()
        scanned = 0
        for vm in self.list_vms():
            scanned += 1
            candidate, reason = self.decide(vm, context.now)
            if candidate is None:
                skipped.append(Skipped(id=getattr(vm, "name", ""), reason=reason))
                continue
            candidate = replace(candidate, extra={"disks": disk_slots(vm)})
            candidates.append(candidate)
            if context.dry_run:
                self._preview(candidate, previewed)
        _logger.region_done(self._config.resource_group, scanned, len(candidates), started)
        related: dict[str, tuple[str, ...]] = {}
        if context.dry_run:
            log_summary(previewed, dry_run=True)
            related = {"NIC": tuple(previewed.nics), "Disk": tuple(previewed.disks)}
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped), related=related)

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned VMs one by one; a failing VM is logged and the run goes on."""
        if context.dry_run:
            return Result(succeeded=tuple(c.id for c in plan.candidates), related=plan.related)
        removed = Removed()
        failed: list[Failure] = []
        for candidate in plan.candidates:
            slots: tuple[DiskSlot, ...] = candidate.extra.get("disks", ())
            try:
                leftovers = detached_disk_refs(candidate.name, slots, self._config.resource_group)
                self.remover.remove_vm(candidate.name, leftovers, removed)
            except Exception as e:  # noqa: BLE001 - SAF-005 per-VM isolation, failure logged and recorded
                logging.error("Error deleting Spot VM %s: %s", candidate.name, e)
                _logger.failed(self._config.resource_group, candidate.id, e)
                failed.append(Failure(id=candidate.id, error=str(e)))
        log_summary(removed, dry_run=False)
        return Result(
            succeeded=tuple(removed.vms),
            failed=tuple(failed),
            related={"NIC": tuple(removed.nics), "Disk": tuple(removed.disks)},
        )

    def _preview(self, candidate: Candidate, previewed: Removed) -> None:
        """Dry-run record of one VM: same reads and records as main, no mutating call."""
        slots: tuple[DiskSlot, ...] = candidate.extra.get("disks", ())
        try:
            leftovers = detached_disk_refs(candidate.name, slots, self._config.resource_group)
            previewed.vms.append(candidate.name)
            _logger.acted("would_delete", self._config.resource_group, candidate.name)
            for resource_group, disk_name, _ in leftovers:
                if not self.remover.disk_protected(resource_group, disk_name):
                    previewed.disks.append(f"{resource_group}/{disk_name}")
            self._network_factory()
            previewed.nics.append(f"{candidate.name}-NIC")
        except Exception as e:  # noqa: BLE001 - per-VM isolation in the dry-run preview, failure logged and recorded
            logging.error("Error deleting Spot VM %s: %s", candidate.name, e)
            _logger.failed(self._config.resource_group, candidate.name, e)

    def list_vms(self) -> list[Any]:
        """The VMs of the resource group; a failure is logged and re-raised."""
        try:
            compute = self._compute_factory()
            return list(compute.virtual_machines.list(self._config.resource_group))
        except Exception as e:
            logging.error(
                "Failed to list Azure VMs in resource group %s: %s",
                self._config.resource_group,
                e,
            )
            raise

    def power_status(self, vm_name: str) -> str:
        """Second status of the instance view, or "" when missing or the read fails."""
        try:
            instance_view = self._compute_factory().virtual_machines.instance_view(
                self._config.resource_group, vm_name
            )
            if instance_view.statuses and len(instance_view.statuses) > 1:
                return str(instance_view.statuses[1].display_status or "")
        except Exception as e:  # noqa: BLE001 - status read failure logged, empty status keeps the VM
            logging.error("Failed to get instance view for %s: %s", vm_name, e)
        return ""

    def tags_skip(self, vm: Any) -> bool:
        """exception_tags (any key) or notags (all keys) protect the VM."""
        if not vm.tags:
            return False
        return policy.should_skip_by_tags(vm.tags, self._exception_tags, self._notags)

    def tags_include(self, vm: Any) -> bool:
        """Tagged VM matching filter_tags (all keys; empty filter matches any tagged VM)."""
        if not vm.tags:
            return False
        return policy.filter_tags_all_keys(vm.tags, self._filter_tags)

    def decide(self, vm: Any, now: datetime.datetime) -> tuple[Candidate | None, str]:
        """Select one SDK VM: the candidate, or None with the reason it was kept."""
        reason, power_status = self._kept_before_age(vm)
        if reason:
            _logger.skip(reason)
            return None, reason
        time_created = getattr(vm, "time_created", None)
        if not time_created:
            _logger.skip("no creation time")
            logging.warning(
                "Skipping Spot VM %s: no creation time (age check not possible)", vm.name
            )
            return None, "no creation time"
        if not old_enough(self._config, vm, now, time_created):
            _logger.skip("younger than the age threshold or invalid custom age tag")
            return None, "younger than the age threshold or invalid custom age tag"
        return (
            Candidate(
                id=vm.name,
                name=vm.name,
                state=power_status,
                launch_time=time_created,
                tags=policy.normalize_tags(vm.tags),
            ),
            "",
        )

    def _kept_before_age(self, vm: Any) -> tuple[str, str]:
        """(reason, power state): a non-empty reason means the VM is kept before the age check."""
        priority = getattr(vm, "priority", None)
        if priority is None or str(priority).lower() != "spot":
            return "not a Spot VM", ""
        if self.tags_skip(vm):
            return "exception_tags or notags match", ""
        if not self.tags_include(vm):
            return "tags do not match filter_tags (or untagged)", ""
        power_status = self._power_state(vm)
        if power_status is None:
            return "power state unreadable", ""
        if not policy.state_in(power_status, STOPPED_POWER_STATES):
            logging.debug("Skipping Spot VM %s: power state '%s'", vm.name, power_status)
            return "not stopped or deallocated", power_status
        return "", power_status

    def _power_state(self, vm: Any) -> str | None:
        try:
            return self.power_status(vm.name)
        except Exception as e:  # noqa: BLE001 - status read failure logged, unknown power state keeps the VM
            logging.error("Error getting status for Spot VM %s: %s", vm.name, e)
            return None


@register("azure", "spot_instances")
def build_spot_vm_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: resource_group, filter_tags, exception_tags, notags, age,
    custom_age_tag_key."""
    options = context.options
    base = Base(options.get("resource_group") or "")
    config = SpotVmConfig(
        resource_group=base.resource_group,
        filter_tags=options.get("filter_tags"),
        exception_tags=options.get("exception_tags"),
        notags=options.get("notags"),
        age=core_age.require_positive_age(options.get("age")),
        custom_age_tag_key=options.get("custom_age_tag_key"),
    )
    return AzureSpotVmCleaner(base.get_compute_client, base.get_network_client, config)


__all__ = [
    "STOPPED_POWER_STATES",
    "AzureSpotVmCleaner",
    "SpotVmConfig",
    "TagSpec",
    "build_spot_vm_cleaner",
    "log_summary",
]
