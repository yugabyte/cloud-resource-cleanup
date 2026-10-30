# Copyright (c) Yugabyte, Inc.

"""GCP IP address cleaner.

`IpCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the regions and
their addresses and filters them, `apply()` deletes the planned RESERVED addresses.
"""

import datetime
import logging
from collections.abc import Sequence

from google.api_core.exceptions import GoogleAPIError
from google.auth.exceptions import GoogleAuthError

from crc.core import age as core_age
from crc.core import logger, policy
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
from crc.gcp._common import TIME_FORMAT
from crc.gcp._scan import ZoneScan
from crc.gcp.ip_client import AddressView, ComputeIpClient, GcpIpClient

_RESERVED = "RESERVED"


def _log_summary(deleted: Sequence[str], *, dry_run: bool) -> None:
    items = list(deleted)
    if not dry_run:
        logging.warning("number of GCP IPs deleted: %s", len(items))
        logging.warning("List of GCP IPs deleted: %s", items)
    else:
        logging.warning("List of GCP IPs (Total: %s) which will be deleted: %s", len(items), items)


class IpCleaner:
    """Deletes RESERVED addresses that are at least `age` old (`creation_timestamp`; BUG-062)
    and whose name contains a `filter_regex` entry (any; empty matches all) unless it
    contains an `exception_regex` entry (any; BUG-001 fixed). Applies to every region of the
    project. A failed delete is recorded and the next address goes on (BUG-063); an address
    counts as deleted only once its operation finished (BUG-064)."""

    cloud = "gcp"
    kind = "ip"

    def __init__(
        self,
        client: GcpIpClient,
        project_id: str,
        filter_regex: Sequence[str] | None,
        exception_regex: Sequence[str] | None,
        state: str = _RESERVED,
        age: core_age.AgeSpec | int | None = None,
    ) -> None:
        self._client = client
        self._project_id = project_id
        self._filter_regex = filter_regex
        self._exception_regex = exception_regex
        self._state = state
        self._age = age or {}
        self.failures: list[Failure] = []

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def regions(self) -> Sequence[str]:
        """Region names of the project (read-only call)."""
        return self._client.list_regions(self._project_id)

    def plan(self, context: RunContext) -> Plan:
        """List every region and filter. Makes no mutating call."""
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        for region in self.regions():
            region_plan = self.plan_region(region, context.now)
            candidates.extend(region_plan.candidates)
            skipped.extend(region_plan.skipped)
            if context.dry_run:
                for c in region_plan.candidates:
                    logger.current().action("would_delete", c.region, c.id, c.name)
        if context.dry_run:
            _log_summary([c.id for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned addresses, region by region (nothing in a dry run)."""
        by_region: dict[str, list[Candidate]] = {}
        for candidate in plan.candidates:
            by_region.setdefault(candidate.region, []).append(candidate)
        deleted: list[str] = []
        self.failures = []
        for region, items in by_region.items():
            deleted.extend(self.apply_region(region, items, dry_run=context.dry_run))
        _log_summary(deleted, dry_run=context.dry_run)
        return Result(succeeded=tuple(deleted), failed=tuple(self.failures))

    def plan_region(self, region: str, now: datetime.datetime | None = None) -> Plan:
        """List one region and decide, in the order of `main`: log the address, read its
        name, skip other states, then the filter and the exception test, then the age gate
        (`creationTimestamp` at least `age` old; skipped when no age is set, a dry run only;
        an unreadable timestamp keeps the address and is recorded as a failure)."""
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        scan = ZoneScan(region)
        for address in self._client.list_addresses(self._project_id, region):
            logging.debug(address)
            scan.scanned += 1
            name = address.name
            if not policy.state_in(address.status, (self._state,)):
                skipped.append(Skipped(id=name, reason=f"status is not {self._state}", name=name))
                logger.current().skip("state")
                continue
            matches_filter = self._matches_filter(name)
            excluded = self._excluded(name)
            if not matches_filter or excluded:
                reason = "no filter_regex match" if not matches_filter else "exception_regex"
                skipped.append(Skipped(id=name, reason=reason, name=name))
                logger.current().skip("filter_mismatch" if not matches_filter else reason)
                continue
            if not self._old_enough(address, region, now):
                skipped.append(Skipped(id=name, reason="too young or age unknown", name=name))
                continue
            candidates.append(Candidate(id=name, name=name, region=region, state=address.status))
            scan.selected += 1
        scan.finish()
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def _old_enough(self, address: AddressView, region: str, now: datetime.datetime | None) -> bool:
        if not self._age:
            return True
        try:
            if now is None:
                raise ValueError("the run time is required for the age gate")
            stamp = datetime.datetime.strptime(address.creation_timestamp, TIME_FORMAT)  # noqa: DTZ007 - TIME_FORMAT ends in %z, so the result is aware
            old = core_age.is_old(self._age, now.astimezone(stamp.tzinfo), stamp)
        except Exception as e:  # noqa: BLE001 - per-address isolation, failure logged and recorded, address kept
            logging.error("Error occurred while evaluating IP address %s: %s", address.name, e)
            logger.current().fail(region, str(getattr(address, "name", "")), e, logged=True)
            return False
        if not old:
            logger.current().skip("too_young")
        return old

    def apply_region(
        self, region: str, candidates: Sequence[Candidate], *, dry_run: bool
    ) -> list[str]:
        """Delete `candidates` one by one and return the names deleted (all of them in a dry
        run, where nothing is deleted). A failed address is recorded in `failures` and skipped."""
        deleted: list[str] = []
        for candidate in candidates:
            if not dry_run and not self._delete(region, candidate):
                continue
            kind = "would_delete" if dry_run else "deleted"
            logger.current().action(kind, region, candidate.id, candidate.name)
            deleted.append(candidate.id)
        return deleted

    def _delete(self, region: str, candidate: Candidate) -> bool:
        try:
            self._client.delete_address(self._project_id, region, candidate.id).result()
        except (GoogleAPIError, GoogleAuthError, OSError) as e:
            logging.error("Error occurred while deleting IP address %s: %s", candidate.id, e)
            logger.current().fail(region, candidate.id, e, logged=True)
            self.failures.append(Failure(id=candidate.id, error=str(e)))
            return False
        except Exception as e:
            logger.current().fail(region, candidate.id, e)
            raise
        logging.info("Deleting IP address: %s", candidate.id)
        return True

    def _matches_filter(self, name: str) -> bool:
        return policy.filter_substring_matches(self._filter_regex or (), name)

    def _excluded(self, name: str) -> bool:
        return any(pattern in name for pattern in self._exception_regex or ())


@register("gcp", "ip")
def build_ip_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `project_id`, `name_regex`, `exception_regex` and `age` (a
    positive age is required on a real run, BUG-062)."""
    options = context.options
    project_id = options.get("project_id")
    if not project_id:
        raise ValueError("project_id is required for GCP IP cleanup")
    age = options.get("age")
    if not context.dry_run:
        age = core_age.require_positive_age(age, "age")
    return IpCleaner(
        client=ComputeIpClient(),
        project_id=project_id,
        filter_regex=options.get("name_regex"),
        exception_regex=options.get("exception_regex"),
        age=age,
    )
