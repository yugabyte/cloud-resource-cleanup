"""Parity: the Azure disk Cleaner, run through `crc.core.engine.run`, against main.

The oracle is `tests/legacy/azu_disk.txt`. Both run against the same fake Azure compute client
(the SDK classes `crc.azu._base` builds are replaced, so the real `Base` runs in both) and the
same frozen clock. SDK calls (in order,
including the lazy paging of `disks.list`), results, log records, stdout and raised errors must be
identical for every case, except that `plan()` decides every disk before `apply()` deletes (so a
listing or selection error stops the run before any delete). The engine path and the registered
`Cleaner` protocol (factory, `plan()`, `apply()`) are compared on results, errors, stdout,
list/delete calls and log records (decision logs, "Deleted disk" logs and summary lines are
compared as three ordered groups).
The module lists no regions, so there is no region fallback or region auth error to cover.

Two bugs are fixed on purpose, so the oracle differs from the new code:
BUG-172 (the listing is `disks.list_by_resource_group(<configured group>)` instead of the
subscription-wide `disks.list()`; the fake yields the same disks for both, so every case still
compares equal once the oracle's `("list",)` call is mapped to the new call) and BUG-174 (each
delete is isolated and its poller is awaited). Cases that trigger BUG-174 carry
`diverges="BUG-174"`: the test asserts the oracle shows the old outcome and the new code the
exact new outcome in `DIVERGING`; every other case still equals the oracle.
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

from crc.azu.disk import AzureDiskCleaner, build_disk_cleaner
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import get
from tests.helpers_azure_disk import NOW, FakeAzure, ago, disk
from tests.helpers_log_fix import expected_logs
from tests.helpers_wc_azu_engine import candidate_ids, engine_run
from tests.log_fix_azure import RULES

_ADDRESS = re.compile(r"0x[0-9a-f]+")
ORACLE = Path(__file__).parent / "legacy" / "azu_disk.txt"

BUGS = {"BUG-358"}  # log-only divergences from the oracle
ENV_RESOURCE_GROUP = "env-rg"


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
    age: Any = field(default_factory=lambda: {"days": 1})
    custom_key: Any = ""
    dry_run: bool = False
    resource_group: str = "rg-test"
    list_error: Exception | None = None
    begin_script: Mapping[str, Sequence[Exception | None]] = field(default_factory=dict)
    max_age: str | None = None
    error_phase: str | None = None
    result_script: Mapping[str, Sequence[Exception | None]] = field(default_factory=dict)
    diverges: str | None = None


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
    Case("age-empty-matches-all", [disk("a", created=ago(minutes=1))], age={}, diverges="BUG-173"),
    Case("age-none-matches-all", [disk("a", created=ago(minutes=1))], age=None, diverges="BUG-173"),
    Case("age-zero-matches-all", [disk("a", created=ago(minutes=1))], age=0, diverges="BUG-173"),
    Case(
        "age-unknown-key-never-old",
        [disk("a", created=ago(days=99))],
        age={"weeks": 1},
        diverges="BUG-173",
    ),
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
        [disk("a", created=datetime.datetime(2026, 1, 1))],  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical
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
    Case("name-none-delete-error", [disk(None)], error_phase="apply", diverges="BUG-174"),
    Case(
        "poller-error-not-deleted",
        [disk("a"), disk("b"), disk("c")],
        result_script={"b": [_boom("poller failed")]},
        diverges="BUG-174",
    ),
    Case(
        "begin-delete-error-every-disk",
        [disk("a"), disk("b")],
        begin_script={"a": [_boom("a failed")], "b": [_boom("b failed")]},
        diverges="BUG-174",
    ),
    Case("missing-attribute-error", [types.SimpleNamespace(name="a")], error_phase="plan"),
    Case(
        "begin-delete-error-propagates",
        [disk("a"), disk("b"), disk("c")],
        begin_script={"b": [_boom("delete failed")]},
        error_phase="apply",
        diverges="BUG-174",
    ),
    Case(
        "begin-delete-error-first",
        [disk("a"), disk("b")],
        begin_script={"a": [_boom()]},
        error_phase="apply",
        diverges="BUG-174",
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
    waited: list[str] = field(default_factory=list)


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_azu_disk")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    module.__dict__["datetime"] = FROZEN
    return module


def _legacy(case: Case) -> Any:
    return _load_oracle().Disk(*_args(case))


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
    azure = FakeAzure(case.disks, case.list_error, case.begin_script, case.result_script)
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
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    deleted = list(cleaner.get_deleted)
    logs = _records(caplog)
    stdout = capsys.readouterr().out
    caplog.clear()
    count = cleaner.count
    return Outcome(azure.calls, deleted, count, logs, _records(caplog), stdout, error, azure.waited)


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


def _options(case: Case) -> dict[str, Any]:
    return {
        "resource_group": case.resource_group,
        "filter_tags": case.filter_tags,
        "exception_tags": case.exception_tags,
        "notags": case.notags,
        "age": case.age,
        "custom_age_tag_key": case.custom_key,
    }


def _run_engine(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    azure = _setup(case, monkeypatch, caplog, capsys)
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    try:
        outcome = engine_run("disk", _options(case), dry_run=case.dry_run, now=NOW)
        deleted = candidate_ids(outcome)
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    return Outcome(
        azure.calls,
        deleted,
        len(deleted),
        _records(caplog),
        [],
        capsys.readouterr().out,
        error,
        azure.waited,
    )


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_engine(case, monkeypatch, caplog, capsys)
    _assert_matches(case, old, new)


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
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    return Outcome(
        azure.calls,
        deleted,
        len(deleted),
        _records(caplog),
        [],
        capsys.readouterr().out,
        error,
        azure.waited,
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


def _assert_matches(case: Case, old: Outcome, new: Outcome) -> None:
    assert old.count_logs == [
        ("root", logging.INFO, f"count of items in disks_names_to_delete: {old.count}")
    ]
    if case.diverges:
        _assert_diverges(case, old, new)
        return
    assert new.error == old.error
    assert (new.error is not None) == (case.error_phase is not None)
    assert new.stdout == old.stdout
    assert _only(new.calls, "client")[:1] == _only(old.calls, "client")[:1]
    assert _only(new.calls, "list_by_resource_group") == _only(
        _fix_172(case, old.calls), "list_by_resource_group"
    )
    assert _only(new.calls, "list") == []
    old_yields = _only(old.calls, "yield")
    assert _only(new.calls, "yield")[: len(old_yields)] == old_yields
    if old.error is None:
        assert _only(new.calls, "yield") == old_yields
    if case.error_phase == "plan":
        assert _only(new.calls, "begin_delete") == []
        assert new.waited == []
        assert _groups(new.logs)[1:] == ([], [])
        return
    assert _only(new.calls, "begin_delete") == _only(old.calls, "begin_delete")
    assert old.waited == []
    assert new.waited == ([] if case.dry_run else new.deleted)
    if case.error_phase == "apply":
        assert _groups(new.logs)[1:] == _groups(expected_logs(old.logs, BUGS, RULES))[1:]
        return
    assert new.deleted == old.deleted
    assert new.count == old.count
    listing = [c for c in _fix_172(case, old.calls) if c[0] != "begin_delete"]
    assert new.calls == listing + _only(old.calls, "begin_delete")
    assert _groups(new.logs) == _groups(expected_logs(old.logs, BUGS, RULES))


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
    _assert_matches(case, old, new)


def _find(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)


def _group(case: Case) -> str:
    return case.resource_group or ENV_RESOURCE_GROUP


def _fix_172(case: Case, calls: Sequence[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """BUG-172: the oracle's subscription-wide `list` is a list of the configured group."""
    return [("list_by_resource_group", _group(case)) if c == ("list",) else c for c in calls]


