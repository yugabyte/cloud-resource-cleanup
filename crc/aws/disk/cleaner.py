# Copyright (c) Yugabyte, Inc.

"""AWS EBS disk cleaner: `plan()` lists and filters, `apply()` deletes volumes.

`DiskCleaner` implements the `crc.core.cleaner.Cleaner` contract for available (unattached)
EBS volumes. Region by region `plan_region()` lists, filters and applies the CloudWatch
detach-age guard (read-only), `note_dry_run()` records the dry-run candidates and
`apply_region()` calls `delete_volume`.
"""

import datetime
import logging
import time
from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, cast

from botocore.exceptions import ClientError

from crc.aws._common import group_by_region
from crc.aws.connectivity import CONNECTIVITY_ERRORS
from crc.core import logger, policy
from crc.core.cleaner import Candidate, Cleaner, Failure, Operation, Plan, Result, RunContext
from crc.core.registry import register

from .client import DiskRuntime, Ec2Client
from .detach import DetachGuard
from .guard import log_summary, region_guard
from .settings import DiskSettings, build_settings
from .volumes import Volume, VolumeFilter, server_filters, volume_label

__all__ = ["DiskCleaner", "DiskRuntime", "build_disk_cleaner"]

_SKIP_FAILED = "Region %s: skipping volume %s after error: %s"
_CONSIDER_FAILED = "Region %s: failed considering volume %s: %s"


