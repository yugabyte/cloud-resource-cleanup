"""Parity: the engine bridge against the oracle `CRC` methods for the AWS kinds
routed through the engine.

The oracle (`tests/legacy/crc_main.txt`) runs `delete_ip`, `delete_kms`, `delete_vm`,
`stop_vm`, `delete_spot_instance_requests` and `delete_disks` of the AWS cloud with a fake adapter;
the bridge runs the engine with a fake cleaner that plans and applies the same ids. Both use the
same fake Slack and Influx clients: the Slack calls, the Influx calls, the printed output and the
raised error must be equal in every Slack and Influx mode, in a dry run and when the adapter
raises (`disk` reports first, as `delete_disks` did; the others send nothing).
"""

from collections.abc import Callable
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from types import ModuleType
from typing import Any

import pytest

from crc.cli import bridge
from crc.cli.inputs import Targets, read_options
from crc.cli.routes import NotifyMode
from crc.cli.runner import CRC as NEW_CRC
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext
from crc.core.registry import Registry
from tests.helpers_cl2_aws import (
    AGE,
    EMPTY,
    FULL,
    Case,
    Logs,
    Scenario,
    build,
    make_fake,
    oracle,
    scenario_grid,
)

__all__ = ["oracle"]

TAGS = {"t": ["v"]}


@dataclass(frozen=True)
class Kind:
    """One routed AWS kind: the oracle call and the flags the bridge is given."""

    name: str
    adapter: str
    act: Callable[[Any], Any]
    flags: dict[str, Any]
    options: dict[str, Any]
    operation: str = "delete"
    notified: bool = True
    reports_before_raise: bool = False


COMMON = {"filter_tags": TAGS, "exception_tags": {}, "notags": {"owner": ["x"]}}
AGED = {**COMMON, "age": AGE, "custom_age_tag_key": "age_tag"}
KINDS = [
    Kind(
        "ip",
        "ElasticIPs",
        lambda c: c.delete_ip(TAGS, {}, ["n"], ["x"]),
        {"name_regex": ["n"], "exception_regex": ["x"]},
        dict(COMMON),
    ),
    Kind(
        "kms",
        "Kms",
        lambda c: c.delete_kms(TAGS, {}, "desc", "arn:u", 7, AGE, "age_tag"),
        {
            "kms_key_description": "desc",
            "kms_user": "arn:u",
            "kms_pending_window": 7,
            "age": AGE,
            "custom_age_tag_key": "age_tag",
        },
        {
            "filter_tags": TAGS,
            "exception_tags": {},
            "kms_key_description": "desc",
            "kms_user": "arn:u",
            "kms_pending_window": 7,
            "age": AGE,
            "custom_age_tag_key": "age_tag",
        },
    ),
    Kind(
        "vm",
        "AWS_VM",
        lambda c: c.delete_vm(TAGS, {}, AGE, "age_tag", ["running"]),
        {"age": AGE, "custom_age_tag_key": "age_tag", "resource_states": ["running"]},
        {**AGED, "instance_state": ["running"]},
    ),
    Kind(
        "vm",
        "AWS_VM",
        lambda c: c.stop_vm(TAGS, {}, AGE, "age_tag"),
        {"age": AGE, "custom_age_tag_key": "age_tag", "resource_states": ["running"]},
        dict(AGED),
        operation="stop",
    ),
    Kind(
        "spot_instance_requests",
        "SpotInstanceRequests",
        lambda c: c.delete_spot_instance_requests(TAGS, {}, AGE, "age_tag"),
        {"age": AGE, "custom_age_tag_key": "age_tag"},
        dict(AGED),
    ),
    Kind(
        "disk",
        "AWS_Disk",
        lambda c: c.delete_disks(TAGS, {}, AGE, "age_tag", {"days": 2}, ["r1"], ["r2"], True, "l"),
        {
            "age": AGE,
            "custom_age_tag_key": "age_tag",
            "detach_age": {"days": 2},
            "name_regex": ["r1"],
            "exception_regex": ["r2"],
        },
        {
            **AGED,
            "name_regex": ["r1"],
            "exception_regex": ["r2"],
            "detach_age": {"days": 2},
        },
        reports_before_raise=True,
    ),
]


