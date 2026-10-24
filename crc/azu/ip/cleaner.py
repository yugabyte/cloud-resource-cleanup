# Copyright (c) Yugabyte, Inc.

"""Azure public IP cleaner.

`AzureIpCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the public
IP addresses of the configured resource group (`list(resource_group)`) and filters them,
`apply()` calls `begin_delete`.
"""

import logging
from collections.abc import Iterable
from typing import Any

from crc.azu import _logger
from crc.azu._base import Base
from crc.azu._common import kept, log_delete_summary, log_would_delete, real_run_age
from crc.azu._created import IP_TYPE, AgeGate
from crc.azu.ip.client import ClientFactory, TagSpec
from crc.core import policy
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext, Skipped
from crc.core.registry import register

_OPTIONAL_IN_USE_FIELDS = (
    "nat_gateway",
    "linked_public_ip_address",
    "service_public_ip_address",
)


def _in_use(ip: Any) -> bool:
    """True when the address is attached to anything (BUG-076).

    `ip_configuration` is read directly, so a model without it raises `AttributeError` as on
    `main` instead of being deleted; the newer fields count as unset when the SDK lacks them.
    """
    return ip.ip_configuration is not None or any(
        getattr(ip, field, None) is not None for field in _OPTIONAL_IN_USE_FIELDS
    )


class AzureIpCleaner:
    """Deletes unused public IPs of the configured resource group that match `filter_tags` (any
    key) and do not match `exception_tags` (any key) or `notags` (all keys)."""

    cloud = "azure"
    kind = "ip"

    def __init__(
        self,
        client_factory: ClientFactory,
        resource_group: str,
        filter_tags: TagSpec | None,
        exception_tags: TagSpec | None,
        notags: TagSpec | None,
        age_gate: AgeGate | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._resource_group = resource_group
        self._filter_tags = filter_tags
        self._exception_tags = exception_tags
        self._notags = notags
        self._age_gate = age_gate
        self._started = 0.0

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List the public IPs of the configured group and filter. Makes no mutating call."""
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        self._started = started = _logger.started()
        scanned = 0
        ips = self.list_ips()
        if self._age_gate is not None:
            self._age_gate.load(context.now)
        for ip in ips:
            scanned += 1
            candidate, reason = self.decide(ip)
            if candidate is not None:
                candidates.append(candidate)
            else:
                skipped.append(Skipped(id=getattr(ip, "name", ""), reason=reason))
        if context.dry_run:
            _logger.region_done(self._resource_group, scanned, len(candidates), started)
            log_would_delete(self._resource_group, candidates)
            log_delete_summary("IPs", [c.id for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned addresses in plan order (nothing in a dry run).

        A real run logs its REGION line here, after the deletes: an error from a delete
        leaves it out.
        """
        deleted: list[str] = []
        for candidate in plan.candidates:
            if not context.dry_run:
                self.delete_candidate(candidate)
            deleted.append(candidate.id)
        if not context.dry_run:
            scanned = len(plan.candidates) + len(plan.skipped)
            _logger.region_done(self._resource_group, scanned, len(plan.candidates), self._started)
        log_delete_summary("IPs", deleted, dry_run=context.dry_run)
        return Result(succeeded=tuple(deleted))

    def list_ips(self) -> Iterable[Any]:
        """The (lazy) listing of the configured resource group only (BUG-074).

        Raises `ValueError` before any SDK call when the resource group is empty.
        """
        if not self._resource_group or not self._resource_group.strip():
            raise ValueError("Azure IP cleanup needs a non-empty resource group")
        return self._client_factory().public_ip_addresses.list(self._resource_group)

    def delete_candidate(self, candidate: Candidate) -> None:
        """Start the deletion of one planned address and log it."""
        self._client_factory().public_ip_addresses.begin_delete(
            resource_group_name=self._resource_group,
            public_ip_address_name=candidate.id,
        )
        logging.info("Deleted IP address: %s", candidate.id)
        _logger.acted("deleted", self._resource_group, candidate.id)

    def decide(self, ip: Any) -> tuple[Candidate | None, str]:
        """Select one SDK address: the candidate, or None with the reason it was kept."""
        exception_tags = self._exception_tags or {}
        notags = self._notags or {}
        filter_tags = self._filter_tags or {}
        filter_match = not filter_tags or (
            bool(ip.tags) and policy.filter_tags_any_key(ip.tags, filter_tags)
        )
        exception_match = bool(exception_tags) and policy.should_skip_by_tags(
            ip.tags or {}, exception_tags, {}
        )
        notags_match = bool(notags) and policy.should_skip_by_tags(ip.tags or {}, {}, notags)
        if not filter_match:
            return kept("no filter_tags match")
        if exception_match or notags_match:
            return kept("matches exception_tags or notags")
        if _in_use(ip):
            return kept("in_use")
        if self._age_gate is not None:
            reason = self._age_gate.keep_reason(getattr(ip, "id", None))
            if reason:
                return kept(reason)
        tags = policy.normalize_tags(getattr(ip, "tags", None))
        return Candidate(id=ip.name, name=ip.name, tags=tags), ""


@register("azure", "ip")
def build_ip_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `resource_group`, `filter_tags`, `exception_tags`, `notags`, `age`
    (positive on a real run)."""
    options = context.options
    age = real_run_age(context, "IP")
    base = Base(options.get("resource_group") or "")
    gate = AgeGate(age, base.get_resource_client, base.resource_group, IP_TYPE) if age else None
    return AzureIpCleaner(
        client_factory=base.get_network_client,
        resource_group=base.resource_group,
        filter_tags=options.get("filter_tags"),
        exception_tags=options.get("exception_tags"),
        notags=options.get("notags"),
        age_gate=gate,
    )
