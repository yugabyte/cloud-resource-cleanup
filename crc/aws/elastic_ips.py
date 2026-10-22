# Copyright (c) Yugabyte, Inc.

"""AWS Elastic IP cleaner.

`ElasticIpCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the
addresses (`describe_addresses`) and filters them, `apply()` calls `release_address`.
`ElasticIPs` is the legacy class `crc.py` uses; it keeps its constructor and attributes and
runs the cleaner region by region, in the exact call order of the original implementation.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import boto3

from crc.aws import _regionlog
from crc.aws._base import get_all_regions
from crc.aws.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.core import logger, policy
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext, Skipped
from crc.core.registry import register
from crc.service import Service

_SERVICE_NAME = "ec2"
_DEFAULT_REGION = "us-west-2"
_SKIP_CONTEXT = "elastic IP cleanup"
_ALLOCATION_ID = "allocation_id"

TagSpec = Mapping[str, Sequence[str]]


class ElasticIpClient(Protocol):
    """The two EC2 calls this cleaner uses."""

    def describe_addresses(self) -> Mapping[str, Any]:
        """List the Elastic IP addresses of the client's region."""
        ...

    def release_address(self, *, AllocationId: str) -> Mapping[str, Any]:
        """Release one Elastic IP by allocation id."""
        ...


ClientFactory = Callable[[str], ElasticIpClient]
RegionLister = Callable[[], Sequence[str]]


def _boto3_client(region: str) -> ElasticIpClient:
    client: ElasticIpClient = boto3.client(_SERVICE_NAME, region_name=region)
    return client


def _all_regions() -> Sequence[str]:
    return get_all_regions(_SERVICE_NAME, _DEFAULT_REGION)


@dataclass(frozen=True, slots=True)
class ElasticIpRuntime:
    """Injected collaborators of `ElasticIPs`: the SDK client factory and the region lister."""

    client_factory: ClientFactory = _boto3_client
    list_regions: RegionLister = _all_regions


def _skip_region(region: str, error: BaseException) -> None:
    _regionlog.region_skipped(region, error)
    log_skipped_region(region, _SKIP_CONTEXT, error)


def _log_summary(deleted: Sequence[str], *, dry_run: bool) -> None:
    items = list(deleted)
    if not dry_run:
        logging.warning(f"number of AWS Elastic IPs deleted: {len(items)}")
        logging.warning(f"List of AWS Elastic IPs deleted: {items}")
    else:
        logging.warning(
            f"List of AWS Elastic IPs (Total: {len(items)}) which will be deleted: {items}"
        )


def _pair(tag: Mapping[str, Any], spec: TagSpec | None) -> dict[str, Any]:
    key = tag["Key"]
    value = tag["Value"] if spec and key in spec and spec[key] else ""
    return {key: value}