class FakeCleaner:
    def __init__(self, kind: Kind, scenario: Scenario, seen: list[RunContext]) -> None:
        self.cloud = "aws"
        self.kind = kind.name
        self.spec = kind
        self.scenario = scenario
        self.seen = seen
        values = scenario.values
        attr = "get_stopped" if kind.operation == "stop" else "get_deleted"
        self.ids = list(values[attr])
        self.related = (
            {"Disk": tuple(values["volumes_to_delete"])}
            if kind.name == "spot_instance_requests"
            else {}
        )
        self.partial_succeeded: list[str] = []

    def supports(self, operation: Operation) -> bool:
        return True

    def _fail(self) -> None:
        if self.scenario.delete_error is not None:
            self.partial_succeeded = list(self.ids)
            raise self.scenario.delete_error

    def plan(self, context: RunContext) -> Plan:
        self.seen.append(context)
        if context.dry_run or not self.spec.reports_before_raise:
            self._fail()
        return Plan(candidates=tuple(Candidate(id=i) for i in self.ids), related=self.related)

    def apply(self, plan: Plan, context: RunContext) -> Result:
        self._fail()
        return Result(succeeded=tuple(c.id for c in plan.candidates), related=plan.related)


def _flags(kind: Kind, dry_run: bool) -> dict[str, Any]:
    return {
        "cloud": "aws",
        "resource": kind.name,
        "operation_type": kind.operation,
        "dry_run": dry_run,
        "filter_tags": TAGS,
        "exception_tags": {},
        "notags": {"owner": ["x"]},
        **kind.flags,
    }


def _run(case: Case, run: Callable[[Case, Logs], None]) -> dict[str, Any]:
    logs = Logs()
    out = StringIO()
    result: Any = ("ok",)
    with redirect_stdout(out):
        try:
            run(case, logs)
        except Exception as exc:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
            result = ("error", type(exc).__name__, str(exc))
    return {"result": result, "slack": logs.slack, "influx": logs.influx, "stdout": out.getvalue()}


def _legacy(kind: Kind, module: ModuleType, mp: pytest.MonkeyPatch) -> Callable[[Case, Logs], None]:
    def run(case: Case, logs: Logs) -> None:
        mp.setattr(module, kind.adapter, make_fake(kind.adapter, case.scenario, logs))
        kind.act(build(module.CRC, case, logs))

    return run


def _bridged(
    kind: Kind, mp: pytest.MonkeyPatch, seen: list[RunContext]
) -> Callable[[Case, Logs], None]:
    def run(case: Case, logs: Logs) -> None:
        registry = Registry()

        def factory(context: RunContext) -> Cleaner:
            return FakeCleaner(kind, case.scenario, seen)

        registry.register("aws", kind.name)(factory)
        mp.setattr(bridge, "init_run_logging", lambda: None)
        mp.setattr(bridge, "build_registry", lambda: registry)
        route = bridge.route_for("aws", kind.name)
        assert route is not None
        options = read_options(_flags(kind, bool(case.scenario.dry_run)))
        targets = Targets("aws", kind.name == "disk", ["aws"], [kind.name])
        bridge.run_routed(build(NEW_CRC, case, logs), "aws", kind.name, options, targets, route)

    return run


def _grid(kind: Kind) -> list[tuple[str, Scenario]]:
    cases: list[tuple[str, Scenario]] = []
    for data_name, data in (("full", FULL), ("empty", EMPTY)):
        for name, scenario in scenario_grid(data):
            if scenario.slack != "off" or scenario.influx != "off":
                cases.append((f"{data_name}-{name}", scenario))
        if kind.operation == "stop":
            continue
        cases.append((f"{data_name}-error", Scenario(dict(data), delete_error=RuntimeError("x"))))
        cases.append(
            (f"{data_name}-dry-error", Scenario(dict(data), True, delete_error=OSError("y")))
        )
    return cases


PARAMS = [
    pytest.param(kind, name, scenario, id=f"{kind.name}-{kind.operation}-{name}")
    for kind in KINDS
    for name, scenario in _grid(kind)
]


