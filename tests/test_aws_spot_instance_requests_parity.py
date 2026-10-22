"""Parity: `crc.aws.spot_instance_requests` against main's version
(`tests/legacy/aws_spot_instance_requests.txt`).

Every case runs against the same stateful fake EC2 client. SDK calls (in order), results,
log records and raised errors must be identical. The table runs three times: the oracle vs
the legacy adapter class, and the oracle vs the registered Cleaner (`plan()` then `apply()`,
or `plan()` only in a dry run).
"""

import copy
import dataclasses
import datetime
import logging
import re
import types
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from crc.aws.spot_instance_requests import SpotInstanceRequests, SpotRuntime
from crc.aws.spot_instance_requests.model import Waiter
from crc.core import registry
from crc.core.cleaner import Operation, RunContext
from crc.core.report import DISK
from tests.helpers_log_fix import expected_logs
from tests.log_fix_aws_compute import RULES

FIXED_BUGS = {"BUG-356"}

ORACLE = Path(__file__).parent / "legacy" / "aws_spot_instance_requests.txt"
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
QA = [{"Key": "yb_owner", "Value": "qa"}]
COUNT_LOG = "count of spot requests"


def ce(code: str, op: str = "Op") -> Exception:
    error: Exception = ClientError({"Error": {"Code": code, "Message": "x"}}, op)
    return error


def conn() -> Exception:
    error: Exception = EndpointConnectionError(endpoint_url="https://ec2.example")
    return error


def req(
    rid: str = "sir-1",
    instance: str | None = "i-1",
    rtype: Any = "persistent",
    days: float = 7,
    tags: Any = None,
    state: str = "active",
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "SpotInstanceRequestId": rid,
        "State": state,
        "CreateTime": NOW - datetime.timedelta(days=days),
        "Tags": QA if tags is None else tags,
    }
    if instance is not None:
        out["InstanceId"] = instance
    if rtype is not None:
        out["Type"] = rtype
    return out


def inst(state: str = "running", dot_false: tuple[str, ...] = ("vol-1",)) -> dict[str, Any]:
    maps = [{"Ebs": {"VolumeId": v, "DeleteOnTermination": False}} for v in dot_false]
    maps.append({"Ebs": {"VolumeId": "vol-root", "DeleteOnTermination": True}})
    return {"State": {"Name": state}, "BlockDeviceMappings": maps}


def vol(
    state: str = "in-use", tags: Any = None, days: float = 10, attached: str | None = "i-1"
) -> dict[str, Any]:
    return {
        "State": state,
        "Tags": tags or [],
        "CreateTime": NOW - datetime.timedelta(days=days),
        "attached": attached,
    }


@dataclass
class Spec:
    requests: list[dict[str, Any]] = field(default_factory=list)
    instances: dict[str, Any] = field(default_factory=dict)
    volumes: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: dict[str, Exception] = field(default_factory=dict)
    key_errors: dict[tuple[str, str], Exception] = field(default_factory=dict)
    page_size: int | None = None
    release_on_terminate: bool = True
    terminate_error_state: str | None = None


@dataclass
class Case:
    id: str
    regions: dict[str, Spec]
    dry_run: bool = False
    filter_tags: Any = field(default_factory=lambda: {"yb_owner": ["qa"]})
    exception_tags: Any = field(default_factory=dict)
    notags: Any = field(default_factory=dict)
    age: Any = field(default_factory=lambda: {"days": 1})
    custom: Any = None
    regions_error: Exception | None = None
    client_errors: dict[str, Exception] = field(default_factory=dict)


def one(spec: Spec, **kw: Any) -> dict[str, Spec]:
    return {"us-west-2": spec}


def std(**kw: Any) -> Spec:
    """A running persistent request with one DoT=false volume."""
    base: dict[str, Any] = {
        "requests": [req()],
        "instances": {"i-1": inst()},
        "volumes": {"vol-1": vol()},
    }
    base.update(kw)
    return Spec(**base)


def sweep_spec(volume: dict[str, Any], **kw: Any) -> Spec:
    base: dict[str, Any] = {"requests": [], "volumes": {"vol-s": volume}}
    base.update(kw)
    return Spec(**base)


LEFT = "crc_leftover_of"
PFX = "crc_leftover_tag_"
GONE = [ce("InvalidSpotInstanceRequestID.NotFound")]


