"""Parity: `crc.azu.vm.VM` against main (`tests/legacy/azu_vm.txt`).

Both run against the same fake Azure SDK (the SDK classes `crc.azu._base` builds are replaced, so
the real `Base` and the real delete helpers run in both). SDK calls (in order, including the lazy
paging of `list_all`), results, log records, stdout and raised errors must be identical for every
case through the legacy adapter. The registered `Cleaner` path (factory with a `RunContext`,
`plan()` then `apply()` unless dry run) is compared on results, errors, stdout, the order of the
listing, status and mutating calls, and the log records. It reads every status before the first
mutation, so the log lines are compared as sets, except the summary lines, which must be last and
identical. The module lists no regions, so there is no region fallback or auth error to cover.
"""

import datetime
import logging
import re
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from azure.core.exceptions import ResourceNotFoundError

from crc.azu.vm import VM, VmCleaner, build_vm_cleaner
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import get
from tests.helpers_azure_vm import NOW, RUNNING, STOPPED, FakeAzure, not_found, vm
from tests.helpers_log_fix import expected_logs
from tests.log_fix_azure import RULES

ORACLE = Path(__file__).parent / "legacy" / "azu_vm.txt"
ENV = {"env": ["ci"]}
_ADDRESS = re.compile(r"0x[0-9a-f]+")
NIC_ATTEMPTS = 24
THREE = 3
SUMMARY = ("No Azure instances", "number of Azure", "List of Azure")

BUGS = {"BUG-358"}  # log-only divergences of the LOG-AZU rung


@dataclass
class Case:
    id: str
    vms: Sequence[Any]
    filter_tags: Any = field(default_factory=lambda: dict(ENV))
    exception_tags: Any = field(default_factory=dict)
    notags: Any = field(default_factory=dict)
    age: Any = field(default_factory=lambda: {"hours": 1})
    custom_age_tag_key: str = ""
    dry_run: bool = False
    operation: str = "delete"
    instance_state: Any = None
    resource_group: str = "rg-test"
    max_age: str | None = None
    list_error: Exception | None = None
    scripts: Mapping[str, Mapping[str, Sequence[Exception | None]]] = field(default_factory=dict)
    fails_in_plan: bool = False


def _boom(text: str = "boom") -> Exception:
    return RuntimeError(text)


def _many(count: int) -> list[Any]:
    return [vm(f"vm-{i}") for i in range(count)]


def _nic_fail(count: int, error: Exception | None = None) -> dict[str, list[Exception | None]]:
    return {"vm-a-NIC": [error or _boom("nic")] * count}


