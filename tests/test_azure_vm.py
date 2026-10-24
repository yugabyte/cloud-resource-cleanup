"""Unit tests for `crc.azu.vm`: `VmCleaner.plan()` is read-only, `apply()` mutates."""

import logging
import types
from typing import Any

import pytest

from crc.azu.vm import VmCleaner, VmSettings
from crc.core.cleaner import Operation, Plan, RunContext
from tests.helpers_azure_vm import NOW, STOPPED, FakeAzure, vm

MUTATING = (
    "vm_begin_delete",
    "vm_result",
    "nic_begin_delete",
    "nic_result",
    "begin_power_off",
    "begin_deallocate",
)
FILTER = {"env": ["ci"]}


def cleaner_for(azure: FakeAzure, **settings: Any) -> VmCleaner:
    compute = azure.compute_client(None, "sub")
    config = {"filter_tags": FILTER, "age": {"hours": 1}, **settings}
    return VmCleaner(
        lambda: compute, lambda: azure.network_client(None, "sub"), "rg", VmSettings(**config)
    )


def context(operation: Operation = Operation.DELETE, *, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=operation, options={}, now=NOW)


def mutations(azure: FakeAzure) -> list[tuple[Any, ...]]:
    return [c for c in azure.calls if c[0] in MUTATING]


@pytest.mark.parametrize("operation", [Operation.DELETE, Operation.STOP])
@pytest.mark.parametrize("dry_run", [True, False])
def test_plan_never_mutates(operation: Operation, dry_run: bool) -> None:
    azure = FakeAzure([vm("a"), vm("b")])
    plan = cleaner_for(azure).plan(context(operation, dry_run=dry_run))
    assert [c.id for c in plan.candidates] == ["a", "b"]
    assert mutations(azure) == []
    assert [c[0] for c in azure.calls if c[0] == "network_client"] == []


def test_plan_lists_then_reads_status_of_each_selected_vm() -> None:
    azure = FakeAzure([vm("a"), vm("b", {"x": "1"}), vm("c", hours=0.1)])
    cleaner_for(azure).plan(context())
    names = [c[0] for c in azure.calls if c[0] in ("list", "instance_view")]
    assert names == ["list", "instance_view"]
    assert ("instance_view", "rg", "a") in azure.calls


def test_plan_reports_skipped_reasons() -> None:
    azure = FakeAzure(
        [
            vm("untagged", {}),
            vm("excluded", {"env": "ci", "keep": "1"}),
            vm("other", {"env": "prod"}),
            vm("young", hours=0.5),
            vm("stopped", statuses=STOPPED),
            vm("broken", statuses=("only",)),
            vm("ok"),
        ]
    )
    cleaner = cleaner_for(azure, exception_tags={"keep": []})
    plan = cleaner.plan(context())
    assert [c.id for c in plan.candidates] == ["ok"]
    assert {s.id: s.reason for s in plan.skipped} == {
        "untagged": "untagged",
        "excluded": "matches exception_tags or notags",
        "other": "does not match filter_tags",
        "young": "not older than age",
        "stopped": "status does not contain an allowed state",
        "broken": "no_power_state",
    }


def test_plan_candidate_carries_normalised_tags_and_creation_time() -> None:
    item = vm("a", {"env": "ci", "n": "1"})
    plan = cleaner_for(FakeAzure([item])).plan(context())
    candidate = plan.candidates[0]
    assert dict(candidate.tags) == {"env": "ci", "n": "1"}
    assert candidate.launch_time == item.time_created