def tagged(rid: str, *extra: dict[str, str]) -> list[dict[str, str]]:
    return [{"Key": LEFT, "Value": rid}, {"Key": PFX + "yb_owner", "Value": "qa"}, *extra]


def swept(rid: str = "sir-gone", days: float = 10, tags: Any = None, **kw: Any) -> Spec:
    volume = vol("available", tagged(rid) if tags is None else tags, days, None)
    return sweep_spec(volume, **kw)


CASES: list[Case] = [
    Case("dry-run", one(std()), dry_run=True),
    Case("real", one(std())),
    Case("real-no-instance-one-time", one(Spec([req(instance=None, rtype="one-time")]))),
    Case("dry-no-instance", one(Spec([req(instance=None)])), dry_run=True),
    Case(
        "dry-already-cancelled-running", one(std(requests=[req(state="cancelled")])), dry_run=True
    ),
    Case("real-already-cancelled-running", one(std(requests=[req(state="cancelled")]))),
    Case(
        "cancelled-instance-gone-skipped",
        one(Spec([req(state="cancelled")], {"i-1": inst("terminated", ())})),
    ),
    Case("cancelled-instance-notfound-skipped", one(Spec([req(state="cancelled")]))),
    Case("cancelled-without-instance", one(Spec([req(state="cancelled", instance=None)]))),
    Case(
        "retry-tagged-leftover-after-instance-gone",
        one(
            Spec(
                [req(state="cancelled")],
                {"i-1": inst("terminated", ())},
                {"vol-1": vol("available", tagged("sir-1"), attached=None)},
            )
        ),
    ),
    Case(
        "retry-already-gone-volume-not-recounted",
        one(Spec([req(state="cancelled")], {"i-1": inst("shutting-down", ("vol-1",))}, {})),
    ),
    Case("untagged", one(Spec([req(tags=[])], {"i-1": inst()})), filter_tags={}),
    Case("untagged-key-missing", one(Spec([{**req(), "Tags": None}])), filter_tags={}),
    Case("age-young", one(std(requests=[req(days=0.5)]))),
    Case("age-exactly-one-day", one(std(requests=[req(days=1)]))),
    Case("age-just-under", one(std(requests=[req(days=0.999)]))),
    Case("age-hours", one(std(requests=[req(days=0.2)])), age={"hours": 4}),
    Case("age-days-hours", one(std(requests=[req(days=1.1)])), age={"days": 1, "hours": 3}),
    Case("age-zero-raises", one(std()), age={"days": 0}),
    Case("age-empty-raises", one(std()), age={}),
    Case("age-bad-key-raises", one(std()), age={"weeks": 1}),
    Case(
        "custom-age-shorter",
        one(std(requests=[req(days=0.2, tags=[*QA, {"Key": "keep", "Value": "{'hours': 1}"}])])),
        custom="keep",
    ),
    Case(
        "custom-age-longer-keeps",
        one(std(requests=[req(days=3, tags=[*QA, {"Key": "keep", "Value": "{'days': 10}"}])])),
        custom="keep",
    ),
    Case(
        "custom-age-empty-skips",
        one(std(requests=[req(tags=[*QA, {"Key": "keep", "Value": ""}])])),
        custom="keep",
    ),
    Case(
        "custom-age-unparseable-skips",
        one(std(requests=[req(tags=[*QA, {"Key": "keep", "Value": "soon"}])])),
        custom="keep",
    ),
    Case(
        "custom-age-zero-skips",
        one(std(requests=[req(tags=[*QA, {"Key": "keep", "Value": "{'days': 0}"}])])),
        custom="keep",
    ),
    Case("custom-age-absent-uses-age", one(std()), custom="keep"),
    Case(
        "filter-glob-match",
        one(std(requests=[req(tags=[{"Key": "yb_owner", "Value": "qa-1"}])])),
        filter_tags={"yb_owner": ["qa-*"]},
    ),
    Case(
        "filter-glob-no-match",
        one(std(requests=[req(tags=[{"Key": "yb_owner", "Value": "dev"}])])),
        filter_tags={"yb_owner": ["qa-*"]},
    ),
    Case("filter-key-missing", one(std(requests=[req(tags=[{"Key": "other", "Value": "x"}])]))),
    Case("filter-key-only", one(std()), filter_tags={"yb_owner": []}),
    Case("filter-two-keys-one-missing", one(std()), filter_tags={"yb_owner": ["qa"], "env": ["x"]}),
    Case("filter-empty-selects-all-tagged", one(std()), filter_tags={}),
    Case("filter-none", one(std()), filter_tags=None),
    Case(
        "exception-tag-skips",
        one(std(requests=[req(tags=[*QA, {"Key": "keep"}])])),
        exception_tags={"keep": []},
    ),
    Case(
        "exception-tag-value-list",
        one(std(requests=[req(tags=[*QA, {"Key": "env", "Value": "prod"}])])),
        exception_tags={"env": ["prod"]},
    ),
    Case(
        "exception-tag-other-value",
        one(std(requests=[req(tags=[*QA, {"Key": "env", "Value": "dev"}])])),
        exception_tags={"env": ["prod"]},
    ),
    Case(
        "notags-all-match-skips",
        one(std(requests=[req(tags=[*QA, {"Key": "a", "Value": "1"}])])),
        notags={"a": [], "yb_owner": ["qa"]},
    ),
    Case("notags-partial-keeps", one(std()), notags={"a": [], "yb_owner": ["qa"]}),
    Case(
        "duplicate-tag-keys",
        one(
            std(
                requests=[
                    req(tags=[*QA, {"Key": "env", "Value": "prod"}, {"Key": "env", "Value": "dev"}])
                ]
            )
        ),
        exception_tags={"env": ["prod"]},
    ),
    Case(
        "tag-without-value",
        one(std(requests=[req(tags=[*QA, {"Key": "x"}])])),
        exception_tags={"x": ["v"]},
    ),
    Case(
        "type-default-and-upper",
        one(
            std(
                requests=[req(rtype=None), req("sir-2", "i-2", "ONE-TIME")],
                instances={"i-1": inst(), "i-2": inst(dot_false=("vol-2",))},
                volumes={"vol-1": vol(), "vol-2": vol(attached="i-2")},
            )
        ),
    ),
    Case(
        "naive-create-time-error-logged",
        one(std(requests=[{**req(), "CreateTime": datetime.datetime(2020, 1, 1)}])),
    ),
    Case(
        "missing-create-time-error-logged",
        one(
            std(
                requests=[
                    {k: v for k, v in req().items() if k != "CreateTime"},
                    req("sir-2", "i-1"),
                ]
            )
        ),
    ),
    Case(
        "missing-request-id-error-logged",
        one(std(requests=[{k: v for k, v in req().items() if k != "SpotInstanceRequestId"}])),
    ),
    Case("empty-results", one(Spec())),
    Case(
        "pagination",
        one(
            std(requests=[req("sir-1", None), req("sir-2", None), req("sir-3", None)], page_size=1)
        ),
    ),
    Case("regions-connectivity-fallback", one(std()), regions_error=conn()),
    Case("regions-auth-error", one(std()), regions_error=ce("AuthFailure")),
    Case(
        "region-client-connectivity-skipped",
        {"us-west-2": std(), "eu-west-1": std()},
        client_errors={"eu-west-1": conn()},
    ),
    Case(
        "describe-requests-client-error",
        one(std(errors={"describe_spot_instance_requests": ce("UnauthorizedOperation")})),
    ),
    Case(
        "describe-requests-connectivity-error",
        one(std(errors={"describe_spot_instance_requests": conn()})),
    ),
    Case(
        "describe-requests-generic-error",
        one(std(errors={"describe_spot_instance_requests": RuntimeError("boom")})),
    ),
    Case("two-regions-mixed", {"us-west-2": std(), "eu-west-1": Spec([req("sir-9", None)])}),
    Case(
        "tag-volume-fails-no-cancel", one(std(errors={"create_tags": ce("UnauthorizedOperation")}))
    ),
    Case(
        "tag-volume-describe-fails-no-cancel",
        one(std(key_errors={("describe_volumes", "vol-1"): ce("RequestLimitExceeded")})),
    ),
    Case("tag-volume-botocore-error", one(std(errors={"create_tags": conn()}))),
    Case(
        "cancel-fails-client-error",
        one(std(errors={"cancel_spot_instance_requests": ce("UnauthorizedOperation")})),
    ),
    Case("cancel-fails-botocore", one(std(errors={"cancel_spot_instance_requests": conn()}))),
    Case(
        "cancel-fails-delete-tags-denied",
        one(
            std(
                errors={
                    "cancel_spot_instance_requests": ce("UnauthorizedOperation"),
                    "delete_tags": ce("UnauthorizedOperation"),
                }
            )
        ),
    ),
    Case(
        "cancel-fails-all-tag-writes-denied",
        one(
            std(
                errors={
                    "cancel_spot_instance_requests": ce("UnauthorizedOperation"),
                    "delete_tags": ce("UnauthorizedOperation"),
                    "create_tags": ce("UnauthorizedOperation"),
                }
            )
        ),
    ),
    Case(
        "terminate-fails-client-error", one(std(errors={"terminate_instances": ce("Unauthorized")}))
    ),
    Case(
        "terminate-not-found",
        one(std(errors={"terminate_instances": ce("InvalidInstanceID.NotFound")})),
    ),
    Case(
        "terminate-botocore-shutting-down",
        one(std(errors={"terminate_instances": conn()}, terminate_error_state="shutting-down")),
    ),
    Case(
        "terminate-botocore-terminated",
        one(std(errors={"terminate_instances": conn()}, terminate_error_state="terminated")),
    ),
    Case("terminate-botocore-still-running", one(std(errors={"terminate_instances": conn()}))),
    Case(
        "terminate-generic-exception", one(std(errors={"terminate_instances": RuntimeError("x")}))
    ),
    Case(
        "describe-instances-client-error-aborts",
        one(std(errors={"describe_instances": ce("RequestLimitExceeded")})),
    ),
    Case("describe-instances-botocore-aborts", one(std(errors={"describe_instances": conn()}))),
    Case("describe-instances-not-found", one(Spec([req()]))),
    Case(
        "describe-instances-malformed",
        one(Spec([req()], errors={"describe_instances": ce("InvalidInstanceID.Malformed")})),
    ),
    Case(
        "instance-two-dot-false-volumes",
        one(
            std(
                instances={"i-1": inst(dot_false=("vol-1", "vol-2"))},
                volumes={"vol-1": vol(), "vol-2": vol()},
            )
        ),
    ),
    Case("instance-no-dot-false-no-tagged", one(std(instances={"i-1": inst(dot_false=())}))),
    Case(
        "volume-sensitive-yb-task",
        one(std(volumes={"vol-1": vol(tags=[{"Key": "yb_task", "Value": "Prod-db"}])})),
    ),
    Case(
        "volume-sensitive-yb-task-dash-key",
        one(std(volumes={"vol-1": vol(tags=[{"Key": "yb-task", "Value": "vpn"}])})),
    ),
    Case(
        "volume-yb-task-not-sensitive",
        one(std(volumes={"vol-1": vol(tags=[{"Key": "yb_task", "Value": "unit-test"}])})),
    ),
    Case(
        "volume-yb-task-case-key",
        one(std(volumes={"vol-1": vol(tags=[{"Key": "YB_TASK", "Value": "prod"}])})),
    ),
    Case(
        "volume-exception-tag",
        one(std(volumes={"vol-1": vol(tags=[{"Key": "keep"}])})),
        exception_tags={"keep": []},
    ),
    Case(
        "volume-notags",
        one(std(volumes={"vol-1": vol(tags=[{"Key": "a", "Value": "1"}])})),
        notags={"a": ["1"]},
    ),
    Case(
        "volume-exception-dry-run",
        one(std(volumes={"vol-1": vol(tags=[{"Key": "keep"}])})),
        exception_tags={"keep": []},
        dry_run=True,
    ),
    Case(
        "volume-describe-fails-fail-closed",
        one(std(key_errors={("describe_volumes", "vol-1"): ce("RequestLimitExceeded")})),
    ),
    Case("volume-wait-timeout", one(std(release_on_terminate=False))),
    Case("volume-not-found-on-delete", one(std(volumes={}))),
    Case("volume-delete-client-error", one(std(errors={"delete_volume": ce("VolumeInUse")}))),
    Case("volume-delete-botocore", one(std(errors={"delete_volume": conn()}))),
    Case(
        "volume-delete-generic-error-isolated",
        one(std(errors={"delete_volume": RuntimeError("x")})),
    ),
    Case(
        "volume-wait-describe-error",
        one(std(key_errors={("describe_volumes", "vol-1"): ce("RequestLimitExceeded")})),
    ),
    Case("sweep-gone-request", one(swept())),
    Case("sweep-gone-request-dry-run", one(swept()), dry_run=True),
    Case("sweep-live-request", one(swept("sir-live", requests=[req("sir-live", None)]))),
    Case(
        "sweep-live-request-young",
        one(swept("sir-live", requests=[req("sir-live", None, days=0.1)])),
    ),
    Case(
        "sweep-live-request-exception",
        one(swept("sir-live", requests=[req("sir-live", None, tags=[*QA, {"Key": "keep"}])])),
        exception_tags={"keep": []},
    ),
    Case(
        "sweep-live-request-untagged",
        one(swept("sir-live", requests=[req("sir-live", None, tags=[])])),
        filter_tags={},
    ),
    Case(
        "sweep-live-request-filter-mismatch",
        one(
            swept(
                "sir-live",
                requests=[req("sir-live", None, tags=[{"Key": "yb_owner", "Value": "dev"}])],
            )
        ),
    ),
    Case(
        "sweep-live-request-bad-custom-age",
        one(
            swept(
                "sir-live",
                requests=[req("sir-live", None, tags=[*QA, {"Key": "keep", "Value": "x"}])],
            )
        ),
        custom="keep",
    ),
    Case("sweep-invalid-sentinel", one(swept(tags=[{"Key": LEFT, "Value": "terminate-failed"}]))),
    Case("sweep-no-marker", one(swept(tags=[{"Key": "other"}]))),
    Case("sweep-young-volume", one(swept(days=0.2))),
    Case(
        "sweep-filter-mismatch-copy",
        one(
            swept(
                tags=[{"Key": LEFT, "Value": "sir-gone"}, {"Key": PFX + "yb_owner", "Value": "dev"}]
            )
        ),
    ),
    Case(
        "sweep-exception-copy",
        one(swept(tags=tagged("sir-gone", {"Key": PFX + "keep", "Value": "1"}))),
        exception_tags={"keep": []},
    ),
    Case(
        "sweep-volume-own-sensitive",
        one(swept(tags=tagged("sir-gone", {"Key": "yb_task", "Value": "infra"}))),
    ),
    Case("sweep-in-use-not-swept", one(sweep_spec(vol("in-use", tagged("sir-gone"), 10, "i-9")))),
    Case(
        "sweep-lookup-error-fails-closed",
        one(
            swept(
                key_errors={
                    ("describe_spot_instance_requests", "sir-gone"): ce("RequestLimitExceeded")
                }
            )
        ),
    ),
    Case(
        "sweep-lookup-botocore-fails-closed",
        one(swept(key_errors={("describe_spot_instance_requests", "sir-gone"): conn()})),
    ),
    Case(
        "sweep-lookup-malformed-is-gone",
        one(
            swept(
                key_errors={
                    ("describe_spot_instance_requests", "sir-gone"): ce(
                        "InvalidSpotInstanceRequestID.Malformed"
                    )
                }
            )
        ),
    ),
    Case("sweep-paginator-error", one(swept(errors={"paginate": ce("RequestLimitExceeded")}))),
    Case("sweep-paginator-generic-error", one(swept(errors={"paginate": RuntimeError("x")}))),
    Case("sweep-delete-error-isolated", one(swept(errors={"delete_volume": RuntimeError("x")}))),
    Case(
        "sweep-not-recounted",
        one(
            Spec(
                [req(state="cancelled")],
                {"i-1": inst("terminated", ())},
                {"vol-1": vol("available", tagged("sir-1"), attached=None)},
            )
        ),
    ),
    Case(
        "sweep-two-volumes",
        one(
            Spec(
                [],
                volumes={
                    "vol-a": vol("available", tagged("sir-gone"), 10, None),
                    "vol-b": vol("available", tagged("sir-gone"), 10, None),
                },
            )
        ),
    ),
    Case(
        "sweep-and-cleanup-same-region",
        one(
            Spec(
                [req()],
                {"i-1": inst()},
                {"vol-1": vol(), "vol-old": vol("available", tagged("sir-gone"), 10, None)},
            )
        ),
    ),
    Case(
        "sweep-after-describe-error-not-run",
        one(swept(errors={"describe_spot_instance_requests": RuntimeError("x")})),
    ),
]


