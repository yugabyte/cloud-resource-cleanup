# Copyright (c) Yugabyte, Inc.

"""Azure VM cleaner.

`VmCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the VMs of the
subscription, applies the tag and age policy and reads each VM's instance view; `apply()` deletes
(and removes the `<vm>-NIC` NIC) or powers off the planned VMs. The legacy class in `adapter.py`
drives the same pieces VM by VM, in the exact call order of the original implementation.
"""

import datetime
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from crc.azu import _logger
from crc.azu._base import Base
from crc.azu.vm.client import ComputeFactory, NetworkFactory, SdkVm
from crc.azu.vm_delete_helpers import begin_delete_vm_and_wait, delete_vm_primary_nic
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

DEFAULT_INSTANCE_STATES = ("running",)
NIC_NAME_SUFFIX = "-NIC"
NIC_LABEL = "NIC"

TagSpec = Mapping[str, Sequence[str]]
Clock = Callable[[], datetime.datetime]


@dataclass(frozen=True, slots=True)
class VmSettings:
    """Filters and thresholds of one run."""

    filter_tags: TagSpec = field(default_factory=dict)
    exception_tags: TagSpec = field(default_factory=dict)
    notags: TagSpec = field(default_factory=dict)
    age: core_age.AgeSpec = field(default_factory=dict)
    custom_age_tag_key: str = ""
    instance_states: Sequence[str] = DEFAULT_INSTANCE_STATES


@dataclass(slots=True)
class Tally:
    """Names recorded so far: VMs deleted, VMs stopped and NICs deleted (also in a dry run)."""

    deleted: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    nics: list[str] = field(default_factory=list)


def _log_deleted(tally: Tally, *, dry_run: bool) -> None:
    if not dry_run:
        logging.warning(f"number of Azure instances deleted: {len(tally.deleted)}")
        logging.warning(f"List of Azure instances deleted: {tally.deleted}")
        logging.warning(f"number of Azure nics deleted: {len(tally.nics)}")
        logging.warning(f"List of Azure nics deleted: {tally.nics}")
    else:
        logging.warning(
            f"List of Azure instances (Total: {len(tally.deleted)}) "
            f"which will be deleted: {tally.deleted}"
        )
        logging.warning(
            f"List of Azure nics (Total: {len(tally.nics)}) which will be deleted: {tally.nics}"
        )


def _log_stopped(tally: Tally, *, dry_run: bool) -> None:
    if not dry_run:
        logging.warning(f"number of Azure instances stopped: {len(tally.stopped)}")
        logging.warning(f"List of Azure instances stopped: {tally.stopped}")
    else:
        logging.warning(
            f"List of Azure instances (Total: {len(tally.stopped)}) "
            f"which will be stopped: {tally.stopped}"
        )


def log_summary(operation: Operation, tally: Tally, *, dry_run: bool) -> None:
    """The closing lines of main. The "nothing to do" warning looks at both name lists,
    whichever operation runs."""
    if not tally.deleted and not tally.stopped:
        logging.warning(f"No Azure instances to {operation}.")
    if operation is Operation.DELETE:
        _log_deleted(tally, dry_run=dry_run)
    if operation is Operation.STOP:
        _log_stopped(tally, dry_run=dry_run)


def _tally_of(operation: Operation, candidates: Iterable[str]) -> Tally:
    names = list(candidates)
    if operation is Operation.DELETE:
        return Tally(deleted=names, nics=[f"{name}{NIC_NAME_SUFFIX}" for name in names])
    return Tally(stopped=names)


def _log_processing_error(name: str, error: Exception) -> None:
    logging.error(f"Error occurred while processing {name} instance: {error}")