class ElasticIpCleaner:
    """Releases unassociated, tagged Elastic IPs that match `filter_tags` (any key) and
    do not match `exception_tags`. Applies to every region the injected lister returns."""

    cloud = "aws"
    kind = "ip"

    def __init__(
        self,
        client_factory: ClientFactory,
        list_regions: RegionLister,
        filter_tags: TagSpec | None,
        exception_tags: TagSpec | None,
        notags: TagSpec | None,
    ) -> None:
        self._client_factory = client_factory
        self._list_regions = list_regions
        self._filter_tags = filter_tags
        self._exception_tags = exception_tags
        self._notags = notags

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List every region and filter. Makes no mutating call."""
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        for region in self._list_regions():
            try:
                client = self._client_factory(region)
                region_plan = self.plan_region(client, region)
            except CONNECTIVITY_ERRORS as e:
                _skip_region(region, e)
                continue
            candidates.extend(region_plan.candidates)
            skipped.extend(region_plan.skipped)
        if context.dry_run:
            _log_summary([c.id for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Release the planned addresses, region by region (nothing in a dry run)."""
        by_region: dict[str, list[Candidate]] = {}
        for candidate in plan.candidates:
            by_region.setdefault(candidate.region, []).append(candidate)
        deleted: list[str] = []
        for region, items in by_region.items():
            try:
                client = self._client_factory(region)
                deleted.extend(self.apply_region(client, items, dry_run=context.dry_run))
            except CONNECTIVITY_ERRORS as e:
                _skip_region(region, e)
        _log_summary(deleted, dry_run=context.dry_run)
        return Result(succeeded=tuple(deleted))

    def plan_region(self, client: ElasticIpClient, region: str) -> Plan:
        """List one region and decide. Only unassociated addresses that carry a `Tags` key
        are considered; the public IP is the identity, as on `main`."""
        started = _regionlog.start()
        addresses = client.describe_addresses()["Addresses"]
        selected: dict[str, str] = {}
        skipped: list[Skipped] = []
        for eip in addresses:
            if "NetworkInterfaceId" in eip or "Tags" not in eip:
                continue
            tags = eip["Tags"]
            if self._should_skip(tags):
                logger.current().skip("exception_tag")
                skipped.append(Skipped(id=eip.get("PublicIp", ""), reason="matches exception_tags"))
                continue
            if not self._select_on_filter(eip, tags, selected):
                logger.current().skip("no_filter_match")
                skipped.append(Skipped(id=eip.get("PublicIp", ""), reason="no filter_tags match"))
        candidates = tuple(
            Candidate(id=ip, name=ip, region=region, extra={_ALLOCATION_ID: allocation_id})
            for ip, allocation_id in selected.items()
        )
        _regionlog.region_done(region, len(addresses), len(candidates), started)
        return Plan(candidates=candidates, skipped=tuple(skipped))

    def apply_region(
        self, client: ElasticIpClient, candidates: Sequence[Candidate], *, dry_run: bool
    ) -> list[str]:
        """Release `candidates` one by one and return their ids (also in a dry run, where
        nothing is released). The caller records the ids only once the whole region is done."""
        run = logger.current()
        for candidate in candidates:
            if dry_run:
                run.action("would_delete", candidate.region, candidate.id, candidate.name)
                continue
            try:
                client.release_address(AllocationId=candidate.extra[_ALLOCATION_ID])
            except Exception as e:
                run.fail(candidate.region, candidate.id, e)
                raise
            logging.info(f"Deleted IP: {candidate.id}")
            run.action("deleted", candidate.region, candidate.id, candidate.name)
        return [candidate.id for candidate in candidates]

    def should_skip(self, tags: Sequence[Mapping[str, Any]]) -> bool:
        """Exception tags and notags decision for one address (see `_should_skip`)."""
        return self._should_skip(tags)

    def _should_skip(self, tags: Sequence[Mapping[str, Any]]) -> bool:
        if not self._exception_tags and not self._notags:
            return False
        for tag in tags:
            pair = _pair(tag, self._exception_tags)
            if self._exception_tags and policy.aws_eip_should_skip(pair, self._exception_tags):
                return True
            if self._notags:
                # BUG-011: main evaluates all(<bool>,) here, which raises this TypeError.
                raise TypeError("'bool' object is not iterable")
        return False

    def _select_on_filter(
        self, eip: Mapping[str, Any], tags: Sequence[Mapping[str, Any]], selected: dict[str, str]
    ) -> bool:
        # main stores the address at each matching tag and still reads the later tags, so a
        # missing AllocationId raises before a malformed later tag does.
        if not self._filter_tags:
            selected[eip["PublicIp"]] = eip["AllocationId"]
            return True
        matched = False
        for tag in tags:
            if policy.filter_tags_any_key(_pair(tag, self._filter_tags), self._filter_tags):
                selected[eip["PublicIp"]] = eip["AllocationId"]
                matched = True
        return matched


@register("aws", "ip")
def build_elastic_ip_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `filter_tags`, `exception_tags` and `notags` (dicts)."""
    options = context.options
    return ElasticIpCleaner(
        client_factory=_boto3_client,
        list_regions=_all_regions,
        filter_tags=options.get("filter_tags"),
        exception_tags=options.get("exception_tags"),
        notags=options.get("notags"),
    )


class ElasticIPs(Service):
    """Legacy entry point used by `crc.py`: releases unassociated Elastic IPs that match
    `filter_tags` and are not in `exception_tags`. In dry run it only lists them.

    Attributes: `deleted_ips` (public IPs released, or listed in a dry run), `filter_tags`,
    `exception_tags`, `notags`, `dry_run`. `get_deleted` and `count` read the result.
    """

    service_name = _SERVICE_NAME
    default_region_name = _DEFAULT_REGION

    def __init__(
        self,
        dry_run: bool,
        filter_tags: TagSpec | None,
        exception_tags: TagSpec | None,
        notags: TagSpec | None,
        runtime: ElasticIpRuntime | None = None,
    ) -> None:
        super().__init__()
        self.deleted_ips: list[str] = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.notags = notags
        self._runtime = runtime or ElasticIpRuntime()

    @property
    def get_deleted(self) -> list[str]:
        """Public IPs released (listed in a dry run)."""
        return self.deleted_ips

    @property
    def count(self) -> int:
        """Number of Elastic IPs released (listed in a dry run); logs the count."""
        count = len(self.deleted_ips)
        logging.info(f"count of items in deleted_ips: {count}")
        return count

    def _cleaner(self) -> ElasticIpCleaner:
        runtime = self._runtime
        return ElasticIpCleaner(
            runtime.client_factory,
            runtime.list_regions,
            self.filter_tags,
            self.exception_tags,
            self.notags,
        )

    def _should_skip_instance(self, tags: Sequence[Mapping[str, Any]]) -> bool:
        return self._cleaner().should_skip(tags)

    def delete(self) -> None:
        """Release (or, in a dry run, only list) the matching Elastic IPs of every region."""
        runtime = self._runtime
        cleaner = self._cleaner()
        for region in runtime.list_regions():
            try:
                client = runtime.client_factory(region)
                region_plan = cleaner.plan_region(client, region)
                released = cleaner.apply_region(
                    client, region_plan.candidates, dry_run=self.dry_run
                )
                self.deleted_ips.extend(released)
            except CONNECTIVITY_ERRORS as e:
                _skip_region(region, e)
        _log_summary(self.deleted_ips, dry_run=self.dry_run)
