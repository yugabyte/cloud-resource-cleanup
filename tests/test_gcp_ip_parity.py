"""Parity: `crc.gcp.ip.IP` against main (`tests/legacy/gcp_ip.txt`).

Both run against the same fake `google.cloud.compute_v1` clients; SDK calls (in order), results,
log records, stdout and raised errors must be identical for every case. The registered `Cleaner`
path (factory with a `RunContext`, `plan()` then `apply()` unless dry run) is compared on
results, errors, stdout, delete calls and log records.
"""

import datetime
import logging
import types
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from google.api_core.exceptions import PermissionDenied, ServiceUnavailable
from google.auth.exceptions import DefaultCredentialsError

from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import get
from crc.gcp.ip import IP, IpCleaner, build_ip_cleaner
from tests.helpers_log_fix import expected_logs
from tests.log_fix_gcp import RULES

ORACLE = Path(__file__).parent / "legacy" / "gcp_ip.txt"
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)
PROJECT = "proj-1"
BUGS = {"BUG-358"}
THREE = 3


def addr(name: Any, status: Any = "RESERVED") -> Any:
    return types.SimpleNamespace(name=name, status=status)


def _make(kind: type[Exception], message: str) -> Exception:
    return kind(message)


class IterError:
    """A listing that yields `items` and then raises `error` while being iterated."""

    def __init__(self, items: Sequence[Any], error: Exception) -> None:
        self.items = list(items)
        self.error = error

    def __iter__(self) -> Iterator[Any]:
        yield from self.items
        raise self.error


@dataclass
class Case:
    id: str
    regions: Mapping[str, Any]
    filter_regex: Any = field(default_factory=list)
    exception_regex: Any = field(default_factory=list)
    dry_run: bool = False
    delete_errors: Mapping[tuple[str, str], Exception] = field(default_factory=dict)
    regions_error: Exception | IterError | None = None
    regions_ctor_error: Exception | None = None
    fails_in_plan: bool = False
    regions_listing: Any = None


ONE = {"us-east1": [addr("ip-a"), addr("ip-b"), addr("keep-c"), addr("x-d", "IN_USE")]}
TWO_REGIONS = {
    "us-east1": [addr("ip-a"), addr("ip-b")],
    "us-west1": [addr("ip-c"), addr("keep-d")],
}
BOOM = _make(ServiceUnavailable, "unreachable")
DENIED = _make(PermissionDenied, "denied")


