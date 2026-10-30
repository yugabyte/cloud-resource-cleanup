"""BUG-282 (every folded `yb_task` tag is judged) and BUG-283 (CreateTime floor
grows by `detach_age` because CloudWatch cannot see attachments to stopped instances)."""

import datetime
import logging
from typing import Any

import pytest

from crc.aws.disk import DiskCleaner, DiskRuntime
from crc.aws.disk.settings import build_settings
from crc.aws.disk.volumes import VolumeFilter, tag_map, volume_label
from crc.core import logger
from crc.core.cleaner import Operation, RunContext
from tests.helpers_ad_spot_engine import run_through_engine

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)


def _volume(
    days: float = 30, tags: list[dict[str, Any]] | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "VolumeId": "vol-1",
        "State": "available",
        "Attachments": [],
        "CreateTime": NOW - datetime.timedelta(days=days),
        "Tags": tags if tags is not None else [{"Key": "yb_task", "Value": "itest"}],
        **extra,
    }


def _tags(*pairs: tuple[str, Any]) -> list[dict[str, Any]]:
    return [{"Key": key, "Value": value} for key, value in pairs]


def _candidate(volume: dict[str, Any], **options: Any) -> bool:
    return VolumeFilter(build_settings(options)).is_candidate(volume, NOW)


