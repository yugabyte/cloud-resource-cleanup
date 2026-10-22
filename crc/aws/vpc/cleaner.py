# Copyright (c) Yugabyte, Inc.

"""AWS VPC cleaner.

`VpcCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the VPCs of
every region (server side `tag:key` filter) and decides which are protected, `apply()` tears
the VPCs down and deletes them. `VPC` is the legacy class `crc.py` uses; it keeps its
constructor and attributes and drives the cleaner region by region, VPC by VPC, in the exact
call order of the original implementation.
"""

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import boto3

from crc.aws import _regionlog
from crc.aws._base import get_all_regions
from crc.aws.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.aws.vpc.client import Ec2Client, Ec2Resource, Vpc
from crc.aws.vpc.teardown import tear_down
from crc.core import logger, policy
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext, Skipped
from crc.core.registry import register
from crc.service import Service

_SERVICE_NAME = "ec2"
_DEFAULT_REGION = "us-west-2"
_SKIP_CONTEXT = "VPC cleanup"
_DELETE_RETRIES = 5
_PROTECTED = "protected"

TagSpec = Mapping[str, Sequence[str]]


ClientFactory = Callable[[str], Ec2Client]
ResourceFactory = Callable[[str], Ec2Resource]
RegionLister = Callable[[], Sequence[str]]


def _boto3_client(region: str) -> Ec2Client:
    client: Ec2Client = boto3.client(_SERVICE_NAME, region_name=region)
    return client


def _boto3_resource(region: str) -> Ec2Resource:
    resource: Ec2Resource = boto3.resource(_SERVICE_NAME, region_name=region)
    return resource


def _all_regions() -> Sequence[str]:
    return get_all_regions(_SERVICE_NAME, _DEFAULT_REGION)


@dataclass(frozen=True, slots=True)
class VpcRuntime:
    """Injected collaborators of `VPC`: the SDK client and resource factories and the region
    lister (no clock: the module has no age gate)."""

    client_factory: ClientFactory = _boto3_client
    resource_factory: ResourceFactory = _boto3_resource
    list_regions: RegionLister = _all_regions


def _skip_region(region: str, error: BaseException) -> None:
    _regionlog.region_skipped(region, error)
    log_skipped_region(region, _SKIP_CONTEXT, error)


def _log_summary(deleted: Sequence[str], *, dry_run: bool) -> None:
    items = list(deleted)
    if dry_run:
        logging.warning(f"List of AWS VPCs (Total: {len(items)}) which will be deleted: {items}")
    else:
        logging.warning(f"Number of AWS VPCs deleted: {len(items)}")
        logging.warning(f"List of AWS VPCs deleted: {items}")


def _delete_vpc_with_retries(client: Ec2Client, vpc: Vpc, region: str | None = None) -> None:
    run = logger.current()
    last_error: Exception | None = None
    for _ in range(_DELETE_RETRIES):
        try:
            client.delete_vpc(VpcId=vpc.id)
            break
        except Exception as e:
            last_error = e
            logging.error(e)
            logging.error(f"Failed deleting VPC {vpc.id}. Retrying...")
    else:
        logging.error(f"Failed to Delete VPC {vpc.id} after {_DELETE_RETRIES} retries")
        run.fail(region, vpc.id, last_error or "delete_vpc failed")
        return
    run.action("deleted", region, vpc.id)


