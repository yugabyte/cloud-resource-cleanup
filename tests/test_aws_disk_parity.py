"""Parity: `crc.aws.disk` against main's version (`tests/legacy/aws_disk.txt`).

Both run against the same fake `boto3.client`; SDK calls, results, log records, stdout and
raised errors must match for every case. The table runs twice: through `crc.core.engine.run`
and through the registered Cleaner (`plan()` then `apply()`, `plan()` alone in a dry run).
"""

import dataclasses
import datetime
import logging
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError

from crc.aws.disk import DiskCleaner, build_disk_cleaner
from crc.aws.disk.settings import normalize_age
from crc.aws.disk.volumes import server_filters
from crc.core import age as core_age
from crc.core import policy, registry
from crc.core.cleaner import Operation, RunContext
from tests.helpers_ad_spot_engine import run_through_engine
from tests.helpers_log_fix import expected_logs
from tests.log_fix_aws_compute import RULES

FIXED_BUGS = {"BUG-356"}


@dataclass(frozen=True)
class Divergence:
    bug: str
    old_deleted: Sequence[str]
    new_deleted: Sequence[str]
    new_delete_ids: Sequence[str]
    new_logs: Sequence[tuple[str, int, str]]


# Cases whose inputs trigger a fixed bug. The oracle (main) deletes more;
# `_check_divergence` pins both the oracle's buggy outcome and the exact new outcome.
DIVERGING: Mapping[str, Divergence] = {
    "yb-task-key-variants": Divergence(
        bug="BUG-282",
        old_deleted=["us-west-2/d yb_task=dev", "us-west-2/e", "us-west-2/f yb_task=dev"],
        new_deleted=["us-west-2/f yb_task=dev"],
        new_delete_ids=["f"],
        new_logs=[
            ("root", logging.INFO, "Retrieved list of regions: ['us-west-2']"),
            ("root", logging.DEBUG, "Skipping volume a: sensitive yb_task=prod"),
            ("root", logging.DEBUG, "Skipping volume b: sensitive yb_task=prod"),
            ("root", logging.DEBUG, "Skipping volume c: sensitive yb_task=vpn"),
            ("root", logging.DEBUG, "Skipping volume d: sensitive yb_task=prod"),
            ("root", logging.DEBUG, "Skipping volume e: sensitive yb_task=prod"),
            ("root", logging.DEBUG, "No custom_age_tag_key provided to search for."),
            ("root", logging.INFO, "Deleted volume us-west-2/f yb_task=dev"),
            ("root", logging.WARNING, "number of AWS EBS volumes deleted: 1"),
            (
                "root",
                logging.WARNING,
                "List of AWS EBS volumes deleted: ['us-west-2/f yb_task=dev']",
            ),
        ],
    ),
    "detach-age-and-age": Divergence(
        bug="BUG-283",
        old_deleted=["us-west-2/a yb_task=itest", "us-west-2/b yb_task=itest"],
        new_deleted=["us-west-2/a yb_task=itest"],
        new_delete_ids=["a"],
        new_logs=[
            ("root", logging.INFO, "Retrieved list of regions: ['us-west-2']"),
            ("root", logging.DEBUG, "No custom_age_tag_key provided to search for."),
            ("root", logging.DEBUG, "No custom_age_tag_key provided to search for."),
            (
                "root",
                logging.DEBUG,
                "Skipping volume b: detach time unknown and CreateTime younger than "
                "age plus detach_age",
            ),
            ("root", logging.INFO, "Deleted volume us-west-2/a yb_task=itest"),
            ("root", logging.WARNING, "number of AWS EBS volumes deleted: 1"),
            (
                "root",
                logging.WARNING,
                "List of AWS EBS volumes deleted: ['us-west-2/a yb_task=itest']",
            ),
        ],
    ),
}

ORACLE = Path(__file__).parent / "legacy" / "aws_disk.txt"
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
DELETE_PREFIXES = ("Deleted volume", "Failed to delete volume")
BATCH = 50
TWO = 2
THREE = 3
MANY = 60


def tags(**pairs: str | None) -> list[dict[str, Any]]:
    return [{"Key": k.replace("__", "-").replace("_", "_"), "Value": v} for k, v in pairs.items()]


def tag(key: str, value: Any = "") -> dict[str, Any]:
    return {"Key": key, "Value": value}


DEFAULT_TAGS = [tag("yb_task", "itest"), tag("Name", "foo")]


def vol(
    volume_id: str | None = "vol-1",
    days: float | None = 30,
    tag_list: Any = "default",
    **extra: Any,
) -> dict[str, Any]:
    item: dict[str, Any] = {"State": "available", "Attachments": []}
    if volume_id is not None:
        item["VolumeId"] = volume_id
    if days is not None:
        item["CreateTime"] = NOW - datetime.timedelta(days=days)
    if tag_list == "default":
        item["Tags"] = [dict(t) for t in DEFAULT_TAGS]
    elif tag_list != "absent":
        item["Tags"] = tag_list
    item.update(extra)
    return item


def conn_error() -> Exception:
    error: Exception = EndpointConnectionError(endpoint_url="https://ec2.example")
    return error


def client_error(code: str, operation: str = "DescribeVolumes") -> Exception:
    error: Exception = ClientError({"Error": {"Code": code, "Message": "msg"}}, operation)
    return error


@dataclass
class Region:
    pages: Any = field(default_factory=list)
    busy: Sequence[str] = ()
    status: Mapping[str, str] = field(default_factory=dict)
    missing: Sequence[str] = ()
    cw_error: Exception | None = None
    paged: bool = False
    cw_client_error: Exception | None = None


@dataclass
class Case:
    id: str
    regions: Mapping[str, Region]
    opts: Mapping[str, Any] = field(default_factory=dict)
    dry_run: bool = False
    delete_errors: Mapping[tuple[str, str], Exception] = field(default_factory=dict)
    regions_error: Exception | None = None


def one(*volumes: dict[str, Any], region: str = "us-west-2", **kw: Any) -> dict[str, Region]:
    return {region: Region([list(volumes)], **kw)}


def many(prefix: str, count: int) -> list[dict[str, Any]]:
    return [vol(f"{prefix}{i}", 10 + i) for i in range(count)]


BASE: Mapping[str, Any] = {"age": {"days": 1}}


def case(case_id: str, regions: Mapping[str, Region], **kw: Any) -> Case:
    opts = dict(BASE)
    opts.update(kw.pop("opts", {}))
    return Case(case_id, regions, opts, **kw)


