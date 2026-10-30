"""Unit tests of `crc.gcp.vm`: read-only `plan()`, mutating `apply()`, fakes only."""

import datetime
import logging
import types
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pytest

from crc.core.cleaner import Candidate, Operation, Plan, RunContext
from crc.gcp.vm import GcpVmClient, InstanceView, VmCleaner, VmSettings
from tests.helpers_ad_vm_engine import done_ids, run_vm

UTC = datetime.UTC
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
PROJECT = "proj-1"
TWO = 2


def ctx(*, dry_run: bool = False, operation: Operation = Operation.DELETE) -> RunContext:
    return RunContext(dry_run=dry_run, operation=operation, options={}, now=NOW)


def stamp(**delta: float) -> str:
    return (NOW - datetime.timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%S.%f%z")


def inst(
    name: str,
    *,
    days: float = 3,
    status: str = "RUNNING",
    labels: Mapping[str, str] | None = None,
) -> Any:
    return types.SimpleNamespace(
        name=name, status=status, labels=dict(labels or {}), creation_timestamp=stamp(days=days)
    )


class FakeClient:
    def __init__(self, zones: dict[str, list[Any]], fail_on: str | None = None) -> None:
        self.zones = zones
        self.fail_on = fail_on
        self.calls: list[tuple[str, ...]] = []

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        self.calls.append(("list_zones", project_id))
        return [{"name": zone} for zone in self.zones]

    def list_instances(self, project_id: str, zone: str) -> Iterable[InstanceView]:
        self.calls.append(("list_instances", project_id, zone))
        items: list[InstanceView] = self.zones[zone]
        return items

    def delete_instance(self, project_id: str, zone: str, instance: str) -> object:
        self.calls.append(("delete_instance", project_id, zone, instance))
        if instance == self.fail_on:
            raise RuntimeError("boom")
        return {"name": "op"}

    def stop_instance(self, project_id: str, zone: str, instance: str) -> object:
        self.calls.append(("stop_instance", project_id, zone, instance))
        if instance == self.fail_on:
            raise RuntimeError("boom")
        return {"name": "op"}

    def get_zone_operation(self, project_id: str, zone: str, operation: str) -> Mapping[str, Any]:
        self.calls.append(("get_zone_operation", project_id, zone, operation))
        return {"status": "DONE"}


def settings(**kw: Any) -> VmSettings:
    base: dict[str, Any] = {
        "project_id": PROJECT,
        "filter_labels": None,
        "exception_labels": None,
        "notags": None,
        "age": {"days": 1},
        "custom_age_tag_key": "",
    }
    return VmSettings(**{**base, **kw})


def cleaner(client: GcpVmClient, **kw: Any) -> VmCleaner:
    return VmCleaner(client, settings(**kw))


MUTATING = ("delete_instance", "stop_instance")


def test_plan_lists_and_filters_without_mutating() -> None:
    client = FakeClient(
        {"z": [inst("old"), inst("young", days=0.1), inst("off", status="TERMINATED")]}
    )
    plan = cleaner(client).plan(ctx())
    assert [c.id for c in plan.candidates] == ["old"]
    assert plan.candidates[0].region == "z"
    assert plan.candidates[0].state == "RUNNING"
    assert not [c for c in client.calls if c[0] in MUTATING]


@pytest.mark.parametrize("operation", [Operation.DELETE, Operation.STOP])
def test_plan_in_dry_run_never_mutates(operation: Operation) -> None:
    client = FakeClient({"z1": [inst("a")], "z2": [inst("b")]})
    plan = cleaner(client).plan(ctx(dry_run=True, operation=operation))
    assert [c.id for c in plan.candidates] == ["a", "b"]
    assert client.calls == [
        ("list_zones", PROJECT),
        ("list_instances", PROJECT, "z1"),
        ("list_instances", PROJECT, "z2"),
    ]


def test_plan_filters_labels_all_keys_exception_and_notags() -> None:
    client = FakeClient(
        {
            "z": [
                inst("both", labels={"env": "qa", "team": "x"}),
                inst("one", labels={"env": "qa"}),
                inst("keep", labels={"env": "qa", "team": "x", "protect": "yes"}),
            ]
        }
    )
    c = cleaner(
        client,
        filter_labels={"env": ["qa"], "team": ["x"]},
        exception_labels={"protect": []},
    )
    assert [x.id for x in c.plan(ctx()).candidates] == ["both"]
    c = cleaner(client, notags={"env": ["qa"]})
    assert [x.id for x in c.plan(ctx()).candidates] == []


def test_plan_uses_the_custom_age_label_over_age() -> None:
    client = FakeClient({"z": [inst("a", days=5, labels={"ttl": "{'days': 10}"})]})
    assert cleaner(client, custom_age_tag_key="ttl").plan(ctx()).candidates == ()


def test_plan_instance_state_is_configurable() -> None:
    client = FakeClient({"z": [inst("a"), inst("b", status="TERMINATED")]})
    c = cleaner(client, instance_state=("TERMINATED",))
    assert [x.id for x in c.plan(ctx()).candidates] == ["b"]


def test_apply_deletes_in_order_and_records() -> None:
    client = FakeClient({"z": []})
    plan = Plan(candidates=(Candidate(id="a", region="z1"), Candidate(id="b", region="z2")))
    result = cleaner(client).apply(plan, ctx())
    assert result.succeeded == ("a", "b")
    assert client.calls == [
        ("delete_instance", PROJECT, "z1", "a"),
        ("get_zone_operation", PROJECT, "z1", "op"),
        ("delete_instance", PROJECT, "z2", "b"),
        ("get_zone_operation", PROJECT, "z2", "op"),
    ]


def test_apply_stops_when_the_operation_is_stop() -> None:
    client = FakeClient({"z": []})
    plan = Plan(candidates=(Candidate(id="a", region="z"),))
    result = cleaner(client).apply(plan, ctx(operation=Operation.STOP))
    assert result.succeeded == ("a",)
    assert client.calls == [
        ("stop_instance", PROJECT, "z", "a"),
        ("get_zone_operation", PROJECT, "z", "op"),
    ]


def test_apply_skips_a_failed_call_and_logs_it(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = FakeClient({"z": []}, fail_on="b")
    plan = Plan(candidates=tuple(Candidate(id=n, region="z") for n in "abc"))
    result = cleaner(client).apply(plan, ctx())
    assert result.succeeded == ("a", "c")
    assert "Error occurred while delete instance b: boom" in caplog.messages


def test_apply_in_dry_run_makes_no_call() -> None:
    client = FakeClient({"z": []})
    plan = Plan(candidates=(Candidate(id="a", region="z"),))
    assert cleaner(client).apply(plan, ctx(dry_run=True)).succeeded == ("a",)
    assert client.calls == []


def test_supports_delete_and_stop() -> None:
    c = cleaner(FakeClient({}))
    assert (c.cloud, c.kind) == ("gcp", "vm")
    assert c.supports(Operation.DELETE)
    assert c.supports(Operation.STOP)


def test_listing_error_propagates() -> None:
    class Broken(FakeClient):
        def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
            raise PermissionError("denied")

    with pytest.raises(PermissionError, match="denied"):
        cleaner(Broken({})).plan(ctx())


def test_engine_delete_and_stop_use_injected_client() -> None:
    client = FakeClient({"z": [inst("a"), inst("b", days=0.1)]})
    deleted = run_vm("gcp", cleaner(client, age={"days": 1}))
    stopped = run_vm("gcp", cleaner(client, age={"days": 1}), operation=Operation.STOP)
    assert done_ids(deleted) == ["a"]
    assert done_ids(stopped) == ["a"]
    assert [c[0] for c in client.calls if c[0] in MUTATING] == ["delete_instance", "stop_instance"]


def test_engine_dry_run_lists_only() -> None:
    client = FakeClient({"z": [inst("a")]})
    outcome = run_vm("gcp", cleaner(client, instance_state=["RUNNING", "TERMINATED"]), dry_run=True)
    assert done_ids(outcome) == ["a"]
    assert not [c for c in client.calls if c[0] in MUTATING]
