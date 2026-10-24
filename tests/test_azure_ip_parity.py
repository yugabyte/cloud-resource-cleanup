"""Parity: the Azure IP Cleaner, run through `crc.core.engine.run`, against main.

The oracle is `tests/legacy/azu_ip.txt`. Both run against the same fake Azure network client (the
SDK classes `crc.azu._base` builds are replaced, so the real `Base` runs in both). SDK calls,
results, log records, stdout and raised errors must match for every case, except the documented
divergences. First, every case lists the configured resource group (`list(resource_group)`)
instead of the subscription (`list_all()`): BUG-074, the oracle's `("list_all",)` call is mapped
to `("list", group)`. Second, the cases in `DIVERGING` (BUG-013, BUG-076, BUG-077) assert the
new outcome and that it differs from the oracle. Third, the cleaner lists everything before it
deletes, so a listing or selection error stops the run before any delete (fewer deletes). The
registered `Cleaner` protocol (factory, `plan()`, `apply()`) is compared too.
"""

import datetime
import logging
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from crc.azu.ip import AzureIpCleaner, build_ip_cleaner
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import get
from tests.helpers_azure_ip import NOW, FakeAzure, ip
from tests.helpers_wc_azu_engine import candidate_ids, engine_run

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
    age: Any = field(default_factory=lambda: {"days": 1})


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
    Case(
        "in-use-nat-gateway-skipped",
        [
            types.SimpleNamespace(
                name="a", tags=ENV, ip_configuration=None, nat_gateway={"id": "g"}
            ),
            ip("b", ENV),
        ],
    ),
    Case(
        "in-use-linked-ip-skipped",
        [
            types.SimpleNamespace(
                name="a", tags=ENV, ip_configuration=None, linked_public_ip_address={"id": "l"}
            ),
            ip("b", ENV),
        ],
    ),
    Case(
        "in-use-service-ip-skipped",
        [
            types.SimpleNamespace(
                name="a", tags=ENV, ip_configuration=None, service_public_ip_address={"id": "s"}
            ),
            ip("b", ENV),
        ],
    ),
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
    Case(
        "young-ip-kept-by-age-gate",
        [
            types.SimpleNamespace(
                name="a",
                tags=ENV,
                ip_configuration=None,
                created_time=NOW.replace(tzinfo=datetime.UTC) - datetime.timedelta(hours=1),
            ),
            ip("b", ENV),
        ],
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


@dataclass
class Diverges:
    bug: str
    deleted: list[str]
    old_error: tuple[str, str] | None
    old_deleted: list[str] | None = None


TYPE_ERROR = ("TypeError", "argument of type 'NoneType' is not iterable")
ATTRIBUTE_ERROR = (
    "AttributeError",
    "'types.SimpleNamespace' object has no attribute 'ip_configuration'",
)

DIVERGING: dict[str, Diverges] = {
    "untagged-with-exception-raises": Diverges("BUG-013", ["a"], TYPE_ERROR, []),
    "untagged-with-notags-raises": Diverges("BUG-013", ["a"], TYPE_ERROR, []),
    "untagged-raises-even-if-filter-excludes": Diverges("BUG-077", [], TYPE_ERROR, []),
    "untagged-raises-after-earlier-deletes": Diverges(
        "BUG-077", ["a", "b", "c", "d"], TYPE_ERROR, ["a", "b"]
    ),
    "untagged-raises-in-dry-run": Diverges("BUG-013", ["a", "c"], TYPE_ERROR, ["a"]),
    "untagged-with-exception-attached-ip-still-raises": Diverges("BUG-013", [], TYPE_ERROR, []),
    "in-use-nat-gateway-skipped": Diverges("BUG-076", ["b"], None, ["a", "b"]),
    "in-use-linked-ip-skipped": Diverges("BUG-076", ["b"], None, ["a", "b"]),
    "in-use-service-ip-skipped": Diverges("BUG-076", ["b"], None, ["a", "b"]),
    "young-ip-kept-by-age-gate": Diverges("BUG-075", ["b"], None, ["a", "b"]),
}


def _expected_logs(case: Case, deleted: Sequence[str]) -> list[tuple[str, int, str]]:
    items = list(deleted)
    if case.dry_run:
        logs = [
            (
                "root",
                logging.WARNING,
                f"List of Azure IPs (Total: {len(items)}) which will be deleted: {items}",
            )
        ]
    else:
        logs = [("root", logging.INFO, f"Deleted IP address: {name}") for name in items]
        logs.extend(
            [
                ("root", logging.WARNING, f"number of Azure IPs deleted: {len(items)}"),
                ("root", logging.WARNING, f"List of Azure IPs deleted: {items}"),
            ]
        )
    logs.append(("root", logging.INFO, f"count of items in deleted_ips: {len(items)}"))
    return logs


def _scoped(calls: Sequence[tuple[Any, ...]], case: Case) -> list[tuple[Any, ...]]:
    """The oracle's calls with its subscription-wide listing mapped to the group listing."""
    group = case.resource_group or "env-rg"
    return [("list", group) if c == ("list_all",) else c for c in calls]


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_azu_ip")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    return module


def _legacy(case: Case) -> Any:
    return _load_oracle().IP(
        case.resource_group, case.dry_run, case.filter_tags, case.exception_tags, case.notags
    )


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
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    deleted = list(cleaner.get_deleted)
    count = cleaner.count
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(azure.calls, deleted, count, logs, capsys.readouterr().out, error)


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


def _expected_engine_calls(case: Case, deleted: Sequence[str]) -> list[tuple[Any, ...]]:
    group = case.resource_group or "env-rg"
    wanted = set(deleted)
    calls: list[tuple[Any, ...]] = [("client", "sub-1"), ("list", group)]
    calls.extend(("yield", getattr(item, "name", None)) for item in case.ips)
    if not case.dry_run:
        for item in case.ips:
            name = getattr(item, "name", None)
            if name in wanted:
                calls.append(("begin_delete", group, name))
    return calls


def _options(case: Case) -> dict[str, Any]:
    return {
        "resource_group": case.resource_group,
        "filter_tags": case.filter_tags,
        "exception_tags": case.exception_tags,
        "notags": case.notags,
        "age": None if case.dry_run else case.age,
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
        deleted = candidate_ids(engine_run("ip", _options(case), dry_run=case.dry_run, now=NOW))
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    logs = [
        (r.name, r.levelno, r.getMessage()) for r in caplog.records if r.levelno > logging.DEBUG
    ]
    return Outcome(azure.calls, deleted, len(deleted), logs, capsys.readouterr().out, error)


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
    assert old.logs[-1] == ("root", logging.INFO, f"count of items in deleted_ips: {old.count}")
    if case.id in DIVERGING:
        fix = DIVERGING[case.id]
        assert old.error == fix.old_error, f"{fix.bug}: oracle no longer shows the bug"
        assert old.deleted == fix.old_deleted, f"{fix.bug}: oracle no longer shows the bug"
        assert new.error is None
        assert new.deleted == fix.deleted
        assert new.count == len(fix.deleted)
        assert (new.error, new.deleted) != (old.error, old.deleted)
        assert new.calls == _expected_engine_calls(case, fix.deleted)
        assert new.logs == _expected_logs(case, fix.deleted)[:-1]
        assert new.stdout == ""
        return
    assert new.error == old.error
    assert new.stdout == old.stdout
    assert _only(new.calls, "list") == _only(_scoped(old.calls, case), "list")
    old_yields = _only(old.calls, "yield")
    assert _only(new.calls, "yield")[: len(old_yields)] == old_yields
    if old.error is None:
        assert _only(new.calls, "yield") == old_yields
    if case.fails_in_plan:
        assert _only(new.calls, "begin_delete") == []
        assert new.logs == []
        return
    assert _only(new.calls, "begin_delete") == _only(old.calls, "begin_delete")
    assert new.logs == old.logs[:-1]
    if new.error is None:
        assert new.deleted == old.deleted
        assert new.count == old.count
        assert new.calls == _expected_engine_calls(case, old.deleted or [])


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
        options=_options(case),
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
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    logs = [
        (r.name, r.levelno, r.getMessage()) for r in caplog.records if r.levelno > logging.DEBUG
    ]
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
    if case.id in DIVERGING:
        fix = DIVERGING[case.id]
        group = case.resource_group or "env-rg"
        assert old.error == fix.old_error, f"{fix.bug}: oracle no longer shows the bug"
        assert old.deleted == fix.old_deleted, f"{fix.bug}: oracle no longer shows the bug"
        assert new.error is None
        assert new.deleted == fix.deleted
        assert new.stdout == ""
        assert _only(new.calls, "list", "list_all") == [("list", group)]
        expected_deletes = [] if case.dry_run else [("begin_delete", group, n) for n in fix.deleted]
        assert _only(new.calls, "begin_delete") == expected_deletes
        assert new.logs == _expected_logs(case, fix.deleted)[:-1]
        return
    assert new.error == old.error
    assert new.stdout == old.stdout
    assert _only(new.calls, "list", "list_all") == _only(_scoped(old.calls, case), "list")
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


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
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
    (
        "missing-ip-configuration-attribute",
        lambda o: o.error == ATTRIBUTE_ERROR and o.deleted == [],
    ),
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


@dataclass
class FixedQuirk:
    case_id: str
    bug: str
    old_check: Callable[[Outcome], bool]
    new_check: Callable[[Outcome], bool]


FIXED_QUIRKS: Sequence[FixedQuirk] = [
    FixedQuirk(
        "untagged-with-exception-raises",
        "BUG-013",
        lambda o: o.error == TYPE_ERROR and o.deleted == [],
        lambda o: o.error is None and o.deleted == ["a"],
    ),
    FixedQuirk(
        "untagged-with-notags-raises",
        "BUG-013",
        lambda o: o.error == TYPE_ERROR and o.deleted == [],
        lambda o: o.error is None and o.deleted == ["a"],
    ),
    FixedQuirk(
        "untagged-raises-even-if-filter-excludes",
        "BUG-077",
        lambda o: o.error == TYPE_ERROR,
        lambda o: o.error is None and o.deleted == [],
    ),
    FixedQuirk(
        "untagged-raises-after-earlier-deletes",
        "BUG-077",
        lambda o: (
            o.error == TYPE_ERROR
            and o.deleted == ["a", "b"]
            and [c[0] for c in o.calls if c[0] == "begin_delete"] == ["begin_delete"] * 2
        ),
        lambda o: o.error is None and o.deleted == ["a", "b", "c", "d"],
    ),
    FixedQuirk(
        "untagged-raises-in-dry-run",
        "BUG-013",
        lambda o: o.error == TYPE_ERROR and o.deleted == ["a"],
        lambda o: o.error is None and o.deleted == ["a", "c"],
    ),
    FixedQuirk(
        "young-ip-kept-by-age-gate",
        "BUG-075",
        lambda o: o.error is None and o.deleted == ["a", "b"],
        lambda o: o.error is None and o.deleted == ["b"],
    ),
]


ENGINE_QUIRKS: dict[str, Callable[[Outcome], bool]] = {
    # Allowed change: the cleaner lists the whole group before it deletes, so a listing error
    # (even one raised mid-listing) stops the run before any delete.
    "iteration-error-mid-listing": lambda o: (
        o.error == ("RuntimeError", "page failed")
        and o.deleted == []
        and [c for c in o.calls if c[0] == "begin_delete"] == []
    ),
    # The delete error propagates from apply(); earlier IPs were already deleted, in plan order.
    "delete-error-mid-run-keeps-earlier-record": lambda o: (
        o.error == ("RuntimeError", "delete failed")
        and [c[2] for c in o.calls if c[0] == "begin_delete"] == ["ip-0", "ip-1", "ip-2"]
    ),
}


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
    assert check(old), "oracle no longer shows the quirk"
    assert ENGINE_QUIRKS.get(case_id, check)(new)


@pytest.mark.parametrize(
    "quirk",
    FIXED_QUIRKS,
    ids=[f"{q.case_id}[{q.bug}]" for q in FIXED_QUIRKS],
)
def test_fixed_quirk(
    quirk: FixedQuirk,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find(quirk.case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_engine(case, monkeypatch, caplog, capsys)
    assert quirk.old_check(old), f"{quirk.bug}: oracle no longer shows the bug"
    assert quirk.new_check(new), f"{quirk.bug}: fix did not produce expected outcome"


def test_oracle_lists_subscription_wide_new_code_lists_the_configured_group(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """BUG-074: main lists the subscription, the fix lists only the configured group."""
    case = _find("delete")
    azure = _setup(case, monkeypatch, caplog, capsys)
    _legacy(case).delete()
    assert ("list_all",) in azure.calls
    assert ("begin_delete", "rg-test", "a") in azure.calls
    azure = _setup(case, monkeypatch, caplog, capsys)
    candidate_ids(engine_run("ip", _options(case), dry_run=False, now=NOW))
    assert ("list", "rg-test") in azure.calls
    assert ("list_all",) not in azure.calls
    assert ("begin_delete", "rg-test", "a") in azure.calls


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
    old_calls = _scoped(azure.calls, case)
    old_deleted = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Deleted")]
    azure = _setup(case, monkeypatch, caplog, capsys)
    runs = [
        candidate_ids(engine_run("ip", _options(case), dry_run=False, now=NOW)) for _ in range(2)
    ]
    new_deleted = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Deleted")]
    assert _only(azure.calls, "list") == _only(old_calls, "list")
    assert _only(azure.calls, "begin_delete") == _only(old_calls, "begin_delete")
    assert new_deleted == old_deleted
    assert legacy.get_deleted == ["a", "b", "a", "b"]
    assert runs == [["a", "b"], ["a", "b"]]


def test_registered_factory_builds_cleaner(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAzure([], None, {}).install(monkeypatch)
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    cleaner: Cleaner = get("azure", "ip")(ctx)
    assert isinstance(cleaner, AzureIpCleaner)
    assert get("azure", "ip") is build_ip_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("azure", "ip")