CASES = [
    Case("delete", [vm("vm-a"), vm("vm-b")]),
    Case("delete-dry-run", [vm("vm-a"), vm("vm-b")], dry_run=True),
    Case("stop", [vm("vm-a"), vm("vm-b")], operation="stop"),
    Case("stop-dry-run", [vm("vm-a"), vm("vm-b")], operation="stop", dry_run=True),
    Case("empty-result", []),
    Case("empty-result-dry-run", [], dry_run=True),
    Case("empty-result-stop", [], operation="stop"),
    Case("empty-result-stop-dry-run", [], operation="stop", dry_run=True),
    Case("many-order", _many(12)),
    Case("many-dry-run", _many(12), dry_run=True),
    Case("many-stop", _many(12), operation="stop"),
    Case("resource-group-from-env", [vm("vm-a")], resource_group=""),
    Case("duplicate-names", [vm("vm-a"), vm("vm-a")]),
    # filters
    Case(
        "filter-all-keys-match",
        [vm("a", {"env": "ci", "team": "x"})],
        {"env": ["ci"], "team": ["x"]},
    ),
    Case("filter-one-key-missing", [vm("a", {"env": "ci"})], {"env": ["ci"], "team": ["x"]}),
    Case("filter-value-not-listed", [vm("a", {"env": "prod"}), vm("b")]),
    Case(
        "filter-several-values-any",
        [vm("a", {"env": "x"}), vm("b", {"env": "y"})],
        {"env": ["x", "y"]},
    ),
    Case(
        "filter-empty-values-means-present",
        [vm("a", {"env": "z"}), vm("b", {"k": "v"})],
        {"env": []},
    ),
    Case("filter-empty-selects-every-tagged", [vm("a", {"x": "1"}), vm("b", {"y": "2"})], {}),
    Case("filter-none-selects-every-tagged", [vm("a", {"x": "1"})], None),
    Case("filter-case-sensitive", [vm("a", {"env": "CI"}), vm("b", {"env": "ci"})]),
    Case("filter-key-case-sensitive", [vm("a", {"ENV": "ci"})]),
    Case("filter-empty-tag-value", [vm("a", {"env": ""})], {"env": ["ci"]}),
    Case("filter-string-spec-is-substring-test", [vm("a", {"env": "c"})], {"env": "ci"}),
    Case("filter-no-match", [vm("a", {"x": "1"}), vm("b", {"y": "2"})]),
    Case("untagged-none", [vm("a", None), vm("b")]),
    Case("untagged-empty-dict", [vm("a", {}), vm("b")]),
    Case("untagged-with-empty-filter", [vm("a", {}), vm("b", None)], {}),
    Case("untagged-with-exception-and-notags", [vm("a", {})], {}, {"x": []}, {"y": []}),
    Case(
        "exception-any-key",
        [vm("a", {"env": "ci", "keep": "1"}), vm("b")],
        exception_tags={"keep": ["1"], "z": []},
    ),
    Case(
        "exception-empty-values-key-present",
        [vm("a", {"env": "ci", "keep": "x"})],
        exception_tags={"keep": []},
    ),
    Case(
        "exception-value-differs",
        [vm("a", {"env": "ci", "keep": "2"})],
        exception_tags={"keep": ["1"]},
    ),
    Case("exception-wins-over-filter", [vm("a", {"env": "ci"})], exception_tags={"env": ["ci"]}),
    Case("exception-none", [vm("a")], exception_tags=None),
    Case(
        "notags-all-keys-skip",
        [vm("a", {"env": "ci", "n1": "1", "n2": "2"})],
        notags={"n1": [], "n2": ["2"]},
    ),
    Case(
        "notags-partial-keeps-resource-selected",
        [vm("a", {"env": "ci", "n1": "1"})],
        notags={"n1": [], "n2": []},
    ),
    Case("notags-none", [vm("a")], notags=None),
    Case(
        "notags-and-exception-both",
        [vm("a", {"env": "ci", "n": "1", "k": "1"}), vm("b")],
        exception_tags={"k": []},
        notags={"n": []},
    ),
    # age
    Case("age-not-old", [vm("a", hours=0.5), vm("b", hours=2)]),
    Case("age-exact-edge", [vm("a", hours=1), vm("b", hours=1 - 1e-6)]),
    Case("age-days", [vm("a", hours=23), vm("b", hours=24), vm("c", hours=48)], age={"days": 1}),
    Case("age-days-and-hours", [vm("a", hours=25), vm("b", hours=26)], age={"days": 1, "hours": 2}),
    Case("age-int-days", [vm("a", hours=23), vm("b", hours=30)], age=1),
    Case("age-empty-matches-all", [vm("a", hours=0), vm("b", hours=0.001)], age={}),
    Case("age-none-matches-all", [vm("a", hours=0)], age=None),
    Case("age-unknown-keys-keep", [vm("a", hours=1000)], age={"weeks": 1}),
    Case("age-future-created", [vm("a", hours=-5)]),
    Case(
        "age-non-utc-timezone",
        [
            vm(
                "a",
                created=(NOW - datetime.timedelta(hours=5)).astimezone(
                    datetime.timezone(datetime.timedelta(hours=-7))
                ),
            )
        ],
    ),
    Case(
        "custom-age-longer-keeps",
        [vm("a", {"env": "ci", "ttl": "{'days': 30}"})],
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-shorter-selects",
        [vm("a", {"env": "ci", "ttl": "{'hours': 1}"}, hours=2)],
        age={"days": 5},
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-empty-falls-back",
        [vm("a", {"env": "ci", "ttl": ""}, hours=5)],
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-unparseable-falls-back",
        [
            vm("a", {"env": "ci", "ttl": "oops"}, hours=5),
            vm("b", {"env": "ci", "ttl": "oops"}, hours=0.1),
        ],
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-zero-dict-falls-back",
        [vm("a", {"env": "ci", "ttl": "{}"}, hours=5)],
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-inf-raises",
        [vm("a", {"env": "ci", "ttl": "{'days': 1e999}"})],
        custom_age_tag_key="ttl",
        fails_in_plan=True,
    ),
    Case("custom-age-key-absent", [vm("a", hours=5)], custom_age_tag_key="ttl"),
    Case("max-age-shortens", [vm("a", hours=3)], age={"days": 5}, max_age="{'hours': 2}"),
    Case("max-age-longer-ignored", [vm("a", hours=3)], age={"hours": 2}, max_age="{'days': 5}"),
    Case("max-age-malformed-raises", [vm("a")], max_age="not a dict", fails_in_plan=True),
    Case("max-age-with-empty-age-unused", [vm("a", hours=0)], age={}, max_age="{'days': 5}"),
    Case("time-created-none", [vm("a", created=None)], fails_in_plan=True),
    Case(
        "time-created-naive", [vm("a", created=datetime.datetime(2026, 1, 1))], fails_in_plan=True
    ),
    Case(
        "time-created-skipped-not-read",
        [vm("a", {}, created=None), vm("b", {"x": "1"}, created=None)],
    ),
    # status (BUG-010 and the substring match)
    Case("status-stopped-not-deleted", [vm("a", statuses=STOPPED), vm("b")]),
    Case("status-explicit-state", [vm("a", statuses=STOPPED), vm("b")], instance_state=["stopped"]),
    Case(
        "status-state-is-substring",
        [vm("a", statuses=("p", "VM running")), vm("b", statuses=("p", "VM deallocated"))],
        instance_state=["run"],
    ),
    Case(
        "status-state-case-sensitive",
        [vm("a", statuses=("p", "VM running"))],
        instance_state=["Running"],
    ),
    Case(
        "status-several-states-any",
        [vm("a", statuses=STOPPED), vm("b"), vm("c", statuses=("p", "VM deallocated"))],
        instance_state=["stopped", "running"],
    ),
    Case("status-empty-state-list-nothing", [vm("a")], instance_state=[]),
    Case("status-empty-string-state-matches-all", [vm("a", statuses=STOPPED)], instance_state=[""]),
    Case("status-stop-ignores-state-list", [vm("a", statuses=STOPPED), vm("b")], operation="stop"),
    Case("status-single-entry-index-error", [vm("a", statuses=("only",)), vm("b")]),
    Case(
        "status-single-entry-index-error-stop",
        [vm("a", statuses=("only",)), vm("b")],
        operation="stop",
    ),
    Case("status-empty-statuses", [vm("a", statuses=()), vm("b")]),
    Case("status-none-display", [vm("a", statuses=("p", None)), vm("b")]),
    Case("status-first-entry-ignored", [vm("a", statuses=("VM running", "VM stopped"))]),
    Case("status-instance-view-error", [vm("a", statuses=_boom("view failed")), vm("b")]),
    Case(
        "status-error-in-dry-run", [vm("a", statuses=_boom("view failed")), vm("b")], dry_run=True
    ),
    Case("status-read-for-old-vms-only", [vm("a", hours=0.1), vm("b", {"x": "1"}), vm("c")]),
    # errors while acting
    Case(
        "vm-begin-delete-error-swallowed",
        [vm("a"), vm("b")],
        scripts={"vm_begin": {"a": [_boom("begin")]}},
    ),
    Case(
        "vm-result-error-swallowed",
        [vm("a"), vm("b")],
        scripts={"vm_result": {"a": [_boom("timeout")]}},
    ),
    Case(
        "vm-delete-error-dry-run-ignored",
        [vm("a")],
        dry_run=True,
        scripts={"vm_begin": {"a": [_boom()]}},
    ),
    Case(
        "power-off-error-swallowed",
        [vm("a"), vm("b")],
        operation="stop",
        scripts={"off": {"a": [_boom("off")]}},
    ),
    Case(
        "power-off-error-dry-run-ignored",
        [vm("a")],
        operation="stop",
        dry_run=True,
        scripts={"off": {"a": [_boom()]}},
    ),
    Case("error-message-empty", [vm("a"), vm("b")], scripts={"vm_begin": {"a": [RuntimeError()]}}),
    # NIC delete after the VM
    Case("nic-fails-once-then-succeeds", [vm("vm-a")], scripts={"nic_begin": _nic_fail(1)}),
    Case("nic-fails-many-then-succeeds", [vm("vm-a")], scripts={"nic_begin": _nic_fail(5)}),
    Case(
        "nic-fails-all-24-attempts", [vm("vm-a"), vm("vm-b")], scripts={"nic_begin": _nic_fail(24)}
    ),
    Case("nic-fails-more-than-24-attempts", [vm("vm-a")], scripts={"nic_begin": _nic_fail(30)}),
    Case("nic-result-error-retries", [vm("vm-a")], scripts={"nic_result": _nic_fail(2)}),
    Case(
        "nic-not-found-exception",
        [vm("vm-a"), vm("vm-b")],
        scripts={"nic_begin": _nic_fail(1, ResourceNotFoundError("gone"))},
    ),
    Case(
        "nic-http-404-treated-as-removed",
        [vm("vm-a")],
        scripts={"nic_begin": _nic_fail(1, not_found(404))},
    ),
    Case("nic-http-500-retried", [vm("vm-a")], scripts={"nic_begin": _nic_fail(2, not_found(500))}),
    Case(
        "nic-http-no-status-retried",
        [vm("vm-a")],
        scripts={"nic_begin": _nic_fail(1, not_found(None))},
    ),
    Case(
        "nic-failure-keeps-vm-recorded",
        [vm("vm-a"), vm("vm-b")],
        scripts={"nic_begin": _nic_fail(24)},
        dry_run=False,
    ),
    Case("vm-delete-fails-no-nic-call", [vm("vm-a")], scripts={"vm_begin": {"vm-a": [_boom()]}}),
    # listing failures
    Case("list-all-error", [vm("a")], list_error=_boom("list failed"), fails_in_plan=True),
    Case(
        "list-all-error-dry-run", [], dry_run=True, list_error=ValueError("x"), fails_in_plan=True
    ),
    Case(
        "list-all-error-stop",
        [],
        operation="stop",
        list_error=_boom("list failed"),
        fails_in_plan=True,
    ),
    Case(
        "iteration-error-mid-listing",
        [vm("a"), RuntimeError("page failed"), vm("c")],
        fails_in_plan=True,
    ),
    Case(
        "iteration-error-mid-listing-dry-run",
        [vm("a"), RuntimeError("page failed")],
        dry_run=True,
        fails_in_plan=True,
    ),
    Case(
        "name-missing-selected-vm",
        [types.SimpleNamespace(tags={"env": "ci"}, time_created=NOW - datetime.timedelta(hours=5))],
        fails_in_plan=True,
    ),
    Case("name-missing-skipped-vm", [types.SimpleNamespace(tags=None, time_created=None)]),
    Case(
        "tags-attribute-missing",
        [types.SimpleNamespace(name="a", time_created=NOW)],
        fails_in_plan=True,
    ),
]


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[str]
    nics: list[str]
    stopped: list[str]
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None
    counts: list[tuple[str, int, str]] = field(default_factory=list)


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_azu_vm")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    module.__dict__["datetime"] = types.SimpleNamespace(
        datetime=types.SimpleNamespace(now=lambda: NOW)
    )
    return module