CASES = [
    case("dry-run", one(vol("a"), vol("b", 5)), dry_run=True),
    case("live-delete", one(vol("a"), vol("b", 5))),
    case(
        "multi-region",
        {"us-west-2": Region([[vol("a")]]), "eu-west-1": Region([[vol("b")], [vol("c")]])},
    ),
    case(
        "multi-region-dry-run",
        {"us-west-2": Region([[vol("a")]]), "eu-west-1": Region([[vol("b")]])},
        dry_run=True,
    ),
    case("no-regions", {}),
    case("empty-region", {"us-west-2": Region([[]]), "eu-west-1": Region([])}),
    case("page-without-volumes-key", {"us-west-2": Region([{}])}),
    case("page-volumes-none", {"us-west-2": Region([{"Volumes": None}])}),
    # age gate and normalisation
    case("no-age-gate", one(vol()), opts={"age": None}),
    case("age-empty-dict", one(vol()), opts={"age": {}}),
    case("age-unsupported-key", one(vol()), opts={"age": {"minutes": 5}}),
    case("age-unsupported-key-with-valid", one(vol()), opts={"age": {"days": 1, "weeks": 1}}),
    case("age-days-zero", one(vol()), opts={"age": {"days": 0}}),
    case("age-hours-zero-days-zero", one(vol()), opts={"age": {"days": 0, "hours": 0}}),
    case("age-negative", one(vol()), opts={"age": {"days": -1}}),
    case("age-not-integer", one(vol()), opts={"age": {"days": "x"}}),
    case("age-none-value", one(vol()), opts={"age": {"days": None}}),
    case("age-float-value", one(vol("a", 2), vol("b", 0.5)), opts={"age": {"days": 1.9}}),
    case("age-numeric-string", one(vol("a", 2), vol("b", 0.5)), opts={"age": {"days": "1"}}),
    case("age-int", one(vol("a", 8), vol("b", 5)), opts={"age": 7}),
    case("age-true", one(vol("a", 2), vol("b", 0.5)), opts={"age": True}),
    case("age-hours", one(vol("a", 0.5), vol("b", 0.1)), opts={"age": {"hours": 6}}),
    case(
        "age-days-and-hours",
        one(vol("a", 1.3), vol("b", 1.2)),
        opts={"age": {"days": 1, "hours": 6}},
    ),
    case("age-hours-only-valid-key-with-zero", one(vol("a", 1)), opts={"age": {"hours": 0}}),
    case("age-string-value", one(vol()), opts={"age": "days"}),
    case("age-overflow", one(vol()), opts={"age": {"days": 10**30}}),
    case("age-float-inf", one(vol()), opts={"age": {"days": float("inf")}}),
    # CreateTime edges
    case("age-exactly-floor", one(vol("a", 1), vol("b", 0.99999), vol("c", 1.00001))),
    case("age-future-create-time", one(vol("a", -3))),
    case("create-time-missing", one(vol("a", None), vol("b"))),
    case("create-time-naive", one(vol("a", None, CreateTime=datetime.datetime(2020, 1, 1)))),  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical
    case("create-time-string", one(vol("a", None, CreateTime="2020-01-01"))),
    case(
        "create-time-other-timezone",
        one(
            vol(
                "a",
                None,
                CreateTime=datetime.datetime(
                    2026, 2, 1, tzinfo=datetime.timezone(datetime.timedelta(hours=5))
                ),
            )
        ),
    ),
    # state, attachments
    case(
        "state-in-use",
        one(vol("a", State="in-use"), vol("b", State="creating"), vol("c", State=None)),
    ),
    case("state-missing", one({"VolumeId": "a", "CreateTime": NOW - datetime.timedelta(days=9)})),
    case("attachments-present", one(vol("a", Attachments=[{"InstanceId": "i-1"}]), vol("b"))),
    case("attachments-none", one(vol("a", Attachments=None))),
    case(
        "multi-attach", one(vol("a", MultiAttachEnabled=True), vol("b", MultiAttachEnabled=False))
    ),
    # tags
    case("untagged-skipped", one(vol("a", tag_list="absent"), vol("b", tag_list=[]))),
    case("untagged-with-tags-none", one(vol("a", tag_list=None))),
    case(
        "untagged-included-with-notags",
        one(vol("a", tag_list="absent"), vol("b", tag_list=[])),
        opts={"notags": {"yb_owner": []}},
    ),
    case(
        "untagged-with-notags-and-filter",
        one(vol("a", tag_list="absent")),
        opts={"notags": {"yb_owner": []}, "filter_tags": {"yb_task": []}},
    ),
    case("tags-without-keys", one(vol("a", tag_list=[{"Value": "x"}, {"Key": ""}]))),
    case(
        "tags-without-keys-notags",
        one(vol("a", tag_list=[{"Value": "x"}])),
        opts={"notags": {"k": []}},
    ),
    case("tag-without-value", one(vol("a", tag_list=[{"Key": "Name"}]))),
    case("tag-value-none", one(vol("a", tag_list=[tag("yb_task", None), tag("Name", None)]))),
    case(
        "tag-value-none-filter",
        one(vol("a", tag_list=[tag("Name", None)])),
        opts={"filter_tags": {"Name": ["x"]}},
    ),
    case("tag-entry-not-a-dict", one(vol("a", tag_list=["oops"]), vol("b"))),
    case("tag-entry-key-int", one(vol("a", tag_list=[{"Key": 5, "Value": "x"}]))),
    case("tags-is-dict", one(vol("a", tag_list={"yb_task": "x"}))),
    case("label-name-tag", one(vol("a", tag_list=[tag("Name", "mydisk")]))),
    case("label-yb-task-wins", one(vol("a", tag_list=[tag("Name", "n"), tag("yb_task", "t")]))),
    case("label-yb-task-dash-key", one(vol("a", tag_list=[tag("YB-Task", " dev ")]))),
    case("label-name-equals-id", one(vol("a", tag_list=[tag("Name", "a")]))),
    case("label-empty-name-uses-id", one(vol("a", tag_list=[tag("Name", ""), tag("x", "y")]))),
    # filter tags
    case(
        "filter-tags-values",
        one(vol("a"), vol("b", tag_list=[tag("yb_task", "other")])),
        opts={"filter_tags": {"yb_task": ["itest", "uitest"]}},
    ),
    case(
        "filter-tags-key-only",
        one(vol("a"), vol("b", tag_list=[tag("Name", "x")])),
        opts={"filter_tags": {"yb_task": []}},
    ),
    case(
        "filter-tags-two-keys-and",
        one(
            vol("a", tag_list=[tag("yb_task", "itest"), tag("yb_owner", "qa")]),
            vol("b", tag_list=[tag("yb_task", "itest")]),
        ),
        opts={"filter_tags": {"yb_task": ["itest"], "yb_owner": ["qa"]}},
    ),
    case(
        "filter-tags-glob",
        one(
            vol("a", tag_list=[tag("Name", "jenkins-1")]),
            vol("b", tag_list=[tag("Name", "prod-jenkins")]),
            vol("c", tag_list=[tag("Name", "jenkins-")]),
        ),
        opts={"filter_tags": {"Name": ["jenkins-*"]}},
    ),
    case(
        "filter-tags-question-mark-glob",
        one(vol("a", tag_list=[tag("Name", "ab")]), vol("b", tag_list=[tag("Name", "abc")])),
        opts={"filter_tags": {"Name": ["a?"]}},
    ),
    case(
        "filter-tags-case-sensitive",
        one(vol("a", tag_list=[tag("Name", "ABC")])),
        opts={"filter_tags": {"Name": ["abc"]}},
    ),
    case(
        "filter-tags-bracket-glob",
        one(vol("a", tag_list=[tag("Name", "a1")]), vol("b", tag_list=[tag("Name", "ax")])),
        opts={"filter_tags": {"Name": ["a[0-9]"]}},
    ),
    case(
        "filter-tags-values-none",
        one(vol("a")),
        opts={"filter_tags": {"yb_task": None}},
    ),
    case(
        "filter-tags-empty-dict",
        one(vol("a")),
        opts={"filter_tags": {}},
    ),
    # exception tags and notags
    case(
        "exception-key-only",
        one(vol("a", tag_list=[tag("keep")]), vol("b", tag_list=[tag("other")])),
        opts={"exception_tags": {"keep": []}},
    ),
    case(
        "exception-value-match",
        one(
            vol("a", tag_list=[tag("autoclean", "false")]),
            vol("b", tag_list=[tag("autoclean", "true")]),
        ),
        opts={"exception_tags": {"autoclean": ["false", "False"]}},
    ),
    case(
        "exception-first-tag-logged",
        one(vol("a", tag_list=[tag("b"), tag("a")])),
        opts={"exception_tags": {"a": [], "b": []}},
    ),
    case(
        "exception-value-none",
        one(vol("a", tag_list=[tag("env", None)])),
        opts={"exception_tags": {"env": [""]}},
    ),
    case(
        "notags-all-match-skips",
        one(
            vol("a", tag_list=[tag("x", "1"), tag("y", "2")]),
            vol("b", tag_list=[tag("x", "1")]),
        ),
        opts={"notags": {"x": ["1"], "y": []}},
    ),
    case(
        "notags-key-only",
        one(vol("a", tag_list=[tag("skipme", "z")]), vol("b")),
        opts={"notags": {"skipme": []}},
    ),
    # regex
    case(
        "name-regex",
        one(vol("a", tag_list=[tag("Name", "test-1")]), vol("b", tag_list=[tag("Name", "prod-1")])),
        opts={"name_regex": ["^test"]},
    ),
    case(
        "name-regex-uses-id-without-name",
        one(vol("vol-abc", tag_list=[tag("x", "y")]), vol("vol-zzz", tag_list=[tag("x", "y")])),
        opts={"name_regex": ["abc"]},
    ),
    case(
        "name-regex-any-of-two",
        one(vol("a", tag_list=[tag("Name", "foo")]), vol("b", tag_list=[tag("Name", "bar")])),
        opts={"name_regex": ["^foo$", "^zzz$"]},
    ),
    case(
        "exception-regex",
        one(vol("a", tag_list=[tag("Name", "keep-me")]), vol("b", tag_list=[tag("Name", "drop")])),
        opts={"exception_regex": ["^keep"]},
    ),
    case(
        "exception-regex-on-volume-id",
        one(vol("vol-keep", tag_list=[tag("x", "y")]), vol("vol-del", tag_list=[tag("x", "y")])),
        opts={"exception_regex": ["keep"]},
    ),
    case(
        "name-and-exception-regex",
        one(
            vol("a", tag_list=[tag("Name", "test-keep")]),
            vol("b", tag_list=[tag("Name", "test-del")]),
        ),
        opts={"name_regex": ["^test"], "exception_regex": ["keep"]},
    ),
    case("name-regex-invalid", one(vol()), opts={"name_regex": ["test-*("]}),
    case("exception-regex-invalid", one(vol()), opts={"exception_regex": ["(["]}),
    case(
        "regex-case-sensitive",
        one(vol("a", tag_list=[tag("Name", "TEST")])),
        opts={"name_regex": ["test"]},
    ),
    # sensitive yb_task and kubernetes
    case(
        "yb-task-sensitive-values",
        one(
            *[
                vol(f"v{i}", tag_list=[tag("yb_task", v)])
                for i, v in enumerate(
                    [
                        "infrastructure",
                        "infra",
                        "ipsec",
                        "vpn",
                        "prod",
                        "production",
                        "PROD",
                        " Production ",
                        "dev",
                    ]
                )
            ]
        ),
    ),
    case(
        "yb-task-sensitive-tokens",
        one(
            *[
                vol(f"v{i}", tag_list=[tag("yb_task", v)])
                for i, v in enumerate(
                    [
                        "prod-cluster",
                        "my_vpn_box",
                        "a/infra/b",
                        "prod_x",
                        "production-1",
                        "productive",
                        "vpnx",
                        "eng prod",
                    ]
                )
            ]
        ),
    ),
    case(
        "yb-task-key-variants",
        one(
            vol("a", tag_list=[tag("YB_Task", "prod")]),
            vol("b", tag_list=[tag("yb-task", "prod")]),
            vol("c", tag_list=[tag("YB-TASK", "vpn")]),
            vol("d", tag_list=[tag("yb_task", "dev"), tag("YB-task", "prod")]),
            vol("e", tag_list=[tag("yb_task", ""), tag("yb-task", "prod")]),
            vol("f", tag_list=[tag("yb_task", "prod"), tag("yb_task", "dev")]),
        ),
    ),
    case("yb-task-non-string", one(vol("a", tag_list=[tag("yb_task", 5)]))),
    case(
        "kubernetes-skipped",
        one(
            vol("a", tag_list=[tag("kubernetes.io/cluster/x", "owned")]),
            vol("b", tag_list=[tag("ebs.csi.aws.com/cluster", "true")]),
            vol("c", tag_list=[tag("KubernetesCluster", "x")]),
            vol("d", tag_list=[tag("CSIVolumeName", "x")]),
            vol("e", tag_list=[tag("kubernetes.io", "x")]),
        ),
    ),
    case(
        "kubernetes-allowed-with-filter",
        one(
            vol("a", tag_list=[tag("kubernetes.io/cluster/x", "owned")]),
            vol("b", tag_list=[tag("kubernetes.io/cluster/y", "owned")]),
        ),
        opts={"filter_tags": {"kubernetes.io/cluster/x": ["owned"]}},
    ),
    case(
        "kubernetes-filter-other-key",
        one(vol("a", tag_list=[tag("kubernetes.io/x", "1"), tag("yb_task", "itest")])),
        opts={"filter_tags": {"yb_task": ["itest"]}},
    ),
    case(
        "kubernetes-filter-cluster-key",
        one(vol("a", tag_list=[tag("KubernetesCluster", "c")])),
        opts={"filter_tags": {"KubernetesCluster": []}},
    ),
    # custom age tag
    case(
        "custom-age-tag-valid",
        one(
            vol("a", 3, tag_list=[tag("ret", "{'days': 2}")]),
            vol("b", 3, tag_list=[tag("ret", "{'days': 5}")]),
        ),
        opts={"age": {"days": 1}, "custom_age_tag_key": "ret"},
    ),
    case(
        "custom-age-tag-hours",
        one(vol("a", 0.3, tag_list=[tag("ret", "{'hours': 2}")])),
        opts={"age": {"days": 1}, "custom_age_tag_key": "ret"},
    ),
    case(
        "custom-age-tag-int",
        one(vol("a", 3, tag_list=[tag("ret", "2")]), vol("b", 3, tag_list=[tag("ret", "9")])),
        opts={"age": {"days": 7}, "custom_age_tag_key": "ret"},
    ),
    case(
        "custom-age-tag-zero-skips",
        one(vol("a", 30, tag_list=[tag("ret", "{'days': 0}")])),
        opts={"age": {"days": 1}, "custom_age_tag_key": "ret"},
    ),
    case(
        "custom-age-tag-bad-key-skips",
        one(vol("a", 30, tag_list=[tag("ret", "{'minutes': 5}")])),
        opts={"age": {"days": 1}, "custom_age_tag_key": "ret"},
    ),
    case(
        "custom-age-tag-negative-skips",
        one(vol("a", 30, tag_list=[tag("ret", "{'days': -1}")])),
        opts={"age": {"days": 1}, "custom_age_tag_key": "ret"},
    ),
    case(
        "custom-age-tag-unparseable-fails-open",
        one(
            vol("a", 30, tag_list=[tag("ret", "not a dict")]),
            vol("b", 0.5, tag_list=[tag("ret", "{")]),
        ),
        opts={"age": {"days": 1}, "custom_age_tag_key": "ret"},
    ),
    case(
        "custom-age-tag-empty-fails-open",
        one(vol("a", 30, tag_list=[tag("ret", "")]), vol("b", 0.5, tag_list=[tag("ret", "")])),
        opts={"age": {"days": 1}, "custom_age_tag_key": "ret"},
    ),
    case(
        "custom-age-tag-zero-literal-fails-open",
        one(vol("a", 30, tag_list=[tag("ret", "0")]), vol("b", 0.5, tag_list=[tag("ret", "0")])),
        opts={"age": {"days": 1}, "custom_age_tag_key": "ret"},
    ),
    case(
        "custom-age-tag-overflow",
        one(vol("a", 30, tag_list=[tag("ret", "{'days': 1e999}")])),
        opts={"age": {"days": 1}, "custom_age_tag_key": "ret"},
    ),
    case(
        "custom-age-tag-detach-only-uses-max",
        one(
            vol("a", 3, tag_list=[tag("ret", "{'days': 1}")]),
            vol("b", 10, tag_list=[tag("ret", "{'days': 1}")]),
            vol("c", 10, tag_list=[tag("ret", "{'days': 20}")]),
        ),
        opts={"age": None, "detach_age": {"days": 7}, "custom_age_tag_key": "ret"},
    ),
    case(
        "custom-age-tag-key-not-present",
        one(vol("a", 30), vol("b", 0.5)),
        opts={"age": {"days": 1}, "custom_age_tag_key": "ret"},
    ),
    # detach age
    case(
        "detach-age-only",
        one(vol("a", 10), vol("b", 2)),
        opts={"age": None, "detach_age": {"days": 3}},
    ),
    case(
        "detach-age-and-age",
        one(vol("a", 10), vol("b", 2)),
        opts={"age": {"days": 1}, "detach_age": {"days": 3}},
    ),
    case("detach-age-int", one(vol("a", 10)), opts={"age": None, "detach_age": 3}),
    case("detach-age-zero", one(vol("a")), opts={"age": None, "detach_age": {"days": 0}}),
    case(
        "detach-age-unsupported-key",
        one(vol("a")),
        opts={"age": None, "detach_age": {"minutes": 30}},
    ),
    case("detach-age-too-long", one(vol("a")), opts={"age": None, "detach_age": {"days": 900}}),
    case(
        "detach-age-max-lookback",
        one(vol("a", 500)),
        opts={"age": None, "detach_age": {"days": 455}},
    ),
    case("detach-age-hours", one(vol("a", 5)), opts={"age": None, "detach_age": {"hours": 5}}),
    case(
        "detach-age-sub-minute-hours-window",
        one(vol("a", 5)),
        opts={"age": None, "detach_age": {"hours": 1}},
    ),
    case(
        "detach-busy-volume-dropped",
        one(vol("idle"), vol("busy"), busy=["busy"]),
        opts={"detach_age": {"days": 3}},
    ),
    case(
        "detach-incomplete-status-kept",
        one(vol("idle"), vol("forbidden"), status={"forbidden": "Forbidden"}),
        opts={"detach_age": {"days": 3}},
    ),
    case(
        "detach-partial-status-kept",
        one(vol("idle"), vol("partial"), status={"partial": "PartialData"}),
        opts={"detach_age": {"days": 3}},
    ),
    case(
        "detach-missing-result-kept",
        one(vol("idle"), vol("gone"), missing=["gone"]),
        opts={"detach_age": {"days": 3}},
    ),
    case(
        "detach-cloudwatch-client-error",
        one(vol("a"), vol("b"), cw_error=client_error("AccessDenied", "GetMetricData")),
        opts={"detach_age": {"days": 3}},
    ),
    case(
        "detach-cloudwatch-connection-error",
        one(vol("a"), vol("b"), cw_error=conn_error()),
        opts={"detach_age": {"days": 3}},
    ),
    case(
        "detach-cloudwatch-unexpected-error",
        {
            "us-west-2": Region([[vol("a")]], cw_error=ValueError("boom")),
            "eu-west-1": Region([[vol("b")]]),
        },
        opts={"detach_age": {"days": 3}},
    ),
    case(
        "detach-paged-metrics",
        one(vol("idle"), vol("busy"), busy=["busy"], paged=True),
        opts={"detach_age": {"days": 3}},
    ),
    case(
        "detach-many-volumes-batches",
        {"us-west-2": Region([many("m", MANY)], busy=["m3", "m55"], status={"m7": "Forbidden"})},
        opts={"detach_age": {"days": 3}},
    ),
    case(
        "detach-exactly-one-batch",
        {"us-west-2": Region([many("m", BATCH)])},
        opts={"detach_age": {"days": 3}},
    ),
    case(
        "detach-none-candidates-no-cloudwatch-client",
        one(vol("young", 0.1), vol("tagged-skip", tag_list=[tag("yb_task", "prod")])),
        opts={"age": {"days": 1}, "detach_age": {"days": 3}},
    ),
    case(
        "detach-duplicate-volume-ids",
        one(vol("a"), vol("a"), vol("b"), busy=["b"]),
        opts={"detach_age": {"days": 3}},
    ),
    case(
        "detach-dry-run",
        one(vol("idle"), vol("busy"), busy=["busy"]),
        opts={"detach_age": {"days": 3}},
        dry_run=True,
    ),
    # API errors
    case(
        "describe-client-error-generic",
        {"us-west-2": Region(client_error("AccessDenied")), "eu-west-1": Region([[vol("b")]])},
    ),
    *[
        case(
            f"describe-client-error-{code}",
            {"us-west-2": Region(client_error(code)), "eu-west-1": Region([[vol("b")]])},
        )
        for code in (
            "AuthFailure",
            "UnauthorizedOperation",
            "OptInRequired",
            "InvalidClientTokenId",
        )
    ],
    case(
        "describe-connection-error-skips-region",
        {"us-west-2": Region(conn_error()), "eu-west-1": Region([[vol("b")]])},
    ),
    case(
        "describe-timeout-skips-region",
        {
            "us-west-2": Region(ConnectTimeoutError(endpoint_url="https://ec2.example")),
            "eu-west-1": Region([[vol("b")]]),
        },
    ),
    case(
        "describe-unexpected-error",
        {"us-west-2": Region(ValueError("boom")), "eu-west-1": Region([[vol("b")]])},
    ),
    case(
        "describe-error-after-first-page",
        {"us-west-2": Region([[vol("a")], conn_error()]), "eu-west-1": Region([[vol("b")]])},
    ),
    case(
        "describe-error-after-first-page-unexpected",
        {"us-west-2": Region([[vol("a")], KeyError("x")]), "eu-west-1": Region([[vol("b")]])},
    ),
    case(
        "client-error-without-code",
        {
            "us-west-2": Region(ClientError({}, "DescribeVolumes")),
            "eu-west-1": Region([[vol("b")]]),
        },
    ),
    case(
        "volume-error-isolated",
        one(vol("a"), vol("b", None, CreateTime=datetime.datetime(2020, 1, 1)), vol("c")),  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical
    ),
    case("volume-error-tags-not-dicts", one(vol("a", tag_list=["oops"]), vol("c"))),
    case(
        "delete-client-error-isolated",
        one(*many("s", 4)),
        delete_errors={("us-west-2", "s1"): client_error("VolumeInUse", "DeleteVolume")},
    ),
    case(
        "delete-client-error-every-volume",
        one(*many("s", 3)),
        delete_errors={
            ("us-west-2", f"s{i}"): client_error("VolumeInUse", "DeleteVolume") for i in range(3)
        },
    ),
    case(
        "delete-unexpected-error-isolated",
        one(*many("s", 4)),
        delete_errors={("us-west-2", "s2"): RuntimeError("no")},
    ),
    case(
        "delete-connection-error-skips-rest-of-region",
        {"us-west-2": Region([many("s", 4)]), "eu-west-1": Region([[vol("c")]])},
        delete_errors={("us-west-2", "s2"): conn_error()},
    ),
    case(
        "delete-error-in-dry-run-never-called",
        one(vol("a")),
        dry_run=True,
        delete_errors={("us-west-2", "a"): client_error("VolumeInUse", "DeleteVolume")},
    ),
    case(
        "delete-client-error-without-code",
        one(vol("a")),
        delete_errors={("us-west-2", "a"): ClientError({}, "DeleteVolume")},
    ),
    case("duplicate-ids-in-region", one(vol("a"), vol("a", 40), vol("b"))),
    case(
        "same-id-two-regions",
        {"us-west-2": Region([[vol("a")]]), "eu-west-1": Region([[vol("a")]])},
    ),
    case("volume-id-missing", one(vol(None), vol("b"))),
    case(
        "many-volumes", {"us-west-2": Region([many("v", 25)]), "eu-west-1": Region([many("x", 9)])}
    ),
    case("many-volumes-dry-run", one(*many("v", 25)), dry_run=True),
    case(
        "mixed-decisions",
        one(
            vol("old"),
            vol("young", 0.2),
            vol("prod", tag_list=[tag("yb_task", "prod")]),
            vol("attached", Attachments=[{"InstanceId": "i"}]),
            vol("k8s", tag_list=[tag("kubernetes.io/x", "1")]),
            vol("old2", 40),
        ),
    ),
    # regions
    case(
        "describe-regions-client-error-propagates",
        one(vol("a")),
        regions_error=client_error("UnauthorizedOperation", "DescribeRegions"),
    ),
    case(
        "describe-regions-connection-error-falls-back",
        one(vol("a")),
        regions_error=conn_error(),
    ),
    case(
        "describe-regions-connection-error-falls-back-dry-run",
        one(vol("a")),
        regions_error=conn_error(),
        dry_run=True,
    ),
]