@dataclass(frozen=True)
class Fixed:
    """The exact new outcome of a case that triggers BUG-174 (the oracle shows the old one)."""

    deleted: list[str]
    waited: list[Any]
    errors: list[str]
    old_error: str | None = None


DIVERGING: dict[str, Fixed] = {
    "begin-delete-error-propagates": Fixed(
        ["a", "c"],
        ["a", "c"],
        ["Error occurred while deleting disk b: delete failed"],
        old_error="delete failed",
    ),
    "begin-delete-error-first": Fixed(
        ["b"], ["b"], ["Error occurred while deleting disk a: boom"], old_error="boom"
    ),
    "name-none-delete-error": Fixed(
        [],
        [None],
        [
            "Error occurred while deleting disk None: "
            'can only concatenate str (not "NoneType") to str'
        ],
        old_error='can only concatenate str (not "NoneType") to str',
    ),
    "poller-error-not-deleted": Fixed(
        ["a", "c"], ["a", "b", "c"], ["Error occurred while deleting disk b: poller failed"]
    ),
    "begin-delete-error-every-disk": Fixed(
        [],
        [],
        [
            "Error occurred while deleting disk a: a failed",
            "Error occurred while deleting disk b: b failed",
        ],
        old_error="a failed",
    ),
}


def _errors(logs: Sequence[tuple[str, int, str]]) -> list[str]:
    return [r[2] for r in logs if r[1] == logging.ERROR]


