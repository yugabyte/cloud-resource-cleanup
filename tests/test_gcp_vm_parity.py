"""Parity: the registered GCP VM `Cleaner`, run through `crc.core.engine.run`, against main
(`tests/legacy/gcp_vm.txt`).

Both run against the same fake `googleapiclient.discovery.build` and
`google.cloud.compute_v1.InstancesClient`, with a frozen clock; results, count, stdout, raised
errors, delete and stop calls (BUG-185: with the zone operation wait) and log records are
compared for every case. The one allowed difference: `plan()` lists every zone before `apply()`
mutates, so an error in a later zone stops the run before any delete or stop. The module lists
zones, not regions; zone listing errors (auth and connectivity) propagate on main and here.
"""

import datetime
import logging
import re
import types
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from google.api_core.exceptions import PermissionDenied, ServiceUnavailable
from google.auth.exceptions import DefaultCredentialsError

from crc.core import engine
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import Registry, get
from crc.core.selection import Selection
from crc.gcp.vm import VmCleaner, VmSettings, build_vm_cleaner
from tests.helpers_log_fix import expected_logs
from tests.log_fix_gcp import RULES

ORACLE = Path(__file__).parent / "legacy" / "gcp_vm.txt"
UTC = datetime.UTC
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
PROJECT = "proj-1"
BUGS = {"BUG-358"}
ACTION_CALLS = ("instances.delete.execute", "instances.stop.execute")
WAIT_CALLS = ("zoneOperations.get.execute",)
TS_ERR = "time data {!r} does not match format '%Y-%m-%dT%H:%M:%S.%f%z'"
NO_ATTR = "'types.SimpleNamespace' object has no attribute {!r}"
BUG_183 = frozenset(
    {"age-empty-matches-everything", "age-none-matches-everything", "age-zero-days"}
)
FIX_186: dict[str, list[str]] = {
    "age-max-age-invalid-raises": [],
    "age-timestamp-unparseable": [],
    "age-timestamp-without-fraction": [],
    "age-timestamp-none-raises": [],
    "age-error-after-earlier-delete": ["ok"],
    "age-error-after-earlier-zone": ["ok"],
    "instance-without-labels-attribute": [],
    "instance-without-status-attribute": [],
    "instances-list-permission-denied": [],
    "instances-list-error-after-first-zone": ["a"],
    "instances-iteration-error": ["a", "b"],
    "instances-iteration-error-stop": ["a"],
}
TWO = 2
THREE = 3
DAY = {"days": 1}


def _make(kind: type[Exception], message: str) -> Exception:
    return kind(message)


