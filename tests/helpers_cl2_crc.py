"""Shared fakes of the CLI parity tests: scenario, recording fakes, routed driver.

`make_fake`, `FakeSlack` and `FakeInflux` drive the frozen oracle (`load_legacy_crc`); the routed
side runs `crc.cli.bridge.run_routed` with a recording fake cleaner (`run_routed_op`) on the same
scenario. Nothing here imports the legacy CLI modules.
"""

import io
from collections.abc import Callable
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import pytest

from crc.core.cleaner import Candidate, Operation, Plan, Result, RunContext
from crc.core.report import Section
from crc.notify.slack import SlackNotifier
from tests.helpers_oracle import load_legacy_crc

ADAPTERS = [
    "AWS_Disk",
    "ElasticIPs",
    "KeyPairs",
    "Kms",
    "SpotInstanceRequests",
    "AWS_VM",
    "VPC",
    "Disk",
    "AZU_IP",
    "NIC",
    "AZU_SpotVM",
    "AZU_VM",
    "GCP_Disk",
    "GCP_IP",
    "GCP_SpotVM",
    "GCP_VM",
    "OCI_Disk",
    "OCI_SpotVM",
    "OCI_VM",
]
FULL = {
    "get_deleted": ["a", "b"],
    "get_stopped": ["a"],
    "get_deleted_nic": ["n1"],
    "get_deleted_disks": ["d1", "d2"],
    "get_deleted_volumes": ["v1"],
    "volumes_to_delete": ["v1", "v2"],
}
EMPTY: dict[str, Any] = {key: [] for key in FULL}
DATA = {"full": FULL, "empty": EMPTY}
MIN_CASES = 5000
MIN_SEEN = 5
GROUPS_FAIL = 1
OPEN_FAIL = 2
SLACK_MODES = ["off", "on", "post_fail", "no_channel"]
INFLUX_MODES = ["off", "on", "on_suffix", "api_error", "write_error", "no_conn"]
Recorded = dict[str, Any]


@dataclass
class Scenario:
    values: dict[str, Any]
    dry_run: bool = False
    slack: str = "on"
    influx: str = "on"
    delete_error: Exception | None = None
    scan_supported: bool = True
    resource_group: str | None = "rg"
    project_id: str | None = "proj"
    slack_users: dict[str, str] = field(default_factory=lambda: {"alice@yugabyte.com": "U1"})
    slack_groups: list[dict[str, str]] = field(
        default_factory=lambda: [{"handle": "team", "id": "G1"}]
    )
    groups_fail: bool = False
    open_fail: bool = False


class Logs:
    def __init__(self) -> None:
        self.adapters: list[Any] = []
        self.reads: list[Any] = []
        self.slack: list[Any] = []
        self.influx: list[Any] = []


def make_fake(name: str, scenario: Scenario, logs: Logs) -> type:
    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        logs.adapters.append(("new", name, args, kwargs))

    def delete(self: Any, *args: Any) -> None:
        logs.adapters.append((name, "delete", args))
        if scenario.delete_error is not None:
            raise scenario.delete_error

    def stop(self: Any) -> None:
        logs.adapters.append((name, "stop"))

    def find(self: Any, key: str, value: str) -> list[dict[str, str]]:
        logs.adapters.append((name, "find_instances_with_tag", (key, value)))
        return [{"id": "i-1", "name": "vm-1", "region": "r"}]

    def getter(attr: str) -> Callable[[Any], Any]:
        def read(self: Any) -> Any:
            logs.reads.append((name, attr))
            return scenario.values[attr]

        return read

    body: dict[str, Any] = {"__init__": init, "delete": delete, "stop": stop}
    if scenario.scan_supported:
        body["find_instances_with_tag"] = find
    for attr in FULL:
        body[attr] = property(getter(attr))
    return type(name, (), body)


class FakeSlack:
    def __init__(self, scenario: Scenario, logs: Logs) -> None:
        self.scenario, self.logs = scenario, logs

    def chat_postMessage(self, **kwargs: Any) -> None:
        self.logs.slack.append(("chat_postMessage", kwargs))
        if self.scenario.slack == "post_fail":
            raise RuntimeError("slack down")

    def users_lookupByEmail(self, **kwargs: Any) -> dict[str, Any]:
        self.logs.slack.append(("users_lookupByEmail", kwargs))
        return {"user": {"id": self.scenario.slack_users[kwargs["email"]]}}

    def usergroups_list(self) -> dict[str, Any]:
        self.logs.slack.append(("usergroups_list", {}))
        if self.scenario.groups_fail:
            raise RuntimeError("groups down")
        return {"usergroups": self.scenario.slack_groups}

    def conversations_open(self, **kwargs: Any) -> dict[str, Any]:
        self.logs.slack.append(("conversations_open", kwargs))
        if self.scenario.open_fail:
            raise RuntimeError("open down")
        return {"channel": {"id": "D-" + kwargs["users"][0]}}


