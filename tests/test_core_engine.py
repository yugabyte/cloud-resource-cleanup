"""Engine: plan/apply orchestration with fake cleaners and notifiers in an isolated registry."""

import datetime
import importlib.util
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from crc.cli import resource_ops, vm_ops
from crc.core.cleaner import Candidate, Operation, Plan, Result, RunContext
from crc.core.engine import KIND_LABELS, RunOutcome, run
from crc.core.registry import Registry, UnknownCleanerError
from crc.core.report import Report
from crc.core.selection import RESOURCES, Selection

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime.datetime(2026, 1, 1)


@pytest.fixture
def crc_cli() -> Iterator[ModuleType]:
    spec = importlib.util.spec_from_file_location("crc_cli_engine", ROOT / "crc.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["crc_cli_engine"] = module
    spec.loader.exec_module(module)
    yield module
    del sys.modules["crc_cli_engine"]


def make_context(dry_run: bool = False, operation: Operation = Operation.DELETE) -> RunContext:
    return RunContext(dry_run, operation, {}, NOW)


class FakeCleaner:
    def __init__(
        self,
        cloud: str,
        kind: str,
        events: list[str],
        supported: bool = True,
        fail_in: str | None = None,
    ) -> None:
        self.cloud = cloud
        self.kind = kind
        self.events = events
        self.supported = supported
        self.fail_in = fail_in
        if self.fail_in == "init":
            raise RuntimeError("init failed")

    def supports(self, operation: Operation) -> bool:
        self.events.append(f"{self.cloud}.{self.kind}.supports")
        return self.supported

    def plan(self, context: RunContext) -> Plan:
        self.events.append(f"{self.cloud}.{self.kind}.plan")
        if self.fail_in == "plan":
            raise RuntimeError("list failed")
        return Plan((Candidate(f"{self.cloud}-{self.kind}-1"),))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        self.events.append(f"{self.cloud}.{self.kind}.apply")
        if self.fail_in == "apply":
            raise RuntimeError("delete failed")
        return Result(tuple(c.id for c in plan.candidates))


class FakeNotifier:
    def __init__(
        self, name: str, events: list[str], fail_open: bool = False, error: Exception | None = None
    ) -> None:
        self.name = name
        self.fail_open = fail_open
        self.events = events
        self.error = error
        self.reports: list[Report] = []

    def send(self, report: Report, context: RunContext) -> None:
        self.events.append(f"notify.{self.name}.{report.cloud}")
        self.reports.append(report)
        if self.error is not None:
            raise self.error


def make_registry(
    events: list[str], pairs: list[tuple[str, str]], **kwargs: object
) -> Registry:
    registry = Registry()
    for cloud, kind in pairs:

        def factory(
            context: RunContext, cloud: str = cloud, kind: str = kind
        ) -> FakeCleaner:
            return FakeCleaner(cloud, kind, events, **kwargs)  # type: ignore[arg-type]

        registry.register(cloud, kind)(factory)
    return registry


def test_dry_run_never_calls_apply_and_result_is_none() -> None:
    events: list[str] = []
    registry = make_registry(events, [("aws", "vm"), ("azure", "vm")])
    outcomes = run(Selection((("aws", "vm"), ("azure", "vm"))), make_context(True), registry)
    assert not [e for e in events if e.endswith(".apply")]
    assert [o.result for o in outcomes] == [None, None]
    assert all(o.report.dry_run for o in outcomes)
    assert [c.id for c in outcomes[0].plan.candidates] == ["aws-vm-1"]
    assert outcomes[0].report.sections[0].ids == ("aws-vm-1",)


def test_apply_runs_once_per_cleaner_in_order() -> None:
    events: list[str] = []
    pairs = [("aws", "vm"), ("azure", "vm"), ("gcp", "ip")]
    registry = make_registry(events, pairs)
    outcomes = run(Selection(tuple(pairs)), make_context(False), registry)
    assert events == [
        "aws.vm.supports", "aws.vm.plan", "aws.vm.apply",
        "azure.vm.supports", "azure.vm.plan", "azure.vm.apply",
        "gcp.ip.supports", "gcp.ip.plan", "gcp.ip.apply",
    ]  # fmt: skip
    assert [(o.cloud, o.kind) for o in outcomes] == pairs
    assert all(isinstance(o, RunOutcome) and o.result is not None for o in outcomes)
    assert outcomes[2].result == Result(("gcp-ip-1",))
    assert outcomes[2].report.sections[0].label == "IP"
    assert not outcomes[2].report.dry_run


def test_empty_selection_runs_nothing() -> None:
    assert run(Selection(()), make_context(), Registry()) == []


def test_notifiers_run_after_each_cleaner_in_the_given_order() -> None:
    events: list[str] = []
    pairs = [("aws", "vm"), ("azure", "vm")]
    registry = make_registry(events, pairs)
    slack = FakeNotifier("slack", events)
    influx = FakeNotifier("influx", events, fail_open=True)
    run(Selection(tuple(pairs)), make_context(), registry, [slack, influx])
    assert [e for e in events if e.startswith("notify") or e.endswith("apply")] == [
        "aws.vm.apply", "notify.slack.aws", "notify.influx.aws",
        "azure.vm.apply", "notify.slack.azure", "notify.influx.azure",
    ]  # fmt: skip
    assert slack.reports[0] is not None
    assert slack.reports[0].cloud == "aws"


def test_notifiers_run_in_dry_run_with_a_plan_report() -> None:
    events: list[str] = []
    registry = make_registry(events, [("aws", "vm")])
    slack = FakeNotifier("slack", events)
    run(Selection((("aws", "vm"),)), make_context(True), registry, [slack])
    assert len(slack.reports) == 1
    assert slack.reports[0].dry_run is True
    assert slack.reports[0].sections[0].ids == ("aws-vm-1",)


@pytest.mark.parametrize("fail_in", ["init", "plan", "apply"])
def test_cleaner_exceptions_propagate_and_stop_the_run(fail_in: str) -> None:
    events: list[str] = []
    registry = make_registry(events, [("aws", "vm"), ("azure", "vm")], fail_in=fail_in)
    slack = FakeNotifier("slack", events)
    with pytest.raises(RuntimeError, match="failed"):
        run(Selection((("aws", "vm"), ("azure", "vm"))), make_context(), registry, [slack])
    assert not [e for e in events if e.startswith("azure")]
    assert slack.reports == []


def test_dry_run_apply_failure_is_not_reached() -> None:
    events: list[str] = []
    registry = make_registry(events, [("aws", "vm")], fail_in="apply")
    outcomes = run(Selection((("aws", "vm"),)), make_context(True), registry)
    assert outcomes[0].result is None


@pytest.mark.parametrize("slack_first", [True, False])
def test_fail_open_notifier_error_is_logged_and_swallowed(
    caplog: pytest.LogCaptureFixture, slack_first: bool
) -> None:
    events: list[str] = []
    pairs = [("aws", "vm"), ("azure", "vm")]
    registry = make_registry(events, pairs)
    influx = FakeNotifier("influx", events, fail_open=True, error=OSError("influx down"))
    slack = FakeNotifier("slack", events)
    notifiers = [slack, influx] if slack_first else [influx, slack]
    with caplog.at_level(logging.ERROR):
        outcomes = run(Selection(tuple(pairs)), make_context(), registry, notifiers)
    assert len(outcomes) == len(pairs)
    assert len(slack.reports) == len(pairs)
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "Notifier influx failed for aws report: influx down",
        "Notifier influx failed for azure report: influx down",
    ]


