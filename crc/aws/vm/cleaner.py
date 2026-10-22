# Copyright (c) Yugabyte, Inc.

"""AWS EC2 VM cleaner.

`VmCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the instances
of every region (`describe_instances`, `describe_network_interfaces`) and filters them,
`apply()` calls `terminate_instances` or `stop_instances`. It is registered as `aws` / `vm`;
`crc.aws.vm.VM` (`adapter.py`) is the legacy adapter `crc.py` uses.
"""

import datetime
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import boto3

from crc.aws._base import get_all_regions
from crc.aws.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.aws.vm import scan as vm_scan
from crc.aws.vm.client import VmClient
from crc.core import age as core_age
from crc.core import logger, policy
from crc.core.age import AgeSpec
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

SERVICE_NAME = "ec2"
DEFAULT_REGION = "us-west-2"
DEFAULT_STATE: list[str] = ["running"]

ClientFactory = Callable[[str], VmClient]
RegionLister = Callable[[], Sequence[str]]
Decision = Candidate | Skipped
Filters = list[dict[str, Any]]


def boto3_client(region: str) -> VmClient:
    client: VmClient = boto3.client(SERVICE_NAME, region_name=region)
    return client


def all_regions() -> Sequence[str]:
    return get_all_regions(SERVICE_NAME, DEFAULT_REGION)


@dataclass(frozen=True, slots=True)
class VmSettings:
    """Filters and thresholds of one run (the constructor arguments of `VM`)."""

    filter_tags: Any
    exception_tags: Any
    notags: Any
    age: Any
    custom_age_tag_key: Any
    instance_state: Any


_ERROR_TEXT = {
    Operation.DELETE: "Error occured while delete instance",
    Operation.STOP: "Error occurred while stop instance",
}
_DONE_TEXT = {Operation.DELETE: "Instance: {} deleted.", Operation.STOP: "Instance {} stopped."}


_SKIP_REASONS = {
    "no Tags key": "no_tags",
    "exception tag or notags": "exception_tag",
    "younger than age": "too_young",
}
_ACTED = {
    (Operation.DELETE, False): "deleted",
    (Operation.DELETE, True): "would_delete",
    (Operation.STOP, False): "stopped",
    (Operation.STOP, True): "would_stop",
}


def skip_region(region: str, operation: Operation, error: BaseException) -> None:
    log_skipped_region(region, f"ec2 vm {operation.value}", error)
    logger.current().region_skipped(region, error)


def note_region(
    region: str, started: float, found: Sequence[Candidate], kept: Sequence[Skipped]
) -> None:
    """The REGION progress line of a region that was listed and filtered."""
    seconds = time.monotonic() - started
    logger.current().region(region, len(found) + len(kept), len(found), seconds)


def note_dry_run(operation: Operation, candidates: Sequence[Candidate]) -> None:
    """One ACTION would_* line per instance a dry run lists."""
    for candidate in candidates:
        logger.current().action(
            _ACTED[operation, True], candidate.region, candidate.id, candidate.name
        )


def log_summary(
    operation: Operation, dry_run: bool, deleted: Sequence[str], stopped: Sequence[str]
) -> None:
    """The closing warnings of a run, as on main (`deleted` and `stopped` are both checked
    for the empty message, the one of `operation` is listed)."""
    if not deleted and not stopped:
        logging.warning(f"No AWS instances to {operation.value}.")
    names = list(deleted if operation is Operation.DELETE else stopped)
    verb = "deleted" if operation is Operation.DELETE else "stopped"
    if not dry_run:
        logging.warning(f"number of AWS instances {verb}: {len(names)}")
        logging.warning(f"List of AWS instances {verb}: {names}")
    else:
        logging.warning(
            f"List of AWS instances (Total: {len(names)}) which will be {verb}: {names}"
        )


def _summarise(operation: Operation, dry_run: bool, names: Sequence[str]) -> None:
    is_delete = operation is Operation.DELETE
    log_summary(operation, dry_run, names if is_delete else [], [] if is_delete else names)


def dry_run_names(operation: Operation, candidates: Sequence[Candidate]) -> list[str]:
    """What a dry run records: names for delete, but instance ids for stop (as on main)."""
    if operation is Operation.DELETE:
        return [c.name for c in candidates]
    return [c.id for c in candidates]


