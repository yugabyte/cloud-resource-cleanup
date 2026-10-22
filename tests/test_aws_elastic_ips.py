"""Unit tests for `crc.aws.elastic_ips`: read-only `plan()`, mutating `apply()`, adapter."""

import datetime
from collections.abc import Mapping
from typing import Any

import pytest
from botocore.exceptions import EndpointConnectionError

from crc.aws.elastic_ips import ElasticIpCleaner, ElasticIpRuntime, ElasticIPs
from crc.core.cleaner import Candidate, Operation, Plan, RunContext

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)


def tagged(ip: str, **tags: str) -> dict[str, Any]:
    return {
        "PublicIp": ip,
        "AllocationId": f"alloc-{ip}",
        "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
    }


class FakeEc2:
    def __init__(self, addresses: list[dict[str, Any]], calls: list[tuple[str, ...]]) -> None:
        self.addresses = addresses
        self.calls = calls

    def describe_addresses(self) -> Mapping[str, Any]:
        self.calls.append(("describe_addresses",))
        return {"Addresses": self.addresses}

    def release_address(self, *, AllocationId: str) -> Mapping[str, Any]:
        self.calls.append(("release_address", AllocationId))
        return {}


class Aws:
    def __init__(self, regions: Mapping[str, list[dict[str, Any]]]) -> None:
        self.regions = regions
        self.calls: list[tuple[str, ...]] = []
        self.errors: dict[str, Exception] = {}

    def client(self, region: str) -> FakeEc2:
        self.calls.append(("client", region))
        if region in self.errors:
            raise self.errors[region]
        return FakeEc2(self.regions[region], self.calls)

    def list_regions(self) -> list[str]:
        return list(self.regions)


def context(*, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def cleaner(aws: Aws, **overrides: Any) -> ElasticIpCleaner:
    params: dict[str, Any] = {"filter_tags": {}, "exception_tags": {}, "notags": {}}
    params.update(overrides)
    return ElasticIpCleaner(aws.client, aws.list_regions, **params)


def mutating(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [c for c in calls if c[0] == "release_address"]


def test_plan_makes_no_mutating_call() -> None:
    aws = Aws({"r1": [tagged("1.1.1.1", a="b")], "r2": [tagged("2.2.2.2", a="b")]})
    plan = cleaner(aws).plan(context())
    assert [c.id for c in plan.candidates] == ["1.1.1.1", "2.2.2.2"]
    assert mutating(aws.calls) == []
    assert aws.calls[0] == ("client", "r1")


def test_plan_normalises_and_records_skips() -> None:
    aws = Aws(
        {
            "r1": [
                tagged("1.1.1.1", env="test"),
                tagged("2.2.2.2", env="prod"),
                tagged("3.3.3.3", other="x"),
                {"PublicIp": "4.4.4.4", "AllocationId": "a4"},
                {**tagged("5.5.5.5", env="test"), "NetworkInterfaceId": "eni-1"},
            ]
        }
    )
    plan = cleaner(
        aws, filter_tags={"env": ["test", "prod"]}, exception_tags={"env": ["prod"]}
    ).plan(context())
    assert plan.candidates == (
        Candidate(
            id="1.1.1.1", name="1.1.1.1", region="r1", extra={"allocation_id": "alloc-1.1.1.1"}
        ),
    )
    assert [(s.id, s.reason) for s in plan.skipped] == [
        ("2.2.2.2", "matches exception_tags"),
        ("3.3.3.3", "no filter_tags match"),
    ]


def test_plan_skips_region_on_connectivity_error() -> None:
    aws = Aws({"r1": [tagged("1.1.1.1", a="b")], "r2": [tagged("2.2.2.2", a="b")]})
    aws.errors["r1"] = EndpointConnectionError(endpoint_url="https://ec2.example")
    plan = cleaner(aws).plan(context())
    assert [c.id for c in plan.candidates] == ["2.2.2.2"]


def test_apply_releases_in_plan_order_and_reports() -> None:
    aws = Aws({"r1": [tagged("1.1.1.1", a="b"), tagged("2.2.2.2", a="b")]})
    subject = cleaner(aws)
    plan = subject.plan(context())
    result = subject.apply(plan, context())
    assert result.succeeded == ("1.1.1.1", "2.2.2.2")
    assert mutating(aws.calls) == [
        ("release_address", "alloc-1.1.1.1"),
        ("release_address", "alloc-2.2.2.2"),
    ]


def test_apply_in_dry_run_releases_nothing() -> None:
    aws = Aws({"r1": [tagged("1.1.1.1", a="b")]})
    subject = cleaner(aws)
    plan = subject.plan(context(dry_run=True))
    subject.apply(plan, context(dry_run=True))
    assert mutating(aws.calls) == []


def test_apply_empty_plan_calls_nothing() -> None:
    aws = Aws({"r1": []})
    result = cleaner(aws).apply(Plan(), context())
    assert result.succeeded == ()
    assert aws.calls == []


def test_apply_skips_region_on_connectivity_error() -> None:
    aws = Aws({"r1": [tagged("1.1.1.1", a="b")], "r2": [tagged("2.2.2.2", a="b")]})
    subject = cleaner(aws)
    plan = subject.plan(context())
    aws.errors["r1"] = EndpointConnectionError(endpoint_url="https://ec2.example")
    result = subject.apply(plan, context())
    assert result.succeeded == ("2.2.2.2",)


def test_notags_raises_as_on_main() -> None:
    aws = Aws({"r1": [tagged("1.1.1.1", a="b")]})
    with pytest.raises(TypeError, match="'bool' object is not iterable"):
        cleaner(aws, notags={"a": []}).plan(context())


@pytest.mark.parametrize(
    ("operation", "expected"), [(Operation.DELETE, True), (Operation.STOP, False)]
)
def test_supports_only_delete(operation: Operation, expected: bool) -> None:
    assert cleaner(Aws({})).supports(operation) is expected


def test_adapter_keeps_attributes_and_runs_per_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    aws = Aws({"r1": [tagged("1.1.1.1", a="b")], "r2": [tagged("2.2.2.2", a="b")]})
    runtime = ElasticIpRuntime(client_factory=aws.client, list_regions=aws.list_regions)
    ips = ElasticIPs(False, {"a": []}, {}, {}, runtime)
    ips.delete()
    assert ips.get_deleted == ips.deleted_ips == ["1.1.1.1", "2.2.2.2"]
    assert ips.count == len(aws.regions)
    assert [c[0] for c in aws.calls] == [
        "client",
        "describe_addresses",
        "release_address",
        "client",
        "describe_addresses",
        "release_address",
    ]