def _begin_deletes(calls: Sequence[tuple[Any, ...]], case: Case, names: Sequence[Any]) -> list[Any]:
    return [("begin_delete", _group(case), n) for n in names]


def _assert_diverges(case: Case, old: Outcome, new: Outcome) -> None:
    if case.diverges == "BUG-173":
        assert old.error is None, "oracle changed"
        assert old.deleted == ([] if case.id == "age-unknown-key-never-old" else ["a"])
        assert new.error is not None and new.error[0] == "ValueError" and "--age" in new.error[1]
        assert new.deleted == [] and new.calls == [] and new.waited == [] and new.logs == []
        return
    fixed = DIVERGING[case.id]
    assert case.diverges == "BUG-174"
    assert (old.error[1] if old.error else None) == fixed.old_error, "oracle changed"
    assert (old.error, old.deleted) != (new.error, new.deleted)
    assert new.error is None
    assert new.deleted == fixed.deleted
    assert new.count == len(fixed.deleted)
    assert new.waited == fixed.waited
    assert _errors(new.logs) == fixed.errors
    assert [r[2] for r in new.logs if r[2].startswith("Deleted disk: ")] == [
        f"Deleted disk: {n}" for n in fixed.deleted
    ]
    assert [r[2] for r in new.logs if "Azure Disks" in r[2]] == [
        f"number of Azure Disks deleted: {len(fixed.deleted)}",
        f"List of Azure Disks deleted: {fixed.deleted}",
    ]
    listed = [c for c in new.calls if c[0] in ("list", "list_by_resource_group")]
    assert listed == [("list_by_resource_group", _group(case))]
    assert [c for c in new.calls if c[0] == "begin_delete"] == _begin_deletes(
        new.calls, case, [d.name for d in case.disks if not isinstance(d, Exception)]
    )
    assert new.stdout == old.stdout


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
    ("age-days-exact", lambda o: o.deleted == ["a"]),
    ("age-max-age-shortens", lambda o: o.deleted == ["a"]),
    ("custom-age-tag-unparseable-falls-back", lambda o: o.deleted == []),
    ("custom-age-tag-unparseable-falls-back-keeps", lambda o: o.deleted == []),
    ("custom-age-tag-empty-falls-back", lambda o: o.deleted == []),
    ("custom-age-tag-longer", lambda o: o.deleted == []),
    ("custom-age-tag-shorter", lambda o: o.deleted == ["a"]),
    ("created-none-error", lambda o: o.error is not None and o.deleted == ["a"]),
    ("duplicate-names-recorded-twice", lambda o: o.deleted == ["a", "a"]),
    ("list-error", lambda o: o.error == ("RuntimeError", "list failed") and o.deleted == []),
    (
        "iteration-error-mid-listing",
        lambda o: o.error == ("RuntimeError", "page failed") and o.deleted == ["a"],
    ),
    ("resource-group-from-env", lambda o: ("begin_delete", "env-rg", "a") in o.calls),
    (
        "begin-delete-error-ignored-in-dry-run",
        lambda o: o.deleted == ["a"] and _only(o.calls, "begin_delete") == [],
    ),
]


