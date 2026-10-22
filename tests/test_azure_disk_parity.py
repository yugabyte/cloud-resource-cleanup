"""Parity: `crc.azu.disk.Disk` against main (`tests/legacy/azu_disk.txt`).

Both run against the same fake Azure compute client (the SDK classes `crc.azu._base` builds are
replaced, so the real `Base` runs in both) and the same frozen clock. SDK calls (in order,
including the lazy paging of `disks.list`), results, log records, stdout and raised errors must be
identical for every case through the legacy adapter. The registered `Cleaner` path (factory with
a `RunContext`, `plan()` then `apply()` unless dry run) is compared on results, errors, stdout,
list/delete calls and log records (decision logs, "Deleted disk" logs and summary lines are
compared as three ordered groups, because `plan()` decides every disk before `apply()` deletes).
The module lists no regions, so there is no region fallback or region auth error to cover.
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

from crc.azu.disk import AzureDiskCleaner, Disk, build_disk_cleaner
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import get
from tests.helpers_azure_disk import NOW, FakeAzure, ago, disk
from tests.helpers_log_fix import expected_logs
from tests.log_fix_azure import RULES

_ADDRESS = re.compile(r"0x[0-9a-f]+")
ORACLE = Path(__file__).parent / "legacy" / "azu_disk.txt"

BUGS = {"BUG-358"}  # log-only divergences of the LOG-AZU rung


class _FrozenDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz: Any = None) -> Any:
        return NOW


FROZEN = types.SimpleNamespace(datetime=_FrozenDatetime)
TAGS = {"owner": "qa", "env": "test"}


@dataclass
class Case:
    id: str
    disks: Sequence[Any]
    filter_tags: Any = field(default_factory=dict)
    exception_tags: Any = field(default_factory=dict)
    notags: Any = field(default_factory=dict)
    age: Any = field(default_factory=dict)
    custom_key: Any = ""
    dry_run: bool = False
    resource_group: str = "rg-test"
    list_error: Exception | None = None
    begin_script: Mapping[str, Sequence[Exception | None]] = field(default_factory=dict)
    max_age: str | None = None
    error_phase: str | None = None


def _boom(text: str = "boom") -> Exception:
    return RuntimeError(text)


def _many(count: int) -> list[Any]:
    return [disk(f"disk-{i}") for i in range(count)]


def _tagged(name: str, **tags: str) -> Any:
    return disk(name, tags)


CASES = [
    Case("dry-run", [disk("a"), disk("b")], dry_run=True),
    Case("delete", [disk("a"), disk("b", state="Attached"), disk("c")]),
    Case("empty-result", []),
    Case("empty-result-dry-run", [], dry_run=True),
    Case("many-order", _many(25)),
    Case("many-dry-run", _many(25), dry_run=True),
    Case("duplicate-names-recorded-twice", [disk("a"), disk("a")]),
    Case("resource-group-from-env", [disk("a")], resource_group=""),
    Case("filter-key-present", [_tagged("a", owner="x"), _tagged("b", other="x")], {"owner": []}),
    Case(
        "filter-value-match", [_tagged("a", owner="x"), _tagged("b", owner="y")], {"owner": ["x"]}
    ),
    Case(
        "filter-any-key",
        [_tagged("a", env="t"), _tagged("b", owner="q")],
        {"owner": [], "env": ["t"]},
    ),
    Case("filter-value-case-sensitive", [_tagged("a", owner="QA")], {"owner": ["qa"]}),
    Case(
        "filter-value-string-is-substring",
        [_tagged("a", owner="ab"), _tagged("b", owner="z")],
        {"owner": "xaby"},
    ),
    Case("filter-no-match", [_tagged("a", owner="x")], {"zzz": []}),
    Case("filter-untagged-not-matched", [disk("a"), _tagged("b", owner="x")], {"owner": []}),
    Case("filter-empty-tags-not-matched", [disk("a", {})], {"owner": []}),
    Case("untagged-no-filter-included", [disk("a"), disk("b", {})]),
    Case("untagged-with-exception-no-error", [disk("a")], exception_tags={"x": []}),
    Case("untagged-with-notags-no-error", [disk("a")], notags={"x": []}),
    Case("tags-none-logs-none", [disk("a", None)], dry_run=True),
    Case("filter-none", [disk("a", TAGS)], filter_tags=None),
    Case(
        "exception-any-key",
        [_tagged("a", owner="x"), _tagged("b", env="t")],
        exception_tags={"owner": [], "zz": []},
    ),
    Case(
        "exception-value",
        [_tagged("a", owner="x"), _tagged("b", owner="y")],
        exception_tags={"owner": ["x"]},
    ),
    Case("exception-none", [disk("a", TAGS)], exception_tags=None),
    Case("exception-wins-over-filter", [disk("a", TAGS)], {"owner": []}, {"env": []}),
    Case("notags-all-keys-skips", [disk("a", TAGS)], notags={"owner": [], "env": []}),
    Case("notags-partial-keeps", [disk("a", TAGS)], notags={"owner": [], "zz": []}),
    Case(
        "notags-value", [_tagged("a", owner="x"), _tagged("b", owner="y")], notags={"owner": ["x"]}
    ),
    Case("notags-none", [disk("a", TAGS)], notags=None),
    Case(
        "exception-and-notags-both-evaluated",
        [disk("a", TAGS)],
        exception_tags={"owner": []},
        notags={"env": ["x", 1]},
    ),
    Case(
        "exception-non-list-value-error",
        [disk("a", TAGS)],
        exception_tags={"owner": 5},
        error_phase="plan",
    ),
    Case("age-days-old", [disk("a", created=ago(days=3))], age={"days": 2}),
    Case("age-days-exact", [disk("a", created=ago(days=2))], age={"days": 2}),
    Case("age-days-just-under", [disk("a", created=ago(days=1, hours=23))], age={"days": 2}),
    Case("age-hours-exact", [disk("a", created=ago(hours=5))], age={"hours": 5}),
    Case("age-hours-just-under", [disk("a", created=ago(hours=4, minutes=59))], age={"hours": 5}),
    Case(
        "age-days-and-hours",
        [disk("a", created=ago(days=1, hours=2)), disk("b", created=ago(days=1, hours=1))],
        age={"days": 1, "hours": 2},
    ),
    Case("age-int", [disk("a", created=ago(days=3)), disk("b", created=ago(days=1))], age=2),
    Case("age-empty-matches-all", [disk("a", created=ago(minutes=1))]),
    Case("age-none-matches-all", [disk("a", created=ago(minutes=1))], age=None),
    Case("age-zero-matches-all", [disk("a", created=ago(minutes=1))], age=0),
    Case("age-unknown-key-never-old", [disk("a", created=ago(days=99))], age={"weeks": 1}),
    Case(
        "age-max-age-shortens",
        [disk("a", created=ago(days=2))],
        age={"days": 5},
        max_age="{'days': 1}",
    ),
    Case(
        "age-max-age-longer-ignored",
        [disk("a", created=ago(days=2))],
        age={"days": 1},
        max_age="{'days': 9}",
    ),
    Case("age-future-creation", [disk("a", created=ago(days=-2))], age={"days": 1}),
    Case("created-none-error", [disk("a"), disk("b", created=None)], error_phase="plan"),
    Case(
        "created-naive-error",
        [disk("a", created=datetime.datetime(2026, 1, 1))],
        age={"days": 1},
        error_phase="plan",
    ),
    Case(
        "custom-age-tag-longer",
        [disk("a", {"ttl": "{'days': 20}"})],
        age={"days": 1},
        custom_key="ttl",
    ),
    Case(
        "custom-age-tag-shorter",
        [disk("a", {"ttl": "{'days': 1}"})],
        age={"days": 30},
        custom_key="ttl",
    ),
    Case(
        "custom-age-tag-hours",
        [disk("a", {"ttl": "{'hours': 3}"}, ago(hours=4))],
        age={"days": 30},
        custom_key="ttl",
    ),
    Case(
        "custom-age-tag-unparseable-falls-back",
        [disk("a", {"ttl": "soon"})],
        age={"days": 1},
        custom_key="ttl",
    ),
    Case(
        "custom-age-tag-unparseable-falls-back-keeps",
        [disk("a", {"ttl": "soon"})],
        age={"days": 30},
        custom_key="ttl",
    ),
    Case(
        "custom-age-tag-empty-falls-back",
        [disk("a", {"ttl": ""})],
        age={"days": 1},
        custom_key="ttl",
    ),
    Case(
        "custom-age-tag-empty-dict-falls-back",
        [disk("a", {"ttl": "{}"})],
        age={"days": 1},
        custom_key="ttl",
    ),
    Case("custom-age-tag-absent", [disk("a", TAGS)], age={"days": 30}, custom_key="ttl"),
    Case("custom-age-tag-none-key", [disk("a", TAGS)], age={"days": 1}, custom_key=None),
    Case("custom-age-tag-no-tags", [disk("a", None)], age={"days": 1}, custom_key="ttl"),
    Case(
        "custom-age-tag-int-literal",
        [disk("a", {"ttl": "7"})],
        age={"days": 1},
        custom_key="ttl",
    ),
    Case("state-unattached", [disk("a", state="Unattached")]),
    Case("state-attached-capital", [disk("a", state="Attached")]),
    Case("state-attached-lower-passes", [disk("a", state="attached")]),
    Case("state-empty-passes", [disk("a", state="")]),
    Case("state-substring-passes", [disk("a", state="Unatt"), disk("b", state="tached")]),
    Case("state-reserved", [disk("a", state="Reserved"), disk("b", state="ActiveSAS")]),
    Case("state-none-error", [disk("a", state=None)], error_phase="plan"),
    Case(
        "state-checked-after-age",
        [disk("a", created=ago(minutes=1), state="Attached")],
        age={"days": 1},
    ),
    Case("list-error", [disk("a")], list_error=_boom("list failed"), error_phase="plan"),
    Case("list-error-dry-run", [], dry_run=True, list_error=ValueError("x"), error_phase="plan"),
    Case(
        "iteration-error-mid-listing",
        [disk("a"), RuntimeError("page failed"), disk("c")],
        error_phase="plan",
    ),
    Case(
        "iteration-error-mid-listing-dry-run",
        [disk("a"), RuntimeError("page failed")],
        dry_run=True,
        error_phase="plan",
    ),
    Case("name-none-dry-run-ok", [disk(None)], dry_run=True),
    Case("name-none-delete-error", [disk(None)], error_phase="apply"),
    Case("missing-attribute-error", [types.SimpleNamespace(name="a")], error_phase="plan"),
    Case(
        "begin-delete-error-propagates",
        [disk("a"), disk("b"), disk("c")],
        begin_script={"b": [_boom("delete failed")]},
        error_phase="apply",
    ),
    Case(
        "begin-delete-error-first",
        [disk("a"), disk("b")],
        begin_script={"a": [_boom()]},
        error_phase="apply",
    ),
    Case(
        "begin-delete-error-ignored-in-dry-run",
        [disk("a")],
        dry_run=True,
        begin_script={"a": [_boom()]},
    ),
    Case(
        "full-combination",
        [
            _tagged("a", owner="x"),
            disk("b", TAGS, ago(days=5)),
            disk("c", {"owner": "x", "ttl": "{'days': 9}"}, ago(days=5)),
        ],
        {"owner": []},
        {"env": []},
        {},
        {"days": 2},
        "ttl",
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
    module = types.ModuleType("legacy_azu_disk")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    module.__dict__["datetime"] = FROZEN
    return module


def _legacy(case: Case) -> Any:
    return _load_oracle().Disk(*_args(case))


def _new(case: Case) -> Any:
    adapter = Disk(*_args(case))
    adapter.clock = lambda: NOW
    return adapter


def _args(case: Case) -> tuple[Any, ...]:
    return (
        case.resource_group,
        case.dry_run,
        case.filter_tags,
        case.exception_tags,
        case.age,
        case.custom_key,
        case.notags,
    )


def _setup(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeAzure:
    azure = FakeAzure(case.disks, case.list_error, case.begin_script)
    azure.install(monkeypatch)
    if case.max_age is not None:
        monkeypatch.setenv("MAX_AGE", case.max_age)
    caplog.clear()
    capsys.readouterr()
    return azure


def _records(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [(r.name, r.levelno, _ADDRESS.sub("0x?", r.getMessage())) for r in caplog.records]


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
    count = cleaner.count
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
    assert new.logs == expected_logs(old.logs, BUGS, RULES)
    assert new.count_logs == old.count_logs
    assert new.stdout == old.stdout
    assert (new.error is not None) == (case.error_phase is not None)


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
            "custom_age_tag_key": case.custom_key,
        },
        now=NOW,
    )
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    try:
        cleaner = get("azure", "disk")(ctx)
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


def _groups(
    logs: Sequence[tuple[str, int, str]],
) -> tuple[list[tuple[str, int, str]], ...]:
    deleted = [r for r in logs if r[2].startswith("Deleted disk: ")]
    summary = [r for r in logs if "Azure Disks" in r[2]]
    rest = [r for r in logs if r not in deleted and r not in summary]
    return rest, deleted, summary


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
    assert _only(new.calls, "client")[:1] == _only(old.calls, "client")[:1]
    assert _only(new.calls, "list") == _only(old.calls, "list")
    if case.error_phase == "plan":
        assert _only(new.calls, "begin_delete") == []
        assert _groups(new.logs)[1:] == ([], [])
        return
    assert _only(new.calls, "begin_delete") == _only(old.calls, "begin_delete")
    if case.error_phase == "apply":
        assert _groups(new.logs)[1:] == _groups(expected_logs(old.logs, BUGS, RULES))[1:]
        return
    assert new.deleted == old.deleted
    assert new.count == old.count
    assert _groups(new.logs) == _groups(expected_logs(old.logs, BUGS, RULES))


def _find(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("state-attached-lower-passes", lambda o: o.deleted == ["a"]),
    ("state-empty-passes", lambda o: o.deleted == ["a"]),
    ("state-substring-passes", lambda o: o.deleted == ["a", "b"]),
    ("state-attached-capital", lambda o: o.deleted == []),
    ("state-none-error", lambda o: o.error is not None and o.error[0] == "TypeError"),
    ("state-checked-after-age", lambda o: o.deleted == [] and any("older" in r[2] for r in o.logs)),
    ("filter-any-key", lambda o: o.deleted == ["a", "b"]),
    ("filter-value-string-is-substring", lambda o: o.deleted == ["a"]),
    ("filter-untagged-not-matched", lambda o: o.deleted == ["b"]),
    ("untagged-no-filter-included", lambda o: o.deleted == ["a", "b"]),
    ("untagged-with-exception-no-error", lambda o: o.deleted == ["a"] and o.error is None),
    ("untagged-with-notags-no-error", lambda o: o.deleted == ["a"] and o.error is None),
    ("tags-none-logs-none", lambda o: any(r[2] == "None" for r in o.logs)),
    ("exception-wins-over-filter", lambda o: o.deleted == []),
    ("notags-all-keys-skips", lambda o: o.deleted == []),
    ("notags-partial-keeps", lambda o: o.deleted == ["a"]),
    ("age-empty-matches-all", lambda o: o.deleted == ["a"]),
    ("age-zero-matches-all", lambda o: o.deleted == ["a"]),
    ("age-unknown-key-never-old", lambda o: o.deleted == []),
    ("age-days-exact", lambda o: o.deleted == ["a"]),
    ("age-max-age-shortens", lambda o: o.deleted == ["a"]),
    ("custom-age-tag-unparseable-falls-back", lambda o: o.deleted == ["a"]),
    ("custom-age-tag-unparseable-falls-back-keeps", lambda o: o.deleted == []),
    ("custom-age-tag-empty-falls-back", lambda o: o.deleted == ["a"]),
    ("custom-age-tag-longer", lambda o: o.deleted == []),
    ("custom-age-tag-shorter", lambda o: o.deleted == ["a"]),
    ("created-none-error", lambda o: o.error is not None and o.deleted == ["a"]),
    ("duplicate-names-recorded-twice", lambda o: o.deleted == ["a", "a"]),
    ("list-error", lambda o: o.error == ("RuntimeError", "list failed") and o.deleted == []),
    (
        "iteration-error-mid-listing",
        lambda o: o.error == ("RuntimeError", "page failed") and o.deleted == ["a"],
    ),
    (
        "begin-delete-error-propagates",
        lambda o: o.error == ("RuntimeError", "delete failed") and o.deleted == ["a"],
    ),
    ("name-none-delete-error", lambda o: o.error is not None and o.error[0] == "TypeError"),
    ("resource-group-from-env", lambda o: ("begin_delete", "env-rg", "a") in o.calls),
    (
        "begin-delete-error-ignored-in-dry-run",
        lambda o: o.deleted == ["a"] and _only(o.calls, "begin_delete") == [],
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


def test_delete_is_interleaved_with_paging_like_main(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("delete")
    expected = [
        ("client", "sub-1"),
        ("list",),
        ("yield", "a"),
        ("begin_delete", "rg-test", "a"),
        ("yield", "b"),
        ("yield", "c"),
        ("begin_delete", "rg-test", "c"),
    ]
    for build in (_legacy, _new):
        azure = _setup(case, monkeypatch, caplog, capsys)
        build(case).delete()
        assert azure.calls == expected


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
    old, new = results
    assert new[:2] == old[:2]
    assert new[2] == expected_logs(old[2], BUGS, RULES)
    assert new[2] != old[2]
    assert new[1] == ["a", "c", "a", "c"]


def test_registered_factory_builds_cleaner(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAzure([]).install(monkeypatch)
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    cleaner: Cleaner = get("azure", "disk")(ctx)
    assert isinstance(cleaner, AzureDiskCleaner)
    assert get("azure", "disk") is build_disk_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("azure", "disk")