class FakeEc2:
    def __init__(self, aws: "FakeAws", region: str) -> None:
        self.aws = aws
        self.region = region

    def describe_regions(self) -> dict[str, Any]:
        self.aws.calls.append(("describe_regions", self.region))
        if self.aws.case.regions_error is not None:
            raise self.aws.case.regions_error
        return {"Regions": [{"RegionName": r} for r in self.aws.case.regions]}

    def get_paginator(self, name: str) -> "FakePaginator":
        self.aws.calls.append(("get_paginator", self.region, name))
        return FakePaginator(self.aws, self.region)

    def delete_volume(self, *, VolumeId: str) -> dict[str, Any]:
        self.aws.calls.append(("delete_volume", self.region, VolumeId))
        error = self.aws.case.delete_errors.get((self.region, VolumeId))
        if error is not None:
            raise error
        return {"ResponseMetadata": {}}


class FakePaginator:
    def __init__(self, aws: "FakeAws", region: str) -> None:
        self.aws = aws
        self.region = region

    def paginate(self, *, Filters: Any) -> Any:
        self.aws.calls.append(("paginate", self.region, repr(Filters)))
        spec = self.aws.case.regions[self.region].pages
        if isinstance(spec, Exception):
            raise spec
        return self._pages(spec)

    @staticmethod
    def _pages(spec: Sequence[Any]) -> Any:
        for page in spec:
            if isinstance(page, Exception):
                raise page
            yield page if isinstance(page, dict) else {"Volumes": page}


