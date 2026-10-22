"""Unit tests for `crc.aws.keypairs`: `plan()` is read-only, `apply()` mutates, adapter wiring."""

import datetime
from collections.abc import Mapping
from typing import Any

import pytest
from botocore.exceptions import EndpointConnectionError

from crc.aws.keypairs import (
    KeyPairCleaner,
    KeyPairRuntime,
    KeyPairs,
    build_keypair_cleaner,
)
from crc.core.cleaner import Candidate, Operation, Plan, RunContext
from crc.core.registry import get

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)
NOW_UTC = NOW.astimezone(datetime.UTC)


def pair(name: str, hours: float) -> dict[str, Any]:
    return {"KeyName": name, "CreateTime": NOW_UTC - datetime.timedelta(hours=hours)}


class FakeEc2:
    def __init__(self, pairs: list[dict[str, Any]], calls: list[tuple[str, ...]]) -> None:
        self.pairs = pairs
        self.calls = calls

    def describe_key_pairs(self) -> Mapping[str, Any]:
        self.calls.append(("describe_key_pairs",))
        return {"KeyPairs": self.pairs}

    def delete_key_pair(self, *, KeyName: str) -> Mapping[str, Any]:
        self.calls.append(("delete_key_pair", KeyName))
        return {"Return": True}


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


def cleaner(aws: Aws, **overrides: Any) -> KeyPairCleaner:
    params: dict[str, Any] = {"name_regex": [], "exception_regex": [], "age": {"hours": 5}}
    params.update(overrides)
    return KeyPairCleaner(aws.client, aws.list_regions, **params)


def mutating(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [c for c in calls if c[0] == "delete_key_pair"]


def test_plan_makes_no_mutating_call() -> None:
    aws = Aws({"r1": [pair("old", 10), pair("new", 1)], "r2": [pair("old2", 20)]})
    plan = cleaner(aws).plan(context())
    assert {c.id for c in plan.candidates} == {"old", "old2"}
    assert mutating(aws.calls) == []
    assert aws.calls[0] == ("client", "r1")


def test_plan_normalises_and_records_skips() -> None:
    aws = Aws({"r1": [pair("old", 10), pair("new", 1), pair("keep", 10), pair("zzz", 10)]})
    plan = cleaner(aws, name_regex=["old|new|keep"], exception_regex=["keep"]).plan(context())
    assert plan.candidates == (
        Candidate(
            id="old", name="old", region="r1", launch_time=NOW_UTC - datetime.timedelta(hours=10)
        ),
    )
    assert {(s.id, s.reason) for s in plan.skipped} == {
        ("new", "not older than age"),
        ("keep", "matches exception_regex"),
        ("zzz", "does not match name_regex"),
    }


def test_plan_skips_region_on_connectivity_error() -> None:
    aws = Aws({"r1": [pair("a", 10)], "r2": [pair("b", 10)]})
    aws.errors["r1"] = EndpointConnectionError(endpoint_url="https://ec2.example")
    plan = cleaner(aws).plan(context())
    assert [c.id for c in plan.candidates] == ["b"]


def test_apply_deletes_in_plan_order_per_region() -> None:
    aws = Aws({"r1": [], "r2": []})
    plan = Plan(
        candidates=(
            Candidate(id="a", region="r1"),
            Candidate(id="b", region="r2"),
            Candidate(id="c", region="r1"),
        )
    )
    result = cleaner(aws).apply(plan, context())
    assert result.succeeded == ("a", "c", "b")
    assert [c for c in aws.calls if c[0] != "client"] == [
        ("delete_key_pair", "a"),
        ("delete_key_pair", "c"),
        ("delete_key_pair", "b"),
    ]


def test_apply_in_dry_run_deletes_nothing() -> None:
    aws = Aws({"r1": []})
    result = cleaner(aws).apply(
        Plan(candidates=(Candidate(id="a", region="r1"),)), context(dry_run=True)
    )
    assert result.succeeded == ("a",)
    assert mutating(aws.calls) == []


def test_apply_skips_region_on_connectivity_error() -> None:
    aws = Aws({"r1": [], "r2": []})
    aws.errors["r1"] = EndpointConnectionError(endpoint_url="https://ec2.example")
    plan = Plan(candidates=(Candidate(id="a", region="r1"), Candidate(id="b", region="r2")))
    result = cleaner(aws).apply(plan, context())
    assert result.succeeded == ("b",)


def test_supports_only_delete() -> None:
    aws = Aws({})
    assert cleaner(aws).supports(Operation.DELETE)
    assert not cleaner(aws).supports(Operation.STOP)


def test_registered_factory_builds_cleaner_from_context() -> None:
    assert get("aws", "keypair") is build_keypair_cleaner
    ctx = RunContext(
        dry_run=True,
        operation=Operation.DELETE,
        options={"name_regex": ["a"], "age": {"days": 1}},
        now=NOW,
    )
    built = build_keypair_cleaner(ctx)
    assert (built.cloud, built.kind) == ("aws", "keypair")
    omitted_age = build_keypair_cleaner(context(dry_run=True))
    assert (omitted_age.cloud, omitted_age.kind) == ("aws", "keypair")


@pytest.mark.parametrize("dry_run", [True, False])
def test_keypairs_adapter_uses_injected_runtime(dry_run: bool) -> None:
    aws = Aws({"r1": [pair("a", 10), pair("b", 1)]})
    runtime = KeyPairRuntime(aws.client, aws.list_regions, lambda: NOW)
    keypairs = KeyPairs(dry_run, [], [], {"hours": 5}, runtime)
    keypairs.delete()
    assert keypairs.get_deleted == ["a"]
    assert keypairs.count == 1
    assert (mutating(aws.calls) == []) is dry_run