@pytest.mark.parametrize(("kind", "name", "scenario"), PARAMS)
def test_bridge_matches_the_oracle_crc_method(
    kind: Kind, name: str, scenario: Scenario, oracle: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = Case(name, "aws", lambda crc: None, scenario)
    old = _run(case, _legacy(kind, oracle, monkeypatch))
    seen: list[RunContext] = []
    new = _run(case, _bridged(kind, monkeypatch, seen))
    assert new == old
    assert len(seen) == 1
    assert seen[0].dry_run is bool(scenario.dry_run)
    assert seen[0].operation is Operation(kind.operation)
    assert dict(seen[0].options) == kind.options


def test_route_notify_modes() -> None:
    modes = {kind: route.notify for kind, route in bridge.TABLES["aws"].items()}
    assert "vpc" not in modes
    assert modes["snapshot"] is NotifyMode.NONE
    assert modes["disk"] is NotifyMode.REPORT_THEN_RERAISE
    assert {modes[k] for k in ("ip", "kms", "vm", "spot_instance_requests", "keypair")} == {
        NotifyMode.REPORT
    }


def test_report_then_reraise_notifies_before_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    disk = next(kind for kind in KINDS if kind.name == "disk")
    scenario = Scenario(dict(FULL), delete_error=RuntimeError("late"))
    logs = Logs()
    seen: list[RunContext] = []
    case = Case("late", "aws", lambda crc: None, scenario)
    with pytest.raises(RuntimeError, match="late"):
        _bridged(disk, monkeypatch, seen)(case, logs)
    assert logs.slack
    assert logs.influx


def test_extra_notifiers_run_after_slack_and_influx() -> None:
    sent: list[str] = []

    class Extra:
        name = "extra"
        fail_open = True

        def send(self, report: Any, context: RunContext) -> None:
            sent.append(report.cloud)

    base = bridge.TABLES["aws"]["ip"]
    route = type(base)(base.options, extra_notifiers=lambda crc: [Extra()])
    crc = NEW_CRC("aws", False, None, object(), object(), slack_channel="c", influxdb_conn={})
    assert [n.name for n in bridge.build_notifiers(crc, route)] == ["slack", "influx", "extra"]


@pytest.mark.parametrize("operation", ["delete", "stop"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_real_vm_cleaner_through_bridge_matches_oracle_report_names_and_ids(
    operation: str, dry_run: bool, oracle: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real `VmCleaner` through `bridge.run_routed` matches `oracle.CRC.delete_vm` / `stop_vm`:
    instance names in live delete/stop and dry-run delete, instance IDs in dry-run stop."""
    from datetime import UTC, datetime

    from crc.aws.vm.cleaner import VmCleaner, VmSettings
    from tests.test_aws_vm import FakeClient, instance

    client = FakeClient([instance("i-1", "vm"), instance("i-2", "vm")])
    monkeypatch.setattr(bridge, "init_run_logging", lambda: None)
    monkeypatch.setattr("crc.aws.vm.cleaner.boto3_client", lambda region: client)
    monkeypatch.setattr("crc.aws.vm.cleaner.all_regions", lambda: ["us-west-2"])
    kind = next(k for k in KINDS if k.name == "vm" and k.operation == operation)
    expected_ids = ["i-1", "i-2"] if (dry_run and operation == "stop") else ["vm-i-1", "vm-i-2"]
    scenario = Scenario(
        {**FULL, "get_deleted": expected_ids, "get_stopped": expected_ids},
        dry_run=dry_run,
    )
    case = Case(f"real-vm-{operation}-{dry_run}", "aws", lambda c: None, scenario)
    old = _run(case, _legacy(kind, oracle, monkeypatch))

    def run_real_vm(case: Case, logs: Logs) -> None:
        route = bridge.route_for("aws", "vm")
        assert route is not None
        options = read_options(_flags(kind, dry_run))
        targets = Targets("aws", False, ["aws"], ["vm"])
        registry = Registry()
        registry.register("aws", "vm")(
            lambda ctx: VmCleaner(
                lambda region: client,
                lambda: ["us-west-2"],
                VmSettings({}, {}, {}, {"days": 1}, None, ["running"]),
            )
        )
        monkeypatch.setattr(bridge, "build_registry", lambda: registry)
        now = datetime(2026, 3, 1, 12, tzinfo=UTC)
        clock = type("D", (), {"now": staticmethod(lambda tz=None: now)})
        monkeypatch.setattr(bridge, "datetime", clock)
        bridge.run_routed(build(NEW_CRC, case, logs), "aws", "vm", options, targets, route)

    new = _run(case, run_real_vm)
    assert new == old
    method = "terminate_instances" if operation == "delete" else "stop_instances"
    mutating = {"terminate_instances", "stop_instances"}
    mutations = [call for call in client.calls if call[0] in mutating]
    assert mutations == ([] if dry_run else [(method, ("i-1",)), (method, ("i-2",))])
