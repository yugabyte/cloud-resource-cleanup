"""Parity: `crc.azu.ip.IP` against main (`tests/legacy/azu_ip.txt`).

Both run against the same fake Azure network client (the SDK classes `crc.azu._base` builds
are replaced, so the real `Base` runs in both). SDK calls (in order, including the lazy
paging of `list_all`), results, log records, stdout and raised errors must be identical for
every case through the legacy adapter. The registered `Cleaner` path (factory with a
`RunContext`, `plan()` then `apply()` unless dry run) is compared on results, errors, stdout,
delete calls and log records. The module lists no regions, so there is no region fallback.
"""

import logging
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from crc.azu.ip import IP, AzureIpCleaner, build_ip_cleaner
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import get
from tests.helpers_azure_ip import NOW, FakeAzure, ip

ORACLE = Path(__file__).parent / "legacy" / "azu_ip.txt"
ENV = {"env": "test", "owner": "qa"}
PROD = {"env": "prod"}


@dataclass
class Case:
    id: str
    ips: Sequence[Any]
    filter_tags: Any = field(default_factory=dict)
    exception_tags: Any = field(default_factory=dict)
    notags: Any = field(default_factory=dict)
    dry_run: bool = False
    resource_group: str = "rg-test"
    list_error: Exception | None = None
    delete_errors: Mapping[str, Exception] = field(default_factory=dict)
    fails_in_plan: bool = False


def _many(prefix: str, count: int) -> list[Any]:
    return [ip(f"{prefix}{i}", ENV) for i in range(count)]