def _legacy(case: Case) -> Any:
    return _load_oracle().VM(
        case.resource_group,
        case.dry_run,
        case.filter_tags,
        case.exception_tags,
        case.age,
        case.custom_age_tag_key,
        case.notags,
    )


def _new(case: Case) -> Any:
    return VM(
        case.resource_group,
        case.dry_run,
        case.filter_tags,
        case.exception_tags,
        case.age,
        case.custom_age_tag_key,
        case.notags,
        clock=lambda: NOW,
    )


def _setup(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeAzure:
    azure = FakeAzure(case.vms, case.list_error, case.scripts)
    azure.install(monkeypatch)
    if case.max_age is not None:
        monkeypatch.setenv("MAX_AGE", case.max_age)
    else:
        monkeypatch.delenv("MAX_AGE", raising=False)
    caplog.clear()
    capsys.readouterr()
    return azure


def _records(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [(r.name, r.levelno, _ADDRESS.sub("0x0", r.getMessage())) for r in caplog.records]


def _run(
    build: Callable[[Case], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    azure = _setup(case, monkeypatch, caplog, capsys)
    service = build(case)
    error: tuple[str, str] | None = None
    try:
        if case.operation == "stop":
            service.stop()
        elif case.instance_state is None:
            service.delete()
        else:
            service.delete(case.instance_state)
    except Exception as e:
        error = (type(e).__name__, str(e))
    logs = _records(caplog)
    stdout = capsys.readouterr().out
    caplog.clear()
    counts = [service.delete_count, service.nic_delete_count, service.stopped_count]
    return Outcome(
        azure.calls,
        list(service.get_deleted),
        list(service.get_deleted_nic),
        list(service.get_stopped),
        logs,
        stdout,
        error,
        [*_records(caplog), *[("count", 0, str(c)) for c in counts]],
    )


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


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
    assert new.deleted == old.deleted
    assert new.nics == old.nics
    assert new.stopped == old.stopped
    assert new.logs == expected_logs(old.logs, BUGS, RULES)
    assert new.counts == old.counts
    assert new.stdout == old.stdout
    assert (new.error is not None) == case.fails_in_plan


def _run_cleaner(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    azure = _setup(case, monkeypatch, caplog, capsys)
    operation = Operation(case.operation)
    ctx = RunContext(
        dry_run=case.dry_run,
        operation=operation,
        options={
            "resource_group": case.resource_group,
            "filter_tags": case.filter_tags,
            "exception_tags": case.exception_tags,
            "notags": case.notags,
            "age": case.age,
            "custom_age_tag_key": case.custom_age_tag_key,
            "instance_state": case.instance_state,
        },
        now=NOW,
    )
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    nics: list[str] = []
    stopped: list[str] = []
    try:
        cleaner = get("azure", "vm")(ctx)
        plan = cleaner.plan(ctx)
        result = cleaner.apply(plan, ctx)
        names = list(result.succeeded)
        deleted, stopped = (names, []) if operation is Operation.DELETE else ([], names)
        nics = list(result.related.get("NIC", ()))
    except Exception as e:
        error = (type(e).__name__, str(e))
    return Outcome(
        azure.calls, deleted, nics, stopped, _records(caplog), capsys.readouterr().out, error
    )


def _only(calls: Sequence[tuple[Any, ...]], *names: str) -> list[tuple[Any, ...]]:
    return [c for c in calls if c[0] in names]


MUTATING = (
    "vm_begin_delete",
    "vm_result",
    "nic_begin_delete",
    "nic_result",
    "begin_power_off",
    "sleep",
)


def _is_summary(record: tuple[str, int, str]) -> bool:
    return record[2].startswith(SUMMARY)


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
    assert _only(new.calls, "list_all") == _only(old.calls, "list_all")
    if case.fails_in_plan:
        assert _only(new.calls, *MUTATING) == []
        return
    assert _only(new.calls, *MUTATING) == _only(old.calls, *MUTATING)
    assert _only(new.calls, "instance_view") == _only(old.calls, "instance_view")
    assert new.deleted == old.deleted
    assert new.nics == old.nics
    assert new.stopped == old.stopped
    assert sorted(r for r in new.logs if not _is_summary(r)) == sorted(
        r for r in expected_logs(old.logs, BUGS, RULES) if not _is_summary(r)
    )
    new_summary = [r for r in new.logs if _is_summary(r)]
    assert new_summary == [r for r in expected_logs(old.logs, BUGS, RULES) if _is_summary(r)]
    assert new.logs[len(new.logs) - len(new_summary) :] == new_summary


def _find(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)


def _swallowed(o: Outcome, message: str) -> bool:
    return o.error is None and any(r[1] == logging.ERROR and message in r[2] for r in o.logs)


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("untagged-none", lambda o: o.deleted == ["b"]),
    ("untagged-empty-dict", lambda o: o.deleted == ["b"]),
    (
        "untagged-with-empty-filter",
        lambda o: o.deleted == [] and _only(o.calls, "instance_view") == [],
    ),
    ("filter-empty-selects-every-tagged", lambda o: o.deleted == ["a", "b"]),
    ("filter-none-selects-every-tagged", lambda o: o.deleted == ["a"]),
    ("filter-one-key-missing", lambda o: o.deleted == []),
    ("filter-empty-values-means-present", lambda o: o.deleted == ["a"]),
    ("filter-string-spec-is-substring-test", lambda o: o.deleted == ["a"]),
    ("filter-case-sensitive", lambda o: o.deleted == ["b"]),
    ("exception-any-key", lambda o: o.deleted == ["b"]),
    ("exception-empty-values-key-present", lambda o: o.deleted == []),
    ("exception-wins-over-filter", lambda o: o.deleted == []),
    ("notags-all-keys-skip", lambda o: o.deleted == []),
    ("notags-partial-keeps-resource-selected", lambda o: o.deleted == ["a"]),
    ("age-exact-edge", lambda o: o.deleted == ["a"]),
    ("age-empty-matches-all", lambda o: o.deleted == ["a", "b"]),
    ("age-unknown-keys-keep", lambda o: o.deleted == []),
    ("custom-age-empty-falls-back", lambda o: o.deleted == ["a"]),
    ("custom-age-unparseable-falls-back", lambda o: o.deleted == ["a"]),
    ("custom-age-zero-dict-falls-back", lambda o: o.deleted == ["a"]),
    ("custom-age-inf-raises", lambda o: o.error is not None and o.error[0] == "OverflowError"),
    ("max-age-shortens", lambda o: o.deleted == ["a"]),
    ("max-age-with-empty-age-unused", lambda o: o.deleted == ["a"]),
    ("max-age-malformed-raises", lambda o: o.error is not None and o.error[0] == "SyntaxError"),
    ("time-created-none", lambda o: o.error is not None and o.error[0] == "AttributeError"),
    ("time-created-naive", lambda o: o.error is not None and o.error[0] == "TypeError"),
    ("time-created-skipped-not-read", lambda o: o.error is None and o.deleted == []),
    ("status-state-is-substring", lambda o: o.deleted == ["a"]),
    ("status-state-case-sensitive", lambda o: o.deleted == []),
    ("status-empty-string-state-matches-all", lambda o: o.deleted == ["a"]),
    ("status-empty-state-list-nothing", lambda o: o.deleted == []),
    ("status-stop-ignores-state-list", lambda o: o.stopped == ["b"]),
    (
        "status-single-entry-index-error",
        lambda o: _swallowed(o, "list index out of range") and o.deleted == ["b"],
    ),
    (
        "status-single-entry-index-error-stop",
        lambda o: _swallowed(o, "list index out of range") and o.stopped == ["b"],
    ),
    ("status-empty-statuses", lambda o: _swallowed(o, "list index out of range")),
    ("status-none-display", lambda o: _swallowed(o, "not iterable") or _swallowed(o, "NoneType")),
    ("status-first-entry-ignored", lambda o: o.deleted == []),
    ("status-instance-view-error", lambda o: _swallowed(o, "view failed") and o.deleted == ["b"]),
    ("status-error-in-dry-run", lambda o: _swallowed(o, "view failed") and o.deleted == ["b"]),
    ("vm-begin-delete-error-swallowed", lambda o: _swallowed(o, "begin") and o.deleted == ["b"]),
    (
        "vm-result-error-swallowed",
        lambda o: _swallowed(o, "timeout") and o.deleted == ["b"] and "a-NIC" not in o.nics,
    ),
    (
        "vm-delete-error-dry-run-ignored",
        lambda o: o.deleted == ["a"] and _only(o.calls, *MUTATING) == [],
    ),
    ("power-off-error-swallowed", lambda o: _swallowed(o, "off") and o.stopped == ["b"]),
    (
        "power-off-error-dry-run-ignored",
        lambda o: o.stopped == ["a"] and _only(o.calls, *MUTATING) == [],
    ),
    (
        "nic-fails-once-then-succeeds",
        lambda o: o.nics == ["vm-a-NIC"] and len(_only(o.calls, "sleep")) == 1,
    ),
    (
        "nic-fails-all-24-attempts",
        lambda o: (
            o.nics == ["vm-b-NIC"]
            and o.deleted == ["vm-a", "vm-b"]
            and len(_only(o.calls, "nic_begin_delete")) == NIC_ATTEMPTS + 1
        ),
    ),
    (
        "nic-fails-more-than-24-attempts",
        lambda o: o.nics == [] and len(_only(o.calls, "nic_begin_delete")) == NIC_ATTEMPTS,
    ),
    (
        "nic-not-found-exception",
        lambda o: o.nics == ["vm-a-NIC", "vm-b-NIC"] and len(_only(o.calls, "sleep")) == 0,
    ),
    ("nic-http-404-treated-as-removed", lambda o: o.nics == ["vm-a-NIC"]),
    ("nic-http-500-retried", lambda o: len(_only(o.calls, "nic_begin_delete")) == THREE),
    (
        "vm-delete-fails-no-nic-call",
        lambda o: _only(o.calls, "nic_begin_delete") == [] and o.deleted == [],
    ),
    ("list-all-error", lambda o: o.error == ("RuntimeError", "list failed")),
    ("iteration-error-mid-listing", lambda o: o.error == ("RuntimeError", "page failed")),
    ("name-missing-skipped-vm", lambda o: o.error is None and o.deleted == []),
    ("name-missing-selected-vm", lambda o: o.error is not None and o.error[0] == "AttributeError"),
    ("delete", lambda o: [c[2] for c in _only(o.calls, "vm_result")] == [3600, 3600]),
    ("stop", lambda o: o.stopped == ["vm-a", "vm-b"] and _only(o.calls, "vm_begin_delete") == []),
    ("stop-dry-run", lambda o: o.stopped == ["vm-a", "vm-b"] and _only(o.calls, *MUTATING) == []),
]


@pytest.mark.parametrize(("case_id", "check"), QUIRKS, ids=[q[0] for q in QUIRKS])
def test_quirk_is_preserved(
    case_id: str,
    check: Callable[[Outcome], bool],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find(case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert check(old), "oracle no longer shows the quirk"
    assert check(new)
    assert check(_run_cleaner(case, monkeypatch, caplog, capsys))


def test_list_is_subscription_wide_and_every_other_call_uses_the_configured_group(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("delete")
    for build in (_legacy, _new):
        azure = _setup(case, monkeypatch, caplog, capsys)
        build(case).delete()
        assert ("list_all",) in azure.calls
        assert ("instance_view", "rg-test", "vm-a") in azure.calls
        assert ("vm_begin_delete", "rg-test", "vm-a") in azure.calls
        assert ("nic_begin_delete", "rg-test", "vm-a-NIC") in azure.calls


def test_stop_powers_off_without_deallocating(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("stop")
    for build in (_legacy, _new):
        azure = _setup(case, monkeypatch, caplog, capsys)
        build(case).stop()
        assert _only(azure.calls, "begin_power_off") == [
            ("begin_power_off", "rg-test", "vm-a"),
            ("begin_power_off", "rg-test", "vm-b"),
        ]


def test_repeated_delete_accumulates_like_main(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("delete")
    results = []
    for build in (_legacy, _new):
        azure = _setup(case, monkeypatch, caplog, capsys)
        service = build(case)
        service.delete()
        service.delete()
        results.append(
            (
                azure.calls,
                service.get_deleted,
                service.get_deleted_nic,
                [(r.levelno, r.getMessage()) for r in caplog.records],
            )
        )
    old, new = results
    assert new[:3] == old[:3]
    assert new[3] == expected_logs(old[3], BUGS, RULES)
    assert new[3] != old[3]
    assert results[1][1] == ["vm-a", "vm-b", "vm-a", "vm-b"]


def test_stop_then_empty_delete_shares_name_lists_like_main(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("stop")
    results = []
    for build in (_legacy, _new):
        azure = _setup(case, monkeypatch, caplog, capsys)
        service = build(case)
        service.stop()
        service.delete(["deallocated"])
        results.append(
            (
                azure.calls,
                service.get_deleted,
                service.get_stopped,
                [(r.levelno, r.getMessage()) for r in caplog.records],
            )
        )
    old, new = results
    assert new[:3] == old[:3]
    assert new[3] == expected_logs(old[3], BUGS, RULES)
    assert new[3] != old[3]
    assert results[1][1] == []
    assert results[1][2] == ["vm-a", "vm-b"]
    assert not any("No Azure instances to delete." in m for _, m in results[1][3])


def test_registered_factory_builds_cleaner(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAzure([]).install(monkeypatch)
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    cleaner: Cleaner = get("azure", "vm")(ctx)
    assert isinstance(cleaner, VmCleaner)
    assert get("azure", "vm") is build_vm_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("azure", "vm")
    assert cleaner.supports(Operation.DELETE)
    assert cleaner.supports(Operation.STOP)


def test_running_state_and_age_are_the_defaults_of_the_adapter() -> None:
    assert VM.default_instance_state == ["running"]
    assert RUNNING[1] == "VM running"
    assert STOPPED[1] == "VM stopped"