def _attach_time(client: VmClient, inst: Mapping[str, Any]) -> datetime.datetime:
    nic_id = inst["NetworkInterfaces"][0]["NetworkInterfaceId"]
    details = client.describe_network_interfaces(NetworkInterfaceIds=[nic_id])
    attached: datetime.datetime = details["NetworkInterfaces"][0]["Attachment"]["AttachTime"]
    return attached


def _safe_id(inst: Any) -> str:
    return str(inst.get("InstanceId", "")) if isinstance(inst, Mapping) else ""


class VmCleaner:
    """Terminates or stops running instances that match the tag filters, carry a Name tag, no
    exception tag and are at least as old as the attach time of their first network interface
    (or the custom age tag). Applies to every region the injected lister returns."""

    cloud = "aws"
    kind = "vm"

    def __init__(
        self, client_factory: ClientFactory, list_regions: RegionLister, settings: VmSettings
    ) -> None:
        self._client_factory = client_factory
        self._list_regions = list_regions
        self._settings = settings
        self._clients: dict[str, VmClient] = {}

    def supports(self, operation: Operation) -> bool:
        return operation in (Operation.DELETE, Operation.STOP)

    def build_filters(self) -> Filters:
        """The server side `describe_instances` filters: state plus one `tag:key` per filter."""
        settings = self._settings
        filters: Filters = [{"Name": "instance-state-name", "Values": settings.instance_state}]
        if settings.filter_tags:
            for key, value in settings.filter_tags.items():
                filters.append({"Name": f"tag:{key}", "Values": value})
        logging.info(f"Filters created: {filters}")
        return filters

    def plan(self, context: RunContext) -> Plan:
        """List every region and filter. Makes no mutating call. In a dry run it also logs the
        closing summary, because `apply()` does not run then."""
        filters = self.build_filters()
        operation = context.operation
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        for region in self._list_regions():
            started = time.monotonic()
            try:
                client = self._client_factory(region)
                found, kept = self.filter_instances(
                    client, client.describe_instances(Filters=filters), region, context.now
                )
            except CONNECTIVITY_ERRORS as e:
                skip_region(region, operation, e)
                continue
            note_region(region, started, found, kept)
            self._clients[region] = client
            candidates.extend(found)
            skipped.extend(kept)
        if context.dry_run:
            note_dry_run(operation, candidates)
            _summarise(operation, True, dry_run_names(operation, candidates))
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Terminate or stop the planned instances region by region (nothing in a dry run) and
        log the closing summary. A failed instance is logged and kept out of the result."""
        if context.dry_run:
            return Result()
        operation = context.operation
        by_region: dict[str, list[Candidate]] = {}
        for candidate in plan.candidates:
            by_region.setdefault(candidate.region, []).append(candidate)
        done: list[str] = []
        failed: list[Failure] = []
        for region, items in by_region.items():
            try:
                client = self._clients.get(region) or self._client_factory(region)
            except CONNECTIVITY_ERRORS as e:
                skip_region(region, operation, e)
                continue
            names, failures = self.apply_region(client, items, operation)
            done.extend(names)
            failed.extend(failures)
        _summarise(operation, False, done)
        return Result(succeeded=tuple(done), failed=tuple(failed))

    def filter_instances(
        self, client: VmClient, details: Mapping[str, Any], region: str, now: datetime.datetime
    ) -> tuple[list[Candidate], list[Skipped]]:
        """Decide every instance of a `describe_instances` response, in order."""
        found: list[Candidate] = []
        kept: list[Skipped] = []
        for reservation in details["Reservations"]:
            for inst in reservation["Instances"]:
                decision = self._decide(client, inst, region, now)
                if isinstance(decision, Candidate):
                    found.append(decision)
                else:
                    kept.append(decision)
                    self._note_skipped(decision, region)
        return found, kept

    @staticmethod
    def _note_skipped(decision: Skipped, region: str) -> None:
        """Count a kept instance under its logger reason. Errors were recorded by `_decide`;
        the missing Name tag is logged at ERROR, so it is also a failure."""
        run = logger.current()
        if decision.reason == "no Name tag":
            run.fail(region or None, decision.id or "-", "instance doesn't have Name Tag")
        elif decision.reason in _SKIP_REASONS:
            run.skip(_SKIP_REASONS[decision.reason])

    def apply_region(
        self, client: VmClient, candidates: Sequence[Candidate], operation: Operation
    ) -> tuple[list[str], list[Failure]]:
        """Act on the instances of one region one by one. Any error is logged and returned;
        the names of the instances that worked are logged after the loop, as on main."""
        names: list[str] = []
        failures: list[Failure] = []
        for candidate in candidates:
            try:
                self._call(client, operation, candidate.id)
                names.append(candidate.name)
                logger.current().action(
                    _ACTED[operation, False], candidate.region, candidate.id, candidate.name
                )
            except Exception as e:
                logging.error(f"{_ERROR_TEXT[operation]} {candidate.id}: {e}")
                logger.current().fail(candidate.region, candidate.id, e)
                failures.append(Failure(id=candidate.id, error=str(e)))
        for name in names:
            logging.info(_DONE_TEXT[operation].format(name))
        return names, failures

    def should_skip(self, tags: Sequence[Mapping[str, str]]) -> bool:
        """Exception tags (any key, checked tag by tag) or notags (all keys) skip an instance.
        A tag without `Key` or `Value` raises KeyError."""
        settings = self._settings
        if not settings.exception_tags and not settings.notags:
            logging.warning("Tags and notags not present")
            return False
        all_tags: dict[str, str] = {}
        for tag in tags:
            key = tag["Key"]
            value = tag["Value"]
            if settings.exception_tags and policy.exception_tags_any(
                {key: value}, settings.exception_tags
            ):
                return True
            all_tags[key] = value
        return policy.notags_all(all_tags, settings.notags or {})

    @staticmethod
    def _call(client: VmClient, operation: Operation, instance_id: str) -> None:
        if operation is Operation.DELETE:
            client.terminate_instances(InstanceIds=[instance_id])
        else:
            client.stop_instances(InstanceIds=[instance_id])

    def _decide(
        self, client: VmClient, inst: Mapping[str, Any], region: str, now: datetime.datetime
    ) -> Decision:
        name: str | None = None
        try:
            screened = self._screen(inst)
            if isinstance(screened, Skipped):
                return screened
            name, tags = screened
            return self._judge(client, inst, (name, region), tags, now)
        except Exception as e:
            logging.error(f"Error occurred while processing {name} instance: {e}")
            logger.current().fail(region or None, _safe_id(inst) or "-", e)
            return Skipped(id=_safe_id(inst), reason=f"error: {e}")

    def _screen(self, inst: Mapping[str, Any]) -> Skipped | tuple[str, Any]:
        if "Tags" not in inst:
            return Skipped(id=_safe_id(inst), reason="no Tags key")
        tags = inst["Tags"]
        if self.should_skip(tags):
            return Skipped(id=_safe_id(inst), reason="exception tag or notags")
        name = vm_scan.instance_name(tags)
        if not name:
            logging.error(f"{name} instance doesn't have Name Tag. Skipping it")
            return Skipped(id=_safe_id(inst), reason="no Name tag")
        return name, tags

    def _judge(
        self,
        client: VmClient,
        inst: Mapping[str, Any],
        where: tuple[str, str],
        tags: Any,
        now: datetime.datetime,
    ) -> Decision:
        name, region = where
        instance_id = inst["InstanceId"]
        attached = _attach_time(client, inst)
        logging.debug(tags)
        retention_age: AgeSpec | None = core_age.get_retention_age(
            tags, self._settings.custom_age_tag_key
        )
        if retention_age:
            logging.info(f"Updating age for instance_id: {instance_id}")
        age = policy.effective_age(retention_age, self._settings.age)
        if not core_age.is_old(age, now.astimezone(attached.tzinfo), attached):
            return Skipped(id=instance_id, name=name, reason="younger than age")
        logging.info(
            f"Instance {name} with ID {instance_id} added to list of instances to be cleaned up."
        )
        return Candidate(
            id=instance_id,
            name=name,
            region=region,
            tags=policy.normalize_tags(tags),
            launch_time=attached,
        )


def _optional(options: Mapping[str, Any], key: str) -> Any:
    return options.get(key, {})


@register("aws", "vm")
def build_vm_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `filter_tags`, `exception_tags`, `notags` (dicts of key to
    values), `age` (days/hours dict), `custom_age_tag_key` and `instance_state` (default
    `["running"]`)."""
    options = context.options
    settings = VmSettings(
        filter_tags=_optional(options, "filter_tags"),
        exception_tags=_optional(options, "exception_tags"),
        notags=_optional(options, "notags"),
        age=_optional(options, "age"),
        custom_age_tag_key=options.get("custom_age_tag_key"),
        instance_state=options.get("instance_state", DEFAULT_STATE),
    )
    return VmCleaner(boto3_client, all_regions, settings)
