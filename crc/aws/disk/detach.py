# Copyright (c) Yugabyte, Inc.

"""CloudWatch detach-age guard: drop volumes with recent AWS/EBS activity.

AWS/EBS metrics are published only while a volume is attached to a *running* instance, so an
empty Complete series means "no running-instance attachment activity in the window", not a
proof of a long detach. Incomplete answers fail closed: the volume is kept.
"""

import datetime
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from crc.core import logger

from .client import CLOUDWATCH_ERRORS, CloudWatchClient, CloudWatchFactory

EBS_METRICS = ("VolumeIdleTime", "VolumeReadOps", "VolumeWriteOps")
CW_BATCH_SIZE = 50

Volume = Mapping[str, Any]


def cloudwatch_period(window: datetime.timedelta) -> int:
    """Metric period in seconds: the window rounded down to whole minutes, at least 60."""
    seconds = max(60, int(window.total_seconds()))
    return seconds - (seconds % 60) or 60


def _query(index: int, metric: str, volume_id: str, period: int) -> dict[str, Any]:
    return {
        "Id": f"m{index}{metric.replace('Volume', '').lower()[:8]}",
        "MetricStat": {
            "Metric": {
                "Namespace": "AWS/EBS",
                "MetricName": metric,
                "Dimensions": [{"Name": "VolumeId", "Value": volume_id}],
            },
            "Period": period,
            "Stat": "Sum",
        },
    }


def build_queries(
    batch: Sequence[Volume], period: int
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Metric queries of a batch and the query ids of each volume."""
    queries: list[dict[str, Any]] = []
    ids_by_volume: dict[str, list[str]] = {}
    for i, volume in enumerate(batch):
        ids: list[str] = []
        for metric in EBS_METRICS:
            query = _query(i, metric, volume["VolumeId"], period)
            ids.append(query["Id"])
            queries.append(query)
        ids_by_volume[volume["VolumeId"]] = ids
    return queries, ids_by_volume


def get_metric_data(
    cloudwatch: CloudWatchClient,
    queries: list[dict[str, Any]],
    start: datetime.datetime,
    end: datetime.datetime,
) -> list[Mapping[str, Any]]:
    """All `MetricDataResults` pages of one batch."""
    results: list[Mapping[str, Any]] = []
    next_token = None
    while True:
        kwargs: dict[str, Any] = {
            "MetricDataQueries": queries,
            "StartTime": start,
            "EndTime": end,
            "ScanBy": "TimestampDescending",
        }
        if next_token:
            kwargs["NextToken"] = next_token
        response = cloudwatch.get_metric_data(**kwargs)
        results.extend(response.get("MetricDataResults") or [])
        next_token = response.get("NextToken")
        if not next_token:
            return results


def merge_metric_results(results: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merge GetMetricData pages per query Id: concatenate Values across pages and take the
    StatusCode of the last page (non-final pages are PartialData, the final one Complete)."""
    merged: dict[str, dict[str, Any]] = {}
    for result in results:
        query_id = result.get("Id")
        if not query_id:
            continue
        status = result.get("StatusCode") or "Complete"
        values = list(result.get("Values") or [])
        existing = merged.get(query_id)
        if not existing:
            merged[query_id] = {"Id": query_id, "StatusCode": status, "Values": values}
            continue
        existing["Values"].extend(values)
        existing["StatusCode"] = status
    return merged


# BUG-283 (wontfix): CloudWatch cannot see attachments to stopped instances; the CreateTime floor in
# crc/aws/disk/volumes.py `_detach_floor` is the only guard (test environments, owner decision).
def volume_recently_attached(
    query_ids: Sequence[str], results_by_id: Mapping[str, Mapping[str, Any]]
) -> tuple[bool, str | None]:
    """`(skip_volume, reason)`: True means do not delete."""
    for query_id in query_ids:
        result = results_by_id.get(query_id)
        if not result:
            return True, "missing CloudWatch result"
        status = result.get("StatusCode") or "Complete"
        if status != "Complete":
            return True, f"CloudWatch StatusCode={status}"
        if result.get("Values"):
            return True, "attached recently"
    return False, None


class DetachGuard:
    """Drops volumes with AWS/EBS datapoints inside the detach window or an incomplete answer."""

    def __init__(self, factory: CloudWatchFactory, on_error: Callable[[], None]) -> None:
        self._factory = factory
        self._on_error = on_error

    def drop_recently_attached(
        self,
        region: str,
        volumes: list[Volume],
        window: datetime.timedelta,
        end: datetime.datetime,
    ) -> list[Volume]:
        """The volumes without activity in `window` ending at `end` (CloudWatch calls only)."""
        if not window:
            # Return value is "candidates to delete". Empty = delete nothing.
            logging.warning(
                "Region %s: detach_age window is zero, not deleting any of %s volume(s)",
                region,
                len(volumes),
            )
            for _ in volumes:
                logger.current().skip("detach_age_zero")
            return []
        start = end - window
        period = cloudwatch_period(window)
        cloudwatch = self._factory(region)
        kept: list[Volume] = []
        for index in range(0, len(volumes), CW_BATCH_SIZE):
            batch = volumes[index : index + CW_BATCH_SIZE]
            kept.extend(self._check_batch(cloudwatch, region, batch, (start, end, period), window))
        return kept

    def _check_batch(
        self,
        cloudwatch: CloudWatchClient,
        region: str,
        batch: list[Volume],
        span: tuple[datetime.datetime, datetime.datetime, int],
        window: datetime.timedelta,
    ) -> list[Volume]:
        start, end, period = span
        queries, ids_by_volume = build_queries(batch, period)
        try:
            results = get_metric_data(cloudwatch, queries, start, end)
        except CLOUDWATCH_ERRORS as e:
            self._on_error()
            logging.warning(
                "Region %s: CloudWatch detach-age lookup failed, skipping %s volume(s): %s",
                region,
                len(batch),
                e,
            )
            logger.current().fail(region, "-", e)
            return []
        results_by_id = merge_metric_results(results)
        kept: list[Volume] = []
        for volume in batch:
            skip, reason = volume_recently_attached(
                ids_by_volume[volume["VolumeId"]], results_by_id
            )
            if skip:
                self._log_skip(volume["VolumeId"], reason, window)
            else:
                kept.append(volume)
        return kept

    def _log_skip(self, volume_id: str, reason: str | None, window: datetime.timedelta) -> None:
        if reason and "StatusCode=" in reason:
            self._on_error()
            logging.warning(
                "Skipping volume %s: %s (detach_age window %s)", volume_id, reason, window
            )
            logger.current().skip("cloudwatch_incomplete")
        else:
            logging.debug(
                "Skipping volume %s: %s (detach_age window %s)", volume_id, reason, window
            )
            logger.current().skip("recently_attached")
