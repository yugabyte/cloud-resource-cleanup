"""Focused unit tests for `crc.azu.nic`: `plan()` is read-only, `apply()` deletes."""

from typing import Any

import pytest

from crc.azu.nic import NIC, AzureNicCleaner
from crc.core.cleaner import Operation, Plan, RunContext
from tests.helpers_azure_nic import NOW, FakeAzure, nic

VM = {"id": "vm-1"}


def _context(*, dry_run: bool) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def _cleaner(
    azure: FakeAzure, name_regex: Any = ("nic",), exception_regex: Any = ()
) -> AzureNicCleaner:
    return AzureNicCleaner(
        lambda: azure.client(None, "sub-1"), "rg", list(name_regex), list(exception_regex)
    )


def _names(calls: list[tuple[Any, ...]], *names: str) -> list[tuple[Any, ...]]:
    return [c for c in calls if c[0] in names]


def test_plan_lists_and_never_deletes() -> None:
    azure = FakeAzure([nic("nic-a"), nic("nic-b", VM)])
    plan = _cleaner(azure).plan(_context(dry_run=False))
    assert [c.id for c in plan.candidates] == ["nic-a"]
    assert [(s.id, s.reason) for s in plan.skipped] == [("nic-b", "attached to a VM")]
    assert _names(azure.calls, "begin_delete", "result") == []


def test_plan_requests_the_listing_twice_like_main() -> None:
    azure = FakeAzure([nic("nic-a")])
    _cleaner(azure).plan(_context(dry_run=True))
    assert _names(azure.calls, "list_all") == [("list_all",), ("list_all",)]


def test_plan_records_skip_reasons() -> None:
    azure = FakeAzure([nic("other"), nic("nic-keep"), nic("nic-a", VM)])
    plan = _cleaner(azure, ["nic"], ["keep"]).plan(_context(dry_run=True))
    assert plan.candidates == ()
    assert [(s.id, s.reason) for s in plan.skipped] == [
        ("other", "no name_regex match"),
        ("nic-keep", "matches exception_regex"),
        ("nic-a", "attached to a VM"),
    ]


def test_plan_candidate_carries_name() -> None:
    (candidate,) = _cleaner(FakeAzure([nic("nic-a")])).plan(_context(dry_run=True)).candidates
    assert (candidate.id, candidate.name) == ("nic-a", "nic-a")


def test_empty_name_regex_plans_nothing_and_lists_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    azure = FakeAzure([nic("nic-a")])
    cleaner = _cleaner(azure, [])
    plan = cleaner.plan(_context(dry_run=False))
    assert plan == Plan()
    assert cleaner.apply(plan, _context(dry_run=False)).succeeded == ()
    assert azure.calls == []
    assert [r.getMessage() for r in caplog.records] == ["No Azure NICs to delete."]


def test_dry_run_plan_logs_summary_only(caplog: pytest.LogCaptureFixture) -> None:
    _cleaner(FakeAzure([nic("nic-a")])).plan(_context(dry_run=True))
    assert [r.getMessage() for r in caplog.records] == [
        "List of Azure NICs (Total: 1) which will be deleted: ['nic-a']"
    ]


def test_apply_deletes_in_plan_order_and_waits_for_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    azure = FakeAzure([nic("nic-a"), nic("nic-b")])
    cleaner = _cleaner(azure)
    plan = cleaner.plan(_context(dry_run=False))
    azure.calls.clear()
    result = cleaner.apply(plan, _context(dry_run=False))
    assert _names(azure.calls, "begin_delete", "result") == [
        ("begin_delete", "rg", "nic-a"),
        ("result", "nic-a"),
        ("begin_delete", "rg", "nic-b"),
        ("result", "nic-b"),
    ]
    assert result.succeeded == ("nic-a", "nic-b")
    assert [r.getMessage() for r in caplog.records] == [
        "Deleted NIC: nic-a",
        "Deleted NIC: nic-b",
        "Number of Azure NICs deleted: 2",
        "List of Azure NICs deleted: ['nic-a', 'nic-b']",
    ]


def test_apply_in_dry_run_makes_no_call() -> None:
    azure = FakeAzure([nic("nic-a")])
    cleaner = _cleaner(azure)
    plan = cleaner.plan(_context(dry_run=True))
    azure.calls.clear()
    assert cleaner.apply(plan, _context(dry_run=True)).succeeded == ("nic-a",)
    assert azure.calls == []


def test_apply_retries_and_swallows_errors(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    boom = RuntimeError("denied")
    azure = FakeAzure([nic("nic-a"), nic("nic-b")], begin_script={"nic-a": [boom, boom, boom]})
    cleaner = _cleaner(azure)
    plan = cleaner.plan(_context(dry_run=False))
    azure.calls.clear()
    result = cleaner.apply(plan, _context(dry_run=False))
    assert result.succeeded == ("nic-b",)
    assert [(f.id, f.error) for f in result.failed] == [("nic-a", "denied")]
    assert _names(azure.calls, "begin_delete") == [
        ("begin_delete", "rg", "nic-a"),
        ("begin_delete", "rg", "nic-a"),
        ("begin_delete", "rg", "nic-a"),
        ("begin_delete", "rg", "nic-b"),
    ]
    messages = [r.getMessage() for r in caplog.records]
    assert messages[:5] == [
        "Error occurred while deleting NIC nic-a: denied",
        "Retrying deletion of NIC nic-a",
        "Error occurred while deleting NIC nic-a: denied",
        "Retrying deletion of NIC nic-a",
        "Error occurred while deleting NIC nic-a: denied",
    ]
    assert messages[5] == "Failed to delete the NIC - nic-a"


def test_apply_retry_succeeds_on_second_attempt() -> None:
    azure = FakeAzure([nic("nic-a")], result_script={"nic-a": [RuntimeError("poll")]})
    cleaner = _cleaner(azure)
    plan = cleaner.plan(_context(dry_run=False))
    assert cleaner.apply(plan, _context(dry_run=False)).succeeded == ("nic-a",)


def test_apply_empty_plan_logs_empty_summary(caplog: pytest.LogCaptureFixture) -> None:
    result = _cleaner(FakeAzure([])).apply(Plan(), _context(dry_run=False))
    assert result.succeeded == ()
    assert [r.getMessage() for r in caplog.records] == [
        "Number of Azure NICs deleted: 0",
        "List of Azure NICs deleted: []",
    ]


def test_name_none_raises_in_plan() -> None:
    with pytest.raises(TypeError, match="NoneType"):
        _cleaner(FakeAzure([nic(None)])).plan(_context(dry_run=True))


def test_supports_only_delete() -> None:
    cleaner = _cleaner(FakeAzure([]))
    assert cleaner.supports(Operation.DELETE)
    assert not cleaner.supports(Operation.STOP)


def test_adapter_delete_with_injected_client(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = FakeAzure([nic("nic-a"), nic("other")])
    azure.install(monkeypatch)
    adapter = NIC("rg", False, ["nic"], [])
    adapter.delete()
    assert adapter.get_deleted == ["nic-a"]
    assert adapter.delete_count == 1
    assert _names(azure.calls, "begin_delete") == [("begin_delete", "rg", "nic-a")]
