"""Parity: `crc.azu.nic.NIC` against main (`tests/legacy/azu_nic.txt`).

Both run against the same fake Azure network client (the SDK classes `crc.azu._base` builds
are replaced, so the real `Base` runs in both). SDK calls (in order, including the lazy
paging of `list_all`), results, log records, stdout and raised errors must be identical for
every case through the legacy adapter. The registered `Cleaner` path (factory with a
`RunContext`, `plan()` then `apply()` unless dry run) is compared on results, errors, stdout,
list/delete/result calls and log records. The module has no age gate and lists no regions, so
there are no age edges, region fallback or region auth errors to cover.
"""

import logging
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from crc.azu.nic import NIC, AzureNicCleaner, build_nic_cleaner
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import get
from tests.helpers_azure_nic import NOW, FakeAzure, nic

ORACLE = Path(__file__).parent / "legacy" / "azu_nic.txt"
VM = {"id": "vm-1"}


@dataclass
class Case:
    id: str
    nics: Sequence[Any]
    name_regex: Any = field(default_factory=lambda: ["nic"])
    exception_regex: Any = field(default_factory=list)
    dry_run: bool = False
    resource_group: str = "rg-test"
    list_error: Exception | None = None
    begin_script: Mapping[str, Sequence[Exception | None]] = field(default_factory=dict)
    result_script: Mapping[str, Sequence[Exception | None]] = field(default_factory=dict)
    fails_in_plan: bool = False


def _many(count: int) -> list[Any]:
    return [nic(f"nic-{i}") for i in range(count)]


def _boom(text: str = "boom") -> Exception:
    return RuntimeError(text)


