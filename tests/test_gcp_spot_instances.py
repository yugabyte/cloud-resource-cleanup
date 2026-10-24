"""Unit tests of the GCP Spot VM cleaner with a fake `GcpSpotClient` (no SDK)."""

import datetime
import types
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pytest

from crc.core.cleaner import Operation, RunContext
from crc.core.registry import get
from crc.gcp.spot_vm import SpotSettings, SpotVMCleaner
from crc.gcp.spot_vm.client import InstanceView
from tests.helpers_ad_spot_engine import run_through_engine

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
OLD = (NOW - datetime.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S.%f%z")
SETTINGS = SpotSettings(
    project_id="p",
    filter_labels={},
    exception_labels={},
    notags={},
    age={"days": 1},
    custom_age_tag_key=None,
    wait_sec=10,
    poll_sec=1,
)


def instance(name: str, disks: Sequence[Any] = (), labels: Mapping[str, str] | None = None) -> Any:
    return types.SimpleNamespace(
        name=name,
        status="RUNNING",
        labels=dict(labels if labels is not None else {"team": "ci"}),
        scheduling=types.SimpleNamespace(preemptible=True, provisioning_model=""),
        disks=list(disks),
        metadata=None,
        creation_timestamp=OLD,
    )


def leftover(disk: str) -> Any:
    return types.SimpleNamespace(source=f"projects/p/zones/z/disks/{disk}", auto_delete=False)


class FakeClient:
    def __init__(self, instances: Sequence[Any]) -> None:
        self.instances = list(instances)
        self.calls: list[tuple[str, ...]] = []

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        self.calls.append(("list_zones", project_id))
        return [{"name": "z"}]

    def list_instances(self, project_id: str, zone: str) -> Iterable[InstanceView]:
        self.calls.append(("list_instances", project_id, zone))
        return list(self.instances)

    def delete_instance(self, project_id: str, zone: str, instance: str) -> Mapping[str, Any]:
        self.calls.append(("delete_instance", zone, instance))
        return {"name": f"op-{instance}"}

    def get_operation(
        self, project_id: str, scope: str, location: str, operation: str
    ) -> Mapping[str, Any]:
        self.calls.append(("get_operation", scope, location, operation))
        return {"status": "DONE"}

    def get_disk(self, project_id: str, scope: str, location: str, disk: str) -> Mapping[str, Any]:
        self.calls.append(("get_disk", scope, location, disk))
        return {"labels": {}}

    def delete_disk(
        self, project_id: str, scope: str, location: str, disk: str
    ) -> Mapping[str, Any]:
        self.calls.append(("delete_disk", scope, location, disk))
        return {"name": f"op-{disk}"}

    def close(self) -> None:
        self.calls.append(("close",))


def context(*, dry_run: bool) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def mutating(client: FakeClient) -> list[tuple[str, ...]]:
    return [c for c in client.calls if c[0].startswith("delete")]


def test_plan_lists_and_filters_without_mutating() -> None:
    client = FakeClient([instance("a", [leftover("d1")]), instance("b")])
    plan = SpotVMCleaner(client, SETTINGS).plan(context(dry_run=False))
    assert [c.id for c in plan.candidates] == ["a", "b"]
    assert plan.candidates[0].region == "z"
    assert mutating(client) == []
    assert ("get_disk", "zone", "z", "d1") not in client.calls
    assert client.calls[-1] == ("close",)


def test_plan_in_dry_run_reads_leftover_disks_and_reports_them() -> None:
    client = FakeClient([instance("a", [leftover("d1")])])
    plan = SpotVMCleaner(client, SETTINGS).plan(context(dry_run=True))
    assert plan.related["Disk"] == ("p/z/d1",)
    assert ("get_disk", "zone", "z", "d1") in client.calls
    assert mutating(client) == []


def test_plan_keeps_young_and_non_spot_instances() -> None:
    young = instance("young")
    young.creation_timestamp = NOW.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    standard = instance("standard")
    standard.scheduling = types.SimpleNamespace(preemptible=False, provisioning_model="STANDARD")
    plan = SpotVMCleaner(FakeClient([young, standard]), SETTINGS).plan(context(dry_run=False))
    assert plan.candidates == ()


def test_apply_deletes_instance_then_waits_then_deletes_leftover_disk() -> None:
    client = FakeClient([instance("a", [leftover("d1")])])
    cleaner = SpotVMCleaner(client, SETTINGS)
    plan = cleaner.plan(context(dry_run=False))
    client.calls.clear()
    result = cleaner.apply(plan, context(dry_run=False))
    assert result.succeeded == ("a",)
    assert result.related["Disk"] == ("p/z/d1",)
    assert [c[0] for c in client.calls] == [
        "delete_instance",
        "get_operation",
        "get_disk",
        "delete_disk",
        "get_operation",
        "close",
    ]


def test_apply_keeps_disks_when_the_instance_operation_fails() -> None:
    class Failing(FakeClient):
        def get_operation(
            self, project_id: str, scope: str, location: str, operation: str
        ) -> Mapping[str, Any]:
            return {"status": "DONE", "error": {"code": 1}}

    client = Failing([instance("a", [leftover("d1")])])
    cleaner = SpotVMCleaner(client, SETTINGS)
    result = cleaner.apply(cleaner.plan(context(dry_run=False)), context(dry_run=False))
    assert result.succeeded == ()
    assert result.related["Disk"] == ()
    assert ("get_disk", "zone", "z", "d1") not in client.calls


def test_supports_only_delete() -> None:
    cleaner = SpotVMCleaner(FakeClient([]), SETTINGS)
    assert cleaner.supports(Operation.DELETE)
    assert not cleaner.supports(Operation.STOP)


def test_registered_factory_requires_project_id_and_positive_age() -> None:
    factory = get("gcp", "spot_instances")
    with pytest.raises(ValueError, match="project_id is required"):
        factory(RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW))
    with pytest.raises(ValueError, match="age is required"):
        factory(
            RunContext(
                dry_run=True, operation=Operation.DELETE, options={"project_id": "p"}, now=NOW
            )
        )


def test_engine_runs_the_cleaner_with_an_injected_client() -> None:
    client = FakeClient([instance("a", [leftover("d1")])])
    outcome = run_through_engine(
        "gcp",
        "spot_instances",
        lambda _ctx: SpotVMCleaner(client, SETTINGS),
        context(dry_run=False),
    )
    assert outcome.result is not None
    assert outcome.result.succeeded == ("a",)
    assert outcome.result.related["Disk"] == ("p/z/d1",)