class VpcCleaner:
    """Deletes the VPCs the server side `tag:key` filter returns, except the protected ones.
    Applies to every region the injected lister returns."""

    cloud = "aws"
    kind = "vpc"

    def __init__(
        self,
        client_factory: ClientFactory,
        resource_factory: ResourceFactory,
        list_regions: RegionLister,
        filter_tags: TagSpec | None,
        exception_tags: TagSpec | None,
        notags: TagSpec | None,
    ) -> None:
        self._client_factory = client_factory
        self._resource_factory = resource_factory
        self._list_regions = list_regions
        self._filter_tags = filter_tags
        self._exception_tags = exception_tags
        self._notags = notags
        self._vpcs: dict[tuple[str, str], list[Vpc]] = {}

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List every region and decide. Makes no mutating call. Every listed VPC is a
        candidate (main records them all before the skip check, BUG-012); protected ones carry
        `protected` in `extra` and are listed in `skipped` too."""
        self._vpcs.clear()
        vpc_filter = self.build_filter()
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        for region in self._list_regions():
            try:
                self._client_factory(region)
                ec2 = self._resource_factory(region)
                region_plan = self.plan_region(ec2, region, vpc_filter, dry_run=context.dry_run)
            except CONNECTIVITY_ERRORS as e:
                _skip_region(region, e)
                continue
            candidates.extend(region_plan.candidates)
            skipped.extend(region_plan.skipped)
        if context.dry_run:
            _log_summary([c.id for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Tear down and delete the planned VPCs, region by region (nothing in a dry run)."""
        ids = [candidate.id for candidate in plan.candidates]
        if context.dry_run:
            for candidate in plan.candidates:
                logger.current().action("would_delete", candidate.region, candidate.id)
            return Result(succeeded=tuple(ids))
        by_region: dict[str, list[Candidate]] = {}
        for candidate in plan.candidates:
            by_region.setdefault(candidate.region, []).append(candidate)
        for region, items in by_region.items():
            try:
                client = self._client_factory(region)
                ec2 = self._resource_factory(region)
                self.apply_region(client, ec2, items)
            except CONNECTIVITY_ERRORS as e:
                _skip_region(region, e)
        _log_summary(ids, dry_run=False)
        return Result(succeeded=tuple(ids))

    def build_filter(self) -> list[dict[str, Any]]:
        """EC2 `tag:key` filters of `filter_tags` (the values list is passed unchanged)."""
        filters: list[dict[str, Any]] = []
        if self._filter_tags:
            filters.extend(
                {"Name": f"tag:{key}", "Values": value} for key, value in self._filter_tags.items()
            )
        logging.info(f"Filters created: {filters}")
        return filters

    def list_vpcs(self, ec2: Ec2Resource, vpc_filter: Sequence[Mapping[str, Any]]) -> list[Vpc]:
        """The VPCs of one region that match the server side filter."""
        return list(ec2.vpcs.filter(Filters=vpc_filter))

    def plan_region(
        self,
        ec2: Ec2Resource,
        region: str,
        vpc_filter: Sequence[Mapping[str, Any]],
        *,
        dry_run: bool,
    ) -> Plan:
        """List one region; unless `dry_run`, also decide which VPCs are protected."""
        started = _regionlog.start()
        vpcs = self.list_vpcs(ec2, vpc_filter)
        ids = [vpc.id for vpc in vpcs]
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        for vpc_id, vpc in zip(ids, vpcs, strict=True):
            protected = False if dry_run else self.is_protected(vpc)
            self._vpcs.setdefault((region, vpc_id), []).append(vpc)
            candidates.append(
                Candidate(id=vpc_id, name=vpc_id, region=region, extra={_PROTECTED: protected})
            )
            if protected:
                logger.current().skip("protected")
                skipped.append(Skipped(id=vpc_id, reason="matches exception_tags or notags"))
        _regionlog.region_done(region, len(ids), len(ids) - len(skipped), started)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply_region(
        self, client: Ec2Client, ec2: Ec2Resource, candidates: Sequence[Candidate]
    ) -> None:
        """Tear down every unprotected candidate of one region."""
        for candidate in candidates:
            vpc = self._vpcs[(candidate.region, candidate.id)].pop(0)
            if candidate.extra[_PROTECTED]:
                continue
            self.delete_vpc(client, ec2, vpc, region=candidate.region)

    def is_protected(self, vpc: Vpc) -> bool:
        """BUG-012: main hands the boto3 Vpc resource to a function that iterates tags, so with
        `exception_tags` or `notags` set a real run raises `TypeError` (a Vpc is not iterable)."""
        return self.should_skip(cast("Iterable[Mapping[str, str]]", vpc))

    def should_skip(self, tags: Iterable[Mapping[str, str]]) -> bool:
        """Exception tags (any key) and notags (all keys) decision for one VPC's tags."""
        if not self._exception_tags and not self._notags:
            logging.warning("Tags and notags not present")
            return False
        all_tags: dict[str, str] = {}
        for tag in tags:
            key = tag["Key"]
            value = tag["Value"]
            if self._exception_tags and policy.exception_tags_any(
                {key: value}, self._exception_tags
            ):
                return True
            all_tags[key] = value
        return policy.notags_all(all_tags, self._notags or {})

    def delete_vpc(
        self, client: Ec2Client, ec2: Ec2Resource, vpc: Vpc, *, region: str | None = None
    ) -> None:
        """Detach and delete everything the VPC owns, in the order of main, then the VPC."""
        try:
            tear_down(client, ec2, vpc)
        except Exception as e:
            logger.current().fail(region, vpc.id, e)
            raise
        _delete_vpc_with_retries(client, vpc, region)


