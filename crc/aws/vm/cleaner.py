# Copyright (c) Yugabyte, Inc.

"""AWS EC2 VM cleaner.

`VmCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the instances
of every region (`describe_instances`, `describe_network_interfaces`) and filters them,
`apply()` calls `terminate_instances` or `stop_instances`. It is registered as `aws` / `vm`.
"""

import dataclasses
import datetime
import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any

from crc.aws.connectivity import CONNECTIVITY_ERRORS
from crc.aws.vm.client import VmClient
from crc.aws.vm.judge import Decision, InstanceJudge
from crc.aws.vm.reporting import (
    ACTED,
    dry_run_names,
    log_summary,
    note_dry_run,
    note_region,
    skip_region,
    summarise,
)
from crc.aws.vm.settings import (
    DEFAULT_REGION,
    DEFAULT_STATE,
    SERVICE_NAME,
    ClientFactory,
    Filters,
    RegionLister,
    VmSettings,
    all_regions,
    boto3_client,
    settings_from_options,
)
from crc.core import age as core_age
from crc.core import logger
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

__all__ = [
    "DEFAULT_REGION",
    "DEFAULT_STATE",
    "SERVICE_NAME",
    "ClientFactory",
    "Decision",
    "Filters",
    "RegionLister",
    "VmCleaner",
    "VmSettings",
    "all_regions",
    "boto3_client",
    "build_vm_cleaner",
    "dry_run_names",
    "log_summary",
    "note_dry_run",
    "note_region",
    "skip_region",
]

_ERROR_TEXT = {
    Operation.DELETE: "Error occured while delete instance",
    Operation.STOP: "Error occurred while stop instance",
}
_DONE_TEXT = {Operation.DELETE: "Instance: {} deleted.", Operation.STOP: "Instance {} stopped."}


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
        self._judge = InstanceJudge(settings)
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
        logging.info("Filters created: %s", filters)
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
            if context.dry_run:
                note_dry_run(operation, found)
            self._clients[region] = client
            candidates.extend(found)
            skipped.extend(kept)
        if context.dry_run:
            names = dry_run_names(operation, candidates)
            summarise(operation, True, names)
            candidates = [
                dataclasses.replace(c, id=name) for c, name in zip(candidates, names, strict=True)
            ]
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
        summarise(operation, False, done)
        return Result(succeeded=tuple(done), failed=tuple(failed))

    def filter_instances(
        self, client: VmClient, details: Mapping[str, Any], region: str, now: datetime.datetime
    ) -> tuple[list[Candidate], list[Skipped]]:
        """Decide every instance of a `describe_instances` response, in order."""
        return self._judge.filter_instances(client, details, region, now)

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
                    ACTED[operation, False], candidate.region, candidate.id, candidate.name
                )
            except Exception as e:  # noqa: BLE001 - per-resource isolation, failure logged and recorded, resource kept
                logging.error("%s %s: %s", _ERROR_TEXT[operation], candidate.id, e)
                logger.current().fail(candidate.region, candidate.id, e, logged=True)
                failures.append(Failure(id=candidate.id, error=str(e)))
        for name in names:
            logging.info(_DONE_TEXT[operation].format(name))
        return names, failures

    def should_skip(self, tags: Sequence[Mapping[str, str]]) -> bool:
        """Exception tags (any key, checked tag by tag) or notags (all keys) skip an instance.
        A tag without `Key` or `Value` raises KeyError."""
        return self._judge.should_skip(tags)

    @staticmethod
    def _call(client: VmClient, operation: Operation, instance_id: str) -> None:
        if operation is Operation.DELETE:
            client.terminate_instances(InstanceIds=[instance_id])
        else:
            client.stop_instances(InstanceIds=[instance_id])


@register("aws", "vm")
def build_vm_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `filter_tags`, `exception_tags`, `notags` (dicts of key to
    values), `age` (days/hours dict), `custom_age_tag_key` and `instance_state` (default
    `["running"]`). A real run (not a dry run) needs a positive `age` (BUG-213, SAF-011): without
    one it raises `ValueError` before any cloud call; a custom age tag still replaces `age` per
    instance."""
    options = context.options
    real = not context.dry_run
    if real:
        core_age.require_positive_age(options.get("age"), "--age (AWS VM)")
    return VmCleaner(boto3_client, all_regions, settings_from_options(options, require_age=real))