CASES = [
    Case("dry-run", [nic("nic-a"), nic("other")], dry_run=True),
    Case("delete", [nic("nic-a"), nic("other"), nic("nic-b")]),
    Case("filter-substring-anywhere", [nic("my-nic-1"), nic("xnicx"), nic("nope")]),
    Case("filter-several-patterns-any", [nic("a-1"), nic("b-2"), nic("c-3")], ["a-", "c-"]),
    Case("filter-case-sensitive", [nic("NIC-A"), nic("nic-b")]),
    Case("filter-regex-syntax-is-literal", [nic("nic-1"), nic("nic.*")], ["nic.*"]),
    Case("filter-anchor-is-literal", [nic("nic-1"), nic("^nic")], ["^nic"]),
    Case("filter-empty-pattern-matches-all", [nic("a"), nic("b")], [""]),
    Case("filter-no-match", [nic("a"), nic("b")], ["zzz"]),
    Case("filter-string-iterates-characters", [nic("abc"), nic("xyz"), nic("q")], "az"),
    Case("name-regex-empty-returns-early", [nic("nic-a")], []),
    Case("name-regex-empty-dry-run", [nic("nic-a")], [], dry_run=True),
    Case("name-regex-none-returns-early", [nic("nic-a")], None),
    Case("name-regex-empty-string-returns-early", [nic("nic-a")], ""),
    Case("name-regex-empty-and-list-error", [], [], list_error=_boom("not listed")),
    Case("exception-substring", [nic("nic-a"), nic("nic-keep")], exception_regex=["keep"]),
    Case(
        "exception-several-any",
        [nic("nic-a"), nic("nic-b"), nic("nic-c")],
        exception_regex=["-a", "-c"],
    ),
    Case("exception-wins-over-filter", [nic("nic-a")], ["nic"], ["nic-a"]),
    Case("exception-none", [nic("nic-a")], exception_regex=None),
    Case("exception-empty-string", [nic("nic-a")], exception_regex=""),
    Case("exception-empty-pattern-excludes-all", [nic("nic-a")], exception_regex=[""]),
    Case("exception-case-sensitive", [nic("nic-KEEP")], exception_regex=["keep"]),
    Case("exception-regex-syntax-is-literal", [nic("nic-a")], exception_regex=["nic.*"]),
    Case("exception-string-iterates-characters", [nic("nic-a"), nic("nic-b")], exception_regex="b"),
    Case("attached-skipped", [nic("nic-a", VM), nic("nic-b")]),
    Case("attached-and-dry-run", [nic("nic-a", VM), nic("nic-b")], dry_run=True),
    Case("attached-truthy-string", [nic("nic-a", "vm")]),
    Case("virtual-machine-falsy-values", [nic("nic-a", ""), nic("nic-b", {}), nic("nic-c", 0)]),
    Case("attached-skipped-before-name-is-read", [nic(None, VM)]),
    Case("attached-with-exception-bad-not-evaluated", [nic("nic-a", VM)], exception_regex=[1]),
    Case("empty-result", []),
    Case("empty-result-dry-run", [], dry_run=True),
    Case("all-attached", [nic("nic-a", VM), nic("nic-b", VM)]),
    Case("duplicate-names-recorded-twice", [nic("nic-a"), nic("nic-a")]),
    Case("many-order", _many(25)),
    Case("many-dry-run", _many(25), dry_run=True),
    Case("resource-group-from-env", [nic("nic-a")], resource_group=""),
    Case("list-all-error", [nic("nic-a")], list_error=_boom("list failed"), fails_in_plan=True),
    Case(
        "list-all-error-dry-run", [], dry_run=True, list_error=ValueError("x"), fails_in_plan=True
    ),
    Case(
        "iteration-error-mid-listing",
        [nic("nic-a"), RuntimeError("page failed"), nic("nic-c")],
        fails_in_plan=True,
    ),
    Case(
        "iteration-error-mid-listing-dry-run",
        [nic("nic-a"), RuntimeError("page failed")],
        dry_run=True,
        fails_in_plan=True,
    ),
    Case("name-none", [nic(None)], fails_in_plan=True),
    Case("name-int", [nic(5)], fails_in_plan=True),
    Case("name-none-after-earlier-delete", [nic("nic-a"), nic(None)], fails_in_plan=True),
    Case(
        "missing-virtual-machine-attribute",
        [types.SimpleNamespace(name="nic-a")],
        fails_in_plan=True,
    ),
    Case(
        "missing-name-attribute", [types.SimpleNamespace(virtual_machine=None)], fails_in_plan=True
    ),
    Case(
        "exception-non-string-evaluated-when-not-included",
        [nic("a")],
        ["zzz"],
        [1],
        fails_in_plan=True,
    ),
    Case(
        "exception-non-string-evaluated-when-included",
        [nic("nic-a")],
        ["nic"],
        [1],
        fails_in_plan=True,
    ),
    Case("name-regex-non-string-entry", [nic("nic-a")], [1], fails_in_plan=True),
    Case("delete-fails-once-then-succeeds", [nic("nic-a")], begin_script={"nic-a": [_boom()]}),
    Case(
        "delete-fails-twice-then-succeeds",
        [nic("nic-a")],
        begin_script={"nic-a": [_boom("one"), _boom("two")]},
    ),
    Case(
        "delete-fails-three-times-is-swallowed",
        [nic("nic-a"), nic("nic-b")],
        begin_script={"nic-a": [_boom("one"), _boom("two"), _boom("three")]},
    ),
    Case(
        "delete-fails-four-times-needs-only-three",
        [nic("nic-a")],
        begin_script={"nic-a": [_boom(), _boom(), _boom(), _boom()]},
    ),
    Case(
        "result-fails-once-begin-delete-runs-again",
        [nic("nic-a")],
        result_script={"nic-a": [_boom("poll")]},
    ),
    Case(
        "result-fails-three-times",
        [nic("nic-a"), nic("nic-b")],
        result_script={"nic-a": [_boom("p1"), _boom("p2"), _boom("p3")]},
    ),
    Case(
        "mixed-begin-and-result-failures",
        [nic("nic-a")],
        begin_script={"nic-a": [_boom("begin"), None]},
        result_script={"nic-a": [None, _boom("poll")]},
    ),
    Case(
        "failure-does-not-stop-other-nics",
        _many(4),
        begin_script={"nic-1": [_boom(), _boom(), _boom()]},
    ),
    Case(
        "failure-with-empty-message",
        [nic("nic-a")],
        begin_script={"nic-a": [RuntimeError(), RuntimeError(), RuntimeError()]},
    ),
    Case(
        "base-exception-subclass-is-caught",
        [nic("nic-a")],
        begin_script={"nic-a": [KeyError("k"), None]},
    ),
    Case(
        "delete-errors-ignored-in-dry-run",
        [nic("nic-a")],
        dry_run=True,
        begin_script={"nic-a": [_boom()]},
        result_script={"nic-a": [_boom()]},
    ),
]


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[str] | None
    count: int | None
    logs: list[tuple[str, int, str]]
    count_logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_azu_nic")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    return module