@register("aws", "vpc")
def build_vpc_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `filter_tags`, `exception_tags` and `notags` (dicts)."""
    options = context.options
    return VpcCleaner(
        client_factory=_boto3_client,
        resource_factory=_boto3_resource,
        list_regions=_all_regions,
        filter_tags=options.get("filter_tags"),
        exception_tags=options.get("exception_tags"),
        notags=options.get("notags"),
    )


class VPC(Service):
    """Legacy entry point used by `crc.py`: deletes the VPCs that match `filter_tags` (EC2 tag
    filter) and are not protected by `exception_tags` / `notags`. In dry run it only lists them.

    Attributes: `vpc_ids_to_delete` (ids deleted, or listed in a dry run), `filter_tags`,
    `exception_tags`, `notags`, `dry_run`. `get_deleted` and `delete_count` read the result.
    """

    service_name = _SERVICE_NAME
    default_region_name = _DEFAULT_REGION

    def __init__(
        self,
        dry_run: bool,
        filter_tags: TagSpec | None,
        exception_tags: TagSpec | None,
        notags: TagSpec | None,
        runtime: VpcRuntime | None = None,
    ) -> None:
        super().__init__()
        self.vpc_ids_to_delete: list[str] = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.notags = notags
        self._runtime = runtime or VpcRuntime()

    @property
    def get_deleted(self) -> list[str]:
        """Ids of the VPCs deleted (listed in a dry run)."""
        return self.vpc_ids_to_delete

    @property
    def delete_count(self) -> int:
        """Number of VPCs deleted (listed in a dry run); logs the count."""
        count = len(self.vpc_ids_to_delete)
        logging.info(f"count of items in vpc_ids_to_delete: {count}")
        return count

    def _cleaner(self) -> VpcCleaner:
        runtime = self._runtime
        return VpcCleaner(
            runtime.client_factory,
            runtime.resource_factory,
            runtime.list_regions,
            self.filter_tags,
            self.exception_tags,
            self.notags,
        )

    def _get_filter(self) -> list[dict[str, Any]]:
        return self._cleaner().build_filter()

    def _should_skip_vpc(self, tags: Iterable[Mapping[str, str]]) -> bool:
        return self._cleaner().should_skip(tags)

    def get_vpc_ids(self, vpcs: Iterable[Vpc]) -> list[str]:
        """Ids of the given boto3 VPC objects."""
        return [vpc.id for vpc in vpcs]

    def delete(self) -> None:
        """Delete (or, in a dry run, only list) the matching VPCs of every region."""
        runtime = self._runtime
        cleaner = self._cleaner()
        vpc_filter = cleaner.build_filter()
        run = logger.current()
        for region in runtime.list_regions():
            started = _regionlog.start()
            try:
                client = runtime.client_factory(region)
                ec2 = runtime.resource_factory(region)
                vpcs = cleaner.list_vpcs(ec2, vpc_filter)
                self.vpc_ids_to_delete.extend(self.get_vpc_ids(vpcs))
                if self.dry_run:
                    for vpc in vpcs:
                        run.action("would_delete", region, vpc.id)
                    _regionlog.region_done(region, len(vpcs), len(vpcs), started)
                    continue
                selected = 0
                for vpc in vpcs:
                    if cleaner.is_protected(vpc):
                        run.skip("protected")
                        continue
                    selected += 1
                    cleaner.delete_vpc(client, ec2, vpc, region=region)
                _regionlog.region_done(region, len(vpcs), selected, started)
            except CONNECTIVITY_ERRORS as e:
                _skip_region(region, e)
        _log_summary(self.vpc_ids_to_delete, dry_run=self.dry_run)