class VmCleaner:
    """Deletes (with its `<vm>-NIC`) or powers off VMs that are tagged, match the filter tags
    (all keys), are not excluded by exception tags or notags, are old enough and whose instance
    view status contains an allowed state. Lists the whole subscription; every other call uses
    the configured resource group."""

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
        tally = _tally_of(context.operation, (c.id for c in candidates))
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
            except Exception as e:
                _log_processing_error(candidate.id, e)
                _logger.failed(self._resource_group, candidate.id, e)
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
        """The (lazy) subscription-wide listing."""
        return self._compute().virtual_machines.list_all()

    def decide(self, vm: SdkVm, clock: Clock) -> Candidate | Skipped:
        """Tag and age checks, in the order of main. `clock` is only read by the age check, as
        `datetime.now()` is on main."""
        tags = policy.normalize_tags(vm.tags)
        reason = self._skip_reason(vm, tags, clock)
        if reason is not None:
            _logger.skip(reason)
            return Skipped(id=getattr(vm, "name", ""), reason=reason)
        return Candidate(id=vm.name, name=vm.name, launch_time=vm.time_created, tags=tags)

    def state_allowed(self, vm_name: str, states: Sequence[str]) -> bool:
        """True when an allowed state is a substring of the second instance view status
        (raises when the VM has fewer than two statuses)."""
        view = self._compute().virtual_machines.instance_view(self._resource_group, vm_name)
        return policy.status_contains_any_allowed(view.statuses[1].display_status, states)

    def process(self, vm_name: str, operation: Operation, tally: Tally, *, dry_run: bool) -> bool:
        """Status check and action for one VM that passed `decide()`. Any error is logged and
        swallowed, as on main. Returns True when the VM passed the status check (selected, as
        in `plan()`), even if the action then failed."""
        selected = False
        try:
            selected = self.state_allowed(vm_name, self._settings.instance_states)
            if selected:
                self.act(vm_name, operation, tally, dry_run=dry_run)
            else:
                _logger.skip("status does not contain an allowed state")
        except Exception as e:
            _log_processing_error(vm_name, e)
            _logger.failed(self._resource_group, vm_name, e)
        return selected

    def act(self, vm_name: str, operation: Operation, tally: Tally, *, dry_run: bool) -> None:
        """Delete (with the NIC) or power off one VM and record its name, also in a dry run,
        where nothing is called."""
        if operation is Operation.DELETE:
            self._remove(vm_name, tally, dry_run=dry_run)
        else:
            self._power_off(vm_name, tally, dry_run=dry_run)

    def _remove(self, vm_name: str, tally: Tally, *, dry_run: bool) -> None:
        if not dry_run:
            logging.info("Deleting virtual machine: %s", vm_name)
            begin_delete_vm_and_wait(self._compute(), self._resource_group, vm_name)
        tally.deleted.append(vm_name)
        _logger.acted("would_delete" if dry_run else "deleted", self._resource_group, vm_name)
        delete_vm_primary_nic(self._network(), self._resource_group, vm_name, dry_run, tally.nics)

    def _power_off(self, vm_name: str, tally: Tally, *, dry_run: bool) -> None:
        if not dry_run:
            self._compute().virtual_machines.begin_power_off(self._resource_group, vm_name)
            logging.info("Stopping virtual machine: %s", vm_name)
        tally.stopped.append(vm_name)
        _logger.acted("would_stop" if dry_run else "stopped", self._resource_group, vm_name)

    def _with_status(self, candidate: Candidate) -> Candidate | Skipped:
        try:
            if self.state_allowed(candidate.id, self._settings.instance_states):
                return candidate
        except Exception as e:
            _log_processing_error(candidate.id, e)
            _logger.failed(self._resource_group, candidate.id, e)
            return Skipped(id=candidate.id, reason="status unavailable")
        _logger.skip("status does not contain an allowed state")
        return Skipped(id=candidate.id, reason="status does not contain an allowed state")

    def _skip_reason(self, vm: SdkVm, tags: policy.Tags, clock: Clock) -> str | None:
        settings = self._settings
        if policy.skip_untagged(tags, policy.UntaggedMode.SKIP):
            return "untagged"
        if policy.should_skip_by_tags(tags, settings.exception_tags, settings.notags):
            return "matches exception_tags or notags"
        if not policy.filter_tags_all_keys(tags, settings.filter_tags):
            return "does not match filter_tags"
        if not self._is_old(vm, clock):
            return "not older than age"
        return None

    def _is_old(self, vm: SdkVm, clock: Clock) -> bool:
        """Older than `age`, or than the VM's own custom age tag (capped by MAX_AGE)."""
        created = vm.time_created
        now = clock().astimezone(created.tzinfo)
        logging.debug(vm.tags)
        retention_age = core_age.get_retention_age(
            cast(dict[str, str], vm.tags), self._settings.custom_age_tag_key
        )
        if retention_age:
            logging.info(f"Updating age for vm: {vm.name}")
        return core_age.is_old(
            policy.effective_age(retention_age, self._settings.age), now, created
        )


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