ENGINE_QUIRKS: dict[str, Callable[[Outcome], bool]] = {
    # Allowed change: the cleaner plans (lists and selects every disk) before it deletes, so an
    # error raised mid-listing or at a later disk stops the run before any delete.
    "iteration-error-mid-listing": lambda o: (
        o.error == ("RuntimeError", "page failed")
        and o.deleted == []
        and _only(o.calls, "begin_delete") == []
    ),
    "created-none-error": lambda o: (
        o.error is not None and o.deleted == [] and _only(o.calls, "begin_delete") == []
    ),
}

BUG_004_FIXED = frozenset(
    ["custom-age-tag-empty-falls-back", "custom-age-tag-unparseable-falls-back"]
)


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
    new = _run_engine(case, monkeypatch, caplog, capsys)
    # BUG-004/BUG-016: the oracle class inherits the live crc.service.Service, so it cannot
    # show the old custom-age-tag behaviour here; tests/test_crc_service_parity.py pins it
    # against the frozen crc_service oracle. Fixed cases assert the new outcome only.
    if case_id not in BUG_004_FIXED:
        assert check(old), "oracle no longer shows the quirk"
    assert ENGINE_QUIRKS.get(case_id, check)(new)


def test_engine_plans_every_disk_before_it_deletes_unlike_main(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("delete")
    azure = _setup(case, monkeypatch, caplog, capsys)
    _legacy(case).delete()
    assert _fix_172(case, azure.calls) == [
        ("client", "sub-1"),
        ("list_by_resource_group", "rg-test"),
        ("yield", "a"),
        ("begin_delete", "rg-test", "a"),
        ("yield", "b"),
        ("yield", "c"),
        ("begin_delete", "rg-test", "c"),
    ]
    azure = _setup(case, monkeypatch, caplog, capsys)
    candidate_ids(engine_run("disk", _options(case), dry_run=False, now=NOW))
    assert azure.calls == [
        ("client", "sub-1"),
        ("list_by_resource_group", "rg-test"),
        ("yield", "a"),
        ("yield", "b"),
        ("yield", "c"),
        ("begin_delete", "rg-test", "a"),
        ("begin_delete", "rg-test", "c"),
    ]


def test_repeated_run_repeats_the_calls_like_main(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The oracle accumulates names over two `delete()` calls; each engine run starts afresh."""
    case = _find("delete")
    azure = _setup(case, monkeypatch, caplog, capsys)
    legacy = _legacy(case)
    legacy.delete()
    legacy.delete()
    old_calls = _fix_172(case, azure.calls)
    old_deleted = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Deleted")]
    azure = _setup(case, monkeypatch, caplog, capsys)
    runs = [
        candidate_ids(engine_run("disk", _options(case), dry_run=False, now=NOW)) for _ in range(2)
    ]
    new_deleted = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Deleted")]
    assert _only(azure.calls, "list_by_resource_group", "begin_delete") == _only(
        old_calls, "list_by_resource_group", "begin_delete"
    )
    assert new_deleted == old_deleted
    assert legacy.get_deleted == ["a", "c", "a", "c"]
    assert runs == [["a", "c"], ["a", "c"]]


def test_registered_factory_builds_cleaner(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAzure([]).install(monkeypatch)
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    cleaner: Cleaner = get("azure", "disk")(ctx)
    assert isinstance(cleaner, AzureDiskCleaner)
    assert get("azure", "disk") is build_disk_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("azure", "disk")
