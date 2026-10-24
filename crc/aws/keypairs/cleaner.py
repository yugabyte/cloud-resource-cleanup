# Copyright (c) Yugabyte, Inc.

"""AWS EC2 key pair cleaner.

`KeyPairCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists key pairs
(`describe_key_pairs`) and filters them, `apply()` calls `delete_key_pair`. `crc/cli` runs it
through `crc.core.engine` (route `crc/cli/routes/aws.py`).
"""

import datetime
import logging
from collections.abc import Sequence

from crc.aws import _regionlog
from crc.aws._common import ec2_regions, group_by_region, log_deleted_summary, skip_region
from crc.aws.connectivity import CONNECTIVITY_ERRORS
from crc.core import age as core_age
from crc.core import logger, policy
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext, Skipped
from crc.core.registry import register

from .client import ClientFactory, KeyPairClient, RegionLister, boto3_client

SKIP_CONTEXT = "keypair cleanup"


def log_summary(deleted: Sequence[str], *, dry_run: bool) -> None:
    """The summary lines `main` logs once all regions are done."""
    log_deleted_summary("keypairs", deleted, dry_run=dry_run)


class KeyPairCleaner:
    """Deletes key pairs whose name matches `name_regex`, not `exception_regex`, and that
    are older than `age`. Applies to every region the injected lister returns."""

    cloud = "aws"
    kind = "keypair"

    def __init__(
        self,
        client_factory: ClientFactory,
        list_regions: RegionLister,
        name_regex: Sequence[str] | None,
        exception_regex: Sequence[str] | None,
        age: core_age.AgeSpec | int | None,
    ) -> None:
        self._client_factory = client_factory
        self._list_regions = list_regions
        self._name_regex = name_regex
        self._exception_regex = exception_regex
        self._age = age
        # A set, as on main: its iteration order decides which pattern is tried first
        # (and so which invalid pattern raises) and the order of deletion.
        self._exception_patterns = tuple(set(exception_regex)) if exception_regex else ()

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List every region and filter. Makes no mutating call."""
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        for region in self._list_regions():
            try:
                client = self._client_factory(region)
                region_plan = self.plan_region(client, region, context.now)
            except CONNECTIVITY_ERRORS as e:
                skip_region(region, SKIP_CONTEXT, e)
                continue
            if context.dry_run:
                self._log_dry_run_actions(region_plan.candidates)
            candidates.extend(region_plan.candidates)
            skipped.extend(region_plan.skipped)
        if context.dry_run:
            log_summary([c.id for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned key pairs, region by region (nothing in a dry run)."""
        deleted: list[str] = []
        for region, items in group_by_region(plan.candidates).items():
            try:
                client = self._client_factory(region)
                self.apply_region(client, items, deleted, dry_run=context.dry_run)
            except CONNECTIVITY_ERRORS as e:
                skip_region(region, SKIP_CONTEXT, e)
        log_summary(deleted, dry_run=context.dry_run)
        return Result(succeeded=tuple(deleted))

    def plan_region(self, client: KeyPairClient, region: str, now: datetime.datetime) -> Plan:
        """List one region and decide. `now` is compared with each `CreateTime`."""
        started = _regionlog.start()
        keypairs = client.describe_key_pairs()
        selected: dict[str, Candidate] = {}
        order: set[str] = set()
        skipped: list[Skipped] = []
        for keypair in keypairs["KeyPairs"]:
            if "KeyName" not in keypair or "CreateTime" not in keypair:
                continue
            name: str = keypair["KeyName"]
            created: datetime.datetime = keypair["CreateTime"]
            reason = self._skip_reason(name, now.astimezone(created.tzinfo), created)
            if reason is None:
                order.add(name)
                selected[name] = Candidate(id=name, name=name, region=region, launch_time=created)
            else:
                skipped.append(Skipped(id=name, reason=reason, name=name))
        _regionlog.region_done(region, len(keypairs["KeyPairs"]), len(selected), started)
        return Plan(candidates=tuple(selected[name] for name in order), skipped=tuple(skipped))

    @staticmethod
    def _log_dry_run_actions(candidates: Sequence[Candidate]) -> None:
        """A dry run never calls `apply()`, so `plan()` logs the would_delete actions per region."""
        run = logger.current()
        for candidate in candidates:
            run.action("would_delete", candidate.region, candidate.id, candidate.name)

    def apply_region(
        self,
        client: KeyPairClient,
        candidates: Sequence[Candidate],
        deleted: list[str],
        *,
        dry_run: bool,
    ) -> None:
        """Delete `candidates` one by one, appending each name to `deleted` as it is done
        (also in a dry run, where nothing is deleted)."""
        run = logger.current()
        for candidate in candidates:
            if dry_run:
                run.action("would_delete", candidate.region, candidate.id, candidate.name)
            else:
                try:
                    response = client.delete_key_pair(KeyName=candidate.id)
                except Exception as e:
                    run.fail(candidate.region, candidate.id, e)
                    raise
                logging.info("Deleted keypair: %s with response: %s", candidate.id, response)
                run.action("deleted", candidate.region, candidate.id, candidate.name)
            deleted.append(candidate.id)

    def _skip_reason(
        self, name: str, now: datetime.datetime, created: datetime.datetime
    ) -> str | None:
        matches_name = policy.name_regex_search_matches(self._name_regex or (), name)
        matches_exception = policy.exception_regex_search_matches(self._exception_patterns, name)
        if matches_name and not matches_exception:
            if core_age.is_old(self._age or {}, now, created):
                return None
            logging.debug("Keypair %s is not old enough to be deleted.", name)
            logger.current().skip("too_young")
            return "not older than age"
        if matches_exception:
            logging.debug("Keypair %s is in exception_regex %s.", name, self._exception_regex)
            logger.current().skip("exception_regex")
            return "matches exception_regex"
        logger.current().skip("name_regex_mismatch")
        return "does not match name_regex"


@register("aws", "keypair")
def build_keypair_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `name_regex`, `exception_regex` (lists) and `age` (dict)."""
    options = context.options
    return KeyPairCleaner(
        client_factory=boto3_client,
        list_regions=ec2_regions,
        name_regex=options.get("name_regex"),
        exception_regex=options.get("exception_regex"),
        age=options.get("age"),
    )
