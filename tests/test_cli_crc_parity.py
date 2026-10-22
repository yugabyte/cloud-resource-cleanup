"""Parity: crc.cli.runner.CRC against the CRC class of main (`tests/legacy/crc_main.txt`).

The oracle is the exact `git show main:crc.py`. The same call runs on the oracle CRC and on the
new CRC with recording fake adapters (patched in each module that looks them up), a fake Slack
client and a fake Influx client. For every case the ordered adapter constructor arguments and
method calls, the attribute reads, the Slack calls, the Influx calls, the printed output and the
return value or raised error must be equal.
"""

import importlib.util
import io
import itertools
from collections.abc import Callable, Iterator
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from crc.cli import resource_ops, vm_ops
from crc.cli.runner import CRC as NewCRC

ORACLE = Path(__file__).parent / "legacy" / "crc_main.txt"
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


@pytest.fixture(scope="module")
def oracle() -> ModuleType:
    loader = SourceFileLoader("legacy_crc_main_crc", str(ORACLE))
    spec = importlib.util.spec_from_file_location("legacy_crc_main_crc", ORACLE, loader=loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


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
        except Exception as exc:
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


def compare(oracle: ModuleType, case: Case) -> Recorded:
    old = execute(oracle.CRC, [oracle], case)
    new = execute(NewCRC, [vm_ops, resource_ops], case)
    for key in old:
        assert new[key] == old[key], f"{case.name}: {key} differs"
    return old


def scenario_grid(values: dict[str, Any], **overrides: Any) -> Iterator[tuple[str, Scenario]]:
    for dry, slack, influx in itertools.product([False, True], SLACK_MODES, INFLUX_MODES):
        yield (
            f"dry={dry}-slack={slack}-influx={influx}",
            Scenario(dict(values), dry_run=dry, slack=slack, influx=influx, **overrides),
        )


TAGS = {"t": ["v"]}
AGE = {"days": 1}
DISK_ARGS = (TAGS, {"e": ["x"]}, AGE, "age_tag", {"days": 2}, ["r1"], ["r2"], True, "label")
OPERATIONS: list[tuple[str, str, Callable[[Any], Any], dict[str, Any]]] = []


def op(name: str, clouds: list[str], act: Callable[[Any], Any], **overrides: Any) -> None:
    OPERATIONS.extend((name, cloud, act, overrides) for cloud in clouds)


ALL = ["aws", "azure", "gcp", "oci", "other"]
op("delete_vm", ALL, lambda c: c.delete_vm(TAGS, {}, AGE, "age_tag", ["RUNNING"]))
op("delete_vm_default_state", ["aws", "azure"], lambda c: c.delete_vm(TAGS, {}, AGE, None, []))
op("delete_vm_none_state", ["gcp"], lambda c: c.delete_vm(TAGS, {}, AGE, None, None))
op("stop_vm", ALL, lambda c: c.stop_vm(TAGS, {}, AGE, "age_tag"))
op("scan", ALL, lambda c: c.scan_instances_with_tag("auto_clean", "false"))
op("scan_unsupported", ALL, lambda c: c.scan_instances_with_tag("k", "v"), scan_supported=False)
op(
    "spot",
    ["aws", "azure", "gcp", "oci", "other"],
    lambda c: c.delete_spot_instances(TAGS, {}, AGE, "age_tag"),
)
op(
    "spot_no_group",
    ["azure"],
    lambda c: c.delete_spot_instances(TAGS, {}, AGE, None),
    resource_group=None,
)
op(
    "azure_spot_alias",
    ["azure", "gcp"],
    lambda c: c.delete_azure_spot_instances(TAGS, {}, AGE, "age_tag"),
)
op(
    "spot_requests",
    ["aws", "azure"],
    lambda c: c.delete_spot_instance_requests(TAGS, {}, AGE, "age_tag"),
)
op("ip", ["aws", "azure", "gcp", "oci"], lambda c: c.delete_ip(TAGS, {}, ["n"], ["x"]))
op("keypairs", ["aws", "gcp"], lambda c: c.delete_keypairs(["n"], ["x"], AGE))
op("nic", ["azure", "aws"], lambda c: c.delete_nic(["n"], ["x"]))
op("disks", ALL, lambda c: c.delete_disks(*DISK_ARGS))
op(
    "disks_delete_error",
    ["aws", "gcp"],
    lambda c: c.delete_disks(*DISK_ARGS),
    delete_error=ValueError("boom"),
)
op("vpc", ["aws", "azure"], lambda c: c.delete_vpc(TAGS, {}))
op("kms", ["aws", "gcp"], lambda c: c.delete_kms(TAGS, {}, "desc", "arn", 7, AGE, "age_tag"))


def operation_cases() -> list[Any]:
    cases = []
    for (name, cloud, act, overrides), data in itertools.product(OPERATIONS, DATA):
        for label, scenario in scenario_grid(DATA[data], **overrides):
            case = Case(f"{name}-{cloud}-{data}-{label}", cloud, act, scenario)
            cases.append(pytest.param(case, id=case.name))
    return cases


OPERATION_CASES = operation_cases()


@pytest.mark.parametrize("case", OPERATION_CASES)
def test_operation_matches_main(oracle: ModuleType, case: Case) -> None:
    compare(oracle, case)


def obj(**attrs: Any) -> SimpleNamespace:
    return SimpleNamespace(**attrs)


DISK_VALUES: list[Any] = [
    ["d1"],
    [],
    {"alice": ["d1"], "team": ["d2"], "nobody": ["d3"]},
    {},
    ("d1",),
    None,
    "d1",
    {"alice": None},
]
DIRECT: list[tuple[str, str, Callable[[Any], Any]]] = [
    ("get_msg_list", "aws", lambda c: c.get_msg("VM", "Deleted", ["a"])),
    ("get_msg_empty", "gcp", lambda c: c.get_msg("Disk", "Stopped", [])),
    ("get_msg_none", "aws", lambda c: c.get_msg("VM", "Deleted", None)),
    ("get_msg_bad_operation", "aws", lambda c: c.get_msg("VM", "Gone", ["a"])),
    ("lookup_known", "aws", lambda c: c.slack_lookup_user_by_email("alice@yugabyte.com")),
    ("lookup_unknown", "aws", lambda c: c.slack_lookup_user_by_email("zed@yugabyte.com")),
    ("groups", "aws", lambda c: c.get_user_groups_list()),
    ("nic", "azure", lambda c: c.notify_deleted_nic_via_slack(obj(get_deleted=["n"]))),
    ("nic_none", "azure", lambda c: c.notify_deleted_nic_via_slack(obj(get_deleted=None))),
    ("vm_aws", "aws", lambda c: c.notify_deleted_vm_via_slack(obj(get_deleted=["v"]))),
    (
        "vm_azure",
        "azure",
        lambda c: c.notify_deleted_vm_via_slack(obj(get_deleted=["v"], get_deleted_nic=["n"])),
    ),
    ("vm_none", "aws", lambda c: c.notify_deleted_vm_via_slack(obj(get_deleted=None))),
    ("stopped", "aws", lambda c: c.notify_stopped_vm_via_slack(obj(get_stopped=["v"]))),
    ("stopped_empty", "gcp", lambda c: c.notify_stopped_vm_via_slack(obj(get_stopped=[]))),
    (
        "spot_request",
        "aws",
        lambda c: c.notify_deleted_spot_request_via_slack(
            obj(get_deleted=["s"], volumes_to_delete=["v"])
        ),
    ),
    (
        "spot_request_no_volumes",
        "aws",
        lambda c: c.notify_deleted_spot_request_via_slack(obj(get_deleted=["s"])),
    ),
    (
        "spot_request_none_volumes",
        "aws",
        lambda c: c.notify_deleted_spot_request_via_slack(
            obj(get_deleted=[], volumes_to_delete=None)
        ),
    ),
    ("ip", "aws", lambda c: c.notify_deleted_ip_via_slack(obj(get_deleted=["i"]))),
    ("ip_empty", "gcp", lambda c: c.notify_deleted_ip_via_slack(obj(get_deleted=[]))),
    ("keypair", "aws", lambda c: c.notify_deleted_keypair_via_slack(obj(get_deleted=["k"]))),
    ("kms", "aws", lambda c: c.notify_deleted_kms_via_slack(obj(get_deleted=["k"]))),
    ("kms_empty", "aws", lambda c: c.notify_deleted_kms_via_slack(obj(get_deleted=[]))),
    ("write_list", "aws", lambda c: c.write_influxdb("VM", ["a", "b"])),
    ("write_empty", "gcp", lambda c: c.write_influxdb("Disk", [])),
    ("write_dict", "aws", lambda c: c.write_influxdb("Disk", {"a": ["x"]})),
    ("write_none", "aws", lambda c: c.write_influxdb("VM", None)),
    (
        "ping_dict",
        "aws",
        lambda c: c.ping_on_slack("Disk", "Deleted", {"alice": ["d"], "team": ["e"], "z": ["f"]}),
    ),
    ("ping_empty", "aws", lambda c: c.ping_on_slack("Disk", "Deleted", {})),
    ("ping_list", "aws", lambda c: c.ping_on_slack("Disk", "Deleted", ["a"])),
    ("ping_no_len", "aws", lambda c: c.ping_on_slack("Disk", "Deleted", {"alice": 3})),
]


def disk_notifier(value: Any) -> Callable[[Any], Any]:
    return lambda c: c.notify_deleted_disk_via_slack(obj(get_deleted=value))


DIRECT.extend(
    (f"disk_{index}", cloud, disk_notifier(value))
    for index, value in enumerate(DISK_VALUES)
    for cloud in ("aws", "gcp")
)


def direct_cases() -> list[Any]:
    cases = []
    for (name, cloud, act), variant in itertools.product(DIRECT, range(3)):
        overrides: dict[str, Any] = {}
        if variant == GROUPS_FAIL:
            overrides = {"groups_fail": True}
        elif variant == OPEN_FAIL:
            overrides = {"open_fail": True}
        for label, scenario in scenario_grid(FULL, **overrides):
            case = Case(f"{name}-{cloud}-v{variant}-{label}", cloud, act, scenario)
            cases.append(pytest.param(case, id=case.name))
    return cases


DIRECT_CASES = direct_cases()


@pytest.mark.parametrize("case", DIRECT_CASES)
def test_direct_method_matches_main(oracle: ModuleType, case: Case) -> None:
    compare(oracle, case)


@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp", "oci", "other"])
@pytest.mark.parametrize("project_id", [None, "", "proj"])
@pytest.mark.parametrize("slack", ["off", "on"])
def test_constructor_matches_main(
    oracle: ModuleType, cloud: str, project_id: Any, slack: str
) -> None:
    scenario = Scenario(dict(FULL), slack=slack, project_id=project_id, influx="no_conn")
    compare(oracle, Case(f"ctor-{cloud}", cloud, lambda c: None, scenario))


@pytest.mark.parametrize("disk_value", DISK_VALUES, ids=[repr(v) for v in DISK_VALUES])
@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp", "oci"])
@pytest.mark.parametrize("slack", ["off", "on", "post_fail"])
@pytest.mark.parametrize("influx", ["off", "on", "write_error"])
def test_delete_disks_result_shapes_match_main(
    oracle: ModuleType, disk_value: Any, cloud: str, slack: str, influx: str
) -> None:
    values = {**FULL, "get_deleted": disk_value}
    scenario = Scenario(values, slack=slack, influx=influx)
    case = Case("disks-shape", cloud, lambda c: c.delete_disks(*DISK_ARGS), scenario)
    compare(oracle, case)


def test_every_adapter_name_is_patched_in_the_new_modules() -> None:
    for adapter in ADAPTERS:
        assert hasattr(vm_ops, adapter) or hasattr(resource_ops, adapter), adapter


def test_tables_are_not_vacuous(oracle: ModuleType) -> None:
    assert len(OPERATION_CASES) + len(DIRECT_CASES) >= MIN_CASES
    seen = {"adapters": 0, "slack": 0, "influx": 0, "stdout": 0, "error": 0, "ok": 0}
    for param in OPERATION_CASES[::37] + DIRECT_CASES[::41]:
        outcome = execute(oracle.CRC, [oracle], param.values[0])
        seen["adapters"] += bool(outcome["adapters"])
        seen["slack"] += bool(outcome["slack"])
        seen["influx"] += bool(outcome["influx"])
        seen["stdout"] += bool(outcome["stdout"])
        seen[outcome["result"][0]] += 1
    assert all(count > MIN_SEEN for count in seen.values()), seen
