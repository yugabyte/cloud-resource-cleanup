"""Parity: `crc.azu.spot_vm` against main (`tests/legacy/azu_spot_vm.txt`).

Both run against the same fake Azure SDK (the SDK classes `crc.azu._base` builds are replaced,
so the real `Base` runs in both). Through the legacy adapter, SDK calls (in order), results, log
records, stdout and raised errors must be identical for every case. The registered `Cleaner`
path (factory with a `RunContext`, `plan()` then `apply()` unless dry run) is compared on
results, errors, stdout, the list/instance_view/mutating calls and log records (as a multiset in
a real run: the Cleaner checks every VM before the first delete). The module lists no regions,
so there is no region fallback or region auth error to cover.
"""

import collections
import datetime
import logging
import re
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from crc.azu.spot_vm import SpotVM
from crc.core.cleaner import Operation, RunContext
from crc.core.registry import get
from tests.helpers_azure_spot import (
    DEALLOCATED,
    NOW,
    RG,
    RUNNING,
    FakeAzure,
    http_error,
    not_found,
    slot,
    vm,
)
from tests.helpers_log_fix import expected_logs
from tests.log_fix_azure import RULES

ORACLE = Path(__file__).parent / "legacy" / "azu_spot_vm.txt"
DAY = datetime.timedelta(days=1)
HOUR = datetime.timedelta(hours=1)
KEEP = {"keep": ["true"]}

BUGS = {"BUG-356"}  # log-only divergences of the LOG-AZU rung


def boom(text: str = "boom") -> Exception:
    return RuntimeError(text)


@dataclass
class Case:
    id: str
    vms: Sequence[Any]
    dry_run: bool = False
    filter_tags: Any = field(default_factory=lambda: {"yb_task": ["itest"]})
    exception_tags: Any = field(default_factory=dict)
    notags: Any = field(default_factory=dict)
    age: Any = field(default_factory=lambda: {"days": 1})
    custom_age_tag_key: Any = "custom_age"
    resource_group: str = RG
    list_error: Exception | None = None
    power: Mapping[str, Sequence[str]] = field(default_factory=dict)
    disk_tags: Mapping[str, Any] = field(default_factory=dict)
    script: Mapping[tuple[str, str], Sequence[Exception | None]] = field(default_factory=dict)
    fails_in_plan: bool = False


OS_A = slot("os-a")
OS_A_NO_RG = types.SimpleNamespace(
    delete_option="Detach",
    managed_disk=types.SimpleNamespace(id="/disks/os-a"),
)
DATA_A = slot("data-a")
BOTH: dict[str, Any] = {
    "os_disk": OS_A,
    "data_disks": [DATA_A, slot("data-del", "Delete"), slot(None)],
}
ALWAYS_FAIL = [boom("nic")] * 24

