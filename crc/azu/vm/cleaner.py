# Copyright (c) Yugabyte, Inc.

"""Azure VM cleaner.

`VmCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the VMs of the
configured resource group only, applies the tag and age policy and reads each VM's power state;
`apply()` deletes (and removes the `<vm>-NIC` NIC) or deallocates the planned VMs.
"""

import logging
from collections.abc import Iterable, Sequence

from crc.azu import _logger
from crc.azu._base import Base
from crc.azu.vm.client import ComputeFactory, NetworkFactory, SdkVm
from crc.azu.vm.helpers import (
    NIC_LABEL,
    SKIP_NO_POWER_STATE,
    SKIP_STATE_NOT_ALLOWED,
    log_processing_error,
    power_state,
    tally_of,
)
from crc.azu.vm.selection import skip_reason
from crc.azu.vm.settings import (
    DEFAULT_INSTANCE_STATES,
    Clock,
    TagSpec,
    Tally,
    VmSettings,
    log_summary,
)
from crc.azu.vm_delete_helpers import begin_delete_vm_and_wait, delete_vm_primary_nic
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


class VmCleaner:
    """Deletes (with its `<vm>-NIC`) or deallocates VMs that are tagged, match the filter tags
    (all keys), are not excluded by exception tags or notags, are old enough and whose power state
    (the instance view status coded `PowerState/...`) equals an allowed state. Every call,
    the listing included, uses the configured resource group."""

    cloud = "azure"
    kind = "vm"

    def __init__(
        self,
        compute_factory: ComputeFactory,
        network_factory: NetworkFactory,
        resource_group: str,
        settings: VmSettings,
    ) -> None:
        self._compute = compute_factory
        self._network = network_factory
        self._resource_group = resource_group
        self._settings = settings

    def supports(self, operation: Operation) -> bool:
        return operation in (Operation.DELETE, Operation.STOP)

    def plan(self, context: RunContext) -> Plan:
        """List the VMs, decide on each and read the status of those that pass. Makes no
        mutating call."""
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        started = _logger.started()
        scanned = 0
        for vm in self.list_vms():
            scanned += 1
            decision = self.decide(vm, lambda: context.now)
            if isinstance(decision, Candidate):
                decision = self._with_status(decision)
            if isinstance(decision, Candidate):
                candidates.append(decision)
            else:
                skipped.append(decision)
        _logger.region_done(self._resource_group, scanned, len(candidates), started)
        tally = tally_of(context.operation, (c.id for c in candidates))
        if context.dry_run:
            kind = "would_delete" if context.operation is Operation.DELETE else "would_stop"
            for candidate in candidates:
                _logger.acted(kind, self._resource_group, candidate.id)
            log_summary(context.operation, tally, dry_run=True)
        related = {NIC_LABEL: tuple(tally.nics)} if context.operation is Operation.DELETE else {}
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped), related=related)

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Act on the planned VMs in plan order (nothing in a dry run). A failed VM is logged
        and left out of the result, as on main."""
        if context.dry_run:
            names = [c.id for c in plan.candidates]
            return Result(succeeded=tuple(names), related=plan.related)
        tally = Tally()
        failed: list[Failure] = []
        for candidate in plan.candidates:
            try:
                self.act(candidate.id, context.operation, tally, dry_run=False)
            except Exception as e:  # noqa: BLE001 - per-resource isolation, failure logged and recorded, resource kept
                log_processing_error(candidate.id, e)
                _logger.failed(self._resource_group, candidate.id, e, logged=True)
                failed.append(Failure(id=candidate.id, error=str(e)))
        log_summary(context.operation, tally, dry_run=False)
        if context.operation is Operation.DELETE:
            return Result(
                succeeded=tuple(tally.deleted),
                failed=tuple(failed),
                related={NIC_LABEL: tuple(tally.nics)},
            )
        return Result(succeeded=tuple(tally.stopped), failed=tuple(failed))

    def list_vms(self) -> Iterable[SdkVm]:
        """The (lazy) listing of the configured resource group (BUG-002). An empty group raises
        before any SDK call."""
        if not self._resource_group:
            raise ValueError("Azure VM cleanup needs a resource group; refusing to list")
        return self._compute().virtual_machines.list(self._resource_group)

    def decide(self, vm: SdkVm, clock: Clock) -> Candidate | Skipped:
        """Tag and age checks, in the order of main. `clock` is only read by the age check, as
        `datetime.now()` is on main."""
        tags = policy.normalize_tags(vm.tags)
        reason = skip_reason(self._settings, vm, tags, clock)
        if reason is not None:
            _logger.skip(reason)
            return Skipped(id=getattr(vm, "name", ""), reason=reason)
        return Candidate(id=vm.name, name=vm.name, launch_time=vm.time_created, tags=tags)

    def state_skip_reason(self, vm_name: str, states: Sequence[str]) -> str | None:
        """None when the VM's power state equals an allowed state, else the skip reason:
        `no_power_state` when the instance view has no `PowerState/` status (BUG-010)."""
        view = self._compute().virtual_machines.instance_view(self._resource_group, vm_name)
        state = power_state(view.statuses)
        if state is None:
            return SKIP_NO_POWER_STATE
        if state in states:
            return None
        return SKIP_STATE_NOT_ALLOWED

    def act(self, vm_name: str, operation: Operation, tally: Tally, *, dry_run: bool) -> None:
        """Delete (with the NIC) or deallocate one VM and record its name, also in a dry run,
        where nothing is called."""
        if operation is Operation.DELETE:
            self._remove(vm_name, tally, dry_run=dry_run)
        else:
            self._deallocate(vm_name, tally, dry_run=dry_run)

    def _remove(self, vm_name: str, tally: Tally, *, dry_run: bool) -> None:
        if not dry_run:
            logging.info("Deleting virtual machine: %s", vm_name)
            begin_delete_vm_and_wait(self._compute(), self._resource_group, vm_name)
        tally.deleted.append(vm_name)
        _logger.acted("would_delete" if dry_run else "deleted", self._resource_group, vm_name)
        delete_vm_primary_nic(self._network(), self._resource_group, vm_name, dry_run, tally.nics)

    def _deallocate(self, vm_name: str, tally: Tally, *, dry_run: bool) -> None:
        if not dry_run:
            self._compute().virtual_machines.begin_deallocate(self._resource_group, vm_name)
            logging.info("Stopping virtual machine: %s", vm_name)
        tally.stopped.append(vm_name)
        _logger.acted("would_stop" if dry_run else "stopped", self._resource_group, vm_name)

    def _with_status(self, candidate: Candidate) -> Candidate | Skipped:
        try:
            reason = self.state_skip_reason(candidate.id, self._settings.instance_states)
        except Exception as e:  # noqa: BLE001 - state read failure recorded, VM skipped (fail closed)
            log_processing_error(candidate.id, e)
            _logger.failed(self._resource_group, candidate.id, e, logged=True)
            return Skipped(id=candidate.id, reason="status unavailable")
        if reason is None:
            return candidate
        _logger.skip(reason)
        return Skipped(id=candidate.id, reason=reason)


@register("azure", "vm")
def build_vm_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `resource_group`, `filter_tags`, `exception_tags`, `notags`
    (dicts), `age` (dict), `custom_age_tag_key` and `instance_state` (list, default
    `["running"]`, used by delete only; `crc.py` already maps an empty list to the default)."""
    options = context.options
    base = Base(options.get("resource_group") or "")
    states = options.get("instance_state") if context.operation is Operation.DELETE else None
    settings = VmSettings(
        filter_tags=options.get("filter_tags") or {},
        exception_tags=options.get("exception_tags") or {},
        notags=options.get("notags") or {},
        age=options.get("age") or {},
        custom_age_tag_key=options.get("custom_age_tag_key") or "",
        instance_states=DEFAULT_INSTANCE_STATES if states is None else states,
    )
    return VmCleaner(
        base.get_compute_client, base.get_network_client, base.resource_group, settings
    )


__all__ = [
    "DEFAULT_INSTANCE_STATES",
    "Clock",
    "TagSpec",
    "Tally",
    "VmCleaner",
    "VmSettings",
    "build_vm_cleaner",
    "log_summary",
]
