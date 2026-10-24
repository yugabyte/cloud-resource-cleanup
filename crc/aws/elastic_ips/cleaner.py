# Copyright (c) Yugabyte, Inc.

"""AWS Elastic IP cleaner.

`ElasticIpCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the
addresses (`describe_addresses`) and filters them, `apply()` calls `release_address`.
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from crc.aws import _regionlog
from crc.aws._common import ec2_regions, group_by_region, log_deleted_summary, skip_region
from crc.aws.connectivity import CONNECTIVITY_ERRORS
from crc.core import logger, policy
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext, Skipped
from crc.core.registry import register

from .client import ClientFactory, ElasticIpClient, RegionLister, TagSpec, boto3_client

SKIP_CONTEXT = "elastic IP cleanup"
_ALLOCATION_ID = "allocation_id"


def log_summary(deleted: Sequence[str], *, dry_run: bool) -> None:
    """The summary lines `main` logs once all regions are done."""
    log_deleted_summary("Elastic IPs", deleted, dry_run=dry_run)


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
                skip_region(region, SKIP_CONTEXT, e)
                continue
            if context.dry_run:
                for candidate in region_plan.candidates:
                    logger.current().action(
                        "would_delete", candidate.region, candidate.id, candidate.name
                    )
            candidates.extend(region_plan.candidates)
            skipped.extend(region_plan.skipped)
        if context.dry_run:
            log_summary([c.id for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Release the planned addresses, region by region (nothing in a dry run)."""
        deleted: list[str] = []
        for region, items in group_by_region(plan.candidates).items():
            try:
                client = self._client_factory(region)
                deleted.extend(self.apply_region(client, items, dry_run=context.dry_run))
            except CONNECTIVITY_ERRORS as e:
                skip_region(region, SKIP_CONTEXT, e)
        log_summary(deleted, dry_run=context.dry_run)
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
            logging.info("Deleted IP: %s", candidate.id)
            run.action("deleted", candidate.region, candidate.id, candidate.name)
        return [candidate.id for candidate in candidates]

    def should_skip(self, tags: Sequence[Mapping[str, Any]]) -> bool:
        """Exception tags and notags decision for one address (see `_should_skip`)."""
        return self._should_skip(tags)

    def _should_skip(self, tags: Sequence[Mapping[str, Any]]) -> bool:
        if not self._exception_tags and not self._notags:
            return False
        all_tags: dict[str, str] = {}
        for tag in tags:
            pair = _pair(tag, self._exception_tags)
            if self._exception_tags and policy.aws_eip_should_skip(pair, self._exception_tags):
                return True
            if self._notags:
                all_tags[tag["Key"]] = tag["Value"]
        # BUG-011: notags keeps an address when every notags key matches, as for AWS VMs.
        return policy.notags_all(all_tags, self._notags or {})

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
        client_factory=boto3_client,
        list_regions=ec2_regions,
        filter_tags=options.get("filter_tags"),
        exception_tags=options.get("exception_tags"),
        notags=options.get("notags"),
    )
