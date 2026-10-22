# Copyright (c) Yugabyte, Inc.

"""AWS EC2 key pair cleaner.

`KeyPairCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists key pairs
(`describe_key_pairs`) and filters them, `apply()` calls `delete_key_pair`. `KeyPairs` is the
legacy class `crc.py` uses; it keeps its constructor and attributes and runs the cleaner
region by region, in the exact call order of the original implementation.
"""

import datetime
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import boto3

from crc.aws import _regionlog
from crc.aws._base import get_all_regions
from crc.aws.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.core import age as core_age
from crc.core import logger, policy
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext, Skipped
from crc.core.registry import register
from crc.service import Service

_SERVICE_NAME = "ec2"
_DEFAULT_REGION = "us-west-2"
_SKIP_CONTEXT = "keypair cleanup"


class KeyPairClient(Protocol):
    """The two EC2 calls this cleaner uses."""

    def describe_key_pairs(self) -> Mapping[str, Any]:
        """List the key pairs of the client's region."""
        ...

    def delete_key_pair(self, *, KeyName: str) -> Mapping[str, Any]:
        """Delete one key pair by name."""
        ...


ClientFactory = Callable[[str], KeyPairClient]
RegionLister = Callable[[], Sequence[str]]
Clock = Callable[[], datetime.datetime]


def _boto3_client(region: str) -> KeyPairClient:
    client: KeyPairClient = boto3.client(_SERVICE_NAME, region_name=region)
    return client


def _all_regions() -> Sequence[str]:
    return get_all_regions(_SERVICE_NAME, _DEFAULT_REGION)


@dataclass(frozen=True, slots=True)
class KeyPairRuntime:
    """Injected collaborators of `KeyPairs`: the SDK client factory, the region lister and the
    clock that supplies `now` (the cleaner itself never reads the clock)."""

    client_factory: ClientFactory = _boto3_client
    list_regions: RegionLister = _all_regions
    clock: Clock = datetime.datetime.now


def _skip_region(region: str, error: BaseException) -> None:
    _regionlog.region_skipped(region, error)
    log_skipped_region(region, _SKIP_CONTEXT, error)


def _log_summary(deleted: Sequence[str], *, dry_run: bool) -> None:
    items = list(deleted)
    if not dry_run:
        logging.warning(f"number of AWS keypairs deleted: {len(items)}")
        logging.warning(f"List of AWS keypairs deleted: {items}")
    else:
        logging.warning(
            f"List of AWS keypairs (Total: {len(items)}) which will be deleted: {items}"
        )


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
                _skip_region(region, e)
                continue
            candidates.extend(region_plan.candidates)
            skipped.extend(region_plan.skipped)
        if context.dry_run:
            _log_summary([c.id for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned key pairs, region by region (nothing in a dry run)."""
        by_region: dict[str, list[Candidate]] = {}
        for candidate in plan.candidates:
            by_region.setdefault(candidate.region, []).append(candidate)
        deleted: list[str] = []
        for region, items in by_region.items():
            try:
                client = self._client_factory(region)
                self.apply_region(client, items, deleted, dry_run=context.dry_run)
            except CONNECTIVITY_ERRORS as e:
                _skip_region(region, e)
        _log_summary(deleted, dry_run=context.dry_run)
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
                logging.info(f"Deleted keypair: {candidate.id} with response: {response}")
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
            logging.debug(f"Keypair {name} is not old enough to be deleted.")
            logger.current().skip("too_young")
            return "not older than age"
        if matches_exception:
            logging.debug(f"Keypair {name} is in exception_regex {self._exception_regex}.")
            logger.current().skip("exception_regex")
            return "matches exception_regex"
        logger.current().skip("name_regex_mismatch")
        return "does not match name_regex"


@register("aws", "keypair")
def build_keypair_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `name_regex`, `exception_regex` (lists) and `age` (dict)."""
    options = context.options
    return KeyPairCleaner(
        client_factory=_boto3_client,
        list_regions=_all_regions,
        name_regex=list(options.get("name_regex") or ()),
        exception_regex=list(options.get("exception_regex") or ()),
        age=options.get("age"),
    )


class KeyPairs(Service):
    """Legacy entry point used by `crc.py`: deletes key pairs that match the name regexes,
    skips the exception regexes and are older than `age`. In dry run it only lists them.

    Attributes: `deleted_keypairs` (names deleted, or listed in a dry run), `name_regex`,
    `exception_regex`, `age`, `dry_run`. `get_deleted` and `count` read the result.
    """

    service_name = _SERVICE_NAME
    default_region_name = _DEFAULT_REGION

    def __init__(
        self,
        dry_run: bool,
        name_regex: list[str] | None,
        exception_regex: list[str] | None,
        age: core_age.AgeSpec | int | None,
        runtime: KeyPairRuntime | None = None,
    ) -> None:
        super().__init__()
        self.deleted_keypairs: list[str] = []
        self.dry_run = dry_run
        self.name_regex = name_regex
        self.exception_regex = exception_regex
        self.age = age
        self._runtime = runtime or KeyPairRuntime()

    @property
    def get_deleted(self) -> list[str]:
        """Names of the key pairs deleted (listed in a dry run)."""
        return self.deleted_keypairs

    @property
    def count(self) -> int:
        """Number of key pairs deleted (listed in a dry run); logs the count."""
        count = len(self.deleted_keypairs)
        logging.info(f"count of items in deleted_keypairs: {count}")
        return count

    def delete(self) -> None:
        """Delete (or, in a dry run, only list) the matching key pairs of every region."""
        runtime = self._runtime
        cleaner = KeyPairCleaner(
            runtime.client_factory,
            runtime.list_regions,
            self.name_regex,
            self.exception_regex,
            self.age,
        )
        for region in runtime.list_regions():
            try:
                client = runtime.client_factory(region)
                region_plan = cleaner.plan_region(client, region, runtime.clock())
                cleaner.apply_region(
                    client, region_plan.candidates, self.deleted_keypairs, dry_run=self.dry_run
                )
            except CONNECTIVITY_ERRORS as e:
                _skip_region(region, e)
        _log_summary(self.deleted_keypairs, dry_run=self.dry_run)