def stamp(**delta: float) -> str:
    return (NOW - datetime.timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%S.%f%z")


def inst(
    name: Any,
    ago: Mapping[str, float] | None = None,
    labels: Any = None,
    status: str = "RUNNING",
    stamp_value: Any = "default",
) -> Any:
    ts = stamp(**(ago or {"days": 3})) if stamp_value == "default" else stamp_value
    return types.SimpleNamespace(
        name=name,
        status=status,
        labels={} if labels is None else labels,
        creation_timestamp=ts,
    )


class IterError:
    """A listing that yields `items` and then raises `error` while being iterated."""

    def __init__(self, items: Sequence[Any], error: Exception) -> None:
        self.items = list(items)
        self.error = error


@dataclass
class Case:
    id: str
    zones: Mapping[str, Any]
    op: str = "delete"
    dry_run: bool = False
    filter_labels: Any = field(default_factory=dict)
    exception_labels: Any = field(default_factory=dict)
    notags: Any = field(default_factory=dict)
    custom_age_tag_key: str = ""
    age: Any = field(default_factory=lambda: dict(DAY))
    state: Any = None
    action_errors: Mapping[tuple[str, str], Exception] = field(default_factory=dict)
    max_age: str | None = None
    zones_response: Any = None
    build_error: Exception | None = None
    zones_error: Exception | None = None
    client_ctor_error: Exception | None = None
    fails_in_plan: bool = False


MIXED = {
    "z1": [
        inst("old-a"),
        inst("stopped-old", status="TERMINATED"),
        inst("young", ago={"hours": 2}),
        inst("old-b"),
        inst("suspended", status="SUSPENDED"),
    ],
}
TWO_ZONES = {"z1": [inst("a1"), inst("a2")], "z2": [inst("b1"), inst("b2", ago={"hours": 1})]}
BOOM = _make(ServiceUnavailable, "unreachable")
DENIED = _make(PermissionDenied, "denied")


def _basic_cases() -> list[Case]:
    one = {"z": [inst("only")]}
    return [
        Case("mixed-deletes-old-running", MIXED),
        Case("mixed-dry-run", MIXED, dry_run=True),
        Case("mixed-stop", MIXED, op="stop"),
        Case("mixed-stop-dry-run", MIXED, op="stop", dry_run=True),
        Case("state-custom-delete", MIXED, state=["RUNNING", "TERMINATED"]),
        Case("state-terminated-only", MIXED, state=["TERMINATED"]),
        Case("state-empty-list-matches-nothing", MIXED, state=[]),
        Case("state-exact-not-substring", {"z": [inst("a", status="RUNNING2")]}),
        Case("state-lowercase-not-matched", {"z": [inst("a", status="running")]}),
        Case("state-custom-ignored-by-stop", MIXED, op="stop", state=["TERMINATED"]),
        Case("two-zones", TWO_ZONES),
        Case("two-zones-dry-run", TWO_ZONES, dry_run=True),
        Case("two-zones-stop", TWO_ZONES, op="stop"),
        Case("single-instance", one),
        Case("single-instance-dry-run", one, dry_run=True),
        Case("single-instance-stop", one, op="stop"),
        Case("duplicate-names", {"z": [inst("dup"), inst("dup")]}),
        Case("same-name-two-zones", {"z1": [inst("dup")], "z2": [inst("dup")]}),
        Case("unicode-name", {"z": [inst("vm-é")]}),
    ]


def _age_cases() -> list[Case]:
    one = {"z": [inst("d", ago={"days": 1})]}
    young = {"z": [inst("d", ago={"seconds": 1})]}
    return [
        Case("age-exactly-at-threshold", one),
        Case("age-one-second-short", {"z": [inst("d", ago={"hours": 23, "seconds": 59})]}),
        Case("age-hours-only", {"z": [inst("d", ago={"hours": 5})]}, age={"hours": 5}),
        Case("age-hours-short", {"z": [inst("d", ago={"hours": 4})]}, age={"hours": 5}),
        Case(
            "age-days-and-hours", {"z": [inst("d", ago={"hours": 30})]}, age={"days": 1, "hours": 6}
        ),
        Case(
            "age-days-and-hours-short",
            {"z": [inst("d", ago={"hours": 29})]},
            age={"days": 1, "hours": 6},
        ),
        Case("age-empty-matches-everything", young, age={}),
        Case("age-none-matches-everything", young, age=None),
        Case("age-int-days", one, age=1),
        Case("age-zero-days", young, age={"days": 0}),
        Case("age-future-creation", {"z": [inst("d", ago={"days": -1})]}),
        Case(
            "age-offset-timestamp", {"z": [inst("d", stamp_value="2026-02-27T01:00:00.000-08:00")]}
        ),
        Case("age-offset-young", {"z": [inst("d", stamp_value="2026-03-01T01:00:00.000-08:00")]}),
        Case(
            "age-max-age-shortens",
            {"z": [inst("d", ago={"hours": 3})]},
            age={"days": 5},
            max_age="{'hours': 2}",
        ),
        Case(
            "age-max-age-longer-ignored",
            {"z": [inst("d", ago={"hours": 3})]},
            max_age="{'days': 9}",
        ),
        Case("age-max-age-invalid-raises", {"z": [inst("d")]}, max_age="nope", fails_in_plan=True),
        Case("age-timestamp-unparseable", {"z": [inst("d", stamp_value="x")]}, fails_in_plan=True),
        Case(
            "age-timestamp-without-fraction",
            {"z": [inst("d", stamp_value="2026-02-20T10:00:00-08:00")]},
            fails_in_plan=True,
        ),
        Case("age-timestamp-none-raises", {"z": [inst("d", stamp_value=None)]}, fails_in_plan=True),
        Case(
            "age-error-after-earlier-delete",
            {"z": [inst("ok"), inst("bad", stamp_value="x")]},
            fails_in_plan=True,
        ),
        Case(
            "age-error-after-earlier-zone",
            {"z1": [inst("ok")], "z2": [inst("bad", stamp_value="x")]},
            fails_in_plan=True,
        ),
        Case(
            "age-error-on-wrong-state-ignored",
            {"z": [inst("skipped", status="TERMINATED", stamp_value="x")]},
        ),
        Case("age-stop-old", one, op="stop"),
        Case("age-stop-short", {"z": [inst("d", ago={"hours": 1})]}, op="stop"),
    ]


def _custom_age_cases() -> list[Case]:
    half = {"hours": 12}
    return [
        Case(
            "custom-age-keeps-young",
            {"z": [inst("d", ago={"days": 5}, labels={"ttl": "{'days': 10}"})]},
            custom_age_tag_key="ttl",
        ),
        Case(
            "custom-age-shortens",
            {"z": [inst("d", ago=half, labels={"ttl": "{'hours': 1}"})]},
            custom_age_tag_key="ttl",
        ),
        Case(
            "custom-age-empty-falls-back",
            {"z": [inst("d", labels={"ttl": ""})]},
            custom_age_tag_key="ttl",
        ),
        Case(
            "custom-age-unparseable-falls-back",
            {"z": [inst("d", labels={"ttl": "soon"})]},
            custom_age_tag_key="ttl",
        ),
        Case(
            "custom-age-zero-falls-back-to-default",
            {"z": [inst("d", labels={"ttl": "{}"})]},
            custom_age_tag_key="ttl",
        ),
        Case("custom-age-absent", {"z": [inst("d", labels={"x": "y"})]}, custom_age_tag_key="ttl"),
        Case(
            "custom-age-key-not-set",
            {"z": [inst("d", ago=half, labels={"ttl": "{'hours': 1}"})]},
        ),
        Case("custom-age-on-unlabelled", {"z": [inst("d")]}, custom_age_tag_key="ttl"),
        Case(
            "custom-age-int-literal",
            {"z": [inst("d", ago={"days": 3}, labels={"ttl": "2"})]},
            custom_age_tag_key="ttl",
        ),
        Case(
            "custom-age-stop",
            {"z": [inst("d", ago=half, labels={"ttl": "{'hours': 1}"})]},
            custom_age_tag_key="ttl",
            op="stop",
        ),
    ]


def _label_cases() -> list[Case]:
    labelled = {
        "z": [
            inst("a", labels={"env": "qa", "team": "x"}),
            inst("b", labels={"env": "prod", "team": "x"}),
            inst("c", labels={"team": "x"}),
            inst("d", labels={"env": "qa"}),
            inst("u"),
        ]
    }
    return [
        Case("filter-value-match", labelled, filter_labels={"env": ["qa"]}),
        Case("filter-value-mismatch", labelled, filter_labels={"env": ["dev"]}),
        Case("filter-all-keys", labelled, filter_labels={"env": ["qa"], "team": ["x"]}),
        Case("filter-one-key-missing-fails", labelled, filter_labels={"env": [], "zzz": []}),
        Case("filter-empty-values-means-key-present", labelled, filter_labels={"env": []}),
        Case("filter-multiple-values", labelled, filter_labels={"env": ["qa", "prod"]}),
        Case("filter-none", labelled, filter_labels=None),
        Case("filter-string-value-is-substring", labelled, filter_labels={"env": "qa-prod"}),
        Case("filter-case-sensitive", labelled, filter_labels={"env": ["QA"]}),
        Case("untagged-without-filter-included", {"z": [inst("u")]}),
        Case("untagged-with-filter-excluded", {"z": [inst("u")]}, filter_labels={"env": ["qa"]}),
        Case("exception-label-skips", labelled, exception_labels={"env": ["qa"]}),
        Case("exception-label-any-key", labelled, exception_labels={"env": ["dev"], "team": ["x"]}),
        Case("exception-label-empty-values", labelled, exception_labels={"env": []}),
        Case("exception-label-none", labelled, exception_labels=None),
        Case("exception-label-untagged-kept", {"z": [inst("u")]}, exception_labels={"env": ["qa"]}),
        Case("notags-all-keys-skips", labelled, notags={"env": ["qa"], "team": ["x"]}),
        Case("notags-partial-match-kept", labelled, notags={"env": ["qa"], "zzz": []}),
        Case("notags-single-key-skips", labelled, notags={"env": ["qa"]}),
        Case("notags-empty-values-key-present", labelled, notags={"team": []}),
        Case("notags-none", labelled, notags=None),
        Case("notags-untagged-kept", {"z": [inst("u")]}, notags={"env": ["qa"]}),
        Case(
            "exception-wins-over-filter",
            labelled,
            filter_labels={"env": ["qa", "prod"]},
            exception_labels={"env": ["prod"]},
        ),
        Case(
            "filter-and-notags",
            labelled,
            filter_labels={"team": ["x"]},
            notags={"env": ["qa"], "team": ["x"]},
        ),
        Case(
            "filter-exception-notags-stop",
            labelled,
            op="stop",
            filter_labels={"team": ["x"]},
            exception_labels={"env": ["prod"]},
            notags={"env": ["qa"]},
        ),
        Case(
            "labels-on-wrong-state-ignored",
            {"z": [inst("a", status="TERMINATED", labels={"env": "qa"})]},
            exception_labels={"env": ["qa"]},
        ),
        Case("label-filter-dry-run", labelled, dry_run=True, filter_labels={"env": ["qa"]}),
    ]


def _action_cases() -> list[Case]:
    err = RuntimeError("boom")
    two = {"z": [inst("a"), inst("b"), inst("c")]}
    return [
        Case("delete-error-swallowed", two, action_errors={("z", "b"): err}),
        Case("delete-error-all-fail", two, action_errors={("z", n): err for n in "abc"}),
        Case("delete-error-ignored-in-dry-run", two, dry_run=True, action_errors={("z", "b"): err}),
        Case("stop-error-swallowed", two, op="stop", action_errors={("z", "b"): err}),
        Case("stop-error-all-fail", two, op="stop", action_errors={("z", n): err for n in "abc"}),
        Case("delete-error-permission", two, action_errors={("z", "a"): DENIED}),
        Case("delete-error-empty-message", two, action_errors={("z", "a"): RuntimeError()}),
    ]


def _empty_and_error_cases() -> list[Case]:
    return [
        Case("zones-empty", {}),
        Case("zones-empty-stop", {}, op="stop"),
        Case("zones-empty-dry-run", {}, dry_run=True),
        Case("zone-no-instances", {"z": []}),
        Case("all-filtered-out", {"z": [inst("y", ago={"hours": 1})]}),
        Case(
            "build-error",
            {"z": [inst("a")]},
            build_error=_make(DefaultCredentialsError, "no creds"),
        ),
        Case("zones-list-permission-denied", {"z": [inst("a")]}, zones_error=DENIED),
        Case(
            "zones-list-connection-error", {"z": [inst("a")]}, zones_error=ConnectionError("down")
        ),
        Case("zones-list-unavailable", {"z": [inst("a")]}, zones_error=BOOM),
        Case("zones-response-without-items", {"z": [inst("a")]}, zones_response={}),
        Case("zones-response-items-empty", {"z": [inst("a")]}, zones_response={"items": []}),
        Case(
            "zone-without-name-after-first-zone",
            {"z1": [inst("a1"), inst("a2")]},
            zones_response={"items": [{"name": "z1"}, {}]},
        ),
        Case(
            "client-credentials-error",
            {"z": [inst("a")]},
            client_ctor_error=_make(DefaultCredentialsError, "no creds"),
        ),
        Case("instances-list-permission-denied", {"z": DENIED}),
        Case("instances-list-error-after-first-zone", {"z1": [inst("a")], "z2": DENIED}),
        Case("instances-iteration-error", {"z": IterError([inst("a"), inst("b")], BOOM)}),
        Case("instances-iteration-error-stop", {"z": IterError([inst("a")], BOOM)}, op="stop"),
        Case(
            "instance-without-labels-attribute",
            {
                "z": [
                    types.SimpleNamespace(
                        name="a", status="RUNNING", creation_timestamp=stamp(days=3)
                    )
                ]
            },
            fails_in_plan=True,
        ),
        Case(
            "instance-without-labels-wrong-state",
            {"z": [types.SimpleNamespace(name="a", status="STOPPED", creation_timestamp="x")]},
        ),
        Case(
            "instance-without-status-attribute",
            {"z": [types.SimpleNamespace(name="a", labels={}, creation_timestamp="x")]},
            fails_in_plan=True,
        ),
        Case(
            "young-instance-without-name",
            {
                "z": [
                    types.SimpleNamespace(
                        status="RUNNING", labels={}, creation_timestamp=stamp(hours=1)
                    )
                ]
            },
        ),
    ]


def _cases() -> list[Case]:
    return (
        _basic_cases()
        + _age_cases()
        + _custom_age_cases()
        + _label_cases()
        + _action_cases()
        + _empty_and_error_cases()
    )


CASES = _cases()


class FakeRequest:
    def __init__(self, gcp: "FakeGcp", label: str, *args: Any) -> None:
        self.gcp = gcp
        self.label = label
        self.args = args

    def execute(self) -> Any:
        self.gcp.calls.append((self.label + ".execute", *self.args))
        if self.label == "zones.list":
            if self.gcp.case.zones_error is not None:
                raise self.gcp.case.zones_error
            if self.gcp.case.zones_response is not None:
                return self.gcp.case.zones_response
            return {"items": [{"name": zone} for zone in self.gcp.case.zones]}
        if self.label == "zoneOperations.get":
            return {"status": "DONE"}
        error = self.gcp.case.action_errors.get((self.args[1], self.args[2]))
        if error is not None:
            raise error
        return {"name": "operation"}


class FakeZones:
    def __init__(self, gcp: "FakeGcp") -> None:
        self.gcp = gcp

    def list(self, *, project: str) -> FakeRequest:
        self.gcp.calls.append(("zones.list", project))
        return FakeRequest(self.gcp, "zones.list", project)


class FakeZoneOperations:
    def __init__(self, gcp: "FakeGcp") -> None:
        self.gcp = gcp

    def get(self, *, project: str, zone: str, operation: str) -> FakeRequest:
        self.gcp.calls.append(("zoneOperations.get", project, zone, operation))
        return FakeRequest(self.gcp, "zoneOperations.get", project, zone, operation)


class FakeInstancesResource:
    def __init__(self, gcp: "FakeGcp") -> None:
        self.gcp = gcp

    def delete(self, *, project: str, zone: str, instance: str) -> FakeRequest:
        self.gcp.calls.append(("instances.delete", project, zone, instance))
        return FakeRequest(self.gcp, "instances.delete", project, zone, instance)

    def stop(self, *, project: str, zone: str, instance: str) -> FakeRequest:
        self.gcp.calls.append(("instances.stop", project, zone, instance))
        return FakeRequest(self.gcp, "instances.stop", project, zone, instance)


class FakeService:
    def __init__(self, gcp: "FakeGcp", name: str, version: str) -> None:
        self.gcp = gcp
        gcp.calls.append(("discovery.build", name, version))
        if gcp.case.build_error is not None:
            raise gcp.case.build_error

    def __enter__(self) -> "FakeService":
        self.gcp.calls.append(("enter",))
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.gcp.calls.append(("close",))

    def zones(self) -> FakeZones:
        self.gcp.calls.append(("zones()",))
        return FakeZones(self.gcp)

    def instances(self) -> FakeInstancesResource:
        self.gcp.calls.append(("instances()",))
        return FakeInstancesResource(self.gcp)

    def zoneOperations(self) -> FakeZoneOperations:
        self.gcp.calls.append(("zoneOperations()",))
        return FakeZoneOperations(self.gcp)


class FakeInstancesClient:
    def __init__(self, gcp: "FakeGcp") -> None:
        self.gcp = gcp
        gcp.calls.append(("InstancesClient",))
        if gcp.case.client_ctor_error is not None:
            raise gcp.case.client_ctor_error

    def list(self, *, project: str, zone: str) -> Any:
        self.gcp.calls.append(("instances.list", project, zone))
        outcome = self.gcp.case.zones[zone]
        if isinstance(outcome, Exception):
            raise outcome
        return self._pages(zone, outcome)

    def _pages(self, zone: str, outcome: Any) -> Iterator[Any]:
        items = outcome.items if isinstance(outcome, IterError) else outcome
        for item in items:
            self.gcp.calls.append(("instances.next", zone, getattr(item, "name", None)))
            yield item
        if isinstance(outcome, IterError):
            raise outcome.error


class FakeGcp:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []

    def build(self, name: str, version: str) -> FakeService:
        return FakeService(self, name, version)

    def instances_client(self) -> FakeInstancesClient:
        return FakeInstancesClient(self)


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[str]
    stopped: list[str]
    count: int
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


class FrozenDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz: datetime.tzinfo | None = None) -> Any:
        return NOW if tz is None else NOW.astimezone(tz)


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_gcp_vm")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    setattr(module, "datetime", FrozenDatetime)  # noqa: B010
    return module


