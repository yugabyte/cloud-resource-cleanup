# Copyright (c) Yugabyte, Inc.

"""AWS EBS snapshot cleaner.

`SnapshotCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the
snapshots owned by the account (`describe_snapshots`, `describe_images`) and filters them,
`apply()` calls `delete_snapshot`. `Snapshot` is the legacy class `crc.py` uses; it keeps its
constructor and attributes and runs the cleaner region by region, snapshot by snapshot, in
the exact call order of the original implementation.
"""

import datetime
import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol

import boto3

from crc.aws import _regionlog
from crc.aws._base import get_all_regions
from crc.aws.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
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
from crc.service import Service

_SERVICE_NAME = "ec2"
_DEFAULT_REGION = "us-west-2"
_SKIP_CONTEXT = "snapshot cleanup"
_DEFAULT_AGE_DAYS = 90


class SnapshotClient(Protocol):
    """The three EC2 calls this cleaner uses."""

    def describe_snapshots(self, *, OwnerIds: Sequence[str]) -> Mapping[str, Any]:
        """List the snapshots of the given owners in the client's region."""
        ...

    def describe_images(self, *, Owners: Sequence[str]) -> Mapping[str, Any]:
        """List the AMIs of the given owners in the client's region."""
        ...

    def delete_snapshot(self, *, SnapshotId: str) -> Mapping[str, Any]:
        """Delete one snapshot by id."""
        ...


ClientFactory = Callable[[str], SnapshotClient]
RegionLister = Callable[[], Sequence[str]]
Clock = Callable[[], datetime.datetime]
Decision = Candidate | Skipped


def _boto3_client(region: str) -> SnapshotClient:
    client: SnapshotClient = boto3.client(_SERVICE_NAME, region_name=region)
    return client


def _all_regions() -> Sequence[str]:
    return get_all_regions(_SERVICE_NAME, _DEFAULT_REGION)


_UTC_CLOCK: Clock = partial(datetime.datetime.now, datetime.UTC)


@dataclass(frozen=True, slots=True)
class SnapshotRuntime:
    """Injected collaborators of `Snapshot`: the SDK client factory, the region lister and the
    clock that supplies `now` (the cleaner itself never reads the clock)."""

    client_factory: ClientFactory = _boto3_client
    list_regions: RegionLister = _all_regions
    clock: Clock = _UTC_CLOCK


def _skip_region(region: str, error: BaseException) -> None:
    _regionlog.region_skipped(region, error)
    log_skipped_region(region, _SKIP_CONTEXT, error)


def _is_protected(tags: Any, snap_id: str, exception_tags: Any) -> bool:
    """Any exception tag key present (with a listed value, when values are given) protects.
    Every tag entry must have `Key` and `Value`, else `KeyError` as on main."""
    if not tags or not exception_tags:
        return False
    tag_map = {t["Key"]: t["Value"] for t in tags}
    for key, values in exception_tags.items():
        if policy.exception_tags_any(tag_map, {key: values}):
            logging.debug(f"Snap {snap_id} has tag {key}")
            return True
    return False


def _used_by_amis(client: SnapshotClient) -> set[str]:
    used: set[str] = set()
    for image in client.describe_images(Owners=["self"])["Images"]:
        for mapping in image.get("BlockDeviceMappings", []):
            snap_id = mapping.get("Ebs", {}).get("SnapshotId")
            if snap_id:
                used.add(snap_id)
    return used