CASES = [
    Case("dry-run", [ip("a", ENV), ip("b", PROD)], dry_run=True),
    Case("delete", [ip("a", ENV), ip("b", PROD)]),
    Case("no-filter-includes-untagged", [ip("a", None), ip("b", {}), ip("c", ENV)]),
    Case("filter-none", [ip("a", ENV)], filter_tags=None),
    Case("filter-key-only", [ip("a", ENV), ip("b", {"x": "y"})], filter_tags={"env": []}),
    Case("filter-key-and-value", [ip("a", ENV), ip("b", PROD)], filter_tags={"env": ["prod"]}),
    Case(
        "filter-any-key-matches",
        [ip("a", ENV), ip("b", PROD)],
        filter_tags={"owner": ["qa"], "env": ["prod"]},
    ),
    Case("filter-case-sensitive", [ip("a", {"env": "Test"})], filter_tags={"env": ["test"]}),
    Case("filter-no-match", [ip("a", ENV)], filter_tags={"zzz": []}),
    Case("filter-value-no-match", [ip("a", ENV)], filter_tags={"env": ["x"]}),
    Case("filter-empty-value-list-is-key-present", [ip("a", {"env": ""})], filter_tags={"env": []}),
    Case("untagged-with-filter", [ip("a", None), ip("b", {})], filter_tags={"env": []}),
    Case("associated-skipped", [ip("a", ENV, config={"id": "nic"}), ip("b", ENV)]),
    Case("associated-and-dry-run", [ip("a", ENV, config={"id": "nic"})], dry_run=True),
    Case("exception-key-only", [ip("a", ENV), ip("b", PROD)], exception_tags={"owner": []}),
    Case(
        "exception-key-and-value",
        [ip("a", ENV), ip("b", PROD)],
        exception_tags={"env": ["prod"]},
    ),
    Case(
        "exception-wins-over-filter",
        [ip("a", ENV)],
        filter_tags={"env": []},
        exception_tags={"owner": ["qa"]},
    ),
    Case("exception-value-mismatch", [ip("a", ENV)], exception_tags={"env": ["x"]}),
    Case("exception-none", [ip("a", ENV)], exception_tags=None),
    Case("exception-empty-tags-dict", [ip("a", {})], exception_tags={"a": []}),
    Case("notags-all-keys-skips", [ip("a", ENV), ip("b", PROD)], notags={"env": [], "owner": []}),
    Case("notags-partial-keys-keeps", [ip("a", PROD)], notags={"env": [], "owner": []}),
    Case("notags-value-match", [ip("a", ENV), ip("b", PROD)], notags={"env": ["test"]}),
    Case("notags-empty-tags-dict", [ip("a", {})], notags={"a": []}),
    Case("notags-none", [ip("a", ENV)], notags=None),
    Case(
        "notags-and-exception-both",
        [ip("a", ENV)],
        exception_tags={"zzz": []},
        notags={"env": []},
    ),
    Case("untagged-with-exception-raises", [ip("a", None)], exception_tags={"env": []}),
    Case("untagged-with-notags-raises", [ip("a", None)], notags={"env": []}),
    Case(
        "untagged-raises-even-if-filter-excludes",
        [ip("a", None)],
        filter_tags={"env": []},
        exception_tags={"env": []},
    ),
    Case(
        "untagged-raises-after-earlier-deletes",
        [ip("a", ENV), ip("b", ENV), ip("c", None), ip("d", ENV)],
        exception_tags={"zzz": []},
        fails_in_plan=True,
    ),
    Case(
        "untagged-raises-in-dry-run",
        [ip("a", ENV), ip("c", None)],
        exception_tags={"zzz": []},
        dry_run=True,
        fails_in_plan=True,
    ),
    Case(
        "untagged-with-exception-attached-ip-still-raises",
        [ip("a", None, config={"x": 1})],
        exception_tags={"a": []},
    ),
    Case("empty-result", []),
    Case("empty-result-dry-run", [], dry_run=True),
    Case("duplicate-names-recorded-twice", [ip("a", ENV), ip("a", ENV)]),
    Case("many-order", _many("ip-", 25)),
    Case("many-dry-run", _many("ip-", 25), dry_run=True),
    Case("resource-group-from-env", [ip("a", ENV)], resource_group=""),
    Case("list-all-error", [ip("a", ENV)], list_error=RuntimeError("list failed")),
    Case("list-all-error-dry-run", [], dry_run=True, list_error=ValueError("boom")),
    Case(
        "iteration-error-mid-listing",
        [ip("a", ENV), RuntimeError("page failed"), ip("c", ENV)],
        fails_in_plan=True,
    ),
    Case(
        "delete-error-mid-run-keeps-earlier-record",
        _many("ip-", 4),
        delete_errors={"ip-2": RuntimeError("delete failed")},
    ),
    Case(
        "delete-error-not-called-in-dry-run",
        [ip("a", ENV)],
        dry_run=True,
        delete_errors={"a": RuntimeError("delete failed")},
    ),
    Case("missing-ip-configuration-attribute", [types.SimpleNamespace(name="a", tags=ENV)]),
    Case(
        "missing-ip-configuration-not-reached-when-filtered",
        [types.SimpleNamespace(name="a", tags=ENV)],
        filter_tags={"zzz": []},
    ),
    Case("missing-tags-attribute", [types.SimpleNamespace(name="a", ip_configuration=None)]),
    Case(
        "missing-name-attribute-deleted",
        [types.SimpleNamespace(tags=ENV, ip_configuration=None)],
    ),
    Case(
        "missing-name-attribute-skipped",
        [types.SimpleNamespace(tags=ENV, ip_configuration={"x": 1})],
    ),
    Case("name-none", [ip(None, ENV)]),
    Case("tag-value-none-key-only-filter", [ip("a", {"env": None})], filter_tags={"env": []}),
    Case(
        "tag-value-none-does-not-match-empty-string-filter",
        [ip("a", {"env": None})],
        filter_tags={"env": [""]},
    ),
    Case(
        "tag-value-none-does-not-match-empty-string-exception",
        [ip("a", {"env": None})],
        exception_tags={"env": [""]},
    ),
    Case(
        "tag-value-none-does-not-match-empty-string-notags",
        [ip("a", {"env": None})],
        notags={"env": [""]},
    ),
    Case(
        "string-spec-value-matches-substring",
        [ip("a", {"env": "test"})],
        filter_tags={"env": "testing"},
    ),
    Case(
        "tag-value-none-with-string-spec-raises",
        [ip("a", {"env": None})],
        filter_tags={"env": "testing"},
    ),
    Case(
        "exception-evaluated-when-filter-misses",
        [ip("a", ENV)],
        filter_tags={"zzz": []},
        exception_tags={"env": 123},
    ),
    Case(
        "notags-evaluated-when-exception-matches",
        [ip("a", ENV)],
        exception_tags={"env": ["test"]},
        notags={"owner": 123},
    ),
]


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[str] | None
    count: int | None
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_azu_ip")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    return module


def _legacy(case: Case) -> Any:
    return _load_oracle().IP(
        case.resource_group, case.dry_run, case.filter_tags, case.exception_tags, case.notags
    )


def _new(case: Case) -> Any:
    return IP(case.resource_group, case.dry_run, case.filter_tags, case.exception_tags, case.notags)