class FakeWriter:
    def __init__(self, scenario: Scenario, logs: Logs) -> None:
        self.scenario, self.logs = scenario, logs

    def write(self, *args: Any, **kwargs: Any) -> None:
        record: Any = kwargs.get("record")
        shown = record.to_line_protocol() if hasattr(record, "to_line_protocol") else record
        self.logs.influx.append(("write", args, {**kwargs, "record": shown}))
        if self.scenario.influx == "write_error":
            raise OSError("write down")


class FakeInflux:
    def __init__(self, scenario: Scenario, logs: Logs) -> None:
        self.scenario, self.logs = scenario, logs

    def write_api(self, **kwargs: Any) -> FakeWriter:
        self.logs.influx.append(("write_api", kwargs))
        if self.scenario.influx == "api_error":
            raise OSError("api down")
        return FakeWriter(self.scenario, self.logs)


@dataclass
class Case:
    name: str
    cloud: str
    act: Callable[[Any], Any]
    scenario: Scenario
    constructor_error: bool = False
    routed: str | None = None


@pytest.fixture(scope="module")
def oracle() -> ModuleType:
    return load_legacy_crc("legacy_crc_main_crc")


def build(crc_class: Any, case: Case, logs: Logs) -> Any:
    scenario = case.scenario
    slack = FakeSlack(scenario, logs) if scenario.slack != "off" else None
    influx = FakeInflux(scenario, logs) if scenario.influx != "off" else None
    conn: dict[str, Any] | None = {"bucket": "bkt"}
    if scenario.influx == "on_suffix":
        conn = {"bucket": "bkt", "resource_suffix": "sfx"}
    elif scenario.influx == "no_conn":
        conn = None
    return crc_class(
        case.cloud,
        scenario.dry_run,
        {"owner": ["x"]},
        slack,
        influx,
        project_id=scenario.project_id,
        resource_group=scenario.resource_group,
        slack_channel=None if scenario.slack == "no_channel" else "chan",
        influxdb_conn=conn,
    )


def execute(crc_class: Any, modules: list[ModuleType], case: Case) -> Recorded:
    logs = Logs()
    out = io.StringIO()
    result: Any = None
    attributes: Any = None
    with pytest.MonkeyPatch.context() as patch, redirect_stdout(out):
        for module in modules:
            for adapter in ADAPTERS:
                if hasattr(module, adapter):
                    patch.setattr(module, adapter, make_fake(adapter, case.scenario, logs))
        try:
            crc = build(crc_class, case, logs)
            attributes = sorted((k, repr(v)) for k, v in vars(crc).items() if "client" not in k)
            result = ("ok", repr(case.act(crc)))
        except Exception as exc:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
            result = ("error", type(exc).__name__, str(exc))
    return {
        "result": result,
        "attributes": attributes,
        "adapters": logs.adapters,
        "reads": logs.reads,
        "slack": logs.slack,
        "influx": logs.influx,
        "stdout": out.getvalue(),
    }


ROUTE_ADAPTERS: dict[tuple[str, str], str] = {
    ("aws", "vm"): "AWS_VM",
    ("azure", "vm"): "AZU_VM",
    ("gcp", "vm"): "GCP_VM",
    ("oci", "vm"): "OCI_VM",
    ("azure", "spot_instances"): "AZU_SpotVM",
    ("gcp", "spot_instances"): "GCP_SpotVM",
    ("oci", "spot_instances"): "OCI_SpotVM",
    ("aws", "spot_instance_requests"): "SpotInstanceRequests",
    ("aws", "ip"): "ElasticIPs",
    ("azure", "ip"): "AZU_IP",
    ("gcp", "ip"): "GCP_IP",
    ("azure", "nic"): "NIC",
    ("aws", "disk"): "AWS_Disk",
    ("azure", "disk"): "Disk",
    ("gcp", "disk"): "GCP_Disk",
    ("oci", "disk"): "OCI_Disk",
    ("aws", "vpc"): "VPC",
    ("aws", "kms"): "Kms",
    ("aws", "keypair"): "KeyPairs",
}