class FakeEc2:
    def __init__(self, aws: "FakeAws", region: str) -> None:
        self.aws = aws
        self.region = region

    @property
    def spec(self) -> Spec:
        return self.aws.case.regions[self.region]

    def _rec(self, method: str, **kw: Any) -> None:
        self.aws.calls.append((self.region, method, copy.deepcopy(kw)))
        if method in self.spec.errors:
            raise self.spec.errors[method]

    def describe_regions(self) -> dict[str, Any]:
        self.aws.calls.append((self.region, "describe_regions", {}))
        if self.aws.case.regions_error is not None:
            raise self.aws.case.regions_error
        return {"Regions": [{"RegionName": r} for r in self.aws.case.regions]}

    def describe_spot_instance_requests(self, **kw: Any) -> Any:
        self._rec("describe_spot_instance_requests", **kw)
        ids = kw.get("SpotInstanceRequestIds")
        if ids:
            error = self.spec.key_errors.get(("describe_spot_instance_requests", ids[0]))
            if error is not None:
                raise error
            found = [r for r in self.spec.requests if r.get("SpotInstanceRequestId") == ids[0]]
            if not found:
                raise ce("InvalidSpotInstanceRequestID.NotFound")
            return {"SpotInstanceRequests": copy.deepcopy(found)}
        reqs = copy.deepcopy(self.spec.requests)
        size = self.spec.page_size
        if not size:
            return {"SpotInstanceRequests": reqs}
        start = int(kw.get("NextToken") or 0)
        page: dict[str, Any] = {"SpotInstanceRequests": reqs[start : start + size]}
        if start + size < len(reqs):
            page["NextToken"] = str(start + size)
        return page

    def describe_instances(self, *, InstanceIds: list[str]) -> Any:
        self._rec("describe_instances", InstanceIds=InstanceIds)
        outcome = self.spec.instances.get(InstanceIds[0])
        if outcome is None:
            raise ce("InvalidInstanceID.NotFound")
        return {"Reservations": [{"Instances": [copy.deepcopy(outcome)]}]}

    def describe_volumes(self, *, VolumeIds: list[str]) -> Any:
        self._rec("describe_volumes", VolumeIds=VolumeIds)
        error = self.spec.key_errors.get(("describe_volumes", VolumeIds[0]))
        if error is not None:
            raise error
        volume = self.spec.volumes.get(VolumeIds[0])
        if volume is None:
            return {"Volumes": []}
        return {"Volumes": [self._view(VolumeIds[0], volume)]}

    @staticmethod
    def _view(volume_id: str, volume: dict[str, Any]) -> dict[str, Any]:
        return {
            "VolumeId": volume_id,
            "State": volume["State"],
            "Tags": copy.deepcopy(volume["Tags"]),
            "CreateTime": volume["CreateTime"],
        }

    def get_paginator(self, name: str) -> Any:
        assert name == "describe_volumes"
        return self

    def paginate(self, *, Filters: list[dict[str, Any]]) -> Any:
        self._rec("paginate", Filters=Filters)
        statuses = next(f["Values"] for f in Filters if f["Name"] == "status")
        out = []
        for vid, volume in self.spec.volumes.items():
            tags = {t["Key"]: t.get("Value") for t in volume["Tags"]}
            ok = volume["State"] in statuses
            for f in Filters:
                if f["Name"] == "tag-key":
                    ok = ok and f["Values"][0] in tags
                if f["Name"].startswith("tag:"):
                    ok = ok and tags.get(f["Name"][4:]) in f["Values"]
            if ok:
                out.append(self._view(vid, volume))
        return [{"Volumes": out[:1]}, {"Volumes": out[1:]}]

    def cancel_spot_instance_requests(self, *, SpotInstanceRequestIds: list[str]) -> Any:
        self._rec("cancel_spot_instance_requests", SpotInstanceRequestIds=SpotInstanceRequestIds)
        for r in self.spec.requests:
            if r.get("SpotInstanceRequestId") in SpotInstanceRequestIds:
                r["State"] = "cancelled"
        return {}

    def terminate_instances(self, *, InstanceIds: list[str]) -> Any:
        try:
            self._rec("terminate_instances", InstanceIds=InstanceIds)
        except Exception:
            if self.spec.terminate_error_state:
                self.spec.instances[InstanceIds[0]]["State"]["Name"] = (
                    self.spec.terminate_error_state
                )
            raise
        self.spec.instances[InstanceIds[0]]["State"]["Name"] = "shutting-down"
        if self.spec.release_on_terminate:
            for volume in self.spec.volumes.values():
                if volume["attached"] == InstanceIds[0]:
                    volume["State"] = "available"
        return {}

    def create_tags(self, *, Resources: list[str], Tags: list[dict[str, str]]) -> Any:
        self._rec("create_tags", Resources=Resources, Tags=Tags)
        volume = self.spec.volumes.get(Resources[0])
        if volume is not None:
            keys = {t["Key"] for t in Tags}
            volume["Tags"] = [t for t in volume["Tags"] if t["Key"] not in keys] + copy.deepcopy(
                Tags
            )
        return {}

    def delete_tags(self, *, Resources: list[str], Tags: list[dict[str, str]]) -> Any:
        self._rec("delete_tags", Resources=Resources, Tags=Tags)
        volume = self.spec.volumes.get(Resources[0])
        if volume is not None:
            keys = {t["Key"] for t in Tags}
            volume["Tags"] = [t for t in volume["Tags"] if t["Key"] not in keys]
        return {}

    def delete_volume(self, *, VolumeId: str) -> Any:
        self._rec("delete_volume", VolumeId=VolumeId)
        self.spec.volumes.pop(VolumeId, None)
        return {}