class FakeCloudWatch:
    def __init__(self, aws: "FakeAws", region: str) -> None:
        self.aws = aws
        self.region = region
        self.page = 0

    def get_metric_data(self, **kwargs: Any) -> dict[str, Any]:
        self.aws.calls.append(("get_metric_data", self.region, repr(sorted(kwargs.items()))))
        spec = self.aws.case.regions[self.region]
        if spec.cw_error is not None:
            raise spec.cw_error
        maybe = [self._result(q, spec) for q in kwargs["MetricDataQueries"]]
        results = [r for r in maybe if r is not None]
        if not spec.paged:
            return {"MetricDataResults": results}
        self.page += 1
        if "NextToken" not in kwargs:
            partial = [{**r, "StatusCode": "PartialData"} for r in results if r["Values"]]
            return {"MetricDataResults": partial, "NextToken": "t1"}
        return {
            "MetricDataResults": [
                {"Id": q["Id"], "StatusCode": "Complete", "Values": []}
                for q in kwargs["MetricDataQueries"]
            ]
        }

    @staticmethod
    def _result(query: Mapping[str, Any], spec: Region) -> dict[str, Any] | None:
        volume_id = query["MetricStat"]["Metric"]["Dimensions"][0]["Value"]
        if volume_id in spec.missing:
            return None
        values = [1.0] if volume_id in spec.busy else []
        status = spec.status.get(volume_id, "Complete")
        return {"Id": query["Id"], "StatusCode": status, "Values": values}


