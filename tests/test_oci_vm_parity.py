"""Parity: `crc.oci.vm.VM` and the registered Cleaner against main's version
(`tests/legacy/oci_vm.txt`).

All run against the same fake OCI SDK; SDK calls (in order), results, log records, stdout and
raised errors must be identical for every case, for `delete()` and for `stop()`. The Cleaner
path runs `plan()` then `apply()` (only `plan()` in a dry run), so reads happen before the
terminate/stop calls: its reads and its mutations are each compared in order.
"""

import datetime
import logging
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import oci
import pytest
from oci.exceptions import ConnectTimeout, RequestException, ServiceError

from crc.core.cleaner import Operation, RunContext
from crc.oci.vm import VM, VmRuntime, build_vm_cleaner
from tests.helpers_log_fix import expected_logs
from tests.log_fix_oci import RULES

BUGS = {"BUG-358"}
ORACLE = Path(__file__).parent / "legacy" / "oci_vm.txt"
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)
NOW_UTC = NOW.astimezone(datetime.UTC)
HOME = "us-sanjose-1"
OPERATIONS = ("delete", "stop")
MUTATING = ("terminate_instance", "instance_action")


def inst(
    inst_id: str,
    name: str | None = None,
    hours: float | None = 100,
    state: str = "RUNNING",
    tags: Any = "default",
) -> Any:
    created = None if hours is None else NOW_UTC - datetime.timedelta(hours=hours)
    return types.SimpleNamespace(
        id=inst_id,
        display_name=inst_id if name is None else name,
        lifecycle_state=state,
        freeform_tags={"env": "ci"} if tags == "default" else tags,
        time_created=created,
    )


def conn_error() -> Exception:
    error: Exception = ConnectTimeout(Exception("timed out"))
    return error


def request_error() -> Exception:
    error: Exception = RequestException(Exception("no route"))
    return error


def service_error(status: int = 503, code: str = "ServiceUnavailable") -> Exception:
    error: Exception = ServiceError(status, code, {}, "service said no")
    return error


@dataclass
class Region:
    instances: Any = field(default_factory=list)
    action_errors: Mapping[str, Exception] = field(default_factory=dict)
    client_error: Exception | None = None


@dataclass
class Case:
    id: str
    regions: Mapping[str, Region]
    filter_tags: Any = field(default_factory=lambda: {"env": ["ci"]})
    exception_tags: Any = field(default_factory=dict)
    notags: Any = field(default_factory=dict)
    age: Any = field(default_factory=lambda: {"hours": 5})
    custom_age_tag_key: Any = ""
    dry_run: bool = False
    max_age: str | None = None
    regions_error: Exception | None = None
    states: list[str] | None = None


def one(**kwargs: Any) -> Mapping[str, Region]:
    return {"r1": Region(**kwargs)}


def custom_tags(ttl: str) -> dict[str, str]:
    return {"env": "ci", "ttl": ttl}