class DiskCleaner:
    """Deletes available EBS volumes that pass the tag, name and age rules of `settings`.

    Two independent age gates, at least one required: `age` is measured from `CreateTime`;
    `detach_age` skips a volume with CloudWatch activity inside that window (incomplete
    answers keep the volume). Attached and Multi-Attach volumes are never deleted.
    """

    cloud = "aws"
    kind = "disk"

    def __init__(self, settings: DiskSettings, runtime: DiskRuntime) -> None:
        self._settings = settings
        self._runtime = runtime
        self.filter = VolumeFilter(settings)
        self.had_errors = False
        self.partial_succeeded: list[str] = []
        self._scanned = 0
        self.detach_guard = DetachGuard(runtime.cloudwatch_factory, self._flag_error)
        self._clients: dict[str, Ec2Client] = {}

    def _flag_error(self) -> None:
        self.had_errors = True

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        """List and filter every region. Makes no mutating call; a dry run also records the
        candidates and logs the dry-run summary, as `main` does."""
        self.had_errors = False
        candidates: list[Candidate] = []
        self.partial_succeeded = []
        for region in self._runtime.list_regions():
            found = self.plan_region(region, context.now)
            if context.dry_run:
                self.note_dry_run(region, found)
                self.partial_succeeded.extend(c.id for c in found)
            candidates.extend(found)
        if context.dry_run:
            self.log_summary([c.id for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned volumes region by region (nothing in a dry run). A failed
        delete is logged and kept out of the succeeded list, as on `main`."""
        if context.dry_run:
            return Result()
        deleted: list[str] = []
        self.partial_succeeded = deleted
        failed: list[Failure] = []
        for region, items in group_by_region(plan.candidates).items():
            done, errors = self.apply_region(region, items)
            deleted.extend(done)
            failed.extend(errors)
        self.log_summary(deleted, dry_run=False)
        return Result(succeeded=tuple(deleted), failed=tuple(failed))

    def region_guard(self, region: str) -> AbstractContextManager[None]:
        """Region level error policy of `main` (see `guard.region_guard`)."""
        return region_guard(region, self._flag_error)

    def plan_region(self, region: str, now: datetime.datetime) -> list[Candidate]:
        """One region: list, filter, drop recently attached volumes. Read-only."""
        candidates: list[Candidate] = []
        started = time.monotonic()
        self._scanned = 0
        with self.region_guard(region):
            client = self._runtime.ec2_factory(region)
            self._clients[region] = client
            volumes = self._drop_recently_attached(
                region, self._list_candidates(client, region, now), now
            )
            candidates = [c for c in (self._to_candidate(region, v) for v in volumes) if c]
            logger.current().region(
                region, self._scanned, len(candidates), time.monotonic() - started
            )
        return candidates

    def note_dry_run(self, region: str, candidates: Sequence[Candidate]) -> None:
        """Log the volumes a dry run would delete (no call)."""
        with self.region_guard(region):
            for candidate in candidates:
                logging.info("Dry run: would delete available volume %s", candidate.id)
                logger.current().action(
                    "would_delete", region, str(candidate.extra.get("VolumeId"))
                )

    def apply_region(
        self, region: str, candidates: Sequence[Candidate]
    ) -> tuple[list[str], list[Failure]]:
        """Delete the candidates of one region; returns the deleted labels and the failures."""
        deleted: list[str] = []
        failed: list[Failure] = []
        with self.region_guard(region):
            client = self._clients.get(region) or self._runtime.ec2_factory(region)
            for candidate in candidates:
                try:
                    failure = self._delete_volume(client, candidate)
                except CONNECTIVITY_ERRORS:
                    raise
                except Exception as e:  # noqa: BLE001 - SAF-003 per-volume isolation, failure logged and recorded
                    self._volume_error(_CONSIDER_FAILED, region, candidate.extra.get("VolumeId"), e)
                    continue
                if failure is None:
                    deleted.append(candidate.id)
                else:
                    failed.append(failure)
        return deleted, failed

    def log_summary(self, labels: Sequence[str], *, dry_run: bool) -> None:
        """The summary lines `main` logs once all regions are done."""
        log_summary(labels, dry_run=dry_run, had_errors=self.had_errors)

    def _list_candidates(
        self, client: Ec2Client, region: str, now: datetime.datetime
    ) -> list[Volume]:
        found: list[Volume] = []
        paginator = client.get_paginator("describe_volumes")
        for page in paginator.paginate(Filters=server_filters(self._settings.filter_tags)):
            for volume in page.get("Volumes") or []:
                self._scanned += 1
                try:
                    if self.filter.is_candidate(volume, now):
                        found.append(volume)
                except CONNECTIVITY_ERRORS:
                    raise
                except Exception as e:  # noqa: BLE001 - SAF-003 per-volume isolation, volume skipped and recorded
                    self._volume_error(_SKIP_FAILED, region, volume.get("VolumeId"), e)
        return found

    def _volume_error(self, template: str, region: str, volume_id: Any, e: Exception) -> None:
        self.had_errors = True
        logging.error(template, region, volume_id, e)
        logger.current().fail(region, str(volume_id), e, logged=True)

    def _drop_recently_attached(
        self, region: str, volumes: list[Volume], now: datetime.datetime
    ) -> list[Volume]:
        detach_age = self._settings.detach_age
        if not detach_age or not volumes:
            return volumes
        window = policy.age_to_timedelta(detach_age)
        return self.detach_guard.drop_recently_attached(
            region, volumes, window, now.astimezone(datetime.UTC)
        )

    def _to_candidate(self, region: str, volume: Volume) -> Candidate | None:
        try:
            label = volume_label(region, volume)
        except CONNECTIVITY_ERRORS:
            raise
        except Exception as e:  # noqa: BLE001 - SAF-003 per-volume isolation, volume skipped and recorded
            self._volume_error(_CONSIDER_FAILED, region, volume.get("VolumeId"), e)
            return None
        return Candidate(
            id=label,
            region=region,
            state="available",
            launch_time=volume.get("CreateTime"),
            extra={"VolumeId": volume.get("VolumeId")},
        )

    def _delete_volume(self, client: Ec2Client, candidate: Candidate) -> Failure | None:
        volume_id = cast(str, candidate.extra.get("VolumeId"))
        try:
            client.delete_volume(VolumeId=volume_id)
            logging.info("Deleted volume %s", candidate.id)
            logger.current().action("deleted", candidate.region, volume_id)
        except ClientError as e:
            code = (e.response.get("Error") or {}).get("Code")
            logging.error("Failed to delete volume %s (%s): %s", volume_id, code, e)
            logger.current().fail(candidate.region, volume_id, e, logged=True)
            self.had_errors = True
            return Failure(id=str(volume_id), error=str(e))
        return None


@register("aws", "disk")
def build_disk_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: filter_tags, exception_tags, notags, name_regex,
    exception_regex, custom_age_tag_key, age and detach_age."""
    return DiskCleaner(build_settings(context.options), DiskRuntime())
