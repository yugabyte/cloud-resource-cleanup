# Copyright (c) Yugabyte, Inc.

"""OCI compute instance cleaner.

`VmCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the instances of
every region and decides on them, `apply()` terminates (delete) or stops them. `VM`
(`crc.oci.vm.adapter`) is the legacy class `crc.py` uses.
"""

import datetime
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

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
from crc.oci import _logger
from crc.oci._base import Base
from crc.oci.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.oci.disk.api import RegionLister, regions_of
from crc.oci.vm.client import ApiFactory, ComputeApi, SdkInstance, compute_api_for

DEFAULT_INSTANCE_STATES = ("RUNNING",)

TagSpec = Mapping[str, Sequence[str]]
SKIP_KEYS = {
    "state is not allowed": "state",
    "matches exception_tags or notags": "exception_tag",
    "does not match filter_tags": "filter_mismatch",
    "not older than age": "too_young",
}
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


def _log_summary(
    operation: Operation,
    deleted: Sequence[str],
    stopped: Sequence[str],
    *,
    dry_run: bool,
) -> None:
    """The closing lines of main. Both name lists are passed because main's "nothing to do"
    warning looks at the two of them, whichever operation runs."""
    if not deleted and not stopped:
        logging.warning(f"No OCI instances to {operation}.")

    if operation is Operation.DELETE:
        if not dry_run:
            logging.info(f"number of OCI instances deleted: {len(deleted)}")
            logging.warning(f"List of OCI instances deleted: {list(deleted)}")
        else:
            logging.warning(
                f"List of OCI instances (Total: {len(deleted)}) "
                f"which will be deleted: {list(deleted)}"
            )

    if operation is Operation.STOP:
        if not dry_run:
            logging.info(f"number of OCI instances stopped: {len(stopped)}")
            logging.warning(f"List of OCI instances stopped: {list(stopped)}")
        else:
            logging.warning(
                f"List of OCI instances (Total: {len(stopped)}) "
                f"which will be stopped: {list(stopped)}"
            )


def _log_for(operation: Operation, names: Sequence[str], *, dry_run: bool) -> None:
    """`_log_summary` for a run that only ever fills one of the two lists."""
    other: list[str] = []
    if operation is Operation.DELETE:
        _log_summary(operation, names, other, dry_run=dry_run)
    else:
        _log_summary(operation, other, names, dry_run=dry_run)