@pytest.mark.parametrize(
    "case",
    [
        ("aws", "vm", Operation.DELETE, "AWS_VM", lambda c: c.delete_vm({}, {}, {}, None, [])),
        ("aws", "vm", Operation.STOP, "AWS_VM", lambda c: c.stop_vm({}, {}, {}, None)),
        (
            "aws",
            "disk",
            Operation.DELETE,
            "AWS_Disk",
            lambda c: c.delete_disks({}, {}, {}, None, {}, [], [], False, None),
        ),
        ("aws", "ip", Operation.DELETE, "ElasticIPs", lambda c: c.delete_ip({}, {}, [], [])),
        ("aws", "keypair", Operation.DELETE, "KeyPairs", lambda c: c.delete_keypairs([], [], {})),
        ("azure", "nic", Operation.DELETE, "NIC", lambda c: c.delete_nic([], [])),
        (
            "aws",
            "kms",
            Operation.DELETE,
            "Kms",
            lambda c: c.delete_kms({}, {}, "d", "u", 7, {}, None),
        ),
        (
            "aws",
            "spot_instance_requests",
            Operation.DELETE,
            "SpotInstanceRequests",
            lambda c: c.delete_spot_instance_requests({}, {}, {}, None),
        ),
        (
            "oci",
            "spot_instances",
            Operation.DELETE,
            "OCI_SpotVM",
            lambda c: c.delete_spot_instances({}, {}, {}, None),
        ),
    ],
)
def test_notifier_order_and_influx_fail_open_match_crc_py(
    crc_cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, Operation, str, Any],
) -> None:
    cloud, kind, operation, attr, call_fn = case
    crc_events: list[str] = []

    class FakeResource:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.get_deleted = [f"{cloud}-{kind}-1"]
            self.get_stopped = [f"{cloud}-{kind}-1"]
            self.get_deleted_nic: list[str] = []
            self.get_deleted_disks: list[str] = []
            self.get_deleted_volumes: list[str] = []
            self.volumes_to_delete: list[str] = []

        def delete(self, *args: Any) -> None:
            crc_events.append(f"{cloud}.{kind}.apply")

        def stop(self) -> None:
            crc_events.append(f"{cloud}.{kind}.apply")

    class FakeSlack:
        def chat_postMessage(self, **kwargs: Any) -> None:
            crc_events.append(f"notify.slack.{cloud}")

    class FakeInflux:
        def write_api(self, **kwargs: Any) -> Any:
            tag = f"notify.influx.{cloud}"
            if not crc_events or crc_events[-1] != tag:
                crc_events.append(tag)
            raise OSError("influx down")

    monkeypatch.setattr(vm_ops if hasattr(vm_ops, attr) else resource_ops, attr, FakeResource)
    crc_obj = crc_cli.CRC(
        cloud,
        False,
        {},
        FakeSlack(),
        FakeInflux(),
        project_id="proj",
        resource_group="rg",
        slack_channel="chan",
        influxdb_conn={"bucket": "b"},
    )
    call_fn(crc_obj)

    engine_events: list[str] = []
    registry = make_registry(engine_events, [(cloud, kind)])
    slack = FakeNotifier("slack", engine_events, fail_open=False)
    influx = FakeNotifier("influx", engine_events, fail_open=True, error=OSError("influx down"))
    run(Selection(((cloud, kind),)), make_context(False, operation), registry, [slack, influx])
    filtered = [e for e in engine_events if e.endswith(".apply") or e.startswith("notify.")]
    assert filtered == crc_events


