# Copyright (c) Yugabyte, Inc.

"""OCI compute instance cleaner.

`VmCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the instances of
every region and decides on them, `apply()` terminates (delete) or stops them.
"""

import dataclasses
import logging
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
from crc.oci._common import group_by_region
from crc.oci.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.oci.disk.api import RegionLister, regions_of
from crc.oci.vm.client import ApiFactory, ComputeApi, SdkInstance, compute_api_for
from crc.oci.vm.settings import (
    DEFAULT_INSTANCE_STATES,
    EVALUATION_FAILED,
    SKIP_KEYS,
    Clock,
    TagSpec,
    VmSettings,
    log_for,
)

__all__ = [
    "DEFAULT_INSTANCE_STATES",
    "Clock",
    "TagSpec",
    "VmCleaner",
    "VmSettings",
    "build_vm_cleaner",
]


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
        *,
        engine: bool = False,
    ) -> None:
        """`engine` is True for the cleaner `crc.core.engine` builds: a dry-run `plan()` then
        logs the ACTION lines per region and names its candidates by display name (the engine
        never calls `apply()` in a dry run)."""
        self._engine = engine
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
                    if context.dry_run and self._engine:
                        self._record(decision, context.operation, dry_run=True)
                else:
                    skipped.append(decision)
            _logger.region_done(region, len(instances), selected, started)
        if context.dry_run:
            log_for(context.operation, [c.name for c in candidates], dry_run=True)
            if self._engine:
                candidates = [dataclasses.replace(c, id=c.name) for c in candidates]
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Act on the planned instances region by region (nothing in a dry run). A failed call
        is logged and left out of the result, as on main."""
        if context.dry_run:
            if not self._engine:
                for candidate in plan.candidates:
                    self._record(candidate, context.operation, dry_run=True)
            return Result(succeeded=tuple(c.name for c in plan.candidates))
        done: list[str] = []
        failed: list[Failure] = []
        for region, items in group_by_region(plan.candidates).items():
            api = self._api_factory(region)
            for candidate in items:
                error = self.act(api, candidate, context.operation, done, dry_run=False)
                if error is not None:
                    failed.append(error)
        log_for(context.operation, done, dry_run=False)
        return Result(succeeded=tuple(done), failed=tuple(failed))

    def decide(self, instance: SdkInstance, region: str, clock: Clock) -> Candidate | Skipped:
        """State, tag and age checks, in the order of main. `clock` is only read by the age
        check, as `datetime.now()` is on main. An unexpected error while evaluating one instance
        (BUG-162: missing or naive creation time, malformed MAX_AGE) is logged and recorded as a
        failure and the instance is kept; the caller carries on with the next one."""
        try:
            reason = self._skip_reason(instance, clock)
        except Exception as e:  # noqa: BLE001 - BUG-162 per-instance isolation, failure recorded, instance kept
            logging.error(
                "Error occurred while evaluating instance %s: %s", instance.display_name, e
            )
            _logger.record_failure(region, instance.id, e, logged=True)
            return Skipped(id=instance.id, reason=EVALUATION_FAILED, name=instance.display_name)
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
                    logging.info("Deleting instance %s", candidate.name)
                else:
                    api.stop_instance(candidate.id)
                    logging.info("Stopping instance %s", candidate.name)
            done.append(candidate.name)
        except Exception as e:  # noqa: BLE001 - per-resource isolation, failure logged and recorded, resource kept
            logging.error("Error occurred while %s instance %s: %s", operation, candidate.name, e)
            _logger.record_failure(candidate.region, candidate.id, e, logged=True)
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
        if policy.oci_is_managed_or_oke_node(
            instance.freeform_tags,
            getattr(instance, "defined_tags", None),
            getattr(instance, "metadata", None) or {},
        ):
            return "OKE or instance pool member"
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
            logging.info("Updating age for instance: %s", instance.display_name)
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
    return VmCleaner(compute_api_for(base), regions_of(base), settings, engine=True)
