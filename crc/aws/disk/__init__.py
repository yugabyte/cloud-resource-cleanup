# Copyright (c) Yugabyte, Inc.

"""AWS EBS disk cleaner package.

`DiskCleaner` (`cleaner.py`) implements the `crc.core.cleaner.Cleaner` contract and is
registered as `aws` / `disk`. `Disk` is the legacy class `crc.py` uses; it keeps its
constructor, attributes and methods and drives the cleaner region by region in the exact call
and log order of the original implementation. The old import path `crc.aws.disk` keeps working.
"""

import datetime
import logging
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from typing import Any

import boto3

from crc.aws._base import get_all_regions
from crc.core import policy
from crc.service import Service

from .cleaner import DiskCleaner, DiskRuntime, build_disk_cleaner
from .client import (
    BOTO_CFG,
    DEFAULT_REGION,
    SERVICE_NAME,
    CloudWatchClient,
    Ec2Client,
)
from .detach import CW_BATCH_SIZE, EBS_METRICS
from .settings import CW_MAX_LOOKBACK, DiskSettings, build_settings, normalize_age
from .volumes import server_filters

__all__ = ["Disk", "DiskCleaner", "DiskRuntime", "build_disk_cleaner"]

Clock = Callable[[], datetime.datetime]
_UTC_CLOCK: Clock = partial(datetime.datetime.now, datetime.UTC)


def _ec2(region: str) -> Ec2Client:
    client: Ec2Client = boto3.client(SERVICE_NAME, region_name=region, config=BOTO_CFG)
    return client


def _cloudwatch(region: str) -> CloudWatchClient:
    client: CloudWatchClient = boto3.client("cloudwatch", region_name=region, config=BOTO_CFG)
    return client


def _regions() -> Sequence[str]:
    return get_all_regions(SERVICE_NAME, DEFAULT_REGION)


class Disk(Service):
    """Delete unattached (available) EBS volumes that match filter/age rules.

    Two independent age gates, at least one of which must be supplied: `age` is measured from
    `CreateTime`; `detach_age` skips a volume if AWS/EBS CloudWatch metrics show activity inside
    that window (published only while attached to a running instance). Incomplete CloudWatch
    answers fail closed. CreateTime is always applied: when `age` is omitted, `detach_age` is the
    creation floor. Attached and Multi-Attach volumes are never deleted. Infra/prod/vpn yb_task
    values and Kubernetes CSI volumes are skipped unless `filter_tags` names a kubernetes key.
    Untagged volumes are skipped unless `notags` is set.

    Attributes: `disks_to_delete` (labels deleted, or listed in a dry run), `dry_run`, the
    filters and the validated `age` / `detach_age`. `get_deleted` and `count` read the result.
    """

    service_name = SERVICE_NAME
    default_region_name = DEFAULT_REGION
    CW_MAX_LOOKBACK = CW_MAX_LOOKBACK
    CW_BATCH_SIZE = CW_BATCH_SIZE
    EBS_METRICS = EBS_METRICS
    SENSITIVE_YB_TASK = policy.SENSITIVE_YB_TASK

    def __init__(  # noqa: PLR0913, PLR0917 - the nine parameters are the public signature
        self,
        dry_run: bool,
        filter_tags: dict[str, list[str]] | None,
        exception_tags: dict[str, list[str]] | None,
        age: Any,
        custom_age_tag_key: Any,
        notags: dict[str, list[str]] | None,
        name_regex: list[str] | None = None,
        exception_regex: list[str] | None = None,
        detach_age: Any = None,
        runtime: DiskRuntime | None = None,
        clock: Clock | None = None,
    ) -> None:
        super().__init__()
        self.disks_to_delete: list[str] = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags or {}
        self.exception_tags = exception_tags or {}
        self.custom_age_tag_key = custom_age_tag_key
        self.notags = notags or {}
        self.name_regex = name_regex or []
        self.exception_regex = exception_regex or []
        self._clock = clock or _UTC_CLOCK
        self._settings: DiskSettings = build_settings(
            {
                "filter_tags": filter_tags,
                "exception_tags": exception_tags,
                "custom_age_tag_key": custom_age_tag_key,
                "notags": notags,
                "name_regex": name_regex,
                "exception_regex": exception_regex,
                "age": age,
                "detach_age": detach_age,
            }
        )
        self.age = self._settings.age
        self.detach_age = self._settings.detach_age
        self._runtime = runtime or DiskRuntime(_ec2, _cloudwatch, _regions)
        self._cleaner = DiskCleaner(self._settings, self._runtime)

    @property
    def _had_errors(self) -> bool:
        return self._cleaner.had_errors

    @property
    def get_deleted(self) -> list[str]:
        """Labels of the volumes deleted (listed in a dry run)."""
        return self.disks_to_delete

    @property
    def count(self) -> int:
        """Number of volumes deleted (listed in a dry run); logs it."""
        count = len(self.disks_to_delete)
        logging.info(f"count of items in disks_to_delete: {count}")
        return count

    def _normalize_age(self, age: Any, label: str) -> dict[str, int]:
        return normalize_age(age, label)

    def _age_to_timedelta(self, age: Any) -> datetime.timedelta:
        return policy.age_to_timedelta(age)

    def _matches_filter_tags(self, tag_map: dict[str, str]) -> bool:
        return self._cleaner.filter.matches_filter_tags(tag_map)

    def _should_skip(self, tag_map: dict[str, str], name: Any, volume_id: Any) -> bool:
        return self._cleaner.filter.should_skip(tag_map, name, volume_id)

    def _volume_filters(self) -> list[dict[str, Any]]:
        return server_filters(self.filter_tags)

    def _is_candidate(self, volume: Mapping[str, Any]) -> bool:
        """Tag, name and creation-age checks. Detach age is applied separately."""
        return self._cleaner.filter.is_candidate(volume, self._clock())

    def _drop_recently_attached(
        self, region: str, volumes: list[Mapping[str, Any]]
    ) -> list[Mapping[str, Any]]:
        """Drop volumes with AWS/EBS datapoints inside the detach_age window, or whose
        CloudWatch answer is incomplete."""
        if not self.detach_age or not volumes:
            return volumes
        window = self._age_to_timedelta(self.detach_age)
        return self._cleaner.detach_guard.drop_recently_attached(
            region, volumes, window, self._clock()
        )

    def delete(self) -> None:
        """Delete available EBS volumes matching filter_tags / age / detach_age.
        dry_run only records candidates."""
        cleaner = self._cleaner
        cleaner.had_errors = False
        for region in self._runtime.list_regions():
            candidates = cleaner.plan_region(region, self._clock())
            if self.dry_run:
                cleaner.note_dry_run(region, candidates)
                self.disks_to_delete.extend(c.id for c in candidates)
            else:
                deleted, _ = cleaner.apply_region(region, candidates)
                self.disks_to_delete.extend(deleted)
        cleaner.log_summary(self.disks_to_delete, dry_run=self.dry_run)