def _cases() -> list[Case]:
    return [
        Case("no-filter-all-reserved", ONE),
        Case("dry-run", ONE, dry_run=True),
        Case("dry-run-two-regions", TWO_REGIONS, dry_run=True),
        Case("two-regions", TWO_REGIONS),
        Case("filter-substring", ONE, filter_regex=["ip-"]),
        Case("filter-any-of-many", ONE, filter_regex=["nomatch", "keep"]),
        Case("filter-no-match", ONE, filter_regex=["zzz"]),
        Case("filter-is-substring-not-regex", {"r": [addr("ip.a"), addr("ipxa")]}, ["ip.a"]),
        Case("filter-regex-syntax-not-compiled", {"r": [addr("ip-a")]}, filter_regex=["ip-("]),
        Case("filter-case-sensitive", ONE, filter_regex=["IP-"]),
        Case("filter-none", ONE, filter_regex=None),
        Case("filter-string-iterates-characters", ONE, filter_regex="zk"),
        Case("exception-inverted-keeps-matching", ONE, exception_regex=["keep"]),
        Case("exception-inverted-no-match-excludes-all", ONE, exception_regex=["zzz"]),
        Case("exception-empty-list-excludes-none", ONE, exception_regex=[]),
        Case("exception-none-excludes-none", ONE, exception_regex=None),
        Case("exception-any-of-many", ONE, exception_regex=["zzz", "ip-a"]),
        Case("filter-and-exception", ONE, filter_regex=["ip-"], exception_regex=["ip-b"]),
        Case("filter-and-exception-disjoint", ONE, filter_regex=["keep"], exception_regex=["ip-"]),
        Case("exception-inverted-dry-run", ONE, dry_run=True, exception_regex=["keep"]),
        Case("status-in-use-skipped", {"r": [addr("a", "IN_USE"), addr("b", "RESERVING")]}),
        Case("status-case-sensitive", {"r": [addr("a", "reserved"), addr("b", "RESERVED")]}),
        Case("status-none", {"r": [addr("a", None), addr("b")]}),
        Case("in-use-with-matching-filter", {"r": [addr("ip-x", "IN_USE")]}, ["ip-"]),
        Case("empty-region-list", {}),
        Case("empty-region-list-dry-run", {}, dry_run=True),
        Case("region-without-addresses", {"r1": [], "r2": [addr("a")]}),
        Case("all-regions-empty", {"r1": [], "r2": []}),
        Case("duplicate-name-deleted-twice", {"r": [addr("dup"), addr("dup")]}),
        Case("same-name-in-two-regions", {"r1": [addr("dup")], "r2": [addr("dup")]}),
        Case("empty-name", {"r": [addr("")]}, filter_regex=[""]),
        Case("empty-name-with-filter", {"r": [addr("")]}, filter_regex=["a"]),
        Case("empty-filter-entry-matches-everything", ONE, filter_regex=[""]),
        Case("non-string-name-no-filter", {"r": [addr(None)]}),
        Case("non-string-name-with-filter", {"r": [addr(None)]}, filter_regex=["a"]),
        Case("non-string-filter-entry", ONE, filter_regex=[1]),
        Case("non-string-exception-entry", ONE, exception_regex=[1]),
        Case(
            "non-string-exception-evaluated-when-filter-misses",
            ONE,
            filter_regex=["zzz"],
            exception_regex=[1],
        ),
        Case("non-string-name-in-use-skipped", {"r": [addr(None, "IN_USE")]}, filter_regex=["a"]),
        Case("address-without-name", {"r": [types.SimpleNamespace(status="RESERVED")]}),
        Case("address-without-status", {"r": [types.SimpleNamespace(name="a")]}),
        Case("address-without-fields", {"r": [types.SimpleNamespace()]}),
        Case("regions-list-permission-denied", ONE, regions_error=DENIED, fails_in_plan=True),
        Case("regions-list-unreachable", ONE, regions_error=BOOM, fails_in_plan=True),
        Case("regions-list-connection-error", ONE, regions_error=ConnectionError("down")),
        Case(
            "regions-iteration-error",
            ONE,
            regions_error=IterError([types.SimpleNamespace(name="us-east1")], DENIED),
        ),
        Case(
            "regions-client-credentials-error",
            ONE,
            regions_ctor_error=_make(DefaultCredentialsError, "no credentials"),
        ),
        Case("regions-listing-empty", ONE, regions_listing=[]),
        Case(
            "regions-object-without-name",
            ONE,
            regions_listing=[types.SimpleNamespace()],
        ),
        Case("addresses-list-permission-denied", {"r1": [addr("a")], "r2": DENIED}),
        Case("addresses-list-first-region-error", {"r1": BOOM, "r2": [addr("a")]}),
        Case("addresses-list-error-dry-run", {"r1": [addr("a")], "r2": BOOM}, dry_run=True),
        Case(
            "addresses-iteration-error",
            {"r1": [addr("a")], "r2": IterError([addr("b")], BOOM)},
        ),
        Case(
            "addresses-iteration-error-after-delete-pending",
            {"r1": IterError([addr("a"), addr("b")], DENIED)},
        ),
        Case("delete-error-first", ONE, delete_errors={("us-east1", "ip-a"): DENIED}),
        Case(
            "delete-error-mid-region-loses-record", ONE, delete_errors={("us-east1", "ip-b"): BOOM}
        ),
        Case(
            "delete-error-second-region-keeps-first",
            TWO_REGIONS,
            delete_errors={("us-west1", "ip-c"): DENIED},
        ),
        Case(
            "delete-error-ignored-in-dry-run",
            ONE,
            dry_run=True,
            delete_errors={("us-east1", "ip-a"): DENIED},
        ),
        Case(
            "delete-error-region-without-matches-not-hit",
            ONE,
            filter_regex=["keep"],
            delete_errors={("us-east1", "ip-a"): DENIED},
        ),
    ]


CASES = _cases()


class FakeRegionsClient:
    def __init__(self, gcp: "FakeGcp") -> None:
        self.gcp = gcp
        gcp.calls.append(("RegionsClient",))
        if gcp.case.regions_ctor_error is not None:
            raise gcp.case.regions_ctor_error

    def list(self, *, project: str) -> Any:
        self.gcp.calls.append(("regions.list", project))
        error = self.gcp.case.regions_error
        if isinstance(error, Exception):
            raise error
        if isinstance(error, IterError):
            return error
        if self.gcp.case.regions_listing is not None:
            return self.gcp.case.regions_listing
        return [types.SimpleNamespace(name=name) for name in self.gcp.case.regions]


