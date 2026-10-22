# Copyright (c) Yugabyte, Inc.

"""GCP VM cleaner.

`VmCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the zones of the
project and their instances and filters them, `apply()` deletes or stops the planned instances.
`VM` is the legacy class `crc.py` uses; it keeps its constructor, attributes and result shapes
and runs the cleaner instance by instance, in the exact call order of the original implementation.
"""

import datetime
import logging
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol, cast

from google.cloud import compute_v1
from googleapiclient import discovery

from crc.core import age as core_age
from crc.core import logger, policy
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext
from crc.core.registry import register
from crc.gcp._scan import ZoneScan, log_action
from crc.service import Service

_SERVICE_NAME = "compute"
_SERVICE_VERSION = "v1"
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"
_DEFAULT_STATE = ("RUNNING",)

Clock = Callable[[], datetime.datetime]


class InstanceView(Protocol):
    """The four fields of a GCP instance this cleaner reads."""

    @property
    def status(self) -> str:
        """Instance status, for example `RUNNING`."""
        ...

    @property
    def name(self) -> str:
        """Name of the instance."""
        ...

    @property
    def labels(self) -> Mapping[str, str]:
        """Labels of the instance; empty when unlabelled."""
        ...

    @property
    def creation_timestamp(self) -> str:
        """RFC 3339 creation time."""
        ...


class GcpVmClient(Protocol):
    """The Compute Engine calls this cleaner uses."""

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        """The `items` of the zones listing (one page, as dictionaries with a `name`)."""
        ...

    def list_instances(self, project_id: str, zone: str) -> Iterable[InstanceView]:
        """Instances of one zone."""
        ...

    def delete_instance(self, project_id: str, zone: str, instance: str) -> object:
        """Delete one instance of a zone."""
        ...

    def stop_instance(self, project_id: str, zone: str, instance: str) -> object:
        """Stop one instance of a zone."""
        ...


class ComputeVmClient:
    """`GcpVmClient` on google-api-python-client (zones, deletes, stops) and
    google-cloud-compute (instance listing). Like `main`, the discovery service is closed as
    soon as the zones were listed and the instances client was built; deletes and stops still
    use that service object afterwards."""

    def __init__(self) -> None:
        self._service: Any = None
        self._instances: compute_v1.InstancesClient | None = None

    def _compute(self) -> Any:
        if self._service is None:
            self._service = discovery.build(_SERVICE_NAME, _SERVICE_VERSION)
        return self._service

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        with discovery.build(_SERVICE_NAME, _SERVICE_VERSION) as service:
            self._service = service
            request = service.zones().list(project=project_id)
            zones: Sequence[Mapping[str, str]] = request.execute()["items"]
            self._instances = compute_v1.InstancesClient()
        return zones

    def list_instances(self, project_id: str, zone: str) -> Iterable[InstanceView]:
        if self._instances is None:
            self._instances = compute_v1.InstancesClient()
        instances: Iterable[InstanceView] = self._instances.list(project=project_id, zone=zone)
        return instances

    def delete_instance(self, project_id: str, zone: str, instance: str) -> object:
        service = self._compute()
        return (
            service.instances().delete(project=project_id, zone=zone, instance=instance).execute()
        )

    def stop_instance(self, project_id: str, zone: str, instance: str) -> object:
        service = self._compute()
        return service.instances().stop(project=project_id, zone=zone, instance=instance).execute()


@dataclass(frozen=True, slots=True)
class VmSettings:
    """Filters and options of one run, as `VM` receives them."""

    project_id: str
    filter_labels: Mapping[str, Sequence[str]] | None
    exception_labels: Mapping[str, Sequence[str]] | None
    notags: Mapping[str, Sequence[str]] | None
    age: core_age.AgeSpec | int
    custom_age_tag_key: str
    instance_state: Sequence[str] = _DEFAULT_STATE


