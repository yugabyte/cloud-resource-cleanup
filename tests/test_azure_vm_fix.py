"""BUG-002 (list the configured group only), BUG-003 (stop deallocates), BUG-010
(power state from the `PowerState/` status) and BUG-198 (exact power state match)."""

import logging
from typing import Any

import pytest

from crc.azu.vm import VmCleaner, VmSettings
from crc.core import logger
from crc.core.cleaner import Operation, RunContext
from tests.helpers_ad_vm_engine import done_ids, run_vm
from tests.helpers_azure_vm import NOW, FakeAzure, vm

MUTATING = ("vm_begin_delete", "vm_result", "nic_begin_delete", "nic_result")
OFF = ("begin_power_off", "begin_deallocate")


def cleaner_for(azure: FakeAzure, group: str = "rg", **settings: Any) -> VmCleaner:
    compute = azure.compute_client(None, "sub")
    config = {"filter_tags": {"env": ["ci"]}, "age": {"hours": 1}, **settings}
    return VmCleaner(
        lambda: compute, lambda: azure.network_client(None, "sub"), group, VmSettings(**config)
    )


def run(
    azure: FakeAzure,
    operation: Operation = Operation.DELETE,
    *,
    dry_run: bool = False,
    **settings: Any,
) -> Any:
    ctx = RunContext(dry_run=dry_run, operation=operation, options={}, now=NOW)
    cleaner = cleaner_for(azure, **settings)
    return cleaner.apply(cleaner.plan(ctx), ctx)


def calls(azure: FakeAzure, *names: str) -> list[tuple[Any, ...]]:
    return [c for c in azure.calls if c[0] in names]


def status(code: str | None, display: str | None = "x") -> tuple[str | None, str | None]:
    return (code, display)


def test_bug_002_lists_only_the_configured_group_and_never_the_subscription() -> None:
    azure = FakeAzure([vm("mine", group="rg"), vm("theirs", group="other-rg"), vm("any")])
    result = run(azure)
    assert calls(azure, "list") == [("list", "rg")]
    assert calls(azure, "list_all") == []
    assert result.succeeded == ("mine", "any")
    assert ("instance_view", "rg", "theirs") not in azure.calls
    assert ("vm_begin_delete", "rg", "theirs") not in azure.calls


def test_bug_002_engine_lists_the_configured_group(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = FakeAzure([vm("mine", group="rg-x"), vm("theirs", group="rg-y")])
    azure.install(monkeypatch)
    outcome = run_vm("azure", cleaner_for(azure, group="rg-x"), dry_run=True, now=NOW)
    assert calls(azure, "list") == [("list", "rg-x")]
    assert done_ids(outcome) == ["mine"]


def test_bug_002_empty_resource_group_fails_closed_before_any_sdk_call() -> None:
    azure = FakeAzure([vm("a")])
    with pytest.raises(ValueError, match="resource group"):
        run(azure, group="")
    assert calls(azure, "list", "list_all", "instance_view", *MUTATING, *OFF) == []


def test_bug_003_stop_deallocates_and_never_powers_off() -> None:
    azure = FakeAzure([vm("a"), vm("b")])
    result = run(azure, Operation.STOP)
    assert result.succeeded == ("a", "b")
    assert calls(azure, *OFF) == [("begin_deallocate", "rg", "a"), ("begin_deallocate", "rg", "b")]


def test_bug_003_dry_run_stop_calls_nothing() -> None:
    azure = FakeAzure([vm("a")])
    run(azure, Operation.STOP, dry_run=True)
    assert calls(azure, *OFF, *MUTATING) == []


def test_bug_003_delete_is_unchanged() -> None:
    azure = FakeAzure([vm("a")])
    run(azure)
    assert [c[:3] for c in calls(azure, *MUTATING, *OFF)] == [
        ("vm_begin_delete", "rg", "a"),
        ("vm_result", "a", 3600),
        ("nic_begin_delete", "rg", "a-NIC"),
        ("nic_result", "a-NIC", 600),
    ]


@pytest.mark.parametrize(
    "statuses",
    [
        pytest.param(("only",), id="single-entry"),
        pytest.param((), id="empty"),
        pytest.param(("p", None), id="no-code"),
        pytest.param((status("ProvisioningState/failed"),), id="provisioning-only"),
        pytest.param((status("PowerState-running"), status("PowerStateX/running")), id="lookalike"),
        pytest.param((status("PowerState/"),), id="empty-power-state"),
    ],
)
def test_bug_010_no_power_state_keeps_the_vm(
    statuses: tuple[Any, ...], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    azure = FakeAzure([vm("a", statuses=statuses), vm("b")])
    cleaner = cleaner_for(azure)
    ctx = RunContext(dry_run=False, operation=Operation.DELETE, options={}, now=NOW)
    logger.begin("azure", "vm", "delete", False, {})
    try:
        plan = cleaner.plan(ctx)
    finally:
        logger.finish()
    assert [c.id for c in plan.candidates] == ["b"]
    assert {s.id: s.reason for s in plan.skipped} == {"a": "no_power_state"}
    assert "SKIPPED cloud=azure resource=vm reason=no_power_state count=1" in caplog.text
    assert calls(azure, *MUTATING, *OFF) == []


def test_bug_010_power_state_is_found_by_code_not_by_position() -> None:
    running_first = (status("PowerState/running", "VM running"), status("ProvisioningState/ok"))
    three = (status("ProvisioningState/ok"), status("OsState/x"), status("PowerState/running"))
    azure = FakeAzure([vm("a", statuses=running_first), vm("b", statuses=three)])
    assert run(azure).succeeded == ("a", "b")


def test_bug_010_first_power_state_status_wins() -> None:
    both = (status("PowerState/stopped"), status("PowerState/running"))
    azure = FakeAzure([vm("a", statuses=both)])
    assert run(azure).succeeded == ()


def test_bug_010_view_error_still_keeps_the_vm_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    azure = FakeAzure([vm("a", statuses=RuntimeError("view failed")), vm("b")])
    assert run(azure).succeeded == ("b",)
    assert "Error occurred while processing a instance: view failed" in caplog.text


@pytest.mark.parametrize(
    ("states", "selected"),
    [
        (["running"], True),
        (["stopped", "running"], True),
        (["run"], False),
        (["runn"], False),
        ([""], False),
        ([], False),
        (["stopped"], False),
        (["Running"], False),
        (["PowerState/running"], False),
        (["running "], False),
    ],
)
def test_bug_198_state_is_matched_exactly(states: list[str], selected: bool) -> None:
    azure = FakeAzure([vm("a")])
    assert (run(azure, instance_states=states).succeeded == ("a",)) is selected


def test_bug_198_deallocating_does_not_match_deallocated() -> None:
    azure = FakeAzure(
        [vm("a", statuses=("p", "VM deallocating")), vm("b", statuses=("p", "VM deallocated"))]
    )
    assert run(azure, instance_states=["deallocated"]).succeeded == ("b",)


def test_bug_198_power_state_code_is_compared_case_insensitively() -> None:
    azure = FakeAzure([vm("a", statuses=(status("PowerState/Running"),))])
    assert run(azure, instance_states=["running"]).succeeded == ("a",)