class VmCleaner:
    """Terminates (delete) or stops instances that are in an allowed lifecycle state, match
    the filter tags (all keys), are not excluded by exception tags or notags, and are old
    enough. Applies to every region the injected lister returns."""

    cloud = "oci"
    kind = "vm"

    def __init__(
        self,
        api_factory: ApiFactory,
        list_regions: RegionLister,
        settings: VmSettings,
    ) -> None:
        self._api_factory = api_factory
        self._list_regions = list_regions
        self._settings = settings

    def supports(self, operation: Operation) -> bool:
        return operation in (Operation.DELETE, Operation.STOP)

    def plan(self, context: RunContext) -> Plan:
        """List every region and decide on each instance. Makes no mutating call."""
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        for region in self._list_regions():
            started = _logger.region_started()
            try:
                api = self._api_factory(region)
                instances = api.list_instances()
            except CONNECTIVITY_ERRORS as e:
                log_skipped_region(region, f"list_instances vm {context.operation}", e)
                _logger.region_skipped(region, e)
                continue
            selected = 0
            for instance in instances:
                decision = self.decide(instance, region, lambda: context.now)
                if isinstance(decision, Candidate):
                    candidates.append(decision)
                    selected += 1
                else:
                    skipped.append(decision)
            _logger.region_done(region, len(instances), selected, started)
        if context.dry_run:
            _log_for(context.operation, [c.name for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Act on the planned instances region by region (nothing in a dry run). A failed call
        is logged and left out of the result, as on main."""
        if context.dry_run:
            for candidate in plan.candidates:
                self._record(candidate, context.operation, dry_run=True)
            return Result(succeeded=tuple(c.name for c in plan.candidates))
        by_region: dict[str, list[Candidate]] = {}
        for candidate in plan.candidates:
            by_region.setdefault(candidate.region, []).append(candidate)
        done: list[str] = []
        failed: list[Failure] = []
        for region, items in by_region.items():
            api = self._api_factory(region)
            for candidate in items:
                error = self.act(api, candidate, context.operation, done, dry_run=False)
                if error is not None:
                    failed.append(error)
        _log_for(context.operation, done, dry_run=False)
        return Result(succeeded=tuple(done), failed=tuple(failed))

    def decide(self, instance: SdkInstance, region: str, clock: Clock) -> Candidate | Skipped:
        """State, tag and age checks, in the order of main. `clock` is only read by the age
        check, as `datetime.now()` is on main."""
        reason = self._skip_reason(instance, clock)
        if reason is not None:
            _logger.record_skip(SKIP_KEYS[reason])
            return Skipped(id=instance.id, reason=reason, name=instance.display_name)
        return Candidate(
            id=instance.id,
            name=instance.display_name,
            region=region,
            state=instance.lifecycle_state,
            launch_time=instance.time_created,
            tags=policy.normalize_tags(instance.freeform_tags),
        )

    def act(
        self,
        api: ComputeApi,
        candidate: Candidate,
        operation: Operation,
        done: list[str],
        *,
        dry_run: bool,
    ) -> Failure | None:
        """Terminate or stop one planned instance and record its name in `done` (also in a dry
        run, where nothing is called). Any error is logged and returned, not raised."""
        try:
            if not dry_run:
                if operation is Operation.DELETE:
                    api.terminate_instance(candidate.id)
                    logging.info(f"Deleting instance {candidate.name}")
                else:
                    api.stop_instance(candidate.id)
                    logging.info(f"Stopping instance {candidate.name}")
            done.append(candidate.name)
        except Exception as e:
            logging.error(f"Error occurred while {operation} instance {candidate.name}: {e}")
            _logger.record_failure(candidate.region, candidate.id, e)
            return Failure(id=candidate.name, error=str(e))
        self._record(candidate, operation, dry_run=dry_run)
        return None

    @staticmethod
    def _record(candidate: Candidate, operation: Operation, *, dry_run: bool) -> None:
        _logger.record_action(
            operation,
            dry_run=dry_run,
            region=candidate.region,
            resource_id=candidate.id,
            name=candidate.name,
        )

    def _skip_reason(self, instance: SdkInstance, clock: Clock) -> str | None:
        settings = self._settings
        if not policy.state_in(instance.lifecycle_state, settings.instance_states):
            return "state is not allowed"
        tags = policy.normalize_tags(instance.freeform_tags)
        if policy.should_skip_by_tags(tags, settings.exception_tags, settings.notags):
            return "matches exception_tags or notags"
        if not policy.filter_tags_all_keys(tags, settings.filter_tags):
            return "does not match filter_tags"
        if not self._is_old(instance, clock):
            return "not older than age"
        return None

    def _is_old(self, instance: SdkInstance, clock: Clock) -> bool:
        """Older than `age`, or than the instance's own custom age tag (capped by MAX_AGE)."""
        created_time = instance.time_created
        now = clock().astimezone(created_time.tzinfo)
        tags = instance.freeform_tags or {}
        logging.debug(tags)
        retention_age = core_age.get_retention_age(
            cast(dict[str, str], tags), self._settings.custom_age_tag_key
        )
        if retention_age:
            logging.info(f"Updating age for instance: {instance.display_name}")
        return core_age.is_old(
            policy.effective_age(retention_age, self._settings.age), now, created_time
        )


@register("oci", "vm")
def build_vm_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `filter_tags`, `exception_tags`, `notags` (dicts), `age`
    (dict), `custom_age_tag_key` and `instance_state` (list, default `["RUNNING"]`)."""
    options = context.options
    base = Base()
    states = options.get("instance_state") if context.operation is Operation.DELETE else None
    settings = VmSettings(
        filter_tags=options.get("filter_tags") or {},
        exception_tags=options.get("exception_tags") or {},
        notags=options.get("notags") or {},
        age=options.get("age") or {},
        custom_age_tag_key=options.get("custom_age_tag_key") or "",
        instance_states=DEFAULT_INSTANCE_STATES if states is None else states,
    )
    return VmCleaner(compute_api_for(base), regions_of(base), settings)