def _setup(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeAzure:
    azure = FakeAzure(case.ips, case.list_error, case.delete_errors)
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
    deleted = list(cleaner.get_deleted)
    count = cleaner.count
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(azure.calls, deleted, count, logs, capsys.readouterr().out, error)


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
    assert new.stdout == old.stdout


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
        },
        now=NOW,
    )
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    try:
        cleaner = get("azure", "ip")(ctx)
        plan = cleaner.plan(ctx)
        if case.dry_run:
            deleted = [c.id for c in plan.candidates]
        else:
            deleted = list(cleaner.apply(plan, ctx).succeeded)
    except Exception as e:
        error = (type(e).__name__, str(e))
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(azure.calls, deleted, len(deleted), logs, capsys.readouterr().out, error)


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
        assert _only(new.calls, "begin_delete") == []
        assert new.logs == []
        return
    assert _only(new.calls, "begin_delete") == _only(old.calls, "begin_delete")
    assert new.logs == old.logs[:-1]
    if new.error is None:
        assert new.deleted == old.deleted
        assert _only(new.calls, "client") == _only(old.calls, "client")


def _find(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)


TYPE_ERROR = ("TypeError", "argument of type 'NoneType' is not iterable")

QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("untagged-with-exception-raises", lambda o: o.error == TYPE_ERROR and o.deleted == []),
    ("untagged-with-notags-raises", lambda o: o.error == TYPE_ERROR and o.deleted == []),
    ("untagged-raises-even-if-filter-excludes", lambda o: o.error == TYPE_ERROR),
    (
        "untagged-raises-after-earlier-deletes",
        lambda o: (
            o.error == TYPE_ERROR
            and o.deleted == ["a", "b"]
            and [c[0] for c in o.calls if c[0] == "begin_delete"] == ["begin_delete"] * 2
        ),
    ),
    ("untagged-raises-in-dry-run", lambda o: o.error == TYPE_ERROR and o.deleted == ["a"]),
    ("exception-empty-tags-dict", lambda o: o.error is None and o.deleted == ["a"]),
    ("notags-empty-tags-dict", lambda o: o.error is None and o.deleted == ["a"]),
    ("no-filter-includes-untagged", lambda o: o.deleted == ["a", "b", "c"]),
    ("untagged-with-filter", lambda o: o.deleted == []),
    ("filter-any-key-matches", lambda o: o.deleted == ["a", "b"]),
    ("filter-case-sensitive", lambda o: o.deleted == []),
    ("filter-empty-value-list-is-key-present", lambda o: o.deleted == ["a"]),
    ("associated-skipped", lambda o: o.deleted == ["b"]),
    ("notags-all-keys-skips", lambda o: o.deleted == ["b"]),
    ("notags-partial-keys-keeps", lambda o: o.deleted == ["a"]),
    ("duplicate-names-recorded-twice", lambda o: o.deleted == ["a", "a"]),
    (
        "delete-error-mid-run-keeps-earlier-record",
        lambda o: o.error == ("RuntimeError", "delete failed") and o.deleted == ["ip-0", "ip-1"],
    ),
    ("list-all-error", lambda o: o.error == ("RuntimeError", "list failed") and o.deleted == []),
    (
        "iteration-error-mid-listing",
        lambda o: o.error == ("RuntimeError", "page failed") and o.deleted == ["a"],
    ),
    ("resource-group-from-env", lambda o: ("begin_delete", "env-rg", "a") in o.calls),
    ("delete-error-not-called-in-dry-run", lambda o: o.error is None and o.deleted == ["a"]),
    ("missing-ip-configuration-attribute", lambda o: o.error is not None),
    ("missing-ip-configuration-not-reached-when-filtered", lambda o: o.error is None),
    ("tag-value-none-does-not-match-empty-string-filter", lambda o: o.deleted == []),
    ("tag-value-none-does-not-match-empty-string-exception", lambda o: o.deleted == ["a"]),
    ("tag-value-none-does-not-match-empty-string-notags", lambda o: o.deleted == ["a"]),
    ("string-spec-value-matches-substring", lambda o: o.deleted == ["a"]),
    (
        "tag-value-none-with-string-spec-raises",
        lambda o: o.error is not None and o.error[0] == "TypeError",
    ),
    (
        "exception-evaluated-when-filter-misses",
        lambda o: o.error is not None and o.error[0] == "TypeError",
    ),
    (
        "notags-evaluated-when-exception-matches",
        lambda o: o.error is not None and o.error[0] == "TypeError",
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
        assert ("begin_delete", "rg-test", "a") in azure.calls


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
    assert results[1][1] == ["a", "b", "a", "b"]


def test_registered_factory_builds_cleaner(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAzure([], None, {}).install(monkeypatch)
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    cleaner: Cleaner = get("azure", "ip")(ctx)
    assert isinstance(cleaner, AzureIpCleaner)
    assert get("azure", "ip") is build_ip_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("azure", "ip")