def _vm_init(cloud: str, dry: bool, opts: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:
    common = (
        opts["filter_tags"],
        opts["exception_tags"],
        opts["age"],
        opts["custom_age_tag_key"],
        opts["notags"],
    )
    if cloud == "azure":
        return (opts["resource_group"], dry, *common), {}
    if cloud == "gcp":
        return (dry, opts["project_id"], *common), {}
    return (dry, *common), {}


def _ip_init(cloud: str, dry: bool, opts: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if cloud == "azure":
        return (
            opts["resource_group"],
            dry,
            opts["filter_tags"],
            opts["exception_tags"],
            opts["notags"],
        ), {}
    if cloud == "gcp":
        return (dry, opts["project_id"], opts["name_regex"], opts["exception_regex"]), {}
    return (dry, opts["filter_tags"], opts["exception_tags"], opts["notags"]), {}


def _disk_init(cloud: str, dry: bool, opts: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:
    common = (
        opts["filter_tags"],
        opts["exception_tags"],
        opts["age"],
        opts["custom_age_tag_key"],
        opts["notags"],
    )
    if cloud == "aws":
        return (
            dry,
            *common,
            opts["name_regex"],
            opts["exception_regex"],
            opts["detach_age"],
        ), {}
    if cloud == "oci":
        return (dry, *common), {}
    if cloud == "azure":
        return (opts["resource_group"], dry, *common), {}
    return (), {
        "dry_run": dry,
        "project_id": opts["project_id"],
        "filter_labels": opts["filter_tags"],
        "exception_labels": opts["exception_tags"],
        "age": opts["age"],
        "custom_age_tag_key": opts["custom_age_tag_key"],
        "detach_age": opts["detach_age"],
        "notags": opts["notags"],
        "name_regex": opts["name_regex"],
        "exception_regex": opts["exception_regex"],
        "slack_notify_users": opts["slack_notify_users"],
        "slack_user_label": opts["slack_user_label"],
    }


def _aws_only_init(kind: str, dry: bool, opts: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if kind == "vpc":
        return (dry, opts["filter_tags"], opts["exception_tags"], opts["notags"]), {}
    if kind == "kms":
        return (
            dry,
            opts["filter_tags"],
            opts["exception_tags"],
            opts["kms_key_description"],
            opts["kms_user"],
            opts["kms_pending_window"],
            opts["age"],
            opts["custom_age_tag_key"],
        ), {}
    return (dry, opts["name_regex"], opts["exception_regex"], opts["age"]), {}


def adapter_init_from_context(
    cloud: str, kind: str, context: RunContext
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Reconstruct the oracle adapter `__init__` `(args, kwargs)` from `context.options`."""
    dry = context.dry_run
    opts = context.options
    if kind in ("vm", "spot_instances", "spot_instance_requests"):
        return _vm_init(cloud, dry, opts)
    if kind == "ip":
        return _ip_init(cloud, dry, opts)
    if kind == "nic":
        return (opts["resource_group"], dry, opts["name_regex"], opts["exception_regex"]), {}
    if kind == "disk":
        return _disk_init(cloud, dry, opts)
    return _aws_only_init(kind, dry, opts)


def record_adapter_call(cloud: str, kind: str, context: RunContext, logs: Logs) -> None:
    """Append the oracle adapter's `delete`/`stop` entry from `context` to `logs.adapters`."""
    adapter = ROUTE_ADAPTERS[(cloud, kind)]
    if context.operation is Operation.STOP:
        logs.adapters.append((adapter, "stop"))
    else:
        state = (context.options["instance_state"],) if "instance_state" in context.options else ()
        logs.adapters.append((adapter, "delete", state))


@dataclass(frozen=True)
class Op:
    """One routed operation: the pair, the engine operation and the flags the CLI would parse."""

    kind: str
    operation: str = "delete"
    flags: dict[str, Any] = field(default_factory=dict)
    related: tuple[tuple[str, str], ...] = ()


class RoutedCleaner:
    """Plans and applies the ids of the scenario, as the adapter of the oracle exposes them."""

    def __init__(
        self, cloud: str, op: Op, scenario: Scenario, seen: list[RunContext], logs: Logs
    ) -> None:
        self.cloud, self.kind, self.op = cloud, op.kind, op
        self.scenario, self.seen, self.logs = scenario, seen, logs
        values = scenario.values
        raw = values["get_stopped" if op.operation == "stop" else "get_deleted"]
        self.by_owner: dict[str, tuple[str, ...]] | None = None
        if isinstance(raw, dict):
            self.by_owner = {k: tuple(v) for k, v in raw.items()}
            raw = [i for ids in self.by_owner.values() for i in ids]
        self.ids = list(raw)
        self.related = {label: tuple(values[attr]) for label, attr in op.related}
        self.partial_succeeded: list[str] = []

    def supports(self, operation: Operation) -> bool:
        return True

    def _fail(self) -> None:
        if self.scenario.delete_error is not None:
            self.partial_succeeded = list(self.ids)
            raise self.scenario.delete_error

    def plan(self, context: RunContext) -> Plan:
        self.seen.append(context)
        adapter = ROUTE_ADAPTERS[(self.cloud, self.kind)]
        args, kwargs = adapter_init_from_context(self.cloud, self.kind, context)
        self.logs.adapters.append(("new", adapter, args, kwargs))
        if context.dry_run:
            record_adapter_call(self.cloud, self.kind, context, self.logs)
            self._fail()
        return Plan(
            candidates=tuple(Candidate(id=i) for i in self.ids),
            related=self.related,
            by_owner=self.by_owner,
        )

    def apply(self, plan: Plan, context: RunContext) -> Result:
        assert not context.dry_run
        record_adapter_call(self.cloud, self.kind, context, self.logs)
        self._fail()
        return Result(
            succeeded=tuple(c.id for c in plan.candidates),
            related=plan.related,
            by_owner=plan.by_owner,
        )


def run_routed(
    case: Case,
    cloud: str,
    op: Op,
    values: dict[str, Any],
    *,
    force_slack: bool = False,
) -> tuple[Recorded, list[Any]]:
    """Run `bridge.run_routed` for one pair with a `RoutedCleaner`; the same fakes as the oracle."""
    from crc.cli import bridge
    from crc.cli.inputs import Targets, read_options
    from crc.cli.runner import CRC
    from crc.core.registry import Registry

    scenario = Scenario(**{**case.scenario.__dict__, "values": values})
    seen: list[RunContext] = []
    logs, out = Logs(), io.StringIO()
    registry = Registry()
    registry.register(cloud, op.kind)(
        lambda context: RoutedCleaner(cloud, op, scenario, seen, logs)
    )
    result: Any = ("ok", "None")
    with pytest.MonkeyPatch.context() as patch, redirect_stdout(out):
        patch.setattr(bridge, "init_run_logging", lambda: None)
        patch.setattr(bridge, "build_registry", lambda: registry)
        if force_slack:
            patch.setattr(
                bridge,
                "build_notifiers",
                lambda crc, route: [SlackNotifier(crc.slack_client, crc.slack_channel)],
            )
        try:
            crc = build(CRC, Case(case.name, cloud, case.act, scenario), logs)
            options = read_options(
                {
                    "cloud": cloud,
                    "resource": op.kind,
                    "operation_type": op.operation,
                    "dry_run": scenario.dry_run,
                    "resource_group": scenario.resource_group,
                    "project_id": scenario.project_id,
                    "notags": {"owner": ["x"]},
                    **op.flags,
                }
            )
            route = bridge.route_for(cloud, op.kind)
            assert route is not None
            targets = Targets(cloud, cloud == "aws" and op.kind == "disk", [cloud], [op.kind])
            bridge.run_routed(crc, cloud, op.kind, options, targets, route)
        except Exception as exc:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
            result = ("error", type(exc).__name__, str(exc))
    recorded = {
        "result": result,
        "adapters": logs.adapters,
        "slack": logs.slack,
        "influx": logs.influx,
        "stdout": out.getvalue(),
    }
    return recorded, seen


def ping_owners_raw(crc: Any, operated: Any) -> None:
    """Run `SlackNotifier` owner pings with main's `BUG-033` pre-loop on raw `operated` input."""
    from crc.core.report import Report

    notifier = SlackNotifier(crc.slack_client, crc.slack_channel)
    user_groups = notifier.user_groups()
    for key in operated.keys():  # noqa: SIM118 - BUG-033: keys() + len() pre-loop before pinging
        len(operated[key])
    report = Report(
        crc.cloud,
        Operation.DELETE,
        crc.dry_run,
        (Section("Disk", ()),),
        by_owner=operated,
    )
    notifier.ping_owners(report, "Disk", get_groups=lambda: user_groups)