class FakeAws:
    def __init__(self, case: Case) -> None:
        self.case = copy.deepcopy(case)
        self.calls: list[tuple[Any, ...]] = []
        self.clock = 0.0

    def client(self, service: str, region_name: str | None = None) -> FakeEc2:
        assert region_name is not None
        self.calls.append((region_name, "client", service))
        if region_name in self.case.client_errors:
            raise self.case.client_errors[region_name]
        return FakeEc2(self, region_name)

    def session(self) -> Any:
        self.calls.append(("-", "session", ""))
        return types.SimpleNamespace(get_available_regions=lambda service: list(self.case.regions))

    def monotonic(self) -> float:
        return self.clock

    def sleep(self, seconds: float) -> None:
        self.calls.append(("-", "sleep", seconds))
        self.clock += max(seconds, 1)


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[str]
    volumes: list[str]
    logs: list[tuple[str, int, str]]
    error: tuple[str, str] | None


def _load_oracle(aws: FakeAws) -> Any:
    module = types.ModuleType("legacy_aws_spot")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    module.__dict__["datetime"] = types.SimpleNamespace(
        datetime=types.SimpleNamespace(now=lambda tz=None: NOW),
        timezone=datetime.timezone,
        timedelta=datetime.timedelta,
    )
    module.__dict__["time"] = types.SimpleNamespace(time=aws.monotonic, sleep=aws.sleep)
    return module