def _log_summary(
    operation: Operation, deleted: Sequence[str], stopped: Sequence[str], *, dry_run: bool
) -> None:
    """The closing log lines of `main`; the empty check looks at both lists."""
    if not deleted and not stopped:
        logging.warning(f"No GCP instances to {operation.value}.")
    past = "deleted" if operation is Operation.DELETE else "stopped"
    names = deleted if operation is Operation.DELETE else stopped
    if not dry_run:
        logging.info(f"number of GCP instances {past}: {len(names)}")
        logging.warning(f"List of GCP instances {past}: {names}")
    else:
        logging.warning(
            f"List of GCP instances (Total: {len(names)}) which will be {past}: {names}"
        )


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
        status, exception labels and notags, filter labels, age; then log the REGION line."""
        scan = ZoneScan(zone)
        for instance in self._client.list_instances(self._settings.project_id, zone):
            scan.scanned += 1
            reason = self._reject(instance, clock)
            if reason is not None:
                logger.current().skip(reason)
                continue
            scan.selected += 1
            yield Candidate(
                id=instance.name,
                name=instance.name,
                region=zone,
                state=instance.status,
                tags=policy.normalize_tags(instance.labels),
            )
        scan.finish()

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
        """Delete or stop one instance (nothing in a dry run) and record it. Any error is
        logged and the instance is not recorded; returns whether it was recorded."""
        project_id = self._settings.project_id
        try:
            if not dry_run:
                if operation is Operation.DELETE:
                    self._client.delete_instance(project_id, candidate.region, candidate.id)
                    logging.info(f"Deleting instance {candidate.id}")
                else:
                    self._client.stop_instance(project_id, candidate.region, candidate.id)
                    logging.info(f"Stopping instance {candidate.id}")
            recorded.append(candidate.id)
        except Exception as e:
            logging.error(f"Error occurred while {operation.value} instance {candidate.id}: {e}")
            logger.current().fail(candidate.region, candidate.id, e)
            return False
        log_action(operation.value, candidate, dry_run=dry_run)
        return True

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
        created = datetime.datetime.strptime(instance.creation_timestamp, _TIME_FORMAT)
        now = clock().astimezone(created.tzinfo)
        logging.debug(instance.labels)
        retention_age = core_age.get_retention_age(
            cast(dict[str, str], instance.labels), settings.custom_age_tag_key
        )
        if retention_age:
            logging.info(f"Updating age for instance: {instance.name}")
        # An int is a number of days, which `is_old` accepts.
        default_age = cast(core_age.AgeSpec, settings.age)
        return core_age.is_old(policy.effective_age(retention_age, default_age), now, created)


@register("gcp", "vm")
def build_vm_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `project_id`, `filter_tags` (labels), `exception_tags`
    (labels), `notags`, `age`, `custom_age_tag_key` and `instance_state`."""
    options = context.options
    project_id = options.get("project_id")
    if not project_id:
        raise ValueError("project_id is required for GCP VM cleanup")
    settings = VmSettings(
        project_id=project_id,
        filter_labels=options.get("filter_tags"),
        exception_labels=options.get("exception_tags"),
        notags=options.get("notags"),
        age=options.get("age") or {},
        custom_age_tag_key=options.get("custom_age_tag_key") or "",
        instance_state=_DEFAULT_STATE
        if context.operation is Operation.STOP or options.get("instance_state") is None
        else options["instance_state"],
    )
    return VmCleaner(ComputeVmClient(), settings)


class VM(Service):
    """Legacy entry point used by `crc.py`: deletes (`delete`) or stops (`stop`) GCP instances
    (see `VmCleaner`); in a dry run it only lists them.

    Attributes: `service_name`, `service_version`, `default_instance_state`, `time_format`,
    `instance_names_to_delete`, `instance_names_to_stop` (lists of names, accumulated over
    calls) and the constructor arguments. `get_deleted`, `delete_count`, `get_stopped` and
    `stopped_count` read the results.
    """

    service_name = _SERVICE_NAME
    service_version = _SERVICE_VERSION
    default_instance_state: ClassVar[list[str]] = ["RUNNING"]
    time_format = _TIME_FORMAT

    def __init__(  # noqa: PLR0913, PLR0917 - the seven parameters are the public signature
        self,
        dry_run: bool,
        project_id: str,
        filter_labels: dict[str, list[str]],
        exception_labels: dict[str, list[str]],
        age: int,
        custom_age_tag_key: str,
        notags: dict[str, list[str]],
        client: GcpVmClient | None = None,
        clock: Clock = datetime.datetime.now,
    ) -> None:
        super().__init__()
        self.instance_names_to_delete: list[str] = []
        self.instance_names_to_stop: list[str] = []
        self.dry_run = dry_run
        self.project_id = project_id
        self.filter_labels = filter_labels
        self.exception_labels = exception_labels
        self.age = age
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags
        self._client: GcpVmClient = client or ComputeVmClient()
        self._clock = clock

    @property
    def get_deleted(self) -> list[str]:
        """Instances deleted (listed in a dry run)."""
        return self.instance_names_to_delete

    @property
    def delete_count(self) -> int:
        """Number of entries of `instance_names_to_delete`; logs the count."""
        count = len(self.instance_names_to_delete)
        logging.info(f"count of items in instance_names_to_delete: {count}")
        return count

    @property
    def get_stopped(self) -> list[str]:
        """Instances stopped (listed in a dry run)."""
        return self.instance_names_to_stop

    @property
    def stopped_count(self) -> int:
        """Number of entries of `instance_names_to_stop`; logs the count."""
        count = len(self.instance_names_to_stop)
        logging.info(f"count of items in instance_names_to_stop: {count}")
        return count

    def _perform_operation(self, operation: Operation, instance_state: Sequence[str]) -> None:
        settings = VmSettings(
            project_id=self.project_id,
            filter_labels=self.filter_labels,
            exception_labels=self.exception_labels,
            notags=self.notags,
            age=self.age,
            custom_age_tag_key=self.custom_age_tag_key,
            instance_state=instance_state,
        )
        cleaner = VmCleaner(self._client, settings)
        recorded = (
            self.instance_names_to_delete
            if operation is Operation.DELETE
            else self.instance_names_to_stop
        )
        for zone_obj in cleaner.zones():
            zone = zone_obj["name"]
            for candidate in cleaner.plan_zone(zone, self._clock):
                cleaner.apply_instance(candidate, operation, recorded, dry_run=self.dry_run)
        _log_summary(
            operation,
            self.instance_names_to_delete,
            self.instance_names_to_stop,
            dry_run=self.dry_run,
        )

    def delete(self, instance_state: list[str] = default_instance_state) -> None:
        """Delete (or, in a dry run, only list) the matching instances of every zone,
        instance by instance."""
        self._perform_operation(Operation.DELETE, instance_state)

    def stop(self) -> None:
        """Stop (or, in a dry run, only list) the matching instances of every zone."""
        self._perform_operation(Operation.STOP, self.default_instance_state)