def test_plan_dry_run_logs_summary_and_lists_nics(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    plan = cleaner_for(FakeAzure([vm("a")])).plan(context(dry_run=True))
    assert plan.related == {"NIC": ("a-NIC",)}
    messages = [r.getMessage() for r in caplog.records]
    assert "List of Azure instances (Total: 1) which will be deleted: ['a']" in messages
    assert "List of Azure nics (Total: 1) which will be deleted: ['a-NIC']" in messages


def test_plan_without_dry_run_logs_no_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    cleaner_for(FakeAzure([vm("a")])).plan(context())
    assert not [r for r in caplog.records if r.getMessage().startswith("List of Azure")]


def test_plan_propagates_list_error() -> None:
    azure = FakeAzure([], list_error=RuntimeError("list failed"))
    with pytest.raises(RuntimeError, match="list failed"):
        cleaner_for(azure).plan(context())


def test_apply_deletes_vm_then_its_nic_in_plan_order() -> None:
    azure = FakeAzure([vm("a"), vm("b")])
    cleaner = cleaner_for(azure)
    ctx = context()
    result = cleaner.apply(cleaner.plan(ctx), ctx)
    assert result.succeeded == ("a", "b")
    assert result.related == {"NIC": ("a-NIC", "b-NIC")}
    assert [c[:3] for c in mutations(azure)] == [
        ("vm_begin_delete", "rg", "a"),
        ("vm_result", "a", 3600),
        ("nic_begin_delete", "rg", "a-NIC"),
        ("nic_result", "a-NIC", 600),
        ("vm_begin_delete", "rg", "b"),
        ("vm_result", "b", 3600),
        ("nic_begin_delete", "rg", "b-NIC"),
        ("nic_result", "b-NIC", 600),
    ]


def test_apply_stop_deallocates_only() -> None:
    azure = FakeAzure([vm("a")])
    cleaner = cleaner_for(azure)
    ctx = context(Operation.STOP)
    result = cleaner.apply(cleaner.plan(ctx), ctx)
    assert result.succeeded == ("a",)
    assert mutations(azure) == [("begin_deallocate", "rg", "a")]


def test_apply_dry_run_calls_nothing() -> None:
    azure = FakeAzure([vm("a")])
    cleaner = cleaner_for(azure)
    ctx = context(dry_run=True)
    result = cleaner.apply(cleaner.plan(ctx), ctx)
    assert result.succeeded == ("a",)
    assert mutations(azure) == []


def test_apply_failed_vm_is_logged_reported_and_does_not_stop_the_rest(
    caplog: pytest.LogCaptureFixture,
) -> None:
    azure = FakeAzure([vm("a"), vm("b")], scripts={"vm_begin": {"a": [RuntimeError("begin")]}})
    cleaner = cleaner_for(azure)
    ctx = context()
    result = cleaner.apply(cleaner.plan(ctx), ctx)
    assert result.succeeded == ("b",)
    assert [(f.id, f.error) for f in result.failed] == [("a", "begin")]
    assert "Error occurred while processing a instance: begin" in caplog.text
    assert ("nic_begin_delete", "rg", "a-NIC") not in azure.calls


def test_apply_with_empty_plan_logs_nothing_to_delete(caplog: pytest.LogCaptureFixture) -> None:
    cleaner = cleaner_for(FakeAzure([]))
    cleaner.apply(Plan(), context())
    assert "No Azure instances to delete." in caplog.text


def test_supports_delete_and_stop() -> None:
    cleaner = cleaner_for(FakeAzure([]))
    assert cleaner.supports(Operation.DELETE)
    assert cleaner.supports(Operation.STOP)


def test_state_skip_reason_compares_the_power_state_exactly() -> None:
    azure = FakeAzure([vm("a", statuses=("VM running", "VM stopped"))])
    cleaner = cleaner_for(azure)
    assert cleaner.state_skip_reason("a", ["stopped"]) is None
    assert cleaner.state_skip_reason("a", ["running"]) == "status does not contain an allowed state"


def test_decide_reads_the_clock_only_for_the_age_check() -> None:
    cleaner = cleaner_for(FakeAzure([]))
    untagged = types.SimpleNamespace(name="a", tags=None, time_created=None)

    def clock() -> Any:
        raise AssertionError("clock must not be read")

    assert cleaner.decide(untagged, clock).id == "a"
