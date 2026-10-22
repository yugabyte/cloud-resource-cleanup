# Copyright (c) Yugabyte, Inc.

"""AWS EBS disk cleaner: `plan()` lists and filters, `apply()` deletes volumes.

`DiskCleaner` implements the `crc.core.cleaner.Cleaner` contract for available (unattached)
EBS volumes. Region by region `plan_region()` lists, filters and applies the CloudWatch
detach-age guard (read-only), `note_dry_run()` records the dry-run candidates and
`apply_region()` calls `delete_volume`. `Disk` (the package `__init__`) drives the same steps
region by region in the call and log order of `main`.
"""

import datetime
import logging
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

from botocore.exceptions import ClientError

from crc.aws.connectivity import CONNECTIVITY_ERRORS, log_skipped_region
from crc.core import logger, policy
from crc.core.cleaner import Candidate, Cleaner, Failure, Operation, Plan, Result, RunContext
from crc.core.registry import register

from .client import (
    CloudWatchFactory,
    Ec2Client,
    Ec2Factory,
    RegionLister,
    all_regions,
    boto3_cloudwatch,
    boto3_ec2,
)
from .detach import DetachGuard
from .settings import DiskSettings, build_settings
from .volumes import Volume, VolumeFilter, server_filters, volume_label

_SKIP_CONTEXT = "EBS disk cleanup"
_UNAUTHORISED_CODES = {
    "AuthFailure",
    "UnauthorizedOperation",
    "OptInRequired",
    "InvalidClientTokenId",
}
_SKIP_FAILED = "Region %s: skipping volume %s after error: %s"
_CONSIDER_FAILED = "Region %s: failed considering volume %s: %s"


@dataclass(frozen=True, slots=True)
class DiskRuntime:
    """Injected collaborators: the SDK client factories and the region lister."""

    ec2_factory: Ec2Factory = boto3_ec2
    cloudwatch_factory: CloudWatchFactory = boto3_cloudwatch
    list_regions: RegionLister = all_regions


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
        for region in self._runtime.list_regions():
            found = self.plan_region(region, context.now)
            if context.dry_run:
                self.note_dry_run(region, found)
            candidates.extend(found)
        if context.dry_run:
            self.log_summary([c.id for c in candidates], dry_run=True)
        return Plan(candidates=tuple(candidates))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        """Delete the planned volumes region by region (nothing in a dry run). A failed
        delete is logged and kept out of the succeeded list, as on `main`."""
        if context.dry_run:
            return Result()
        by_region: dict[str, list[Candidate]] = {}
        for candidate in plan.candidates:
            by_region.setdefault(candidate.region, []).append(candidate)
        deleted: list[str] = []
        failed: list[Failure] = []
        for region, items in by_region.items():
            done, errors = self.apply_region(region, items)
            deleted.extend(done)
            failed.extend(errors)
        self.log_summary(deleted, dry_run=False)
        return Result(succeeded=tuple(deleted), failed=tuple(failed))

    @contextmanager
    def region_guard(self, region: str) -> Iterator[None]:
        """Region level error policy of `main`: connectivity and unauthorised errors skip the
        region, any other error is logged and flagged; nothing propagates."""
        try:
            yield
        except CONNECTIVITY_ERRORS as e:
            log_skipped_region(region, _SKIP_CONTEXT, e)
            logger.current().region_skipped(region, e)
        except ClientError as e:
            code = (e.response.get("Error") or {}).get("Code")
            if code in _UNAUTHORISED_CODES:
                logging.warning("Region %s: skipped EBS disk cleanup (%s)", region, code)
                logger.current().region_skipped(region, str(code))
                return
            self.had_errors = True
            logging.error("Region %s: ClientError during EBS disk cleanup: %s", region, e)
            logger.current().fail(region, "-", e)
        except Exception as e:
            self.had_errors = True
            logging.error("Region %s: error during EBS disk cleanup: %s", region, e)
            logger.current().fail(region, "-", e)

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
                except Exception as e:
                    self._volume_error(_CONSIDER_FAILED, region, candidate.extra.get("VolumeId"), e)
                    continue
                if failure is None:
                    deleted.append(candidate.id)
                else:
                    failed.append(failure)
        return deleted, failed

    def log_summary(self, labels: Sequence[str], *, dry_run: bool) -> None:
        """The summary lines `main` logs once all regions are done."""
        if not dry_run:
            logging.warning("number of AWS EBS volumes deleted: %s", len(labels))
            logging.warning("List of AWS EBS volumes deleted: %s", list(labels))
        else:
            logging.warning(
                "List of AWS EBS volumes (Total: %s) which will be deleted: %s",
                len(labels),
                list(labels),
            )
        if self.had_errors:
            # Log only: raising after the region loop would exit non-zero and fail the Jenkins
            # build without undoing work already done.
            logging.error(
                "AWS EBS disk cleanup did not complete for every volume; "
                "see errors above. Deleted/dry-run list may be incomplete "
                "(count=%s).",
                len(labels),
            )

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
                except Exception as e:
                    self._volume_error(_SKIP_FAILED, region, volume.get("VolumeId"), e)
        return found

    def _volume_error(self, template: str, region: str, volume_id: Any, e: Exception) -> None:
        self.had_errors = True
        logging.error(template, region, volume_id, e)
        logger.current().fail(region, str(volume_id), e)

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
        except Exception as e:
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
            logger.current().fail(candidate.region, volume_id, e)
            self.had_errors = True
            return Failure(id=str(volume_id), error=str(e))
        return None


@register("aws", "disk")
def build_disk_cleaner(context: RunContext) -> Cleaner:
    """Registry factory. Options: filter_tags, exception_tags, notags, name_regex,
    exception_regex, custom_age_tag_key, age and detach_age (as `crc.py` passes them)."""
    return DiskCleaner(build_settings(context.options), DiskRuntime())