def _args(case: Case) -> dict[str, Any]:
    return {
        "dry_run": case.dry_run,
        "project_id": PROJECT,
        "filter_labels": case.filter_labels,
        "exception_labels": case.exception_labels,
        "age": case.age,
        "custom_age_tag_key": case.custom_age_tag_key,
        "notags": case.notags,
    }


def _legacy(case: Case) -> Any:
    return _load_oracle().VM(**_args(case))


def _setup(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeGcp:
    gcp = FakeGcp(case)
    monkeypatch.setattr("googleapiclient.discovery.build", gcp.build)
    monkeypatch.setattr("google.cloud.compute_v1.InstancesClient", gcp.instances_client)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    if case.max_age is None:
        monkeypatch.delenv("MAX_AGE", raising=False)
    else:
        monkeypatch.setenv("MAX_AGE", case.max_age)
    caplog.clear()
    capsys.readouterr()
    return gcp


def _logs(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [
        (r.name, r.levelno, re.sub(r"0x[0-9a-f]+", "0x0", r.getMessage())) for r in caplog.records
    ]


def _run(
    build: Callable[[Case], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    gcp = _setup(case, monkeypatch, caplog, capsys)
    vm = build(case)
    error: tuple[str, str] | None = None
    try:
        if case.op == "stop":
            vm.stop()
        elif case.state is None:
            vm.delete()
        else:
            vm.delete(case.state)
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, re.sub(r"0x[0-9a-f]+", "0x0", str(e)))
    count = vm.stopped_count if case.op == "stop" else vm.delete_count
    return Outcome(
        gcp.calls,
        list(vm.get_deleted),
        list(vm.get_stopped),
        count,
        _logs(caplog),
        capsys.readouterr().out,
        error,
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
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    if case.id in BUG_183:
        _assert_bug_183(old, new)
        return
    bugs = _bugs(case, old)
    if "BUG-186" in bugs:
        assert (new.error, old.error is not None) == (None, True)
        assert _done(new) == _fixed_done(case)
        assert new.count == len(_fixed_done(case))
        assert new.stdout == old.stdout
        _assert_fixed_logs(case, old, new)
        return
    if new.error is not None:
        assert new.error == old.error
        assert new.stdout == old.stdout
        assert _only(new.calls, *ACTION_CALLS) == []
        assert _only(new.calls, "instances.list") == _only(old.calls, "instances.list")
        return
    assert old.error is None
    assert (new.deleted, new.stopped, new.count) == (old.deleted, old.stopped, old.count)
    assert new.stdout == old.stdout
    expected = _only(_with_waits(old.calls, case), *ACTION_CALLS, *WAIT_CALLS)
    assert _only(new.calls, *ACTION_CALLS, *WAIT_CALLS) == expected
    assert _only(new.calls, "instances.list") == _only(old.calls, "instances.list")
    expected_log_lines = expected_logs(old.logs, BUGS, RULES)
    assert expected_log_lines[-1][2].startswith("count of items in instance_names_to_")
    assert Counter(new.logs) == Counter(expected_log_lines[:-1])


def _cleaner_context(case: Case) -> RunContext:
    options: dict[str, Any] = {
        "project_id": PROJECT,
        "filter_tags": case.filter_labels,
        "exception_tags": case.exception_labels,
        "notags": case.notags,
        "age": case.age,
        "custom_age_tag_key": case.custom_age_tag_key,
    }
    if case.state is not None:
        options["instance_state"] = case.state
    return RunContext(
        dry_run=case.dry_run,
        operation=Operation.STOP if case.op == "stop" else Operation.DELETE,
        options=options,
        now=NOW,
    )


def _run_cleaner(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    gcp = _setup(case, monkeypatch, caplog, capsys)
    ctx = _cleaner_context(case)
    registry = Registry()
    registry.register("gcp", "vm")(build_vm_cleaner)
    error: tuple[str, str] | None = None
    names: list[str] = []
    try:
        (outcome,) = engine.run(Selection((("gcp", "vm"),)), ctx, registry)
        names = (
            [c.id for c in outcome.plan.candidates]
            if outcome.result is None
            else list(outcome.result.succeeded)
        )
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, re.sub(r"0x[0-9a-f]+", "0x0", str(e)))
    deleted, stopped = ([], names) if case.op == "stop" else (names, [])
    return Outcome(
        gcp.calls, deleted, stopped, len(names), _logs(caplog), capsys.readouterr().out, error
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
    if case.id in BUG_183:
        _assert_bug_183(old, new)
        return
    actions = ACTION_CALLS
    if case.id in FIX_186:
        assert old.error is not None
        assert new.error is None
        assert _done(new) == _fixed_done(case)
        assert _only(new.calls, *actions) == _only(old.calls, *actions)
        return
    if new.error is not None:
        # main acts while listing; plan() lists everything first, so an error raised while
        # listing or filtering stops the Cleaner path before any delete or stop.
        assert new.error == old.error
        assert new.stdout == old.stdout
        assert _only(new.calls, *actions) == []
        return
    assert old.error is None
    assert new.deleted == old.deleted
    assert new.stopped == old.stopped
    assert new.stdout == old.stdout
    assert _only(new.calls, *actions) == _only(old.calls, *actions)
    assert _only(new.calls, "instances.list") == _only(old.calls, "instances.list")
    expected = expected_logs(old.logs, BUGS, RULES)
    assert Counter(new.logs) == Counter(expected[:-1])
    assert new.logs[-1] == expected[-2]
    if _listed(case) <= 1:
        assert new.logs == expected[:-1]


def _assert_bug_183(old: Outcome, new: Outcome) -> None:
    """BUG-183: main acted on every selected instance for an empty or zero `age`; a real run
    now raises `ValueError` naming the flag before any cloud call."""
    assert old.error is None
    assert old.deleted == ["d"]
    assert new.error is not None
    assert new.error[0] == "ValueError"
    assert new.error[1].startswith("age ")
    assert (new.deleted, new.stopped, new.calls) == ([], [], [])


def _listed(case: Case) -> int:
    return sum(len(v) if isinstance(v, list) else TWO for v in case.zones.values())


def _with_waits(calls: Sequence[tuple[Any, ...]], case: Case) -> list[tuple[Any, ...]]:
    """BUG-185: `calls` of main with the zone operation poll after every action that succeeded."""
    out: list[tuple[Any, ...]] = []
    for call in calls:
        out.append(call)
        if call[0] in ACTION_CALLS and (call[2], call[3]) not in case.action_errors:
            out += [
                ("zoneOperations()",),
                ("zoneOperations.get", call[1], call[2], "operation"),
                ("zoneOperations.get.execute", call[1], call[2], "operation"),
            ]
    return out


def _bugs(case: Case, old: Outcome) -> list[str]:
    found = []
    if _with_waits(old.calls, case) != old.calls:
        found.append("BUG-185")
    if case.id in FIX_186:
        found.append("BUG-186")
    return found


def _fixed_done(case: Case) -> list[str]:
    return FIX_186[case.id]


def _assert_fixed_logs(case: Case, old: Outcome, new: Outcome) -> None:
    """BUG-186: a case that raised on main now continues; its logs are main's plus `EXTRA_LOGS`."""
    expected = Counter(expected_logs(old.logs, BUGS, RULES)[:-1])
    assert expected - Counter(new.logs) == Counter()
    assert Counter(new.logs) - expected == Counter(EXTRA_LOGS[case.id])


def _summary_logs(names: list[str], past: str = "deleted") -> list[tuple[str, int, str]]:
    verb = past.removesuffix("d")
    head = [("root", logging.WARNING, f"No GCP instances to {verb}.")] if not names else []
    return [
        *head,
        ("root", logging.INFO, f"number of GCP instances {past}: {len(names)}"),
        ("root", logging.WARNING, f"List of GCP instances {past}: {names}"),
    ]


def _failure(message: str) -> tuple[str, int, str]:
    return ("root", logging.ERROR, message)


EXTRA_LOGS: dict[str, list[tuple[str, int, str]]] = {
    "age-max-age-invalid-raises": [
        _failure(
            "Error occurred while evaluating instance d: "
            "malformed node or string on line 1: <ast.Name object at 0x0>"
        ),
        *_summary_logs([]),
    ],
    "age-timestamp-unparseable": [
        _failure(f"Error occurred while evaluating instance d: {TS_ERR.format('x')}"),
        *_summary_logs([]),
    ],
    "age-timestamp-without-fraction": [
        _failure(
            "Error occurred while evaluating instance d: "
            + TS_ERR.format("2026-02-20T10:00:00-08:00")
        ),
        *_summary_logs([]),
    ],
    "age-timestamp-none-raises": [
        _failure(
            "Error occurred while evaluating instance d: "
            "strptime() argument 1 must be str, not None"
        ),
        *_summary_logs([]),
    ],
    "age-error-after-earlier-delete": [
        _failure(f"Error occurred while evaluating instance bad: {TS_ERR.format('x')}"),
        *_summary_logs(["ok"]),
    ],
    "age-error-after-earlier-zone": [
        _failure(f"Error occurred while evaluating instance bad: {TS_ERR.format('x')}"),
        *_summary_logs(["ok"]),
    ],
    "instance-without-labels-attribute": [
        _failure(f"Error occurred while evaluating instance a: {NO_ATTR.format('labels')}"),
        *_summary_logs([]),
    ],
    "instance-without-status-attribute": [
        _failure(f"Error occurred while evaluating instance a: {NO_ATTR.format('status')}"),
        *_summary_logs([]),
    ],
    "instances-list-permission-denied": [
        _failure("Zone z: failed listing GCP instances: 403 denied"),
        *_summary_logs([]),
    ],
    "instances-list-error-after-first-zone": [
        _failure("Zone z2: failed listing GCP instances: 403 denied"),
        *_summary_logs(["a"]),
    ],
    "instances-iteration-error": [
        _failure("Zone z: failed listing GCP instances: 503 unreachable"),
        *_summary_logs(["a", "b"]),
    ],
    "instances-iteration-error-stop": [
        _failure("Zone z: failed listing GCP instances: 503 unreachable"),
        *_summary_logs(["a"], "stopped"),
    ],
}


def _find(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)


def _done(o: Outcome) -> list[str]:
    return o.deleted + o.stopped


def _err(name: str) -> Callable[[Outcome], bool]:
    return lambda o: o.error is not None and o.error[0] == name


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("mixed-deletes-old-running", lambda o: o.deleted == ["old-a", "old-b"]),
    (
        "mixed-dry-run",
        lambda o: not _only(o.calls, "instances.delete") and _done(o) == ["old-a", "old-b"],
    ),
    ("mixed-stop", lambda o: o.stopped == ["old-a", "old-b"] and o.deleted == []),
    ("state-custom-delete", lambda o: o.deleted == ["old-a", "stopped-old", "old-b"]),
    ("state-empty-list-matches-nothing", lambda o: o.deleted == []),
    ("state-exact-not-substring", lambda o: o.deleted == []),
    ("state-custom-ignored-by-stop", lambda o: o.stopped == ["old-a", "old-b"]),
    ("age-exactly-at-threshold", lambda o: o.deleted == ["d"]),
    ("age-one-second-short", lambda o: o.deleted == []),
    ("age-empty-matches-everything", lambda o: o.deleted == ["d"]),
    ("age-none-matches-everything", lambda o: o.deleted == ["d"]),
    ("age-zero-days", lambda o: o.deleted == ["d"]),
    ("age-future-creation", lambda o: o.deleted == []),
    ("age-max-age-shortens", lambda o: o.deleted == ["d"]),
    ("age-max-age-invalid-raises", _err("ValueError")),
    ("age-timestamp-without-fraction", _err("ValueError")),
    ("age-timestamp-none-raises", _err("TypeError")),
    ("age-error-after-earlier-delete", lambda o: o.error is not None and o.deleted == ["ok"]),
    ("age-error-on-wrong-state-ignored", lambda o: o.error is None and o.deleted == []),
    ("custom-age-keeps-young", lambda o: o.deleted == []),
    ("custom-age-shortens", lambda o: o.deleted == ["d"]),
    ("custom-age-empty-falls-back", lambda o: o.deleted == []),
    ("custom-age-unparseable-falls-back", lambda o: o.deleted == []),
    ("custom-age-zero-falls-back-to-default", lambda o: o.deleted == []),
    ("filter-all-keys", lambda o: o.deleted == ["a"]),
    ("filter-one-key-missing-fails", lambda o: o.deleted == []),
    ("filter-empty-values-means-key-present", lambda o: o.deleted == ["a", "b", "d"]),
    ("filter-string-value-is-substring", lambda o: o.deleted == ["a", "b", "d"]),
    ("untagged-with-filter-excluded", lambda o: o.deleted == []),
    ("untagged-without-filter-included", lambda o: o.deleted == ["u"]),
    ("exception-label-any-key", lambda o: o.deleted == ["d", "u"]),
    ("notags-all-keys-skips", lambda o: o.deleted == ["b", "c", "d", "u"]),
    ("notags-partial-match-kept", lambda o: len(o.deleted) == THREE + TWO),
    ("notags-single-key-skips", lambda o: o.deleted == ["b", "c", "u"]),
    ("notags-untagged-kept", lambda o: o.deleted == ["u"]),
    ("exception-wins-over-filter", lambda o: o.deleted == ["a", "d"]),
    ("labels-on-wrong-state-ignored", lambda o: o.error is None and o.deleted == []),
    ("delete-error-swallowed", lambda o: o.error is None and o.deleted == ["a", "c"]),
    (
        "delete-error-all-fail",
        lambda o: (
            o.error is None
            and o.deleted == []
            and "No GCP instances to delete." in [m for _, _, m in o.logs]
        ),
    ),
    ("delete-error-ignored-in-dry-run", lambda o: o.deleted == ["a", "b", "c"]),
    ("stop-error-swallowed", lambda o: o.error is None and o.stopped == ["a", "c"]),
    (
        "stop-error-all-fail",
        lambda o: "No GCP instances to stop." in [m for _, _, m in o.logs] and o.stopped == [],
    ),
    ("duplicate-names", lambda o: o.deleted == ["dup", "dup"]),
    ("same-name-two-zones", lambda o: o.deleted == ["dup", "dup"]),
    ("zones-empty", lambda o: o.deleted == [] and _only(o.calls, "instances.list") == []),
    ("build-error", lambda o: o.error is not None and len(o.calls) == 1),
    ("zones-list-permission-denied", lambda o: o.error == ("PermissionDenied", "403 denied")),
    ("zones-list-connection-error", lambda o: o.error == ("ConnectionError", "down")),
    ("zones-response-without-items", lambda o: o.error == ("KeyError", "'items'")),
    ("zones-response-items-empty", lambda o: o.error is None and o.deleted == []),
    (
        "zone-without-name-after-first-zone",
        lambda o: o.error is not None and o.deleted == ["a1", "a2"],
    ),
    ("client-credentials-error", lambda o: o.error is not None and ("close",) in o.calls),
    ("instances-list-permission-denied", lambda o: o.error is not None and o.deleted == []),
    ("instances-list-error-after-first-zone", lambda o: o.error is not None and o.deleted == ["a"]),
    ("instances-iteration-error", lambda o: o.error is not None and o.deleted == ["a", "b"]),
    ("instance-without-labels-attribute", lambda o: o.error is not None and "labels" in o.error[1]),
    ("instance-without-labels-wrong-state", lambda o: o.error is None and o.deleted == []),
    ("instance-without-status-attribute", lambda o: o.error is not None and "status" in o.error[1]),
    ("young-instance-without-name", lambda o: o.error is None and o.deleted == []),
]


BUG_004_FIXED = frozenset(
    [
        "custom-age-empty-falls-back",
        "custom-age-unparseable-falls-back",
        "custom-age-zero-falls-back-to-default",
    ]
)


ENGINE_QUIRKS: Mapping[str, Callable[[Outcome], bool]] = {
    # Allowed change: every zone is listed before the first delete, so the zone without a name
    # stops the run before the first zone is deleted (fewer deletes, never more).
    "zone-without-name-after-first-zone": lambda o: o.error is not None and o.deleted == [],
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
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    # BUG-004/BUG-016: the oracle class inherits the live crc.service.Service, so it cannot
    # show the old custom-age-tag behaviour here; tests/test_crc_service_parity.py pins it
    # against the frozen crc_service oracle. Fixed cases assert the new outcome only.
    if case_id not in BUG_004_FIXED:
        assert check(old), "oracle no longer shows the quirk"
    if case_id in BUG_183:
        _assert_bug_183(old, new)
    elif case_id in FIX_186:
        assert new.error is None
        assert _done(new) == _fixed_done(case)
    else:
        assert ENGINE_QUIRKS.get(case_id, check)(new)


def test_service_is_closed_before_instances_are_listed_like_main(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("two-zones")
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    names = [c[0] for c in new.calls]
    assert names.index("close") < names.index("instances.list")
    assert names.count("close") == 1
    # Allowed change: every zone is listed before the first delete.
    assert names.index("instances.delete.execute") > len(names) - 1 - names[::-1].index(
        "instances.list"
    )
    kept = ("instances.list", "close")
    assert _only(new.calls, *kept) == _only(old.calls, *kept)
    assert _only(new.calls, *ACTION_CALLS) == _only(_with_waits(old.calls, case), *ACTION_CALLS)


@pytest.mark.parametrize("first", ["delete", "stop"])
def test_consecutive_runs_act_like_main(
    first: str,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("two-zones")
    gcp = _setup(case, monkeypatch, caplog, capsys)
    vm = _legacy(case)
    getattr(vm, first)()
    vm.delete()
    vm.stop()
    old_actions = _only(_with_waits(gcp.calls, case), *ACTION_CALLS)
    gcp = _setup(case, monkeypatch, caplog, capsys)
    registry = Registry()
    registry.register("gcp", "vm")(build_vm_cleaner)
    for operation in (first, "delete", "stop"):
        replace_op = Operation.STOP if operation == "stop" else Operation.DELETE
        ctx = replace(_cleaner_context(case), operation=replace_op)
        engine.run(Selection((("gcp", "vm"),)), ctx, registry)
    assert _only(gcp.calls, *ACTION_CALLS) == old_actions


def test_registered_factory_builds_cleaner() -> None:
    ctx = RunContext(
        dry_run=True, operation=Operation.DELETE, options={"project_id": PROJECT}, now=NOW
    )
    cleaner: Cleaner = get("gcp", "vm")(ctx)
    assert isinstance(cleaner, VmCleaner)
    assert get("gcp", "vm") is build_vm_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("gcp", "vm")
    assert cleaner.supports(Operation.DELETE)
    assert cleaner.supports(Operation.STOP)


def test_registered_factory_requires_project_id() -> None:
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    with pytest.raises(ValueError, match="project_id is required"):
        get("gcp", "vm")(ctx)


def test_bug_358_label_dump_is_debug_not_info(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("mixed-deletes-old-running")
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    assert old.logs != new.logs
    expected = expected_logs(old.logs, BUGS, RULES)[:-1]
    assert Counter(new.logs) == Counter(expected)
    assert Counter(m for _, _, m in new.logs) == Counter(m for _, _, m in old.logs[:-1])
    assert _only(new.calls, *ACTION_CALLS) == _only(_with_waits(old.calls, case), *ACTION_CALLS)
    assert new.stdout == old.stdout
    info = [m for _, level, m in new.logs if level == logging.INFO]
    assert not [m for m in info if m.startswith(("{", "namespace("))]
    dumps = [m for _, level, m in old.logs if level == logging.INFO]
    assert any(m.startswith(("{", "namespace(")) for m in dumps)


def test_default_instance_state_matches_main() -> None:
    old = _load_oracle().VM
    settings = VmSettings(PROJECT, None, None, None, DAY, "")
    assert list(settings.instance_state) == list(old.default_instance_state)