CASES = [
    Case("dry-run", one(instances=[inst("a"), inst("b", hours=1), inst("c")]), dry_run=True),
    Case("act-on-matching", one(instances=[inst("a"), inst("b", hours=1), inst("c")])),
    Case("two-regions", {"r1": Region(instances=[inst("a")]), "r2": Region(instances=[inst("b")])}),
    Case("empty-region", one()),
    Case("empty-region-dry-run", one(), dry_run=True),
    Case("no-regions", {}),
    Case("no-regions-dry-run", {}, dry_run=True),
    Case("nothing-matches", one(instances=[inst("a", hours=1), inst("b", tags={})])),
    Case(
        "state-not-running",
        one(
            instances=[
                inst("a", state="STOPPED"),
                inst("b", state="TERMINATED"),
                inst("c", state="PROVISIONING"),
                inst("d", state="STARTING"),
                inst("e"),
            ]
        ),
    ),
    Case(
        "state-match-is-case-sensitive",
        one(instances=[inst("a", state="running"), inst("b", state="RUNNING ")]),
    ),
    Case(
        "custom-states",
        one(instances=[inst("a", state="STOPPED"), inst("b"), inst("c", state="TERMINATED")]),
        states=["RUNNING", "STOPPED"],
    ),
    Case("custom-states-empty", one(instances=[inst("a")]), states=[]),
    Case(
        "no-filter-includes-everything",
        one(instances=[inst("a", tags={}), inst("b")]),
        filter_tags={},
    ),
    Case(
        "filter-none-includes-everything", one(instances=[inst("a", tags=None)]), filter_tags=None
    ),
    Case("untagged-with-filter-skipped", one(instances=[inst("a", tags=None), inst("b", tags={})])),
    Case(
        "untagged-without-filter-included",
        one(instances=[inst("a", tags=None), inst("b", tags={})]),
        filter_tags={},
    ),
    Case(
        "filter-all-keys",
        one(
            instances=[
                inst("a", tags={"x": "1", "y": "2"}),
                inst("b", tags={"x": "1"}),
                inst("c", tags={"y": "2"}),
                inst("d", tags={"x": "1", "y": "0"}),
            ]
        ),
        filter_tags={"x": ["1"], "y": ["2"]},
    ),
    Case(
        "filter-several-values",
        one(
            instances=[
                inst("a", tags={"x": "1"}),
                inst("b", tags={"x": "2"}),
                inst("c", tags={"x": "3"}),
            ]
        ),
        filter_tags={"x": ["1", "2"]},
    ),
    Case(
        "filter-empty-value-list-means-key-present",
        one(instances=[inst("a", tags={"x": "anything"}), inst("b", tags={"z": "1"})]),
        filter_tags={"x": []},
    ),
    Case("filter-value-case-sensitive", one(instances=[inst("a", tags={"env": "CI"})])),
    Case("filter-key-case-sensitive", one(instances=[inst("a", tags={"ENV": "ci"})])),
    Case("filter-no-match", one(instances=[inst("a", tags={"env": "prod"})])),
    Case(
        "filter-string-spec-is-substring-test",
        one(instances=[inst("a", tags={"env": "c"}), inst("b", tags={"env": ""}), inst("c")]),
        filter_tags={"env": "ci"},
    ),
    Case(
        "exception-any-key",
        one(instances=[inst("a", tags={"env": "ci", "keep": "1"}), inst("b")]),
        exception_tags={"keep": ["1"], "other": []},
    ),
    Case(
        "exception-empty-value-list",
        one(instances=[inst("a", tags={"env": "ci", "keep": "x"}), inst("b")]),
        exception_tags={"keep": []},
    ),
    Case(
        "exception-wins-over-filter",
        one(instances=[inst("a", tags={"env": "ci"})]),
        exception_tags={"env": ["ci"]},
    ),
    Case(
        "notags-all-keys-skips",
        one(
            instances=[
                inst("a", tags={"env": "ci", "k1": "v", "k2": "v"}),
                inst("b", tags={"env": "ci", "k1": "v"}),
            ]
        ),
        notags={"k1": ["v"], "k2": []},
    ),
    Case(
        "notags-value-mismatch-not-skipped",
        one(instances=[inst("a", tags={"env": "ci", "k1": "other"})]),
        notags={"k1": ["v"]},
    ),
    Case("exception-none", one(instances=[inst("a")]), exception_tags=None),
    Case("notags-none", one(instances=[inst("a")]), notags=None),
    Case(
        "untagged-with-exception-and-no-filter",
        one(instances=[inst("a", tags=None)]),
        filter_tags={},
        exception_tags={"keep": ["1"]},
        notags={"k": ["v"]},
    ),
    Case("age-equal-hours", one(instances=[inst("a", hours=5)])),
    Case("age-just-under-hours", one(instances=[inst("a", hours=4.99)])),
    Case("age-just-over-hours", one(instances=[inst("a", hours=5.01)])),
    Case(
        "age-days",
        one(instances=[inst("a", hours=48), inst("b", hours=47.9)]),
        age={"days": 2},
    ),
    Case(
        "age-days-and-hours",
        one(instances=[inst("a", hours=30), inst("b", hours=29.9)]),
        age={"days": 1, "hours": 6},
    ),
    Case("age-int-days", one(instances=[inst("a", hours=25), inst("b", hours=23)]), age=1),
    Case("age-empty-matches-all", one(instances=[inst("a", hours=0.1)]), age={}),
    Case("age-none-matches-all", one(instances=[inst("a", hours=0.1)]), age=None),
    Case("age-unknown-keys-keep-all", one(instances=[inst("a", hours=9999)]), age={"weeks": 1}),
    Case(
        "max-age-shortens",
        one(instances=[inst("a", hours=3), inst("b", hours=1)]),
        age={"days": 30},
        max_age="{'hours': 2}",
    ),
    Case("max-age-empty-dict", one(instances=[inst("a", hours=3)]), age={"days": 1}, max_age="{}"),
    Case("max-age-not-a-dict", one(instances=[inst("a", hours=3)]), age={"days": 1}, max_age="2"),
    Case("created-in-the-future", one(instances=[inst("a", hours=-10)])),
    Case(
        "naive-created-time",
        one(instances=[types.SimpleNamespace(**{**inst("a").__dict__, "time_created": NOW})]),
    ),
    Case("missing-created-time", one(instances=[inst("a", hours=None)])),
    Case(
        "custom-age-tag-shortens",
        one(instances=[inst("a", hours=3, tags=custom_tags("{'hours': 2}")), inst("b", hours=3)]),
        age={"days": 5},
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-lengthens",
        one(instances=[inst("a", hours=30, tags=custom_tags("{'days': 3}"))]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-unparseable-falls-back-to-age",
        one(instances=[inst("a", hours=30, tags=custom_tags("{'days': "))]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-empty-falls-back-to-age",
        one(instances=[inst("a", hours=30, tags=custom_tags(""))]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-zero-days-falls-back-to-age",
        one(instances=[inst("a", hours=30, tags=custom_tags("{}"))]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-key-missing-on-instance",
        one(instances=[inst("a", hours=30)]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-none-key",
        one(instances=[inst("a", hours=30, tags=custom_tags("{'days': 9}"))]),
        custom_age_tag_key=None,
    ),
    Case(
        "list-connection-error-skips-region",
        {"r1": Region(instances=conn_error()), "r2": Region(instances=[inst("c")])},
    ),
    Case(
        "list-request-error-skips-region",
        {"r1": Region(instances=request_error()), "r2": Region(instances=[inst("c")])},
    ),
    Case(
        "list-auth-error-skips-region",
        {
            "r1": Region(instances=service_error(401, "NotAuthenticated")),
            "r2": Region(instances=[inst("c")]),
        },
    ),
    Case(
        "list-error-in-every-region",
        {"r1": Region(instances=conn_error()), "r2": Region(instances=service_error())},
    ),
    Case(
        "client-creation-connection-error-skips-region",
        {"r1": Region(client_error=conn_error()), "r2": Region(instances=[inst("c")])},
    ),
    Case(
        "client-creation-unexpected-error",
        {"r1": Region(client_error=ValueError("bad config")), "r2": Region(instances=[inst("c")])},
    ),
    Case(
        "list-unexpected-error",
        {"r1": Region(instances=ValueError("boom")), "r2": Region(instances=[inst("c")])},
    ),
    Case(
        "action-error-swallowed-and-logged",
        one(
            instances=[inst("a"), inst("b"), inst("c")],
            action_errors={"b": RuntimeError("busy")},
        ),
    ),
    Case(
        "action-service-error-swallowed",
        one(
            instances=[inst("a"), inst("b")],
            action_errors={
                "a": service_error(409, "Conflict"),
                "b": service_error(404, "NotFound"),
            },
        ),
    ),
    Case(
        "action-error-in-dry-run-never-called",
        one(instances=[inst("a")], action_errors={"a": RuntimeError("busy")}),
        dry_run=True,
    ),
    Case(
        "all-actions-fail",
        one(
            instances=[inst("a"), inst("b")],
            action_errors={"a": ValueError("x"), "b": ValueError("y")},
        ),
    ),
    Case(
        "action-error-then-other-region",
        {
            "r1": Region(instances=[inst("a")], action_errors={"a": RuntimeError("busy")}),
            "r2": Region(instances=[inst("b")]),
        },
    ),
    Case(
        "duplicate-display-names",
        one(instances=[inst("a", "same"), inst("b", "same"), inst("c", "same")]),
    ),
    Case(
        "display-name-none",
        one(instances=[types.SimpleNamespace(**{**inst("a").__dict__, "display_name": None})]),
    ),
    Case(
        "unexpected-error-in-later-instance-after-action",
        one(instances=[inst("a"), inst("b", hours=None), inst("c")]),
    ),
    Case(
        "unexpected-error-in-later-region",
        {"r1": Region(instances=[inst("a")]), "r2": Region(instances=[inst("b", hours=None)])},
    ),
    Case(
        "many-instances",
        {
            "r1": Region(instances=[inst(f"v{i}", hours=50 + i) for i in range(25)]),
            "r2": Region(instances=[inst(f"w{i}") for i in range(5)]),
        },
    ),
    Case("many-instances-dry-run", one(instances=[inst(f"v{i}") for i in range(25)]), dry_run=True),
    Case(
        "regions-connection-error-falls-back",
        one(instances=[inst("a")]),
        regions_error=conn_error(),
    ),
    Case(
        "regions-auth-error-falls-back",
        {HOME: Region(instances=[inst("a")])},
        regions_error=service_error(401, "NotAuthenticated"),
    ),
    Case(
        "regions-request-error-falls-back",
        {HOME: Region(instances=[inst("a")])},
        regions_error=request_error(),
    ),
    Case(
        "regions-unexpected-error-propagates",
        one(instances=[inst("a")]),
        regions_error=ValueError("boom"),
    ),
]


class FakeCompute:
    def __init__(self, world: "FakeOci", region: str) -> None:
        self.world = world
        self.region = region

    def list_instances(self, compartment_id: str) -> Any:
        self.world.calls.append(("list_instances", self.region, compartment_id))
        outcome = self.world.region(self.region).instances
        if isinstance(outcome, Exception):
            raise outcome
        return types.SimpleNamespace(data=outcome)

    def terminate_instance(self, instance_id: str) -> None:
        self.world.calls.append(("terminate_instance", self.region, instance_id))
        self._maybe_fail(instance_id)

    def instance_action(self, instance_id: str, action: str) -> None:
        self.world.calls.append(("instance_action", self.region, instance_id, action))
        self._maybe_fail(instance_id)

    def _maybe_fail(self, instance_id: str) -> None:
        error = self.world.region(self.region).action_errors.get(instance_id)
        if error is not None:
            raise error


class FakeIdentity:
    def __init__(self, world: "FakeOci", region: str) -> None:
        self.world = world
        self.region = region

    def list_region_subscriptions(self, tenancy_id: str) -> Any:
        self.world.calls.append(("list_region_subscriptions", self.region, tenancy_id))
        if self.world.case.regions_error is not None:
            raise self.world.case.regions_error
        names = [types.SimpleNamespace(region_name=r) for r in self.world.case.regions]
        return types.SimpleNamespace(data=names)


class FakeOci:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []

    def region(self, name: str) -> Region:
        return self.case.regions[name]

    def compute(self, config: Mapping[str, Any]) -> FakeCompute:
        self.calls.append(("ComputeClient", config["region"]))
        client_error = self.region(config["region"]).client_error
        if client_error is not None:
            raise client_error
        return FakeCompute(self, config["region"])

    def identity(self, config: Mapping[str, Any]) -> FakeIdentity:
        self.calls.append(("IdentityClient", config["region"]))
        return FakeIdentity(self, config["region"])

    def paginate(self, fn: Any, **kwargs: Any) -> Any:
        self.calls.append(("list_call_get_all_results", fn.__name__))
        return fn(**kwargs)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(oci.core, "ComputeClient", self.compute)
        monkeypatch.setattr(oci.identity, "IdentityClient", self.identity)
        monkeypatch.setattr(oci.pagination, "list_call_get_all_results", self.paginate)


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    acted: list[Any] | None
    count: int | None
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_oci_vm")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    module.__dict__["datetime"] = types.SimpleNamespace(now=lambda: NOW)
    return module


def _legacy(case: Case) -> Any:
    return _load_oracle().VM(
        case.dry_run,
        case.filter_tags,
        case.exception_tags,
        case.age,
        case.custom_age_tag_key,
        case.notags,
    )


def _new(case: Case) -> Any:
    return VM(
        case.dry_run,
        case.filter_tags,
        case.exception_tags,
        case.age,
        case.custom_age_tag_key,
        case.notags,
        VmRuntime(clock=lambda: NOW),
    )


def _prepare(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeOci:
    oci_world = FakeOci(case)
    oci_world.install(monkeypatch)
    monkeypatch.setenv("OCI_PRIVATE_KEY_CONTENT", "not-a-key")
    monkeypatch.delenv("OCI_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    if case.max_age is None:
        monkeypatch.delenv("MAX_AGE", raising=False)
    else:
        monkeypatch.setenv("MAX_AGE", case.max_age)
    caplog.clear()
    capsys.readouterr()
    return oci_world


def _invoke(vm: Any, case: Case, operation: str) -> None:
    if operation == "stop":
        vm.stop()
    elif case.states is None:
        vm.delete()
    else:
        vm.delete(case.states)


def _run(
    build: Callable[[Case], Any],
    case: Case,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    world = _prepare(case, monkeypatch, caplog, capsys)
    vm = build(case)
    error: tuple[str, str] | None = None
    try:
        _invoke(vm, case, operation)
    except Exception as e:
        error = (type(e).__name__, str(e))
    if operation == "stop":
        acted, count = list(vm.get_stopped), vm.stopped_count
    else:
        acted, count = list(vm.get_deleted), vm.delete_count
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(world.calls, acted, count, logs, capsys.readouterr().out, error)


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


PARAMS = [(case, op) for case in CASES for op in OPERATIONS]
PARAM_IDS = [f"{case.id}-{op}" for case, op in PARAMS]


@pytest.mark.parametrize(("case", "operation"), PARAMS, ids=PARAM_IDS)
def test_adapter_matches_main(
    case: Case,
    operation: str,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, operation, monkeypatch, caplog, capsys)
    new = _run(_new, case, operation, monkeypatch, caplog, capsys)
    assert new.calls == old.calls
    assert new.error == old.error
    assert new.acted == old.acted
    assert new.count == old.count
    assert new.logs == expected_logs(old.logs, BUGS, RULES)
    assert new.stdout == old.stdout


def _run_cleaner(
    case: Case,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    world = _prepare(case, monkeypatch, caplog, capsys)
    options: dict[str, Any] = {
        "filter_tags": case.filter_tags,
        "exception_tags": case.exception_tags,
        "notags": case.notags,
        "age": case.age,
        "custom_age_tag_key": case.custom_age_tag_key,
    }
    if case.states is not None:
        options["instance_state"] = case.states
    context = RunContext(
        dry_run=case.dry_run,
        operation=Operation(operation),
        options=options,
        now=NOW,
    )
    error: tuple[str, str] | None = None
    acted: list[str] = []
    try:
        cleaner = build_vm_cleaner(context)
        plan = cleaner.plan(context)
        if case.dry_run:
            acted = [c.name for c in plan.candidates]
        else:
            acted = list(cleaner.apply(plan, context).succeeded)
    except Exception as e:
        error = (type(e).__name__, str(e))
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(world.calls, acted, len(acted), logs, capsys.readouterr().out, error)


def _is_mutation(call: tuple[Any, ...]) -> bool:
    return call[0] in MUTATING


def _is_apply_log(log: tuple[str, int, str]) -> bool:
    return log[2].startswith(("Deleting instance ", "Stopping instance ", "Error occurred while "))


def _is_summary_log(log: tuple[str, int, str]) -> bool:
    return log[2].startswith(
        ("No OCI instances to ", "number of OCI instances", "List of OCI instances")
    )


@pytest.mark.parametrize(("case", "operation"), PARAMS, ids=PARAM_IDS)
def test_cleaner_protocol_matches_main(
    case: Case,
    operation: str,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, operation, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, operation, monkeypatch, caplog, capsys)
    assert new.error == old.error
    assert new.stdout == old.stdout
    if old.error is not None:
        # main may already have acted on instances before the error; plan() fails before any
        assert [c for c in new.calls if _is_mutation(c)] == []
        return
    assert new.acted == old.acted
    assert [c for c in new.calls if not _is_mutation(c)] == [
        c for c in old.calls if not _is_mutation(c)
    ]
    assert [c for c in new.calls if _is_mutation(c)] == [c for c in old.calls if _is_mutation(c)]
    old_logs = [
        log
        for log in expected_logs(old.logs, BUGS, RULES)
        if not log[2].startswith("count of items")
    ]
    plan_logs = [log for log in old_logs if not _is_apply_log(log) and not _is_summary_log(log)]
    apply_logs = [log for log in old_logs if _is_apply_log(log)]
    summary_logs = [log for log in old_logs if _is_summary_log(log)]
    assert new.logs == plan_logs + apply_logs + summary_logs


def _sequence(build: Callable[[Case], Any], case: Case) -> tuple[list[Any], list[Any], Any]:
    vm = build(case)
    error: Any = None
    try:
        vm.delete()
        vm.stop()
        vm.delete()
    except Exception as e:
        error = (type(e).__name__, str(e))
    return list(vm.get_deleted), list(vm.get_stopped), error


@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "dry-run"])
def test_delete_then_stop_then_delete_shares_name_lists_as_main(
    dry_run: bool,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = Case(
        "sequence",
        one(instances=[inst("a"), inst("b", hours=1)]),
        dry_run=dry_run,
    )
    world = _prepare(case, monkeypatch, caplog, capsys)
    old = _sequence(_legacy, case)
    old_calls, old_logs = list(world.calls), [(r.levelno, r.getMessage()) for r in caplog.records]
    world = _prepare(case, monkeypatch, caplog, capsys)
    new = _sequence(_new, case)
    new_logs = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert new == old
    assert world.calls == old_calls
    assert new_logs == expected_logs(old_logs, BUGS, RULES)


QUIRKS: Sequence[tuple[str, str, Callable[[Outcome], bool]]] = [
    ("list-auth-error-skips-region", "delete", lambda o: o.acted == ["c"] and o.error is None),
    ("list-error-in-every-region", "delete", lambda o: o.acted == [] and o.error is None),
    ("regions-auth-error-falls-back", "delete", lambda o: o.acted == ["a"]),
    ("regions-unexpected-error-propagates", "delete", lambda o: o.error is not None),
    (
        "action-error-swallowed-and-logged",
        "delete",
        lambda o: o.error is None and o.acted == ["a", "c"],
    ),
    (
        "action-error-swallowed-and-logged",
        "stop",
        lambda o: o.error is None and o.acted == ["a", "c"],
    ),
    ("all-actions-fail", "delete", lambda o: o.error is None and o.acted == []),
    ("age-empty-matches-all", "delete", lambda o: o.acted == ["a"]),
    ("age-none-matches-all", "stop", lambda o: o.acted == ["a"]),
    ("age-unknown-keys-keep-all", "delete", lambda o: o.acted == []),
    ("max-age-shortens", "delete", lambda o: o.acted == ["a"]),
    ("max-age-empty-dict", "delete", lambda o: o.acted == []),
    ("max-age-not-a-dict", "delete", lambda o: o.error is not None),
    ("custom-age-tag-unparseable-falls-back-to-age", "delete", lambda o: o.acted == ["a"]),
    ("custom-age-tag-empty-falls-back-to-age", "delete", lambda o: o.acted == ["a"]),
    ("custom-age-tag-zero-days-falls-back-to-age", "delete", lambda o: o.acted == ["a"]),
    ("untagged-with-filter-skipped", "delete", lambda o: o.acted == []),
    ("untagged-without-filter-included", "delete", lambda o: o.acted == ["a", "b"]),
    ("filter-all-keys", "delete", lambda o: o.acted == ["a"]),
    ("filter-empty-value-list-means-key-present", "delete", lambda o: o.acted == ["a"]),
    ("filter-string-spec-is-substring-test", "delete", lambda o: o.acted == ["a", "b", "c"]),
    ("exception-wins-over-filter", "delete", lambda o: o.acted == []),
    ("state-not-running", "delete", lambda o: o.acted == ["e"]),
    ("state-match-is-case-sensitive", "delete", lambda o: o.acted == []),
    ("custom-states-empty", "delete", lambda o: o.acted == []),
    ("naive-created-time", "delete", lambda o: o.error is not None and o.error[0] == "TypeError"),
    (
        "missing-created-time",
        "delete",
        lambda o: o.error is not None and o.error[0] == "AttributeError",
    ),
    (
        "unexpected-error-in-later-instance-after-action",
        "delete",
        lambda o: o.error is not None and ("terminate_instance", "r1", "a") in o.calls,
    ),
    (
        "unexpected-error-in-later-instance-after-action",
        "stop",
        lambda o: o.error is not None and ("instance_action", "r1", "a", "STOP") in o.calls,
    ),
    ("duplicate-display-names", "delete", lambda o: o.acted == ["same", "same", "same"]),
    ("display-name-none", "stop", lambda o: o.acted == [None]),
    (
        "action-error-in-dry-run-never-called",
        "delete",
        lambda o: o.acted == ["a"] and not [c for c in o.calls if c[0] in MUTATING],
    ),
]


@pytest.mark.parametrize("quirk", QUIRKS, ids=[f"{q[0]}-{q[1]}" for q in QUIRKS])
def test_quirk_is_preserved(
    quirk: tuple[str, str, Callable[[Outcome], bool]],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case_id, operation, check = quirk
    case = next(c for c in CASES if c.id == case_id)
    old = _run(_legacy, case, operation, monkeypatch, caplog, capsys)
    new = _run(_new, case, operation, monkeypatch, caplog, capsys)
    assert check(old), "oracle no longer shows the quirk"
    assert check(new)


def test_vm_diverges_from_oracle(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """BUG-358: new logs differ from the oracle by a level only."""
    changed = 0
    for case, operation in PARAMS:
        old = _run(_legacy, case, operation, monkeypatch, caplog, capsys)
        new = _run(_new, case, operation, monkeypatch, caplog, capsys)
        if old.logs == new.logs:
            continue
        changed += 1
        assert [log[2] for log in new.logs] == [log[2] for log in old.logs]
        assert new.calls == old.calls
        assert {log[1] for log in new.logs if log not in old.logs} == {logging.DEBUG}
    assert changed > 0