CASES = [
    Case("delete", [vm("s1"), vm("s2")]),
    Case("dry-run", [vm("s1"), vm("s2")], dry_run=True),
    Case("empty-result", []),
    Case("empty-result-dry-run", [], dry_run=True),
    Case("deallocated-state", [vm("s1")], power={"s1": DEALLOCATED}),
    Case("running-skipped", [vm("s1"), vm("s2")], power={"s1": RUNNING}),
    Case("empty-power-state", [vm("s1")], power={"s1": ["Provisioning succeeded", ""]}),
    Case("single-status-skipped", [vm("s1")], power={"s1": ["VM stopped"]}),
    Case("no-statuses-skipped", [vm("s1")], power={"s1": []}),
    Case("instance-view-error-skips", [vm("s1"), vm("s2")], script={("view", "s1"): [boom()]}),
    Case("not-spot-skipped", [vm("s1", priority="Regular"), vm("s2", priority=None)]),
    Case("spot-case-insensitive", [vm("s1", priority="SPOT"), vm("s2", priority="spot")]),
    Case("untagged-none", [vm("s1", tags=None, priority="Spot")]),
    Case("untagged-empty-dict", [vm("s1", tags={})], filter_tags={}),
    Case("untagged-with-exception", [vm("s1", tags={})], exception_tags=KEEP),
    Case("filter-key-missing", [vm("s1", tags={"other": "x"})]),
    Case("filter-value-mismatch", [vm("s1", tags={"yb_task": "prod"})]),
    Case("filter-empty-values-key-present", [vm("s1")], filter_tags={"yb_task": []}),
    Case("filter-empty-matches-tagged", [vm("s1", tags={"a": "b"})], filter_tags={}),
    Case("filter-none-matches-tagged", [vm("s1", tags={"a": "b"})], filter_tags=None),
    Case(
        "filter-all-keys",
        [vm("s1", tags={"a": "1", "b": "2"}), vm("s2", tags={"a": "1"})],
        filter_tags={"a": ["1"], "b": ["2"]},
    ),
    Case("filter-case-sensitive", [vm("s1", tags={"yb_task": "ITEST"})]),
    Case(
        "exception-any-key",
        [vm("s1", tags={"yb_task": "itest", "keep": "true"}), vm("s2")],
        exception_tags={"keep": ["true"], "other": ["x"]},
    ),
    Case(
        "exception-empty-values",
        [vm("s1", tags={"yb_task": "itest", "keep": "x"})],
        exception_tags={"keep": []},
    ),
    Case("exception-none", [vm("s1")], exception_tags=None),
    Case(
        "notags-all-keys",
        [
            vm("s1", tags={"yb_task": "itest", "a": "1", "b": "2"}),
            vm("s2", tags={"yb_task": "itest", "a": "1"}),
        ],
        notags={"a": ["1"], "b": ["2"]},
    ),
    Case("notags-none", [vm("s1")], notags=None),
    Case("age-exactly-one-day", [vm("s1", age=DAY)]),
    Case("age-just-under-one-day", [vm("s1", age=DAY - datetime.timedelta(seconds=1))]),
    Case("age-hours-edge-old", [vm("s1", age=2 * HOUR)], age={"hours": 2}),
    Case(
        "age-hours-edge-young",
        [vm("s1", age=2 * HOUR - datetime.timedelta(seconds=1))],
        age={"hours": 2},
    ),
    Case(
        "age-days-and-hours",
        [vm("s1", age=DAY + HOUR), vm("s2", age=DAY)],
        age={"days": 1, "hours": 1},
    ),
    Case("age-future-created", [vm("s1", age=-DAY)]),
    Case("age-missing-time-created-skipped", [vm("s1", age=None), vm("s2")]),
    Case("age-naive-time-created-raises", [vm("s1")], fails_in_plan=True),
    Case(
        "custom-age-valid-shorter",
        [vm("s1", tags={"yb_task": "itest", "custom_age": "{'hours': 1}"}, age=2 * HOUR)],
    ),
    Case(
        "custom-age-valid-longer-keeps",
        [vm("s1", tags={"yb_task": "itest", "custom_age": "{'days': 10}"})],
    ),
    Case(
        "custom-age-unparseable-skipped",
        [vm("s1", tags={"yb_task": "itest", "custom_age": "abc"}), vm("s2")],
    ),
    Case("custom-age-empty-skipped", [vm("s1", tags={"yb_task": "itest", "custom_age": ""})]),
    Case(
        "custom-age-zero-skipped",
        [vm("s1", tags={"yb_task": "itest", "custom_age": "{'days': 0}"})],
    ),
    Case(
        "custom-age-key-none",
        [vm("s1", tags={"yb_task": "itest", "custom_age": "abc"})],
        custom_age_tag_key=None,
    ),
    Case("list-error", [vm("s1")], list_error=boom("list failed"), fails_in_plan=True),
    Case("list-error-dry-run", [], dry_run=True, list_error=ValueError("x"), fails_in_plan=True),
    Case("resource-group-from-env", [vm("s1")], resource_group=""),
    Case("duplicate-names", [vm("s1"), vm("s1")]),
    Case("many", [vm(f"s{i}") for i in range(12)]),
    Case(
        "vm-delete-fails-others-continue",
        [vm("s1"), vm("s2")],
        script={("vm_begin", "s1"): [boom("vm")]},
    ),
    Case("vm-delete-poll-fails", [vm("s1", **BOTH)], script={("vm_result", "s1"): [boom("poll")]}),
    Case(
        "vm-delete-fails-dry-run-ignored",
        [vm("s1")],
        dry_run=True,
        script={("vm_begin", "s1"): [boom()]},
    ),
    Case("disks-detach-os-and-data", [vm("s1", **BOTH)]),
    Case("disks-dry-run", [vm("s1", **BOTH)], dry_run=True),
    Case("disks-delete-option-skipped", [vm("s1", os_disk=slot("os-a", "Delete"))]),
    Case(
        "disks-unset-option-is-detach",
        [vm("s1", os_disk=slot("os-a", None), data_disks=[slot("d", "")])],
    ),
    Case("disks-option-case-insensitive", [vm("s1", os_disk=slot("os-a", "DELETE"))]),
    Case("disks-ephemeral-os-no-managed", [vm("s1", os_disk=slot(None))]),
    Case("disks-no-storage-profile", [vm("s1", profile=False)]),
    Case("disks-other-group-skipped", [vm("s1", os_disk=slot("os-a", rg="other-rg"))]),
    Case(
        "disks-other-group-dry-run", [vm("s1", os_disk=slot("os-a", rg="other-rg"))], dry_run=True
    ),
    Case("disks-group-case-insensitive", [vm("s1", os_disk=slot("os-a", rg="RG-TEST"))]),
    Case(
        "disks-exception-tag-keeps",
        [vm("s1", os_disk=OS_A)],
        exception_tags=KEEP,
        disk_tags={f"{RG}/os-a": {"keep": "true"}},
    ),
    Case(
        "disks-exception-tag-keeps-dry-run",
        [vm("s1", os_disk=OS_A)],
        dry_run=True,
        exception_tags=KEEP,
        disk_tags={f"{RG}/os-a": {"keep": "true"}},
    ),
    Case(
        "disks-notags-keeps",
        [vm("s1", os_disk=OS_A)],
        notags={"a": ["1"], "b": []},
        disk_tags={f"{RG}/os-a": {"a": "1", "b": "x"}},
    ),
    Case(
        "disks-notags-partial-deletes",
        [vm("s1", os_disk=OS_A)],
        notags={"a": ["1"], "b": []},
        disk_tags={f"{RG}/os-a": {"a": "1"}},
    ),
    Case(
        "disks-untagged-deletes",
        [vm("s1", os_disk=OS_A)],
        exception_tags=KEEP,
        disk_tags={f"{RG}/os-a": None},
    ),
    Case(
        "disk-get-not-found-deletes",
        [vm("s1", os_disk=OS_A)],
        script={("disk_get", f"{RG}/os-a"): [not_found()]},
    ),
    Case(
        "disk-get-404-deletes",
        [vm("s1", os_disk=OS_A)],
        script={("disk_get", f"{RG}/os-a"): [http_error(404)]},
    ),
    Case(
        "disk-get-500-keeps",
        [vm("s1", os_disk=OS_A)],
        script={("disk_get", f"{RG}/os-a"): [http_error(500)]},
    ),
    Case(
        "disk-get-generic-keeps",
        [vm("s1", os_disk=OS_A)],
        script={("disk_get", f"{RG}/os-a"): [boom()]},
    ),
    Case(
        "disk-delete-not-found-rg-from-id",
        [vm("s1", os_disk=OS_A)],
        script={("disk_begin", f"{RG}/os-a"): [not_found()]},
    ),
    Case(
        "disk-delete-404-rg-from-id",
        [vm("s1", os_disk=OS_A)],
        script={("disk_begin", f"{RG}/os-a"): [http_error(404)]},
    ),
    Case(
        "disk-delete-not-found-rg-fallback",
        [vm("s1", os_disk=OS_A_NO_RG)],
        script={("disk_begin", f"{RG}/os-a"): [not_found()]},
    ),
    Case(
        "disk-delete-404-rg-fallback",
        [vm("s1", os_disk=OS_A_NO_RG)],
        script={("disk_begin", f"{RG}/os-a"): [http_error(404)]},
    ),
    Case(
        "disk-delete-500",
        [vm("s1", os_disk=OS_A)],
        script={("disk_begin", f"{RG}/os-a"): [http_error(500)]},
    ),
    Case(
        "disk-delete-generic",
        [vm("s1", os_disk=OS_A)],
        script={("disk_begin", f"{RG}/os-a"): [boom("d")]},
    ),
    Case(
        "disk-poll-fails",
        [vm("s1", os_disk=OS_A)],
        script={("disk_result", f"{RG}/os-a"): [boom("p")]},
    ),
    Case(
        "disk-failure-does-not-stop-next-disk",
        [vm("s1", **BOTH)],
        script={("disk_begin", f"{RG}/os-a"): [boom()]},
    ),
    Case(
        "nic-retries-then-succeeds",
        [vm("s1")],
        script={("nic_begin", "s1-NIC"): [boom("1"), boom("2")]},
    ),
    Case("nic-poll-fails-once", [vm("s1")], script={("nic_result", "s1-NIC"): [boom("p")]}),
    Case("nic-not-found-counts", [vm("s1")], script={("nic_begin", "s1-NIC"): [not_found()]}),
    Case("nic-404-counts", [vm("s1")], script={("nic_begin", "s1-NIC"): [http_error(404)]}),
    Case("nic-always-fails", [vm("s1"), vm("s2")], script={("nic_begin", "s1-NIC"): ALWAYS_FAIL}),
    Case("tags-value-none-never-matches", [vm("s1", tags={"yb_task": None})]),
    Case("tags-non-dict-filter-string", [vm("s1")], filter_tags={"yb_task": "itest-long"}),
]


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[str]
    nics: list[str]
    disks: list[str]
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


class FrozenDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz: datetime.tzinfo | None = None) -> Any:
        return NOW if tz is None else NOW.astimezone(tz)


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_azu_spot_vm")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    setattr(  # noqa: B010
        module,
        "datetime",
        types.SimpleNamespace(datetime=FrozenDatetime, timezone=datetime.timezone),
    )
    return module


def _args(case: Case) -> dict[str, Any]:
    return {
        "resource_group": case.resource_group,
        "dry_run": case.dry_run,
        "filter_tags": case.filter_tags,
        "exception_tags": case.exception_tags,
        "age": case.age,
        "custom_age_tag_key": case.custom_age_tag_key,
        "notags": case.notags,
    }


def _legacy(case: Case) -> Any:
    return _load_oracle().SpotVM(**_args(case))


def _new(case: Case) -> Any:
    return SpotVM(**_args(case), clock=lambda: NOW)


def _records(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [
        (r.name, r.levelno, re.sub(r"0x[0-9a-f]+", "0x", r.getMessage())) for r in caplog.records
    ]


def _setup(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeAzure:
    vms = list(case.vms)
    if case.id == "age-naive-time-created-raises":
        vms = [vm("s1", age=None)]
        vms[0].time_created = datetime.datetime(2026, 2, 1)
    azure = FakeAzure(
        vms,
        list_error=case.list_error,
        power=case.power,
        disk_tags=case.disk_tags,
        script=case.script,
    )
    azure.install(monkeypatch)
    caplog.clear()
    capsys.readouterr()
    return azure


def _run(
    build: Callable[[Case], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    azure = _setup(case, monkeypatch, caplog, capsys)
    cleaner = build(case)
    error: tuple[str, str] | None = None
    try:
        cleaner.delete()
    except Exception as e:
        error = (type(e).__name__, str(e))
    return Outcome(
        azure.calls,
        list(cleaner.get_deleted),
        list(cleaner.get_deleted_nic),
        list(cleaner.get_deleted_disks),
        _records(caplog),
        capsys.readouterr().out,
        error,
    )


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert new.calls == old.calls
    assert new.error == old.error
    assert (new.deleted, new.nics, new.disks) == (old.deleted, old.nics, old.disks)
    assert new.logs == expected_logs(old.logs, BUGS, RULES)
    assert new.stdout == old.stdout
    assert (new.error is not None) == case.fails_in_plan


def test_leftover_disk_skip_lines_diverge_from_main_in_level_only(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """BUG-356 (LOG-AZU): the per-leftover-disk skip lines are DEBUG, text unchanged."""
    diverging = 0
    for case in CASES:
        old = _run(_legacy, case, monkeypatch, caplog, capsys)
        new = _run(_new, case, monkeypatch, caplog, capsys)
        skips = [r for r in old.logs if r[2].startswith("Skipping leftover Spot disk")]
        if skips:
            diverging += 1
            assert new.logs != old.logs
            assert [r for r in new.logs if r[2].startswith("Skipping leftover Spot disk")] == [
                (name, logging.DEBUG, message) for name, _, message in skips
            ]
    assert diverging


def _run_cleaner(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    azure = _setup(case, monkeypatch, caplog, capsys)
    ctx = RunContext(
        dry_run=case.dry_run,
        operation=Operation.DELETE,
        options={
            "resource_group": case.resource_group,
            "filter_tags": case.filter_tags,
            "exception_tags": case.exception_tags,
            "notags": case.notags,
            "age": case.age,
            "custom_age_tag_key": case.custom_age_tag_key,
        },
        now=NOW,
    )
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    related: Mapping[str, tuple[str, ...]] = {}
    try:
        cleaner = get("azure", "spot_instances")(ctx)
        plan = cleaner.plan(ctx)
        if case.dry_run:
            deleted, related = [c.id for c in plan.candidates], plan.related
        else:
            result = cleaner.apply(plan, ctx)
            deleted, related = list(result.succeeded), result.related
    except Exception as e:
        error = (type(e).__name__, str(e))
    return Outcome(
        azure.calls,
        deleted,
        list(related.get("NIC", ())),
        list(related.get("Disk", ())),
        _records(caplog),
        capsys.readouterr().out,
        error,
    )


def _only(calls: Sequence[tuple[Any, ...]], *names: str) -> list[tuple[Any, ...]]:
    return [c for c in calls if c[0] in names]


READS = ("list", "instance_view")
MUTATING = (
    "vm_begin_delete",
    "vm_result",
    "disk_get",
    "disk_begin_delete",
    "disk_result",
    "nic_begin_delete",
    "nic_result",
)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_cleaner_protocol_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    assert new.error == old.error
    assert new.stdout == old.stdout
    assert _only(new.calls, *READS) == _only(old.calls, *READS)
    if case.fails_in_plan:
        assert _only(new.calls, *MUTATING) == []
        assert new.logs == expected_logs(old.logs, BUGS, RULES)
        return
    assert _only(new.calls, *MUTATING) == _only(old.calls, *MUTATING)
    assert (new.deleted, new.nics, new.disks) == (old.deleted, old.nics, old.disks)
    if case.dry_run:
        assert new.calls == old.calls
        assert new.logs == expected_logs(old.logs, BUGS, RULES)
    else:
        assert collections.Counter(new.logs) == collections.Counter(
            expected_logs(old.logs, BUGS, RULES)
        )
        assert new.logs[-3:] == expected_logs(old.logs, BUGS, RULES)[-3:]


def test_repeated_delete_accumulates_lists_as_main(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = Case("repeat", [vm("s1", os_disk=OS_A)])

    def run_twice(build: Callable[[Case], Any]) -> Outcome:
        azure = _setup(case, monkeypatch, caplog, capsys)
        cleaner = build(case)
        cleaner.delete()
        cleaner.delete()
        return Outcome(
            azure.calls,
            list(cleaner.get_deleted),
            list(cleaner.get_deleted_nic),
            list(cleaner.get_deleted_disks),
            _records(caplog),
            capsys.readouterr().out,
            None,
        )

    old = run_twice(_legacy)
    new = run_twice(_new)
    assert new == old
    assert new.deleted == ["s1", "s1"]
    assert new.nics == ["s1-NIC", "s1-NIC"]
    assert new.disks == [f"{RG}/os-a", f"{RG}/os-a"]