@pytest.mark.parametrize(
    "pairs",
    [
        (("yb_task", ""), ("YB-Task", "prod")),
        (("yb_task", "dev"), ("YB-task", "prod")),
        (("yb-task", "dev"), ("YB_TASK", "vpn")),
        (("yb_task", "itest"), ("Yb-Task", "dev"), ("YB_TASK", "infra")),
        (("YB-Task", "prod"), ("yb_task", "")),
        (("yb_task", None), ("YB-Task", "prod")),
    ],
)
def test_bug_282_any_sensitive_folded_yb_task_keeps_the_volume(
    pairs: tuple[tuple[str, Any], ...], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    logger.begin("aws", "disk", "delete", True, {"age": {"days": 1}})
    try:
        assert not _candidate(_volume(tags=_tags(*pairs)), age={"days": 1})
    finally:
        logger.finish()
    assert "Skipping volume vol-1: sensitive yb_task=" in caplog.text
    assert "SKIPPED cloud=aws resource=disk reason=sensitive_yb_task count=1" in caplog.text


@pytest.mark.parametrize(
    "pairs",
    [
        (("yb_task", "dev"),),
        (("yb_task", ""), ("YB-Task", "dev")),
        (("yb_task", "itest"), ("yb-task", "ybtest")),
        (("yb_task", ""),),
    ],
)
def test_bug_282_without_a_sensitive_value_the_volume_is_still_deleted(
    pairs: tuple[tuple[str, Any], ...],
) -> None:
    assert _candidate(_volume(tags=_tags(*pairs)), age={"days": 1})


def test_bug_282_exact_key_yb_task_alone_is_unchanged() -> None:
    assert not _candidate(_volume(tags=_tags(("yb_task", "prod"))), age={"days": 1})
    assert _candidate(_volume(tags=_tags(("yb_task", "itest"))), age={"days": 1})


def test_bug_282_unrelated_keys_are_not_judged() -> None:
    tags = _tags(("yb_task", "itest"), ("yb_task_owner", "prod"), ("Name", "prod"))
    assert _candidate(_volume(tags=tags), age={"days": 1})


def test_bug_282_label_still_reports_the_first_folded_value() -> None:
    volume = _volume(tags=_tags(("yb_task", "dev"), ("YB-Task", "itest")))
    assert volume_label("r1", volume) == "r1/vol-1 yb_task=dev"
    assert tag_map(volume["Tags"]) == {"yb_task": "dev", "YB-Task": "itest"}


PROD_A = {"age": {"days": 7}, "detach_age": {"days": 3}}


@pytest.mark.parametrize(
    ("days", "expected"),
    [(9.9, False), (7.5, False), (10, True), (10.1, True), (30, True)],
)
def test_bug_283_age_and_detach_age_floor_is_their_sum(days: float, expected: bool) -> None:
    assert _candidate(_volume(days=days), **PROD_A) is expected


def test_bug_283_skip_is_logged_at_debug_and_counted(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    logger.begin("aws", "disk", "delete", True, PROD_A)
    try:
        assert not _candidate(_volume(days=8), **PROD_A)
    finally:
        logger.finish()
    assert [r.levelno for r in caplog.records if "detach time unknown" in r.getMessage()] == [
        logging.DEBUG
    ]
    assert "SKIPPED cloud=aws resource=disk reason=detach_unknown_too_young count=1" in caplog.text


def test_bug_283_second_production_command_floor_is_37_days() -> None:
    options = {"age": {"days": 30}, "detach_age": {"days": 7}}
    assert not _candidate(_volume(days=36), **options)
    assert _candidate(_volume(days=37), **options)


def test_bug_283_hours_are_added() -> None:
    options = {"age": {"hours": 5}, "detach_age": {"hours": 2}}
    assert not _candidate(_volume(days=6 / 24), **options)
    assert _candidate(_volume(days=7 / 24), **options)


def test_bug_283_custom_age_tag_replaces_age_then_detach_age_is_added() -> None:
    tags = _tags(("yb_task", "itest"), ("max_retention_age", "{'days': 2}"))
    options = {**PROD_A, "custom_age_tag_key": "max_retention_age"}
    assert not _candidate(_volume(days=4.9, tags=tags), **options)
    assert _candidate(_volume(days=5, tags=tags), **options)


@pytest.mark.parametrize(
    ("options", "days", "expected"),
    [
        ({"age": {"days": 7}}, 7, True),
        ({"age": {"days": 7}}, 6.9, False),
        ({"detach_age": {"days": 3}}, 3, True),
        ({"detach_age": {"days": 3}}, 2.9, False),
    ],
)
def test_bug_283_single_gate_floors_are_unchanged(
    options: dict[str, Any], days: float, expected: bool
) -> None:
    assert _candidate(_volume(days=days), **options) is expected


class _FakePaginator:
    def __init__(self, volumes: list[dict[str, Any]]) -> None:
        self._volumes = volumes

    def paginate(self, *, Filters: Any) -> list[dict[str, Any]]:
        assert Filters[0] == {"Name": "status", "Values": ["available"]}
        return [{"Volumes": self._volumes}]


class _FakeEc2:
    def __init__(self, volumes: list[dict[str, Any]]) -> None:
        self._volumes = volumes
        self.deleted: list[str] = []

    def get_paginator(self, name: str) -> _FakePaginator:
        assert name == "describe_volumes"
        return _FakePaginator(self._volumes)

    def delete_volume(self, *, VolumeId: str) -> dict[str, Any]:
        self.deleted.append(VolumeId)
        return {}


class _FakeCloudWatch:
    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "MetricDataResults": [
                {"Id": q["Id"], "StatusCode": "Complete", "Values": []}
                for q in kwargs["MetricDataQueries"]
            ]
        }


def test_production_command_1_end_to_end() -> None:
    volumes = [
        {**_volume(days=11, tags=_tags(("yb_task", "itest"))), "VolumeId": "vol-old"},
        {**_volume(days=8, tags=_tags(("yb_task", "itest"))), "VolumeId": "vol-young-detach"},
        {
            **_volume(days=15, tags=_tags(("yb_task", "itest"), ("YB-Task", "prod"))),
            "VolumeId": "vol-folded-prod",
        },
        {
            **_volume(
                days=5,
                tags=_tags(("yb_task", "itest"), ("max_retention_age", "{'days': 2}")),
            ),
            "VolumeId": "vol-custom-ok",
        },
        {
            **_volume(
                days=4,
                tags=_tags(("yb_task", "itest"), ("max_retention_age", "{'days': 2}")),
            ),
            "VolumeId": "vol-custom-young",
        },
        {
            **_volume(days=15, tags=_tags(("yb_task", "itest"), ("autoclean", "false"))),
            "VolumeId": "vol-exc",
        },
    ]
    ec2 = _FakeEc2(volumes)
    runtime = DiskRuntime(lambda r: ec2, lambda r: _FakeCloudWatch(), lambda: ["us-west-2"])
    settings = build_settings(
        {
            "filter_tags": {
                "yb_task": [
                    "bug_bash_22_9_2023",
                    "uitest",
                    "itest",
                    "itests",
                    "itest-system",
                    "db-cross-functional",
                    "itest_yba",
                    "ybtest",
                    "dev-itest",
                    "dev-uitest",
                    "itest-keep-universe",
                    "itest-system-keep-universe",
                    "db-cross-functional-keep-universe",
                    "uitest-keep-universe",
                    "dev-itest-keep-universe",
                    "dev-uitest-keep-universe",
                ]
            },
            "exception_tags": {
                "autoclean": ["false", "False", "FALSE"],
                "auto_clean": ["false", "False", "FALSE"],
                "crc": ["gke"],
            },
            "age": {"days": 7},
            "custom_age_tag_key": "max_retention_age",
            "notags": None,
            "detach_age": {"days": 3},
        }
    )
    outcome = run_through_engine(
        "aws",
        "disk",
        lambda _ctx: DiskCleaner(settings, runtime),
        RunContext(dry_run=False, operation=Operation.DELETE, options={}, now=NOW),
    )
    assert outcome.result is not None
    assert ec2.deleted == ["vol-old", "vol-custom-ok"]
    assert list(outcome.result.succeeded) == [
        "us-west-2/vol-old yb_task=itest",
        "us-west-2/vol-custom-ok yb_task=itest",
    ]


def test_production_command_2_end_to_end() -> None:
    volumes = [
        {**_volume(days=38, tags=[]), "VolumeId": "vol-untagged-old"},
        {**_volume(days=35, tags=[]), "VolumeId": "vol-untagged-young-detach"},
        {**_volume(days=40, tags=_tags(("yb_task", "itest"))), "VolumeId": "vol-has-yb-task"},
        {**_volume(days=40, tags=_tags(("crc", "gke"))), "VolumeId": "vol-gke"},
    ]
    ec2 = _FakeEc2(volumes)
    runtime = DiskRuntime(lambda r: ec2, lambda r: _FakeCloudWatch(), lambda: ["us-west-2"])
    settings = build_settings(
        {
            "filter_tags": None,
            "exception_tags": {
                "autoclean": ["false", "False", "FALSE"],
                "auto_clean": ["false", "False", "FALSE"],
                "crc": ["gke"],
            },
            "age": {"days": 30},
            "custom_age_tag_key": None,
            "notags": {"yb_task": []},
            "detach_age": {"days": 7},
        }
    )
    outcome = run_through_engine(
        "aws",
        "disk",
        lambda _ctx: DiskCleaner(settings, runtime),
        RunContext(dry_run=False, operation=Operation.DELETE, options={}, now=NOW),
    )
    assert outcome.result is not None
    assert ec2.deleted == ["vol-untagged-old"]
    assert list(outcome.result.succeeded) == ["us-west-2/vol-untagged-old"]
