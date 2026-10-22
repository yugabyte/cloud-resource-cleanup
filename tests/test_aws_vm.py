"""Unit tests of the AWS VM cleaner with injected fake clients (no boto3)."""

import datetime
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import pytest

from crc.aws.vm import VM, VmRuntime
from crc.aws.vm.cleaner import VmCleaner, VmSettings
from crc.core.cleaner import Candidate, Operation, Plan, RunContext

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
OLD = NOW - datetime.timedelta(days=3)
SETTINGS = VmSettings({}, {}, {}, {"days": 1}, None, ["running"])


class FakeClient:
    def __init__(
        self, instances: Sequence[Mapping[str, Any]], fail: set[str] | None = None
    ) -> None:
        self.instances = list(instances)
        self.fail = fail or set()
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def describe_instances(self, *, Filters: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        self.calls.append(("describe_instances", ()))
        return {"Reservations": [{"Instances": self.instances}]}

    def describe_network_interfaces(
        self, *, NetworkInterfaceIds: Sequence[str]
    ) -> Mapping[str, Any]:
        self.calls.append(("describe_network_interfaces", tuple(NetworkInterfaceIds)))
        return {"NetworkInterfaces": [{"Attachment": {"AttachTime": OLD}}]}

    def terminate_instances(self, *, InstanceIds: Sequence[str]) -> Mapping[str, Any]:
        self.calls.append(("terminate_instances", tuple(InstanceIds)))
        if InstanceIds[0] in self.fail:
            raise RuntimeError("denied")
        return {}

    def stop_instances(self, *, InstanceIds: Sequence[str]) -> Mapping[str, Any]:
        self.calls.append(("stop_instances", tuple(InstanceIds)))
        return {}


def instance(iid: str, name: str = "n") -> dict[str, Any]:
    return {
        "InstanceId": iid,
        "Tags": [{"Key": "Name", "Value": f"{name}-{iid}"}],
        "NetworkInterfaces": [{"NetworkInterfaceId": f"eni-{iid}"}],
    }


def make(client: FakeClient) -> VmCleaner:
    return VmCleaner(lambda region: client, lambda: ["us-west-2"], SETTINGS)


def ctx(operation: Operation = Operation.DELETE, *, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=operation, options={}, now=NOW)


MUTATING = ("terminate_instances", "stop_instances")


@pytest.mark.parametrize("operation", [Operation.DELETE, Operation.STOP])
@pytest.mark.parametrize("dry_run", [True, False])
def test_plan_makes_no_mutating_call(operation: Operation, dry_run: bool) -> None:
    client = FakeClient([instance("a"), instance("b")])
    plan = make(client).plan(ctx(operation, dry_run=dry_run))
    assert [c.id for c in plan.candidates] == ["a", "b"]
    assert [c.region for c in plan.candidates] == ["us-west-2"] * 2
    assert not [c for c in client.calls if c[0] in MUTATING]


def test_apply_terminates_one_by_one_and_isolates_errors(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = FakeClient([instance("a"), instance("b"), instance("c")], fail={"b"})
    cleaner = make(client)
    plan = cleaner.plan(ctx())
    result = cleaner.apply(plan, ctx())
    assert [c for c in client.calls if c[0] in MUTATING] == [
        ("terminate_instances", ("a",)),
        ("terminate_instances", ("b",)),
        ("terminate_instances", ("c",)),
    ]
    assert result.succeeded == ("n-a", "n-c")
    assert [(f.id, f.error) for f in result.failed] == [("b", "denied")]
    messages = [r.getMessage() for r in caplog.records]
    assert "Error occured while delete instance b: denied" in messages
    assert "number of AWS instances deleted: 2" in messages


def test_apply_stops_instances() -> None:
    client = FakeClient([instance("a")])
    cleaner = make(client)
    result = cleaner.apply(cleaner.plan(ctx(Operation.STOP)), ctx(Operation.STOP))
    assert ("stop_instances", ("a",)) in client.calls
    assert result.succeeded == ("n-a",)


def test_dry_run_apply_makes_no_call_and_plan_logs_summary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = FakeClient([instance("a")])
    cleaner = make(client)
    plan = cleaner.plan(ctx(dry_run=True))
    before = list(client.calls)
    assert cleaner.apply(plan, ctx(dry_run=True)).succeeded == ()
    assert client.calls == before
    assert any("which will be deleted: ['n-a']" in r.getMessage() for r in caplog.records)


def test_apply_ignores_candidates_not_in_plan() -> None:
    client = FakeClient([instance("a")])
    cleaner = make(client)
    plan = Plan(candidates=(Candidate(id="x", name="x", region="eu-west-1"),))
    cleaner.apply(plan, ctx())
    assert client.calls == [("terminate_instances", ("x",))]


def test_supports_delete_and_stop() -> None:
    cleaner = make(FakeClient([]))
    assert cleaner.supports(Operation.DELETE)
    assert cleaner.supports(Operation.STOP)


def test_adapter_runs_with_injected_runtime() -> None:
    client = FakeClient([instance("a")])
    vm = VM(False, {}, {}, {"days": 1}, "", {})
    vm.runtime = replace(
        VmRuntime(),
        client_factory=lambda region: client,
        list_regions=lambda: ["us-west-2"],
        clock=lambda: NOW,
    )
    vm.delete()
    assert vm.get_deleted == ["n-a"]
    assert vm.delete_count == 1
    assert vm.get_stopped == []