def _legacy(case: Case) -> Any:
    return _load_oracle().NIC(
        case.resource_group, case.dry_run, case.name_regex, case.exception_regex
    )


def _new(case: Case) -> Any:
    return NIC(case.resource_group, case.dry_run, case.name_regex, case.exception_regex)


def _setup(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeAzure:
    azure = FakeAzure(case.nics, case.list_error, case.begin_script, case.result_script)
    azure.install(monkeypatch)
    caplog.clear()
    capsys.readouterr()
    return azure


def _records(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [(r.name, r.levelno, r.getMessage()) for r in caplog.records]


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
    deleted = list(cleaner.get_deleted)
    logs = _records(caplog)
    stdout = capsys.readouterr().out
    caplog.clear()
    count = cleaner.delete_count
    return Outcome(azure.calls, deleted, count, logs, _records(caplog), stdout, error)


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
    assert new.count == old.count
    assert new.logs == old.logs
    assert new.count_logs == old.count_logs
    assert new.stdout == old.stdout
    assert (new.error is not None) == case.fails_in_plan


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
            "name_regex": case.name_regex,
            "exception_regex": case.exception_regex,
        },
        now=NOW,
    )
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    try:
        cleaner = get("azure", "nic")(ctx)
        plan = cleaner.plan(ctx)
        if case.dry_run:
            deleted = [c.id for c in plan.candidates]
        else:
            deleted = list(cleaner.apply(plan, ctx).succeeded)
    except Exception as e:
        error = (type(e).__name__, str(e))
    return Outcome(
        azure.calls, deleted, len(deleted), _records(caplog), [], capsys.readouterr().out, error
    )


def _only(calls: Sequence[tuple[Any, ...]], *names: str) -> list[tuple[Any, ...]]:
    return [c for c in calls if c[0] in names]


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
        assert _only(new.calls, "begin_delete", "result") == []
        assert new.logs == []
        return
    assert _only(new.calls, "begin_delete", "result") == _only(old.calls, "begin_delete", "result")
    assert new.logs == old.logs
    assert new.deleted == old.deleted
    assert new.count == old.count
    assert _only(new.calls, "client") == _only(old.calls, "client")


def _find(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)


