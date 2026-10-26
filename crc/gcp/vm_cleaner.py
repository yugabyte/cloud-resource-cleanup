# Copyright (c) Yugabyte, Inc.

"""GCP VM cleaner.

`VmCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the zones of the
project and their instances and filters them, `apply()` deletes or stops the planned instances.
"""

import logging
import time
from collections.abc import Iterator, Mapping, Sequence

from crc.core import age as core_age
from crc.core import logger, policy
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext
from crc.core.registry import register
from crc.gcp._common import Clock, is_old_enough
from crc.gcp._scan import ZoneScan, log_action
from crc.gcp.vm_client import ComputeVmClient, GcpVmClient, InstanceView
from crc.gcp.vm_settings import _DEFAULT_STATE, VmSettings, _log_summary


class VmCleaner:
    """Deletes or stops instances in one of the `instance_state` statuses (default `RUNNING`)
    that are at least `age` (or the custom age label) old, carry every `filter_labels` key
    (empty matches all) and are not excluded by `exception_labels` (any key) or `notags` (all
    keys). Applies to every zone of the project."""

    cloud = "gcp"
    kind = "vm"

    def __init__(self, client: GcpVmClient, settings: VmSettings) -> None:
        self._client = client
        self._settings = settings

    def supports(self, operation: Operation) -> bool:
        return operation in (Operation.DELETE, Operation.STOP)

    def zones(self) -> Sequence[Mapping[str, str]]:
        """Zones of the project (read-only call)."""
        return self._client.list_zones(self._settings.project_id)

    def plan(self, context: RunContext) -> Plan:
        """List every zone and filter. Makes no mutating call."""
        candidates: list[Candidate] = []
        for zone_obj in self.zones():
            zone = zone_obj["name"]
            candidates.extend(self.plan_zone(zone, lambda: context.now))
        if context.dry_run:
            names = [candidate.id for candidate in candidates]
            for candidate in candidates:
                log_action(context.operation.value, candidate, dry_run=True)
            self._summary(context.operation, names, dry_run=True)
        return Plan(candidates=tuple(candidates))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete or stop the planned instances, one by one. A failed call is logged and
        skipped."""
        recorded: list[str] = []
        for candidate in plan.candidates:
            self.apply_instance(candidate, context.operation, recorded, dry_run=context.dry_run)
        self._summary(context.operation, recorded, dry_run=context.dry_run)
        return Result(succeeded=tuple(recorded))

    @staticmethod
    def _summary(operation: Operation, names: Sequence[str], *, dry_run: bool) -> None:
        if operation is Operation.DELETE:
            _log_summary(operation, names, [], dry_run=dry_run)
        else:
            _log_summary(operation, [], names, dry_run=dry_run)

    def plan_zone(self, zone: str, clock: Clock) -> Iterator[Candidate]:
        """List one zone lazily and yield the instances to act on, in the order of `main`:
        status, exception labels and notags, filter labels, age; then log the REGION line.
        A listing error (also while iterating) is logged and recorded and ends the zone (no
        REGION line); an error while evaluating one instance is logged and recorded and the
        instance is kept (BUG-186)."""
        scan = ZoneScan(zone)
        try:
            instances = iter(self._client.list_instances(self._settings.project_id, zone))
        except Exception as e:  # noqa: BLE001 - BUG-186 listing failure keeps every instance of the zone
            self._listing_failed(zone, e)
            return
        while True:
            try:
                instance = next(instances)
            except StopIteration:
                break
            except Exception as e:  # noqa: BLE001 - BUG-186 listing failure keeps every instance of the zone
                self._listing_failed(zone, e)
                return
            scan.scanned += 1
            candidate = self._evaluate(instance, zone, clock)
            if candidate is not None:
                scan.selected += 1
                yield candidate
        scan.finish()

    @staticmethod
    def _listing_failed(zone: str, error: Exception) -> None:
        logging.error("Zone %s: failed listing GCP instances: %s", zone, error)
        logger.current().region_skipped(zone, error)
        logger.current().fail(zone, "-", error, logged=True)

    def _evaluate(self, instance: InstanceView, zone: str, clock: Clock) -> Candidate | None:
        try:
            reason = self._reject(instance, clock)
            if reason is not None:
                logger.current().skip(reason)
                return None
            return Candidate(
                id=instance.name,
                name=instance.name,
                region=zone,
                state=instance.status,
                tags=policy.normalize_tags(instance.labels),
            )
        except Exception as e:  # noqa: BLE001 - per-instance isolation, failure logged and recorded, instance kept
            name = getattr(instance, "name", None) or "?"
            logging.error("Error occurred while evaluating instance %s: %s", name, e)
            logger.current().fail(zone, name, e, logged=True)
            return None

    def _reject(self, instance: InstanceView, clock: Clock) -> str | None:
        if not policy.state_in(instance.status, self._settings.instance_state):
            return "state"
        if self._should_skip(instance):
            return "exception_tag"
        if not self._has_matching_filter_label(instance):
            return "filter_mismatch"
        return None if self._is_old(instance, clock) else "too_young"

    def apply_instance(
        self, candidate: Candidate, operation: Operation, recorded: list[str], *, dry_run: bool
    ) -> bool:
        """Delete or stop one instance (nothing in a dry run), wait for the zone operation and
        record it. Any error, or an operation that fails or does not finish in time, is logged
        and the instance is not recorded; returns whether it was recorded (BUG-185)."""
        try:
            if not dry_run:
                self._act(candidate, operation)
            recorded.append(candidate.id)
        except Exception as e:  # noqa: BLE001 - per-resource isolation, failure logged and recorded, resource kept
            logging.error(
                "Error occurred while %s instance %s: %s", operation.value, candidate.id, e
            )
            logger.current().fail(candidate.region, candidate.id, e, logged=True)
            return False
        log_action(operation.value, candidate, dry_run=dry_run)
        return True

    def _act(self, candidate: Candidate, operation: Operation) -> None:
        project_id = self._settings.project_id
        if operation is Operation.DELETE:
            result = self._client.delete_instance(project_id, candidate.region, candidate.id)
            logging.info("Deleting instance %s", candidate.id)
        else:
            result = self._client.stop_instance(project_id, candidate.region, candidate.id)
            logging.info("Stopping instance %s", candidate.id)
        self._wait_operation(candidate.region, result)

    def _wait_operation(self, zone: str, result: object) -> None:
        """Poll the zone operation until it is DONE; raise when it has no name, ends with an
        error or is not done within `wait_sec`."""
        name = result.get("name") if isinstance(result, Mapping) else None
        if not name:
            raise RuntimeError("the response has no zone operation name to wait for")
        settings = self._settings
        deadline = time.monotonic() + settings.wait_sec
        while time.monotonic() < deadline:
            state = self._client.get_zone_operation(settings.project_id, zone, name)
            status = state.get("status")
            if status == "DONE":
                if state.get("error"):
                    raise RuntimeError(f"zone operation {name} failed: {state['error']}")
                return
            logging.debug("Waiting for GCP zone operation %s/%s (status=%s)", zone, name, status)
            time.sleep(settings.poll_sec)
        raise RuntimeError(f"zone operation {name} not done after {settings.wait_sec}s")

    def _should_skip(self, instance: InstanceView) -> bool:
        settings = self._settings
        exception_labels = settings.exception_labels or {}
        notags = settings.notags or {}
        if not exception_labels and not notags:
            return False
        return policy.should_skip_by_tags(
            policy.normalize_tags(instance.labels), exception_labels, notags
        )

    def _has_matching_filter_label(self, instance: InstanceView) -> bool:
        if not self._settings.filter_labels:
            return True
        tags = policy.normalize_tags(instance.labels)
        return policy.filter_tags_all_keys(tags, self._settings.filter_labels)

    def _is_old(self, instance: InstanceView, clock: Clock) -> bool:
        settings = self._settings
        return is_old_enough(
            instance,
            instance.creation_timestamp,
            clock,
            settings.custom_age_tag_key,
            settings.age,
            "instance",
        )


@register("gcp", "vm")
def build_vm_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `project_id`, `filter_tags` (labels), `exception_tags`
    (labels), `notags`, `age`, `custom_age_tag_key` and `instance_state`."""
    options = context.options
    project_id = options.get("project_id")
    if not project_id:
        raise ValueError("project_id is required for GCP VM cleanup")
    age = options.get("age") or {}
    if not context.dry_run:
        age = core_age.require_positive_age(age, "age")
    settings = VmSettings(
        project_id=project_id,
        filter_labels=options.get("filter_tags"),
        exception_labels=options.get("exception_tags"),
        notags=options.get("notags"),
        age=age,
        custom_age_tag_key=options.get("custom_age_tag_key") or "",
        instance_state=_DEFAULT_STATE
        if context.operation is Operation.STOP or options.get("instance_state") is None
        else options["instance_state"],
    )
    return VmCleaner(ComputeVmClient(), settings)