def _args(case: Case) -> tuple[Any, ...]:
    return (case.dry_run, case.filter_tags, case.exception_tags, case.age, case.custom, case.notags)


def _legacy(case: Case, aws: FakeAws) -> Any:
    return _load_oracle(aws).SpotInstanceRequests(
        case.dry_run, case.filter_tags, case.exception_tags, case.age, case.custom, case.notags
    )


def _adapter(case: Case, aws: FakeAws) -> Any:
    obj = SpotInstanceRequests(
        case.dry_run, case.filter_tags, case.exception_tags, case.age, case.custom, case.notags
    )
    obj._runtime = SpotRuntime(clock=lambda: NOW, waiter=Waiter(aws.sleep, aws.monotonic))
    return obj


def _logs(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [
        (r.name, r.levelno, re.sub(r"0x[0-9a-f]+", "0x", r.getMessage())) for r in caplog.records
    ]


def _prepare(case: Case, mp: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> FakeAws:
    aws = FakeAws(case)
    mp.setattr("boto3.client", aws.client)
    mp.setattr("boto3.session.Session", aws.session)
    mp.setattr("crc.service.init_logging", lambda *a, **k: None)
    caplog.clear()
    return aws


def _run_class(
    build: Callable[[Case, FakeAws], Any],
    case: Case,
    mp: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> Outcome:
    aws = _prepare(case, mp, caplog)
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    volumes: list[str] = []
    try:
        obj = build(case, aws)
        obj.delete()
        deleted, volumes = list(obj.get_deleted), list(obj.volumes_to_delete)
        assert obj.delete_count == len(deleted) + len(volumes)
    except Exception as e:
        error = (type(e).__name__, str(e))
    return Outcome(aws.calls, deleted, volumes, _logs(caplog), error)


def _run_cleaner(case: Case, mp: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> Outcome:
    aws = _prepare(case, mp, caplog)
    options = {
        "filter_tags": case.filter_tags,
        "exception_tags": case.exception_tags,
        "notags": case.notags,
        "age": case.age,
        "custom_age_tag_key": case.custom,
    }
    ctx = RunContext(case.dry_run, Operation.DELETE, options, NOW)
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    volumes: list[str] = []
    try:
        cleaner = registry.get("aws", "spot_instance_requests")(ctx)
        cleaner._clients = {}  # type: ignore[attr-defined]
        cleaner.runtime = SpotRuntime(  # type: ignore[attr-defined]
            clock=lambda: NOW, waiter=Waiter(aws.sleep, aws.monotonic)
        )
        plan = cleaner.plan(ctx)
        if case.dry_run:
            deleted = [c.id for c in plan.candidates]
            volumes = list(plan.related.get(DISK, ()))
        else:
            result = cleaner.apply(plan, ctx)
            deleted = list(result.succeeded)
            volumes = list(result.related.get(DISK, ()))
    except Exception as e:
        error = (type(e).__name__, str(e))
    return Outcome(aws.calls, deleted, volumes, _logs(caplog), error)


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


def _ids(cases: list[Case]) -> list[str]:
    return [c.id for c in cases]


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_adapter_matches_main(
    case: Case, debug_logs: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    old = _run_class(_legacy, case, monkeypatch, caplog)
    new = _run_class(_adapter, case, monkeypatch, caplog)
    old = dataclasses.replace(old, logs=expected_logs(old.logs, FIXED_BUGS, rules=RULES))
    assert new.calls == old.calls
    assert new.error == old.error
    assert new.deleted == old.deleted
    assert new.volumes == old.volumes
    assert new.logs == old.logs


def test_skip_lines_are_debug_only_in_new_code(
    debug_logs: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """BUG-356 (LOG-AWS1): the INFO "Skipping ..." lines of the oracle are DEBUG now."""
    differing = []
    for case in CASES:
        old = _run_class(_legacy, case, monkeypatch, caplog)
        new = _run_class(_adapter, case, monkeypatch, caplog)
        assert new.logs == expected_logs(old.logs, FIXED_BUGS, rules=RULES), case.id
        if old.logs != new.logs:
            differing.append(case.id)
    assert differing


def _per_region(calls: list[tuple[Any, ...]]) -> dict[str, list[tuple[Any, ...]]]:
    out: dict[str, list[tuple[Any, ...]]] = {}
    for call in calls:
        out.setdefault(call[0], []).append(call)
    return out


@pytest.mark.parametrize("case", CASES, ids=_ids(CASES))
def test_cleaner_matches_main(
    case: Case, debug_logs: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """plan() lists every region before apply() mutates: each region's call stream and the
    log lines are the same as main's; a single region is identical including order."""
    old = _run_class(_legacy, case, monkeypatch, caplog)
    new = _run_cleaner(case, monkeypatch, caplog)
    assert new.error == old.error
    if old.error is not None:
        return
    assert _per_region(new.calls) == _per_region(old.calls)
    assert new.deleted == old.deleted
    assert new.volumes == old.volumes
    old_logs = expected_logs(
        [r for r in old.logs if not r[2].startswith(COUNT_LOG)], FIXED_BUGS, rules=RULES
    )
    assert Counter(new.logs) == Counter(old_logs)
    if len(case.regions) == 1:
        assert new.calls == old.calls
        assert new.logs == old_logs


def test_cases_cover_dry_and_real_and_unique_ids() -> None:
    assert len(set(_ids(CASES))) == len(CASES)
    assert {c.dry_run for c in CASES} == {True, False}
    assert any(len(c.regions) > 1 for c in CASES)


def test_oracle_is_main() -> None:
    assert "class SpotInstanceRequests(Service)" in ORACLE.read_text()