class SnapshotCleaner:
    """Deletes snapshots owned by the account that are at least `age_days` old, carry no
    exception tag and back no AMI. Applies to every region the injected lister returns."""

    cloud = "aws"
    kind = "snapshot"

    def __init__(
        self,
        client_factory: ClientFactory,
        list_regions: RegionLister,
        age_days: Any,
        exception_tags: Any,
    ) -> None:
        self._client_factory = client_factory
        self._list_regions = list_regions
        self._age_days = age_days
        self._exception_tags = exception_tags
        self._clients: dict[str, SnapshotClient] = {}

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def check_age(self, now: datetime.datetime) -> None:
        """Compute the cutoff before any call, as main does: an invalid `age_days` fails here."""
        _ = now - datetime.timedelta(days=self._age_days)

    def plan(self, context: RunContext) -> Plan:
        """List every region and filter. Makes no mutating call."""
        self.check_age(context.now)
        candidates: list[Candidate] = []
        skipped: list[Skipped] = []
        for region in self._list_regions():
            started = _regionlog.start()
            try:
                client = self._client_factory(region)
                decisions = list(self.iter_region(client, region, context.now))
            except CONNECTIVITY_ERRORS as e:
                _skip_region(region, e)
                continue
            self._clients[region] = client
            selected = 0
            for decision in decisions:
                if isinstance(decision, Candidate):
                    candidates.append(decision)
                    selected += 1
                else:
                    skipped.append(decision)
            _regionlog.region_done(region, len(decisions), selected, started)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned snapshots region by region (nothing in a dry run). A failed
        delete is logged and kept out of the result, as on main."""
        by_region: dict[str, list[Candidate]] = {}
        for candidate in plan.candidates:
            by_region.setdefault(candidate.region, []).append(candidate)
        deleted: list[str] = []
        failed: list[Failure] = []
        for region, items in by_region.items():
            try:
                client = self._clients.get(region) or self._client_factory(region)
            except CONNECTIVITY_ERRORS as e:
                _skip_region(region, e)
                continue
            for candidate in items:
                failure = self.delete_one(
                    client, candidate.id, dry_run=context.dry_run, region=region
                )
                if failure is None:
                    deleted.append(candidate.id)
                else:
                    failed.append(failure)
        return Result(succeeded=tuple(deleted), failed=tuple(failed))

    def iter_region(
        self, client: SnapshotClient, region: str, now: datetime.datetime
    ) -> Iterator[Decision]:
        """List one region, then decide snapshot by snapshot (lazily, so a caller that
        deletes as it goes interleaves calls and logs exactly like main)."""
        snapshots = client.describe_snapshots(OwnerIds=["self"])["Snapshots"]
        used_by_amis = _used_by_amis(client)
        for snap in snapshots:
            yield self._decide(snap, region, now, used_by_amis)

    def delete_one(
        self, client: SnapshotClient, snap_id: str, *, dry_run: bool, region: str | None = None
    ) -> Failure | None:
        """Delete one snapshot (nothing in a dry run). Any error is logged and returned."""
        run = logger.current()
        if dry_run:
            run.action("would_delete", region, snap_id)
            return None
        try:
            client.delete_snapshot(SnapshotId=snap_id)
            logging.info(f"Deleted snapshot {snap_id}")
        except Exception as e:
            logging.error(f"Failed to delete snapshot {snap_id}: {e}")
            run.fail(region, snap_id, e)
            return Failure(id=snap_id, error=str(e))
        run.action("deleted", region, snap_id)
        return None

    def is_protected(self, tags: Any, snap_id: str) -> bool:
        """Whether an exception tag protects the snapshot (logs the matching key)."""
        return _is_protected(tags, snap_id, self._exception_tags)

    def _decide(
        self, snap: Mapping[str, Any], region: str, now: datetime.datetime, used: set[str]
    ) -> Decision:
        snap_id = snap["SnapshotId"]
        start_time = snap["StartTime"]
        if not policy.snapshot_old_enough(start_time, now, self._age_days):
            logger.current().skip("too_young")
            return Skipped(id=snap_id, reason="newer than age_days")
        if self.is_protected(snap.get("Tags"), snap_id):
            logger.current().skip("exception_tag")
            return Skipped(id=snap_id, reason="has an exception tag")
        if snap_id in used:
            logging.debug(f"Skipping snapshot {snap_id}, used by AMI")
            logger.current().skip("used_by_ami")
            return Skipped(id=snap_id, reason="used by AMI")
        return Candidate(id=snap_id, region=region, launch_time=start_time)


@register("aws", "snapshot")
def build_snapshot_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `age_days` (default 90, the value `crc.py` passes) and
    `exception_tags` (dict of key to values)."""
    options = context.options
    return SnapshotCleaner(
        client_factory=_boto3_client,
        list_regions=_all_regions,
        age_days=options.get("age_days", _DEFAULT_AGE_DAYS),
        exception_tags=options.get("exception_tags"),
    )


class Snapshot(Service):
    """Legacy entry point used by `crc.py`: deletes snapshots at least `age_days` old that
    have no exception tag and are not used by an AMI. In dry run it only lists them.

    Attributes: `deleted_snapshots` (ids deleted, or listed in a dry run), `dry_run`,
    `age_days`, `exception_tags`. `get_deleted` and `delete_count` read the result.
    """

    service_name = _SERVICE_NAME

    def __init__(
        self,
        dry_run: bool,
        age_days: Any,
        exception_tags: Any,
        runtime: SnapshotRuntime | None = None,
    ) -> None:
        super().__init__()
        self.dry_run = dry_run
        self.age_days = age_days
        self.exception_tags = exception_tags
        self.deleted_snapshots: list[str] = []
        self._runtime = runtime or SnapshotRuntime()

    @property
    def get_deleted(self) -> list[str]:
        """Ids of the snapshots deleted (listed in a dry run)."""
        return self.deleted_snapshots

    @property
    def delete_count(self) -> int:
        """Number of snapshots deleted (listed in a dry run)."""
        return len(self.deleted_snapshots)

    def _cleaner(self) -> SnapshotCleaner:
        runtime = self._runtime
        return SnapshotCleaner(
            runtime.client_factory, runtime.list_regions, self.age_days, self.exception_tags
        )

    def _is_protected(self, tags: Any, snap_id: str) -> bool:
        return _is_protected(tags, snap_id, self.exception_tags)

    def _get_snapshots_used_by_amis(self, ec2: SnapshotClient) -> set[str]:
        return _used_by_amis(ec2)

    def delete(self) -> None:
        """Delete (or, in a dry run, only list) the matching snapshots of every region."""
        runtime = self._runtime
        cleaner = self._cleaner()
        now = runtime.clock()
        cleaner.check_age(now)
        for region in runtime.list_regions():
            started = _regionlog.start()
            try:
                client = runtime.client_factory(region)
                scanned = selected = 0
                for decision in cleaner.iter_region(client, region, now):
                    scanned += 1
                    if not isinstance(decision, Candidate):
                        continue
                    selected += 1
                    failure = cleaner.delete_one(
                        client, decision.id, dry_run=self.dry_run, region=region
                    )
                    if failure is None:
                        self.deleted_snapshots.append(decision.id)
                _regionlog.region_done(region, scanned, selected, started)
            except CONNECTIVITY_ERRORS as e:
                _skip_region(region, e)