def test_fail_closed_notifier_error_propagates() -> None:
    events: list[str] = []
    registry = make_registry(events, [("aws", "vm"), ("azure", "vm")])
    slack = FakeNotifier("slack", events, error=OSError("slack down"))
    influx = FakeNotifier("influx", events, fail_open=True)
    with pytest.raises(OSError, match="slack down"):
        run(Selection((("aws", "vm"), ("azure", "vm"))), make_context(), registry, [slack, influx])
    assert influx.reports == []
    assert not [e for e in events if e.startswith("azure")]


def test_unsupported_operation_raises_before_plan_or_apply() -> None:
    events: list[str] = []
    registry = make_registry(events, [("aws", "disk")], supported=False)
    with pytest.raises(ValueError, match="aws disk cleaner does not support operation 'stop'"):
        run(Selection((("aws", "disk"),)), make_context(operation=Operation.STOP), registry)
    assert events == ["aws.disk.supports"]


def test_unregistered_cleaner_raises() -> None:
    registry = make_registry([], [("aws", "vm")])
    with pytest.raises(UnknownCleanerError, match="kind='keypair'"):
        run(Selection((("aws", "vm"), ("azure", "keypair"))), make_context(True), registry)


def test_selection_warnings_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    registry = make_registry([], [("aws", "vm")])
    with caplog.at_level(logging.WARNING):
        run(Selection((("aws", "vm"),), ("skipped x",)), make_context(True), registry)
    assert [r.getMessage() for r in caplog.records] == ["skipped x"]


def test_unknown_kind_has_no_report_label() -> None:
    registry = make_registry([], [("aws", "widget")])
    with pytest.raises(ValueError, match="no report label for resource kind 'widget'"):
        run(Selection((("aws", "widget"),)), make_context(True), registry)


def test_every_selectable_kind_has_a_label() -> None:
    for kind in (*RESOURCES, "vpc", "spot_instance_requests", "spot_instances"):
        assert kind in KIND_LABELS