def _swallowed(o: Outcome, name: str, attempts: int) -> bool:
    deletes = [c for c in o.calls if c[0] == "begin_delete" and c[2] == name]
    return (
        o.error is None
        and len(deletes) == attempts
        and name not in (o.deleted or [])
        and any(r[2] == f"Failed to delete the NIC - {name}" for r in o.logs)
    )


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("filter-regex-syntax-is-literal", lambda o: o.deleted == ["nic.*"]),
    ("filter-anchor-is-literal", lambda o: o.deleted == ["^nic"]),
    ("filter-case-sensitive", lambda o: o.deleted == ["nic-b"]),
    ("filter-empty-pattern-matches-all", lambda o: o.deleted == ["a", "b"]),
    ("filter-string-iterates-characters", lambda o: o.deleted == ["abc", "xyz"]),
    (
        "name-regex-empty-returns-early",
        lambda o: o.calls == [] and [r[2] for r in o.logs] == ["No Azure NICs to delete."],
    ),
    ("name-regex-none-returns-early", lambda o: o.calls == [] and o.deleted == []),
    ("name-regex-empty-and-list-error", lambda o: o.error is None and o.calls == []),
    ("exception-empty-pattern-excludes-all", lambda o: o.deleted == []),
    ("exception-none", lambda o: o.deleted == ["nic-a"]),
    ("exception-regex-syntax-is-literal", lambda o: o.deleted == ["nic-a"]),
    ("exception-string-iterates-characters", lambda o: o.deleted == ["nic-a"]),
    ("attached-skipped", lambda o: o.deleted == ["nic-b"]),
    ("attached-skipped-before-name-is-read", lambda o: o.error is None and o.deleted == []),
    ("attached-with-exception-bad-not-evaluated", lambda o: o.error is None),
    ("virtual-machine-falsy-values", lambda o: o.deleted == ["nic-a", "nic-b", "nic-c"]),
    ("duplicate-names-recorded-twice", lambda o: o.deleted == ["nic-a", "nic-a"]),
    ("list-all-error", lambda o: o.error == ("RuntimeError", "list failed") and o.deleted == []),
    (
        "iteration-error-mid-listing",
        lambda o: o.error == ("RuntimeError", "page failed") and o.deleted == ["nic-a"],
    ),
    (
        "name-none-after-earlier-delete",
        lambda o: o.error is not None and o.error[0] == "TypeError" and o.deleted == ["nic-a"],
    ),
    (
        "exception-non-string-evaluated-when-not-included",
        lambda o: o.error is not None and o.error[0] == "TypeError",
    ),
    ("resource-group-from-env", lambda o: ("begin_delete", "env-rg", "nic-a") in o.calls),
    ("delete-fails-once-then-succeeds", lambda o: o.deleted == ["nic-a"] and o.error is None),
    ("delete-fails-three-times-is-swallowed", lambda o: _swallowed(o, "nic-a", 3)),
    ("delete-fails-four-times-needs-only-three", lambda o: _swallowed(o, "nic-a", 3)),
    (
        "result-fails-once-begin-delete-runs-again",
        lambda o: _only(o.calls, "begin_delete") == [("begin_delete", "rg-test", "nic-a")] * 2,
    ),
    (
        "failure-does-not-stop-other-nics",
        lambda o: o.deleted == ["nic-0", "nic-2", "nic-3"] and o.error is None,
    ),
    (
        "delete-errors-ignored-in-dry-run",
        lambda o: o.deleted == ["nic-a"] and _only(o.calls, "begin_delete", "result") == [],
    ),
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


def test_list_is_requested_twice_and_the_first_is_dropped(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("delete")
    for build in (_legacy, _new):
        azure = _setup(case, monkeypatch, caplog, capsys)
        build(case).delete()
        assert azure.calls[:3] == [("client", "sub-1"), ("list_all",), ("list_all",)]


def test_list_is_subscription_wide_and_delete_uses_configured_group(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("delete")
    for build in (_legacy, _new):
        azure = _setup(case, monkeypatch, caplog, capsys)
        build(case).delete()
        assert ("list_all",) in azure.calls
        assert ("begin_delete", "rg-test", "nic-a") in azure.calls
        assert ("result", "nic-a") in azure.calls


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
        cleaner = build(case)
        cleaner.delete()
        cleaner.delete()
        results.append(
            (
                azure.calls,
                cleaner.get_deleted,
                [(r.levelno, r.getMessage()) for r in caplog.records],
            )
        )
    assert results[0] == results[1]
    assert results[1][1] == ["nic-a", "nic-b", "nic-a", "nic-b"]


def test_registered_factory_builds_cleaner(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAzure([]).install(monkeypatch)
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    cleaner: Cleaner = get("azure", "nic")(ctx)
    assert isinstance(cleaner, AzureNicCleaner)
    assert get("azure", "nic") is build_nic_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("azure", "nic")
