# Copyright (c) Yugabyte, Inc.

"""Azure public IP cleaner.

`AzureIpCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the public
IP addresses (`list_all`) and filters them, `apply()` calls `begin_delete`. `IP` is the legacy
class `crc.py` uses; it keeps its constructor and attributes and runs the cleaner IP by IP, in
the exact call order of the original implementation.
"""

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Protocol

from crc.azu import _logger
from crc.azu._base import Base
from crc.core import policy
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext, Skipped
from crc.core.registry import register
from crc.service import Service

TagSpec = Mapping[str, Sequence[str]]


class PublicIpOperations(Protocol):
    """The two `public_ip_addresses` calls this cleaner uses."""

    def list_all(self) -> Iterable[Any]:
        """List the public IP addresses of the whole subscription."""
        ...

    def begin_delete(self, *, resource_group_name: str, public_ip_address_name: str) -> object:
        """Start the deletion of one public IP address."""
        ...


class IpNetworkClient(Protocol):
    """The part of the Azure network client this cleaner uses."""

    @property
    def public_ip_addresses(self) -> PublicIpOperations:
        """Operations on public IP addresses."""
        ...


ClientFactory = Callable[[], IpNetworkClient]


def _log_summary(deleted: Sequence[str], *, dry_run: bool) -> None:
    items = list(deleted)
    if not dry_run:
        logging.warning(f"number of Azure IPs deleted: {len(items)}")
        logging.warning(f"List of Azure IPs deleted: {items}")
    else:
        logging.warning(f"List of Azure IPs (Total: {len(items)}) which will be deleted: {items}")


class AzureIpCleaner:
    """Deletes public IPs without an IP configuration that match `filter_tags` (any key) and
    do not match `exception_tags` (any key) or `notags` (all keys)."""

    cloud = "azure"
    kind = "ip"

    def __init__(
        self,
        client_factory: ClientFactory,
        resource_group: str,
        filter_tags: TagSpec | None,
        exception_tags: TagSpec | None,
        notags: TagSpec | None,
    ) -> None:
        self._client_factory = client_factory
        self._resource_group = resource_group
        self._filter_tags = filter_tags
        self._exception_tags = exception_tags
        self._notags = notags

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List every public IP of the subscription and filter. Makes no mutating call."""
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        started = _logger.started()
        scanned = 0
        for ip in self.list_ips():
            scanned += 1
            candidate, reason = self.decide(ip)
            if candidate is not None:
                candidates.append(candidate)
            else:
                skipped.append(Skipped(id=getattr(ip, "name", ""), reason=reason))
        _logger.region_done(self._resource_group, scanned, len(candidates), started)
        if context.dry_run:
            for candidate in candidates:
                _logger.acted("would_delete", self._resource_group, candidate.id)
            _log_summary([c.id for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned addresses in plan order (nothing in a dry run)."""
        deleted: list[str] = []
        for candidate in plan.candidates:
            if not context.dry_run:
                self.delete_candidate(candidate)
            deleted.append(candidate.id)
        _log_summary(deleted, dry_run=context.dry_run)
        return Result(succeeded=tuple(deleted))

    def list_ips(self) -> Iterable[Any]:
        """The (lazy) subscription-wide listing, as on `main`."""
        return self._client_factory().public_ip_addresses.list_all()

    def delete_candidate(self, candidate: Candidate) -> None:
        """Start the deletion of one planned address and log it."""
        self._client_factory().public_ip_addresses.begin_delete(
            resource_group_name=self._resource_group,
            public_ip_address_name=candidate.id,
        )
        logging.info(f"Deleted IP address: {candidate.id}")
        _logger.acted("deleted", self._resource_group, candidate.id)

    @staticmethod
    def _kept(reason: str) -> tuple[None, str]:
        _logger.skip(reason)
        return None, reason

    def decide(self, ip: Any) -> tuple[Candidate | None, str]:
        """Select one SDK address: the candidate, or None with the reason it was kept."""
        exception_tags = self._exception_tags or {}
        notags = self._notags or {}
        filter_tags = self._filter_tags or {}
        filter_match = not filter_tags or (
            bool(ip.tags) and policy.filter_tags_any_key(ip.tags, filter_tags)
        )
        exception_match = bool(exception_tags) and policy.should_skip_by_tags(
            ip.tags, exception_tags, {}
        )
        notags_match = bool(notags) and policy.should_skip_by_tags(ip.tags, {}, notags)
        if not filter_match:
            return self._kept("no filter_tags match")
        if exception_match or notags_match:
            return self._kept("matches exception_tags or notags")
        if ip.ip_configuration is not None:
            return self._kept("attached to an IP configuration")
        tags = policy.normalize_tags(getattr(ip, "tags", None))
        return Candidate(id=ip.name, name=ip.name, tags=tags), ""


@register("azure", "ip")
def build_ip_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `resource_group`, `filter_tags`, `exception_tags`, `notags`."""
    options = context.options
    base = Base(options.get("resource_group") or "")
    return AzureIpCleaner(
        client_factory=base.get_network_client,
        resource_group=base.resource_group,
        filter_tags=options.get("filter_tags"),
        exception_tags=options.get("exception_tags"),
        notags=options.get("notags"),
    )


class IP(Service):
    """
    Class for managing public IP addresses in Azure.

    Attributes: `deleted_ips` (names deleted, or listed in a dry run), `base`, `dry_run`,
    `filter_tags`, `exception_tags`, `notags`. `get_deleted` and `count` read the result.
    """

    def __init__(
        self,
        resource_group: str,
        dry_run: bool,
        filter_tags: TagSpec | None,
        exception_tags: TagSpec | None,
        notags: TagSpec | None,
    ) -> None:
        """
        Initialize the IP class with filter and exception tags.
        :param dry_run: When True the class only lists the IP addresses that match the filter
        and exception tags and performs no operation on them.
        :param filter_tags: dict containing key-value pairs to filter IP addresses by
        :param exception_tags: dict containing key-value pairs to exclude IP addresses by
        :param notags: dict containing key-value pairs to skip IP addresses that carry all of them
        """
        super().__init__()
        self.deleted_ips: list[str] = []
        self.base = Base(resource_group)
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.notags = notags

    @property
    def get_deleted(self) -> list[str]:
        """
        Return the list of deleted IP addresses.
        """
        return self.deleted_ips

    @property
    def count(self) -> int:
        """
        Return the count of deleted IP addresses.
        """
        count = len(self.deleted_ips)
        logging.info(f"count of items in deleted_ips: {count}")
        return count

    def _cleaner(self) -> AzureIpCleaner:
        return AzureIpCleaner(
            self.base.get_network_client,
            self.base.resource_group,
            self.filter_tags,
            self.exception_tags,
            self.notags,
        )

    def delete(self) -> None:
        """
        Delete public IP addresses that match the filter and exception tags.
        """
        cleaner = self._cleaner()
        started = _logger.started()
        scanned = selected = 0
        for ip in cleaner.list_ips():
            scanned += 1
            candidate, _ = cleaner.decide(ip)
            if candidate is None:
                continue
            selected += 1
            if not self.dry_run:
                cleaner.delete_candidate(candidate)
            else:
                _logger.acted("would_delete", self.base.resource_group, candidate.id)
            self.deleted_ips.append(candidate.id)
        _logger.region_done(self.base.resource_group, scanned, selected, started)
        _log_summary(self.deleted_ips, dry_run=self.dry_run)
