"""Focused unit tests for `crc.azu.disk`: `plan()` is read-only, `apply()` deletes."""

from typing import Any

import pytest

from crc.azu.disk import AzureDiskCleaner, Disk, DiskSettings
from crc.core.cleaner import Operation, Plan, RunContext
from tests.helpers_azure_disk import NOW, FakeAzure, ago, disk


def _context(*, dry_run: bool) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def _cleaner(azure: FakeAzure, **settings: Any) -> AzureDiskCleaner:
    return AzureDiskCleaner(lambda: azure.client(None, "sub-1"), "rg", DiskSettings(**settings))


def _names(calls: list[tuple[Any, ...]], *names: str) -> list[tuple[Any, ...]]:
    return [c for c in calls if c[0] in names]


def test_supports_only_delete() -> None:
    cleaner = _cleaner(FakeAzure([]))
    assert cleaner.supports(Operation.DELETE)
    assert not cleaner.supports(Operation.STOP)


def test_plan_lists_and_never_deletes() -> None:
    azure = FakeAzure([disk("a"), disk("b", state="Reserved")])
    plan = _cleaner(azure).plan(_context(dry_run=False))
    assert [c.id for c in plan.candidates] == ["a"]
    assert [(s.id, s.reason) for s in plan.skipped] == [("b", "disk state not allowed")]
    assert _names(azure.calls, "begin_delete") == []
    assert _names(azure.calls, "list") == [("list",)]


def test_plan_in_dry_run_logs_the_summary(caplog: pytest.LogCaptureFixture) -> None:
    azure = FakeAzure([disk("a")])
    _cleaner(azure).plan(_context(dry_run=True))
    assert "List of Azure Disks (Total: 1) which will be deleted: ['a']" in caplog.text


@pytest.mark.parametrize(
    ("settings", "tags", "reason"),
    [
        ({"filter_tags": {"owner": []}}, {"env": "t"}, "no filter_tags match"),
        ({"exception_tags": {"env": []}}, {"env": "t"}, "matches exception_tags"),
        ({"notags": {"env": ["t"]}}, {"env": "t"}, "matches notags"),
    ],
)
def test_plan_reports_tag_skip_reason(
    settings: dict[str, Any], tags: dict[str, str], reason: str
) -> None:
    azure = FakeAzure([disk("a", tags)])
    plan = _cleaner(azure, **settings).plan(_context(dry_run=True))
    assert plan.candidates == ()
    assert [s.reason for s in plan.skipped] == [reason]


def test_plan_keeps_young_disks() -> None:
    azure = FakeAzure([disk("a", created=ago(hours=1))])
    plan = _cleaner(azure, age={"days": 1}).plan(_context(dry_run=True))
    assert plan.candidates == ()
    assert [s.reason for s in plan.skipped] == ["not older than the age threshold"]


def test_apply_deletes_in_plan_order() -> None:
    azure = FakeAzure([disk("a"), disk("b")])
    cleaner = _cleaner(azure)
    plan = cleaner.plan(_context(dry_run=False))
    result = cleaner.apply(plan, _context(dry_run=False))
    assert result.succeeded == ("a", "b")
    assert _names(azure.calls, "begin_delete") == [
        ("begin_delete", "rg", "a"),
        ("begin_delete", "rg", "b"),
    ]


def test_apply_in_dry_run_deletes_nothing() -> None:
    azure = FakeAzure([disk("a")])
    cleaner = _cleaner(azure)
    plan = cleaner.plan(_context(dry_run=True))
    cleaner.apply(plan, _context(dry_run=True))
    assert _names(azure.calls, "begin_delete") == []


def test_apply_with_empty_plan_calls_nothing() -> None:
    azure = FakeAzure([disk("a")])
    result = _cleaner(azure).apply(Plan(), _context(dry_run=False))
    assert result.succeeded == ()
    assert azure.calls == []


def test_apply_propagates_delete_error() -> None:
    azure = FakeAzure([disk("a")], begin_script={"a": [RuntimeError("nope")]})
    cleaner = _cleaner(azure)
    plan = cleaner.plan(_context(dry_run=False))
    with pytest.raises(RuntimeError, match="nope"):
        cleaner.apply(plan, _context(dry_run=False))


def test_plan_propagates_list_error() -> None:
    azure = FakeAzure([], list_error=ValueError("list failed"))
    with pytest.raises(ValueError, match="list failed"):
        _cleaner(azure).plan(_context(dry_run=True))


def test_adapter_keeps_the_public_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = FakeAzure([disk("a"), disk("b", state="Reserved")])
    azure.install(monkeypatch)
    adapter = Disk("rg", False, {}, {}, {}, "", {})
    adapter.delete()
    assert adapter.get_deleted == ["a"]
    assert adapter.disks_names_to_delete == ["a"]
    assert adapter.count == 1
    assert Disk.default_disk_state == ["Unattached"]