class FakeAws:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []

    def client(self, service: str, region_name: str | None = None, config: Any = None) -> Any:
        cfg = None if config is None else (config.connect_timeout, config.read_timeout)
        self.calls.append(("client", service, region_name, cfg))
        assert region_name is not None
        if service == "cloudwatch":
            return FakeCloudWatch(self, region_name)
        return FakeEc2(self, region_name)

    def session(self) -> Any:
        self.calls.append(("session",))
        return types.SimpleNamespace(get_available_regions=lambda service: list(self.case.regions))


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[str]
    count: int
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_aws_disk")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    module.__dict__["datetime"] = types.SimpleNamespace(
        datetime=types.SimpleNamespace(now=lambda tz=None: NOW),
        timezone=datetime.timezone,
        timedelta=datetime.timedelta,
    )
    return module


def _kwargs(case: Case) -> dict[str, Any]:
    return {
        "filter_tags": None,
        "exception_tags": None,
        "age": None,
        "custom_age_tag_key": None,
        "notags": None,
        "name_regex": None,
        "exception_regex": None,
        "detach_age": None,
        **case.opts,
    }


def _legacy(case: Case) -> Any:
    return _load_oracle().Disk(case.dry_run, **_kwargs(case))


def _prepare(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeAws:
    aws = FakeAws(case)
    monkeypatch.setattr("boto3.client", aws.client)
    monkeypatch.setattr("boto3.session.Session", aws.session)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    caplog.clear()
    capsys.readouterr()
    return aws


def _logs(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [(r.name, r.levelno, r.getMessage()) for r in caplog.records]


def _run(
    build: Callable[[Case], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    aws = _prepare(case, monkeypatch, caplog, capsys)
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    obj = None
    try:
        obj = build(case)
        obj.delete()
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    logs = _logs(caplog)
    if obj is not None:
        deleted = list(obj.get_deleted)
    out = capsys.readouterr().out
    count = len(deleted)
    if obj is not None:
        caplog.clear()
        assert obj.count == count
    return Outcome(aws.calls, deleted, count, logs, out, error)


def _run_engine(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    aws = _prepare(case, monkeypatch, caplog, capsys)
    ctx = RunContext(
        dry_run=case.dry_run,
        operation=Operation.DELETE,
        options=_kwargs(case),
        now=NOW,
    )
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    try:
        outcome = run_through_engine("aws", "disk", build_disk_cleaner, ctx)
        if outcome.result is None:
            deleted = [c.id for c in outcome.plan.candidates]
        else:
            deleted = list(outcome.result.succeeded)
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    return Outcome(aws.calls, deleted, len(deleted), _logs(caplog), capsys.readouterr().out, error)


def _run_cleaner(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    aws = _prepare(case, monkeypatch, caplog, capsys)
    ctx = RunContext(
        dry_run=case.dry_run,
        operation=Operation.DELETE,
        options=_kwargs(case),
        now=NOW,
    )
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    try:
        cleaner = registry.get("aws", "disk")(ctx)
        plan = cleaner.plan(ctx)
        if case.dry_run:
            deleted = [c.id for c in plan.candidates]
        else:
            deleted = list(cleaner.apply(plan, ctx).succeeded)
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    return Outcome(aws.calls, deleted, len(deleted), _logs(caplog), capsys.readouterr().out, error)


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


def _is_delete_call(call: tuple[Any, ...]) -> bool:
    return bool(call[0] == "delete_volume")


def _is_delete_log(record: tuple[str, int, str]) -> bool:
    return record[2].startswith(DELETE_PREFIXES) or "failed considering volume" in record[2]


def _interleaves(case: Case) -> bool:
    return not case.dry_run and len(case.regions) > 1


def _assert_matches_main(case: Case, old: Outcome, new: Outcome) -> None:
    old = dataclasses.replace(old, logs=expected_logs(old.logs, FIXED_BUGS, rules=RULES))
    if case.id in DIVERGING:
        _check_divergence(case, old, new)
        return
    assert new.error == old.error
    assert new.stdout == old.stdout
    if new.error is None:
        assert new.deleted == old.deleted
        assert new.count == old.count
    if not _interleaves(case) or old.error is not None:
        assert new.calls == old.calls
        assert new.logs == old.logs
        return
    assert [c for c in new.calls if not _is_delete_call(c)] == [
        c for c in old.calls if not _is_delete_call(c)
    ]
    assert [c for c in new.calls if _is_delete_call(c)] == [
        c for c in old.calls if _is_delete_call(c)
    ]
    summary = [r for r in old.logs if "AWS EBS" in r[2]]
    old_rest = [r for r in old.logs if r not in summary]
    new_rest = [r for r in new.logs if "AWS EBS" not in r[2]]
    assert sorted(new_rest) == sorted(old_rest)
    assert [r for r in new.logs if "AWS EBS" in r[2]] == summary
    if not case.delete_errors:
        assert [r for r in new.logs if not _is_delete_log(r) and "AWS EBS" not in r[2]] == [
            r for r in old.logs if not _is_delete_log(r) and "AWS EBS" not in r[2]
        ]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_engine_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_engine(case, monkeypatch, caplog, capsys)
    _assert_matches_main(case, old, new)


def test_skip_lines_are_debug_only_in_new_code(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """BUG-356: every INFO "Skipping volume ..." line of the oracle is DEBUG now."""
    differing = 0
    for case in CASES:
        old = _run(_legacy, case, monkeypatch, caplog, capsys)
        new = _run_engine(case, monkeypatch, caplog, capsys)
        if case.id in DIVERGING:
            skip_levels = {lvl for _, lvl, msg in new.logs if msg.startswith("Skipping volume ")}
            assert skip_levels == {logging.DEBUG}, case.id
            continue
        assert sorted(new.logs) == sorted(expected_logs(old.logs, FIXED_BUGS, rules=RULES)), case.id
        differing += sorted(old.logs) != sorted(new.logs)
    assert differing > 0


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_cleaner_protocol_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A dry run (plan only) and a single region are identical to main, call for call and log
    line for line. A real run over several regions lists every region before it deletes, so
    only the interleaving of the delete calls and delete log lines with the other regions'
    listing differs; each stream, the results and the errors are equal."""
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    _assert_matches_main(case, old, new)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_cleaner_error_path_never_deletes_after_listing_error(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    if new.error is not None or case.dry_run:
        assert not any(_is_delete_call(c) for c in new.calls)
    else:
        reads = [c for c in new.calls if not _is_delete_call(c)]
        deletes = [c for c in new.calls if _is_delete_call(c)]
        assert new.calls == reads + deletes
        assert len(deletes) >= len(new.deleted)


def _flatten(outcome: Outcome) -> str:
    return "\n".join(r[2] for r in outcome.logs)


def _delete_ids(calls: Sequence[tuple[Any, ...]]) -> list[str]:
    return [str(c[-1]) for c in calls if _is_delete_call(c)]


def _check_divergence(case: Case, old: Outcome, new: Outcome) -> None:
    """The oracle shows the bug, and the new code matches the exact pinned outcome
    (calls, deleted labels, count, logs, stdout, errors)."""
    spec = DIVERGING[case.id]
    bug = spec.bug
    assert new.error == old.error is None, bug
    assert new.stdout == old.stdout == "", bug
    assert old.deleted == list(spec.old_deleted), f"oracle no longer shows {bug}"
    assert new.deleted == list(spec.new_deleted), bug
    assert new.count == len(spec.new_deleted), bug
    assert set(new.deleted) < set(old.deleted), bug
    old_ids = _delete_ids(old.calls)
    assert len(old_ids) > len(spec.new_delete_ids), f"oracle no longer shows {bug}"
    assert _delete_ids(new.calls) == list(spec.new_delete_ids), bug
    old_reads = [c for c in old.calls if not _is_delete_call(c)]
    new_reads = [c for c in new.calls if not _is_delete_call(c)]
    if case.id == "yb-task-key-variants":
        assert new_reads == old_reads, bug
    else:
        assert new_reads[:-1] == old_reads[:-1], bug
        assert new_reads[-1][:2] == ("get_metric_data", "us-west-2"), bug
        assert "'Value': 'a'" in new_reads[-1][2] and "'Value': 'b'" not in new_reads[-1][2], bug
    assert new.logs == list(spec.new_logs), bug


@dataclass(frozen=True)
class FixedQuirk:
    case_id: str
    bug_id: str
    old_check: Callable[[Outcome], bool]
    new_check: Callable[[Outcome], bool]


FIXED_QUIRKS: Sequence[FixedQuirk] = [
    FixedQuirk(
        "yb-task-key-variants",
        "BUG-282",
        lambda o: (
            o.deleted == ["us-west-2/d yb_task=dev", "us-west-2/e", "us-west-2/f yb_task=dev"]
        ),
        lambda o: (
            o.deleted == ["us-west-2/f yb_task=dev"]
            and "Skipping volume d: sensitive yb_task=prod" in _flatten(o)
            and "Skipping volume e: sensitive yb_task=prod" in _flatten(o)
        ),
    ),
    FixedQuirk(
        "detach-age-and-age",
        "BUG-283",
        lambda o: o.deleted == ["us-west-2/a yb_task=itest", "us-west-2/b yb_task=itest"],
        lambda o: (
            o.deleted == ["us-west-2/a yb_task=itest"]
            and "Skipping volume b: detach time unknown and CreateTime younger than "
            "age plus detach_age"
            in _flatten(o)
        ),
    ),
]


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("no-age-gate", lambda o: o.error is not None and o.error[0] == "ValueError" and o.calls == []),
    ("age-int", lambda o: o.deleted == ["us-west-2/a yb_task=itest"]),
    ("age-true", lambda o: o.deleted == ["us-west-2/a yb_task=itest"]),
    ("age-string-value", lambda o: o.error is not None and o.error[0] == "ValueError"),
    ("age-overflow", lambda o: o.error is not None and o.error[0] == "OverflowError"),
    ("age-float-inf", lambda o: o.error is not None and o.error[0] == "OverflowError"),
    ("age-float-value", lambda o: o.deleted == ["us-west-2/a yb_task=itest"]),
    (
        "age-exactly-floor",
        lambda o: o.deleted == ["us-west-2/a yb_task=itest", "us-west-2/c yb_task=itest"],
    ),
    ("age-future-create-time", lambda o: o.deleted == []),
    ("create-time-missing", lambda o: o.deleted == ["us-west-2/b yb_task=itest"]),
    (
        "create-time-naive",
        lambda o: (
            o.error is None and o.deleted == [] and "skipping volume a after error" in _flatten(o)
        ),
    ),
    (
        "create-time-string",
        lambda o: o.deleted == [] and "skipping volume a after error" in _flatten(o),
    ),
    ("untagged-skipped", lambda o: o.deleted == []),
    ("untagged-included-with-notags", lambda o: len(o.deleted) == TWO),
    ("tags-without-keys-notags", lambda o: o.deleted == ["us-west-2/a"]),
    ("tag-value-none", lambda o: o.error is None and o.deleted == ["us-west-2/a"]),
    ("tag-entry-not-a-dict", lambda o: o.deleted == ["us-west-2/b yb_task=itest"]),
    ("label-yb-task-dash-key", lambda o: o.deleted == ["us-west-2/a yb_task=dev"]),
    ("label-yb-task-wins", lambda o: o.deleted == ["us-west-2/a yb_task=t"]),
    ("label-name-tag", lambda o: o.deleted == ["us-west-2/a Name=mydisk"]),
    (
        "filter-tags-glob",
        lambda o: o.deleted == ["us-west-2/a Name=jenkins-1", "us-west-2/c Name=jenkins-"],
    ),
    ("filter-tags-case-sensitive", lambda o: o.deleted == []),
    ("filter-tags-values-none", lambda o: len(o.deleted) == 1),
    ("exception-first-tag-logged", lambda o: "exception tag b=" in _flatten(o)),
    ("notags-all-match-skips", lambda o: o.deleted == ["us-west-2/b"]),
    ("name-regex-uses-id-without-name", lambda o: o.deleted == ["us-west-2/vol-abc"]),
    ("exception-regex-on-volume-id", lambda o: o.deleted == ["us-west-2/vol-del"]),
    ("name-regex-invalid", lambda o: o.error is not None and o.error[0] == "ValueError"),
    ("exception-regex-invalid", lambda o: o.error is not None and o.error[0] == "ValueError"),
    ("yb-task-sensitive-values", lambda o: o.deleted == ["us-west-2/v8 yb_task=dev"]),
    (
        "yb-task-sensitive-tokens",
        lambda o: o.deleted == ["us-west-2/v5 yb_task=productive", "us-west-2/v6 yb_task=vpnx"],
    ),
    ("yb-task-non-string", lambda o: o.error is None and o.deleted == []),
    ("kubernetes-skipped", lambda o: o.deleted == ["us-west-2/e"]),
    ("kubernetes-allowed-with-filter", lambda o: len(o.deleted) == 1),
    ("custom-age-tag-zero-skips", lambda o: o.deleted == []),
    ("custom-age-tag-unparseable-fails-open", lambda o: o.deleted == []),
    ("custom-age-tag-empty-fails-open", lambda o: o.deleted == []),
    ("custom-age-tag-zero-literal-fails-open", lambda o: o.deleted == []),
    (
        "custom-age-tag-overflow",
        lambda o: o.deleted == [] and "skipping volume a after error" not in _flatten(o),
    ),
    ("custom-age-tag-detach-only-uses-max", lambda o: len(o.deleted) == 1),
    ("detach-age-too-long", lambda o: o.error is not None and o.error[0] == "ValueError"),
    ("detach-age-max-lookback", lambda o: o.error is None and len(o.deleted) == 1),
    ("detach-busy-volume-dropped", lambda o: o.deleted == ["us-west-2/idle yb_task=itest"]),
    (
        "detach-incomplete-status-kept",
        lambda o: (
            o.deleted == ["us-west-2/idle yb_task=itest"] and "StatusCode=Forbidden" in _flatten(o)
        ),
    ),
    ("detach-missing-result-kept", lambda o: o.deleted == ["us-west-2/idle yb_task=itest"]),
    (
        "detach-cloudwatch-client-error",
        lambda o: o.deleted == [] and "CloudWatch detach-age lookup failed" in _flatten(o),
    ),
    (
        "detach-cloudwatch-connection-error",
        lambda o: o.deleted == [] and "CloudWatch detach-age lookup failed" in _flatten(o),
    ),
    (
        "detach-cloudwatch-unexpected-error",
        lambda o: (
            "error during EBS disk cleanup" in _flatten(o)
            and o.deleted == ["eu-west-1/b yb_task=itest"]
        ),
    ),
    ("detach-paged-metrics", lambda o: o.deleted == ["us-west-2/idle yb_task=itest"]),
    ("detach-many-volumes-batches", lambda o: len(o.deleted) == MANY - 3),
    (
        "detach-none-candidates-no-cloudwatch-client",
        lambda o: not any(c[:2] == ("client", "cloudwatch") for c in o.calls),
    ),
    (
        "describe-client-error-generic",
        lambda o: (
            o.deleted == ["eu-west-1/b yb_task=itest"]
            and "ClientError during EBS disk cleanup" in _flatten(o)
        ),
    ),
    (
        "describe-client-error-AuthFailure",
        lambda o: (
            o.deleted == ["eu-west-1/b yb_task=itest"]
            and "skipped EBS disk cleanup (AuthFailure)" in _flatten(o)
        ),
    ),
    (
        "describe-client-error-OptInRequired",
        lambda o: "skipped EBS disk cleanup (OptInRequired)" in _flatten(o),
    ),
    (
        "describe-connection-error-skips-region",
        lambda o: o.deleted == ["eu-west-1/b yb_task=itest"],
    ),
    (
        "describe-unexpected-error",
        lambda o: o.error is None and o.deleted == ["eu-west-1/b yb_task=itest"],
    ),
    ("describe-error-after-first-page", lambda o: o.deleted == ["eu-west-1/b yb_task=itest"]),
    (
        "volume-error-isolated",
        lambda o: len(o.deleted) == TWO and "AWS EBS disk cleanup did not complete" in _flatten(o),
    ),
    (
        "delete-client-error-isolated",
        lambda o: (
            o.error is None
            and len(o.deleted) == THREE
            and "AWS EBS disk cleanup did not complete" in _flatten(o)
        ),
    ),
    ("delete-client-error-every-volume", lambda o: o.error is None and o.deleted == []),
    (
        "delete-unexpected-error-isolated",
        lambda o: len(o.deleted) == THREE and "failed considering volume s2" in _flatten(o),
    ),
    (
        "delete-connection-error-skips-rest-of-region",
        lambda o: (
            o.deleted
            == [
                "us-west-2/s0 yb_task=itest",
                "us-west-2/s1 yb_task=itest",
                "eu-west-1/c yb_task=itest",
            ]
        ),
    ),
    (
        "delete-client-error-without-code",
        lambda o: "Failed to delete volume a (None)" in _flatten(o),
    ),
    ("duplicate-ids-in-region", lambda o: len(o.deleted) == THREE),
    ("same-id-two-regions", lambda o: len(o.deleted) == TWO),
    (
        "volume-id-missing",
        lambda o: o.deleted == ["us-west-2/None yb_task=itest", "us-west-2/b yb_task=itest"],
    ),
    (
        "mixed-decisions",
        lambda o: o.deleted == ["us-west-2/old yb_task=itest", "us-west-2/old2 yb_task=itest"],
    ),
    ("describe-regions-client-error-propagates", lambda o: o.error is not None and o.deleted == []),
    (
        "describe-regions-connection-error-falls-back",
        lambda o: o.deleted == ["us-west-2/a yb_task=itest"],
    ),
]


BUG_004_FIXED = frozenset(
    [
        "custom-age-tag-empty-fails-open",
        "custom-age-tag-overflow",
        "custom-age-tag-unparseable-fails-open",
        "custom-age-tag-zero-literal-fails-open",
    ]
)


@pytest.mark.parametrize(("case_id", "check"), QUIRKS, ids=[q[0] for q in QUIRKS])
def test_quirk_is_preserved(
    case_id: str,
    check: Callable[[Outcome], bool],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected = next(c for c in CASES if c.id == case_id)
    old = _run(_legacy, selected, monkeypatch, caplog, capsys)
    new = _run_engine(selected, monkeypatch, caplog, capsys)
    # BUG-004/BUG-016: the oracle class inherits the live crc.service.Service, so it cannot
    # show the old custom-age-tag behaviour here; tests/test_crc_service_parity.py pins it
    # against the frozen crc_service oracle. Fixed cases assert the new outcome only.
    if case_id not in BUG_004_FIXED:
        assert check(old), "oracle no longer shows the quirk"
    assert check(new)


@pytest.mark.parametrize(
    "quirk",
    FIXED_QUIRKS,
    ids=[f"{q.case_id}-{q.bug_id}" for q in FIXED_QUIRKS],
)
def test_fixed_quirk_is_pinned(
    quirk: FixedQuirk,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected = next(c for c in CASES if c.id == quirk.case_id)
    old = _run(_legacy, selected, monkeypatch, caplog, capsys)
    new = _run_engine(selected, monkeypatch, caplog, capsys)
    assert quirk.old_check(old), f"oracle no longer shows {quirk.bug_id}"
    assert not quirk.new_check(old), f"oracle unexpectedly satisfies fixed check for {quirk.bug_id}"
    assert quirk.new_check(new), quirk.bug_id


def test_cases_cover_both_dry_run_and_real_runs() -> None:
    assert any(c.dry_run for c in CASES)
    assert any(not c.dry_run for c in CASES)


def test_factory_registers_the_cleaner() -> None:
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options=BASE, now=NOW)
    cleaner = registry.get("aws", "disk")(ctx)
    assert isinstance(cleaner, DiskCleaner)
    assert registry.get("aws", "disk") is build_disk_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("aws", "disk")
    assert cleaner.supports(Operation.DELETE)
    assert not cleaner.supports(Operation.STOP)


def test_factory_requires_an_age_gate() -> None:
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    with pytest.raises(ValueError, match="requires an age gate"):
        registry.get("aws", "disk")(ctx)


def test_second_delete_accumulates_and_count_logs_like_main(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected = case("two-calls", one(vol("a")))
    aws = _prepare(selected, monkeypatch, caplog, capsys)
    disk = _legacy(selected)
    disk.delete()
    disk.delete()
    assert disk.get_deleted == [
        "us-west-2/a yb_task=itest",
        "us-west-2/a yb_task=itest",
    ]
    old_calls = list(aws.calls)
    caplog.clear()
    assert disk.count == TWO
    assert _logs(caplog) == [("root", logging.INFO, "count of items in disks_to_delete: 2")]
    aws = _prepare(selected, monkeypatch, caplog, capsys)
    ctx = RunContext(dry_run=False, operation=Operation.DELETE, options=_kwargs(selected), now=NOW)
    deleted: list[str] = []
    for _ in range(2):
        outcome = run_through_engine("aws", "disk", build_disk_cleaner, ctx)
        assert outcome.result is not None
        deleted.extend(outcome.result.succeeded)
    assert deleted == ["us-west-2/a yb_task=itest", "us-west-2/a yb_task=itest"]
    assert aws.calls == old_calls


def test_cleaner_helpers_match_main(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected = case(
        "helpers",
        one(vol("idle"), vol("busy"), busy=["busy"]),
        opts={
            "filter_tags": {"yb_task": ["itest*"], "yb_owner": []},
            "exception_tags": {"keep": ["1"]},
            "notags": {"skip": ["yes"]},
            "name_regex": ["^foo"],
            "exception_regex": ["never"],
            "detach_age": {"days": 3},
        },
    )
    _prepare(selected, monkeypatch, caplog, capsys)
    old = _legacy(selected)
    ctx = RunContext(dry_run=False, operation=Operation.DELETE, options=_kwargs(selected), now=NOW)
    cleaner = build_disk_cleaner(ctx)
    assert isinstance(cleaner, DiskCleaner)
    assert policy.SENSITIVE_YB_TASK == old.SENSITIVE_YB_TASK
    assert core_age.AGE_KEYS == old.AGE_KEYS
    assert normalize_age({"days": "2", "hours": 4}, "age") == old._normalize_age(
        {"days": "2", "hours": 4}, "age"
    )
    assert policy.age_to_timedelta(5) == old._age_to_timedelta(5)
    assert server_filters(_kwargs(selected)["filter_tags"]) == old._volume_filters()
    for sample in (
        {"yb_task": "itest", "yb_owner": "qa"},
        {"yb_task": "prod", "yb_owner": "qa"},
        {"yb_task": "itest", "yb_owner": "qa", "keep": "1"},
    ):
        caplog.clear()
        old_match = old._matches_filter_tags(sample)
        old_skip = old._should_skip(sample, "foo", "vol-1")
        old_logs = _logs(caplog)
        caplog.clear()
        assert cleaner.filter.matches_filter_tags(sample) == old_match
        assert cleaner.filter.should_skip(sample, "foo", "vol-1") == old_skip
        assert _logs(caplog) == expected_logs(old_logs, FIXED_BUGS, rules=RULES)
    candidate_vol = vol(
        "idle", tag_list=[tag("yb_task", "itest"), tag("yb_owner", "qa"), tag("Name", "foo")]
    )
    assert cleaner.filter.is_candidate(candidate_vol, NOW) == old._is_candidate(candidate_vol)
    vols: list[Mapping[str, Any]] = [vol("idle"), vol("busy")]
    window = policy.age_to_timedelta(selected.opts["detach_age"])
    assert cleaner.detach_guard.drop_recently_attached(
        "us-west-2", vols, window, NOW
    ) == old._drop_recently_attached("us-west-2", vols)
