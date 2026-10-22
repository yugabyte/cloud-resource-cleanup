"""Unit tests of `DiskCleaner` plan() / apply() with fake EC2 and CloudWatch clients."""

import datetime
import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from crc.aws.disk import Disk, DiskCleaner, DiskRuntime
from crc.aws.disk.settings import build_settings
from crc.core.cleaner import Operation, RunContext

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
ONE = 1


class FakeCloudWatch:
    def __init__(self, busy: Sequence[str] = ()) -> None:
        self.busy = busy
        self.calls: list[dict[str, Any]] = []

    def get_metric_data(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        results = []
        for query in kwargs["MetricDataQueries"]:
            volume_id = query["MetricStat"]["Metric"]["Dimensions"][0]["Value"]
            values = [1.0] if volume_id in self.busy else []
            results.append({"Id": query["Id"], "StatusCode": "Complete", "Values": values})
        return {"MetricDataResults": results}


class FakePaginator:
    def __init__(self, volumes: list[dict[str, Any]]) -> None:
        self.volumes = volumes
        self.filters: list[Any] = []

    def paginate(self, *, Filters: Sequence[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
        self.filters.append(Filters)
        return [{"Volumes": self.volumes}]


class FakeEc2:
    def __init__(
        self, volumes: list[dict[str, Any]], errors: Mapping[str, Exception] | None = None
    ) -> None:
        self.paginator = FakePaginator(volumes)
        self.errors = errors or {}
        self.calls: list[tuple[str, str]] = []

    def get_paginator(self, operation_name: str) -> FakePaginator:
        self.calls.append(("get_paginator", operation_name))
        return self.paginator

    def delete_volume(self, *, VolumeId: str) -> Mapping[str, Any]:
        self.calls.append(("delete_volume", VolumeId))
        if VolumeId in self.errors:
            raise self.errors[VolumeId]
        return {}


def volume(volume_id: str, days: float = 30, **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "VolumeId": volume_id,
        "State": "available",
        "Attachments": [],
        "CreateTime": NOW - datetime.timedelta(days=days),
        "Tags": [{"Key": "yb_task", "Value": "itest"}],
    }
    item.update(extra)
    return item


def build(
    ec2: FakeEc2,
    cloudwatch: FakeCloudWatch | None = None,
    regions: Sequence[str] = ("us-west-2",),
    **options: Any,
) -> DiskCleaner:
    settings = build_settings({"age": {"days": 1}, **options})
    runtime = DiskRuntime(
        ec2_factory=lambda region: ec2,
        cloudwatch_factory=lambda region: cloudwatch or FakeCloudWatch(),
        list_regions=lambda: regions,
    )
    return DiskCleaner(settings, runtime)


def context(*, dry_run: bool) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def test_plan_lists_and_filters_without_mutating() -> None:
    ec2 = FakeEc2(
        [
            volume("old"),
            volume("young", 0.1),
            volume("prod", Tags=[{"Key": "yb_task", "Value": "prod"}]),
        ]
    )
    plan = build(ec2).plan(context(dry_run=False))
    assert [c.id for c in plan.candidates] == ["us-west-2/old yb_task=itest"]
    assert plan.candidates[0].extra["VolumeId"] == "old"
    assert ec2.calls == [("get_paginator", "describe_volumes")]


def test_plan_sends_the_server_side_filters() -> None:
    ec2 = FakeEc2([])
    build(ec2, filter_tags={"yb_task": ["itest"], "yb_owner": []}).plan(context(dry_run=False))
    assert ec2.paginator.filters == [
        [
            {"Name": "status", "Values": ["available"]},
            {"Name": "tag:yb_task", "Values": ["itest"]},
            {"Name": "tag-key", "Values": ["yb_owner"]},
        ]
    ]


def test_plan_in_a_dry_run_logs_the_candidates_and_never_deletes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    ec2 = FakeEc2([volume("a"), volume("b")])
    cleaner = build(ec2)
    plan = cleaner.plan(context(dry_run=True))
    assert len(plan.candidates) == ONE + ONE
    assert not any(call[0] == "delete_volume" for call in ec2.calls)
    messages = [r.getMessage() for r in caplog.records]
    assert "Dry run: would delete available volume us-west-2/a yb_task=itest" in messages
    assert any("which will be deleted" in m for m in messages)
    assert cleaner.apply(plan, context(dry_run=True)).succeeded == ()
    assert not any(call[0] == "delete_volume" for call in ec2.calls)


def test_apply_deletes_in_plan_order_and_reports_labels() -> None:
    ec2 = FakeEc2([volume("a"), volume("b")])
    cleaner = build(ec2)
    plan = cleaner.plan(context(dry_run=False))
    result = cleaner.apply(plan, context(dry_run=False))
    assert result.succeeded == ("us-west-2/a yb_task=itest", "us-west-2/b yb_task=itest")
    assert [c for c in ec2.calls if c[0] == "delete_volume"] == [
        ("delete_volume", "a"),
        ("delete_volume", "b"),
    ]


def test_apply_isolates_a_failed_delete(caplog: pytest.LogCaptureFixture) -> None:
    error = ClientError({"Error": {"Code": "VolumeInUse", "Message": "m"}}, "DeleteVolume")
    ec2 = FakeEc2([volume("a"), volume("b")], {"a": error})
    cleaner = build(ec2)
    result = cleaner.apply(cleaner.plan(context(dry_run=False)), context(dry_run=False))
    assert result.succeeded == ("us-west-2/b yb_task=itest",)
    assert [f.id for f in result.failed] == ["a"]
    assert cleaner.had_errors
    assert any("Failed to delete volume a (VolumeInUse)" in r.getMessage() for r in caplog.records)


def test_detach_age_drops_busy_volumes_and_keeps_the_rest() -> None:
    ec2 = FakeEc2([volume("idle"), volume("busy")])
    cloudwatch = FakeCloudWatch(busy=["busy"])
    cleaner = build(ec2, cloudwatch, detach_age={"days": 3})
    plan = cleaner.plan(context(dry_run=False))
    assert [c.extra["VolumeId"] for c in plan.candidates] == ["idle"]
    call = cloudwatch.calls[0]
    assert call["EndTime"] == NOW
    assert call["StartTime"] == NOW - datetime.timedelta(days=3)
    assert call["ScanBy"] == "TimestampDescending"


def test_connectivity_error_skips_the_region(caplog: pytest.LogCaptureFixture) -> None:
    class Broken(FakeEc2):
        def get_paginator(self, operation_name: str) -> FakePaginator:
            raise EndpointConnectionError(endpoint_url="https://ec2.example")

    cleaner = build(Broken([]))
    assert cleaner.plan(context(dry_run=False)).candidates == ()
    assert not cleaner.had_errors
    assert any("skipped EBS disk cleanup" in r.getMessage() for r in caplog.records)


def test_supports_delete_only() -> None:
    cleaner = build(FakeEc2([]))
    assert cleaner.supports(Operation.DELETE)
    assert not cleaner.supports(Operation.STOP)


def test_adapter_keeps_the_public_surface() -> None:
    ec2 = FakeEc2([volume("a")])
    runtime = DiskRuntime(lambda region: ec2, lambda region: FakeCloudWatch(), lambda: ["r1"])
    disk = Disk(False, None, None, {"days": 1}, None, None, runtime=runtime, clock=lambda: NOW)
    disk.delete()
    assert disk.get_deleted == ["r1/a yb_task=itest"]
    assert disk.count == ONE
    assert disk.age == {"days": 1}
    assert disk.detach_age == {}
