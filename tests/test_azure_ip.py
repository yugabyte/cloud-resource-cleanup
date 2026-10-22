"""Focused unit tests for `crc.azu.ip`: `plan()` is read-only, `apply()` deletes."""

from typing import Any

import pytest

from crc.azu.ip import IP, AzureIpCleaner
from crc.core.cleaner import Operation, Plan, RunContext
from tests.helpers_azure_ip import NOW, FakeAzure, ip

ENV = {"env": "test"}


def _context(*, dry_run: bool) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def _cleaner(azure: FakeAzure, **specs: Any) -> AzureIpCleaner:
    return AzureIpCleaner(
        lambda: azure.client(None, "sub-1"),
        "rg",
        specs.get("filter_tags"),
        specs.get("exception_tags"),
        specs.get("notags"),
    )


def _names(calls: list[tuple[Any, ...]], name: str) -> list[tuple[Any, ...]]:
    return [c for c in calls if c[0] == name]


def test_plan_lists_and_never_deletes() -> None:
    azure = FakeAzure([ip("a", ENV), ip("b", ENV, config={"id": "nic"})])
    plan = _cleaner(azure).plan(_context(dry_run=False))
    assert [c.id for c in plan.candidates] == ["a"]
    assert [(s.id, s.reason) for s in plan.skipped] == [("b", "attached to an IP configuration")]
    assert _names(azure.calls, "begin_delete") == []


def test_plan_records_skip_reasons() -> None:
    azure = FakeAzure([ip("a", {"x": "1"}), ip("b", ENV), ip("c", ENV)])
    cleaner = _cleaner(azure, filter_tags={"env": []}, exception_tags={"env": ["test"]})
    plan = cleaner.plan(_context(dry_run=True))
    assert plan.candidates == ()
    assert [(s.id, s.reason) for s in plan.skipped] == [
        ("a", "no filter_tags match"),
        ("b", "matches exception_tags or notags"),
        ("c", "matches exception_tags or notags"),
    ]


def test_plan_candidate_carries_name_and_tags() -> None:
    azure = FakeAzure([ip("a", ENV)])
    (candidate,) = _cleaner(azure).plan(_context(dry_run=True)).candidates
    assert (candidate.id, candidate.name, dict(candidate.tags)) == ("a", "a", ENV)


def test_dry_run_plan_logs_summary_only(caplog: pytest.LogCaptureFixture) -> None:
    azure = FakeAzure([ip("a", ENV)])
    _cleaner(azure).plan(_context(dry_run=True))
    assert [r.getMessage() for r in caplog.records] == [
        "List of Azure IPs (Total: 1) which will be deleted: ['a']"
    ]


def test_apply_deletes_in_plan_order_with_keyword_arguments(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    azure = FakeAzure([ip("a", ENV), ip("b", ENV)])
    cleaner = _cleaner(azure)
    plan = cleaner.plan(_context(dry_run=False))
    azure.calls.clear()
    result = cleaner.apply(plan, _context(dry_run=False))
    assert _names(azure.calls, "begin_delete") == [
        ("begin_delete", "rg", "a"),
        ("begin_delete", "rg", "b"),
    ]
    assert result.succeeded == ("a", "b")
    assert [r.getMessage() for r in caplog.records] == [
        "Deleted IP address: a",
        "Deleted IP address: b",
        "number of Azure IPs deleted: 2",
        "List of Azure IPs deleted: ['a', 'b']",
    ]


def test_apply_in_dry_run_makes_no_call() -> None:
    azure = FakeAzure([ip("a", ENV)])
    cleaner = _cleaner(azure)
    plan = cleaner.plan(_context(dry_run=True))
    azure.calls.clear()
    assert cleaner.apply(plan, _context(dry_run=True)).succeeded == ("a",)
    assert azure.calls == []


def test_apply_error_propagates() -> None:
    azure = FakeAzure([ip("a", ENV), ip("b", ENV)], delete_errors={"a": RuntimeError("denied")})
    cleaner = _cleaner(azure)
    with pytest.raises(RuntimeError, match="denied"):
        cleaner.apply(cleaner.plan(_context(dry_run=False)), _context(dry_run=False))
    assert _names(azure.calls, "begin_delete") == [("begin_delete", "rg", "a")]


def test_apply_empty_plan_logs_empty_summary(caplog: pytest.LogCaptureFixture) -> None:
    result = _cleaner(FakeAzure([])).apply(Plan(), _context(dry_run=False))
    assert result.succeeded == ()
    assert [r.getMessage() for r in caplog.records] == [
        "number of Azure IPs deleted: 0",
        "List of Azure IPs deleted: []",
    ]


def test_untagged_with_exception_tags_raises_in_plan() -> None:
    azure = FakeAzure([ip("a", None)])
    with pytest.raises(TypeError, match="NoneType"):
        _cleaner(azure, exception_tags={"env": []}).plan(_context(dry_run=True))


def test_supports_only_delete() -> None:
    cleaner = _cleaner(FakeAzure([]))
    assert cleaner.supports(Operation.DELETE)
    assert not cleaner.supports(Operation.STOP)


def test_adapter_delete_with_injected_client(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = FakeAzure([ip("a", ENV), ip("b", {"x": "y"})])
    azure.install(monkeypatch)
    adapter = IP("rg", False, {"env": []}, {}, {})
    adapter.delete()
    assert adapter.get_deleted == ["a"]
    assert adapter.count == 1
    assert _names(azure.calls, "begin_delete") == [("begin_delete", "rg", "a")]