class FakeAddressesClient:
    def __init__(self, gcp: "FakeGcp") -> None:
        self.gcp = gcp
        gcp.calls.append(("AddressesClient",))

    def list(self, *, project: str, region: str) -> Any:
        self.gcp.calls.append(("addresses.list", project, region))
        outcome = self.gcp.case.regions[region]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def delete(self, *, project: str, region: str, address: str) -> Any:
        self.gcp.calls.append(("addresses.delete", project, region, address))
        error = self.gcp.case.delete_errors.get((region, address))
        if error is not None:
            raise error
        return types.SimpleNamespace(name="operation")


class FakeGcp:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []

    def regions_client(self) -> FakeRegionsClient:
        return FakeRegionsClient(self)

    def addresses_client(self) -> FakeAddressesClient:
        return FakeAddressesClient(self)


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[Any]
    count: int
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_gcp_ip")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    return module


def _legacy(case: Case) -> Any:
    return _load_oracle().IP(case.dry_run, PROJECT, case.filter_regex, case.exception_regex)


def _new(case: Case) -> Any:
    return IP(case.dry_run, PROJECT, case.filter_regex, case.exception_regex)


def _setup(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeGcp:
    gcp = FakeGcp(case)
    monkeypatch.setattr("google.cloud.compute_v1.RegionsClient", gcp.regions_client)
    monkeypatch.setattr("google.cloud.compute_v1.AddressesClient", gcp.addresses_client)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    caplog.clear()
    capsys.readouterr()
    return gcp


def _logs(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [(r.name, r.levelno, r.getMessage()) for r in caplog.records]


def _run(
    build: Callable[[Case], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    gcp = _setup(case, monkeypatch, caplog, capsys)
    cleaner = build(case)
    error: tuple[str, str] | None = None
    try:
        cleaner.delete()
    except Exception as e:
        error = (type(e).__name__, str(e))
    deleted = list(cleaner.get_deleted)
    count = cleaner.count
    return Outcome(gcp.calls, deleted, count, _logs(caplog), capsys.readouterr().out, error)


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
    assert new.stdout == old.stdout


def _run_cleaner(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    gcp = _setup(case, monkeypatch, caplog, capsys)
    ctx = RunContext(
        dry_run=case.dry_run,
        operation=Operation.DELETE,
        options={
            "project_id": PROJECT,
            "name_regex": case.filter_regex,
            "exception_regex": case.exception_regex,
        },
        now=NOW,
    )
    cleaner = get("gcp", "ip")(ctx)
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    try:
        plan = cleaner.plan(ctx)
        if case.dry_run:
            deleted = [c.id for c in plan.candidates]
        else:
            deleted = list(cleaner.apply(plan, ctx).succeeded)
    except Exception as e:
        error = (type(e).__name__, str(e))
    return Outcome(gcp.calls, deleted, len(deleted), _logs(caplog), capsys.readouterr().out, error)


def _only(calls: Sequence[tuple[Any, ...]], name: str) -> list[tuple[Any, ...]]:
    return [c for c in calls if c[0] == name]


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
    old_deletes = _only(old.calls, "addresses.delete")
    new_deletes = _only(new.calls, "addresses.delete")
    if new.error is not None:
        # main deletes region by region while listing; plan() lists everything first, so an
        # error raised while listing stops the Cleaner path before any delete.
        assert new.error == old.error
        assert new.stdout == old.stdout
        if _lists_fail(case):
            assert new_deletes == []
        else:
            assert new_deletes == old_deletes
        return
    assert old.error is None
    assert new.deleted == old.deleted
    assert new.stdout == old.stdout
    assert new_deletes == old_deletes
    assert _only(new.calls, "addresses.list") == _only(old.calls, "addresses.list")
    expected = expected_logs(old.logs, BUGS, RULES)
    assert Counter(new.logs) == Counter(expected[:-1])
    assert new.logs[-1] == expected[-2]
    if len(case.regions) <= 1:
        assert new.logs == expected[:-1]
        assert new.calls == old.calls


def _lists_fail(case: Case) -> bool:
    if case.regions_error is not None or case.regions_ctor_error is not None:
        return True
    return any(isinstance(v, Exception | IterError) for v in case.regions.values())


def _find(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("exception-inverted-keeps-matching", lambda o: o.deleted == ["keep-c"]),
    ("exception-inverted-no-match-excludes-all", lambda o: o.deleted == []),
    ("exception-empty-list-excludes-none", lambda o: o.deleted == ["ip-a", "ip-b", "keep-c"]),
    ("exception-none-excludes-none", lambda o: o.deleted == ["ip-a", "ip-b", "keep-c"]),
    ("filter-is-substring-not-regex", lambda o: o.deleted == ["ip.a"]),
    ("filter-regex-syntax-not-compiled", lambda o: o.error is None and o.deleted == []),
    ("filter-case-sensitive", lambda o: o.deleted == []),
    ("filter-string-iterates-characters", lambda o: o.deleted == ["keep-c"]),
    ("empty-filter-entry-matches-everything", lambda o: o.deleted == ["ip-a", "ip-b", "keep-c"]),
    ("status-in-use-skipped", lambda o: o.deleted == []),
    ("status-case-sensitive", lambda o: o.deleted == ["b"]),
    ("status-none", lambda o: o.error is None and o.deleted == ["b"]),
    ("duplicate-name-deleted-twice", lambda o: o.deleted == ["dup", "dup"]),
    ("dry-run", lambda o: not _only(o.calls, "addresses.delete") and len(o.deleted) == THREE),
    ("non-string-name-no-filter", lambda o: o.error is None and o.deleted == [None]),
    ("non-string-name-with-filter", lambda o: o.error is not None and o.error[0] == "TypeError"),
    ("non-string-filter-entry", lambda o: o.error is not None and o.error[0] == "TypeError"),
    (
        "non-string-exception-evaluated-when-filter-misses",
        lambda o: o.error is not None and o.error[0] == "TypeError",
    ),
    ("non-string-name-in-use-skipped", lambda o: o.error is None and o.deleted == []),
    ("address-without-name", lambda o: o.error is not None and "name" in o.error[1]),
    ("address-without-status", lambda o: o.error is not None and "status" in o.error[1]),
    ("regions-list-permission-denied", lambda o: o.error == ("PermissionDenied", "403 denied")),
    ("regions-list-connection-error", lambda o: o.error == ("ConnectionError", "down")),
    ("regions-client-credentials-error", lambda o: o.error is not None and o.deleted == []),
    ("regions-listing-empty", lambda o: o.deleted == [] and _only(o.calls, "addresses.list") == []),
    (
        "regions-iteration-error",
        lambda o: o.error is not None and _only(o.calls, "addresses.list") == [],
    ),
    ("addresses-list-permission-denied", lambda o: o.error is not None and o.deleted == ["a"]),
    ("addresses-list-first-region-error", lambda o: o.error is not None and o.deleted == []),
    (
        "addresses-iteration-error-after-delete-pending",
        lambda o: o.error is not None and _only(o.calls, "addresses.delete") == [],
    ),
    (
        "delete-error-mid-region-loses-record",
        lambda o: o.error is not None and o.deleted == [],
    ),
    (
        "delete-error-second-region-keeps-first",
        lambda o: o.error is not None and o.deleted == ["ip-a", "ip-b"],
    ),
    ("delete-error-ignored-in-dry-run", lambda o: o.error is None and len(o.deleted) == THREE),
    ("delete-error-region-without-matches-not-hit", lambda o: o.error is None),
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


def test_second_delete_call_accumulates_like_main(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("two-regions")
    results = []
    for build in (_legacy, _new):
        gcp = _setup(case, monkeypatch, caplog, capsys)
        ips = build(case)
        ips.delete()
        ips.delete()
        results.append((gcp.calls, list(ips.get_deleted), _logs(caplog)))
    assert (*results[0][:-1], expected_logs(results[0][-1], BUGS, RULES)) == results[1]


def test_registered_factory_builds_cleaner() -> None:
    ctx = RunContext(
        dry_run=True, operation=Operation.DELETE, options={"project_id": PROJECT}, now=NOW
    )
    cleaner: Cleaner = get("gcp", "ip")(ctx)
    assert isinstance(cleaner, IpCleaner)
    assert get("gcp", "ip") is build_ip_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("gcp", "ip")


def test_default_state_attribute_is_honoured(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("status-in-use-skipped")
    outcomes = []
    for build in (_legacy, _new):
        _setup(case, monkeypatch, caplog, capsys)
        ips = build(case)
        ips.default_state = "IN_USE"
        ips.delete()
        outcomes.append(list(ips.get_deleted))
    assert outcomes[0] == outcomes[1] == ["a"]


def test_bug_358_address_dump_is_debug_not_info(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("two-regions")
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert old.logs != new.logs
    assert new.logs == expected_logs(old.logs, BUGS, RULES)
    assert [m for _, _, m in new.logs] == [m for _, _, m in old.logs]
    assert new.calls == old.calls
    assert new.stdout == old.stdout
    info = [m for _, level, m in new.logs if level == logging.INFO]
    assert not [m for m in info if m.startswith(("{", "namespace("))]
    dumps = [m for _, level, m in old.logs if level == logging.INFO]
    assert any(m.startswith(("{", "namespace(")) for m in dumps)
