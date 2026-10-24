# Copyright (c) Yugabyte, Inc.

"""AWS EBS snapshot cleaner.

`SnapshotCleaner` implements the `crc.core.cleaner.Cleaner` contract: `plan()` lists the
snapshots owned by the account (`describe_snapshots`, `describe_images`) and filters them,
`apply()` calls `delete_snapshot`.
"""

import datetime
import logging
from collections.abc import Iterator, Mapping
from typing import Any

from crc.aws import _regionlog
from crc.aws._common import ec2_regions, group_by_region, skip_region
from crc.aws.connectivity import CONNECTIVITY_ERRORS
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

from .client import ClientFactory, RegionLister, SnapshotClient, boto3_client
from .protection import is_protected, is_tag_list, read_tags, used_by_amis

SKIP_CONTEXT = "snapshot cleanup"
_DEFAULT_AGE_DAYS = 90
Decision = Candidate | Skipped


def skip_failed_region(region: str, error: BaseException) -> None:
    """BUG-149: a non-connectivity error while listing a region is recorded and the region
    skipped; the other regions continue."""
    _regionlog.region_skipped(region, error)
    logging.error(
        "Region %s: skipped %s — error while listing (%s): %s",
        region,
        SKIP_CONTEXT,
        type(error).__name__,
        error,
    )
    logger.current().fail(region, "-", error)


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
                skip_region(region, SKIP_CONTEXT, e)
                continue
            except Exception as e:  # noqa: BLE001 - per-region isolation, region skipped and logged
                skip_failed_region(region, e)
                continue
            self._clients[region] = client
            selected = 0
            for decision in decisions:
                if isinstance(decision, Candidate):
                    if context.dry_run:
                        logger.current().action("would_delete", region, decision.id)
                    candidates.append(decision)
                    selected += 1
                else:
                    skipped.append(decision)
            _regionlog.region_done(region, len(decisions), selected, started)
        return Plan(candidates=tuple(candidates), skipped=tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned snapshots region by region (nothing in a dry run). A failed
        delete is logged and kept out of the result, as on main."""
        if context.dry_run:
            return Result(succeeded=tuple(c.id for c in plan.candidates))
        deleted: list[str] = []
        failed: list[Failure] = []
        for region, items in group_by_region(plan.candidates).items():
            try:
                client = self._clients.get(region) or self._client_factory(region)
            except CONNECTIVITY_ERRORS as e:
                skip_region(region, SKIP_CONTEXT, e)
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
        used = used_by_amis(client)
        for snap in snapshots:
            yield self._decide(snap, region, now, used)

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
            logging.info("Deleted snapshot %s", snap_id)
        except Exception as e:  # noqa: BLE001 - per-resource isolation, failure logged and recorded, resource kept
            logging.error("Failed to delete snapshot %s: %s", snap_id, e)
            run.fail(region, snap_id, e)
            return Failure(id=snap_id, error=str(e))
        run.action("deleted", region, snap_id)
        return None

    def is_protected(self, tags: Any, snap_id: str, tag_map: dict[str, Any] | None = None) -> bool:
        """Whether an exception tag protects the snapshot (logs the matching key)."""
        return is_protected(tags, snap_id, self._exception_tags, tag_map)

    def _decide(
        self, snap: Mapping[str, Any], region: str, now: datetime.datetime, used: set[str]
    ) -> Decision:
        snap_id = snap["SnapshotId"]
        start_time = snap["StartTime"]
        tags = snap.get("Tags")
        # Tags that are not a list of mappings are read only after the age gate, so they
        # raise for old snapshots only, as on main.
        tag_map = read_tags(tags) if self._exception_tags and is_tag_list(tags) else None
        if not policy.snapshot_old_enough(start_time, now, self._age_days):
            logger.current().skip("too_young")
            return Skipped(id=snap_id, reason="newer than age_days")
        if self.is_protected(tags, snap_id, tag_map):
            logger.current().skip("exception_tag")
            return Skipped(id=snap_id, reason="has an exception tag")
        if snap_id in used:
            logging.debug("Skipping snapshot %s, used by AMI", snap_id)
            logger.current().skip("used_by_ami")
            return Skipped(id=snap_id, reason="used by AMI")
        return Candidate(id=snap_id, region=region, launch_time=start_time)


@register("aws", "snapshot")
def build_snapshot_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: `age_days` (default 90, the value `crc.py` passes) and
    `exception_tags` (dict of key to values)."""
    options = context.options
    return SnapshotCleaner(
        client_factory=boto3_client,
        list_regions=ec2_regions,
        age_days=options.get("age_days", _DEFAULT_AGE_DAYS),
        exception_tags=options.get("exception_tags"),
    )
