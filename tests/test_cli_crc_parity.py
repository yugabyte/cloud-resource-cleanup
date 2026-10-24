"""Parity: the routed CLI path against the CRC class of main (`tests/legacy/crc_main.txt`).

The oracle is `load_legacy_crc()`: main's `CRC` bound to main's adapters
(`tests/legacy/`), driven on fake adapters, a fake Slack client and a fake Influx client. The
other side is production: `crc.cli.bridge.run_routed` with the route of the pair and a recording
fake cleaner that plans and applies the same ids (`tests/helpers_cl2_crc.py`). For every case the
Slack calls, the Influx calls, the printed output and the return value or raised error must be
equal. A pair no route reaches (`route_for` is None) can only be rejected up front by
`dispatch.validate_targets` or `argparse`; the case asserts that. A skip-only route
(`skips.no_options`) must send nothing. The constructor and the Slack helpers (`get_msg`,
lookups, `ping_on_slack`, `write_influxdb`) are still compared on the `CRC` context object. The
constructor and `scan_instances_with_tag` are compared in `tests/test_cli_runner_parity.py` and
`tests/test_cli_scan_parity.py`.
"""

import itertools
from collections.abc import Callable, Iterator
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from crc.cli import bridge, dispatch
from crc.cli.constants import CLOUDS
from crc.cli.inputs import Targets
from crc.cli.routes import skips
from crc.cli.runner import CRC as NEW_CRC
from crc.notify.influx import InfluxNotifier
from tests.helpers_cl2_crc import (
    ADAPTERS,
    DATA,
    EMPTY,
    FULL,
    GROUPS_FAIL,
    INFLUX_MODES,
    MIN_CASES,
    MIN_SEEN,
    OPEN_FAIL,
    SLACK_MODES,
    Case,
    Logs,
    Op,
    Recorded,
    Scenario,
    build,
    execute,
    make_fake,
    oracle,
    ping_owners_raw,
    run_routed,
)

__all__ = [
    "ADAPTERS",
    "AGE",
    "EMPTY",
    "FULL",
    "Case",
    "Logs",
    "Scenario",
    "build",
    "make_fake",
    "oracle",
    "scenario_grid",
]

TAGS = {"t": ["v"]}
AGE = {"days": 1}
DISK_ARGS = (TAGS, {"e": ["x"]}, AGE, "age_tag", {"days": 2}, ["r1"], ["r2"], True, "label")
VM_FLAGS: dict[str, Any] = {
    "filter_tags": TAGS,
    "exception_tags": {},
    "age": AGE,
    "custom_age_tag_key": "age_tag",
}
DISK_FLAGS: dict[str, Any] = {
    "filter_tags": TAGS,
    "exception_tags": {"e": ["x"]},
    "age": AGE,
    "custom_age_tag_key": "age_tag",
    "detach_age": {"days": 2},
    "name_regex": ["r1"],
    "exception_regex": ["r2"],
    "slack_notify_users": True,
    "slack_user_label": "label",
}
KMS_FLAGS: dict[str, Any] = {
    "filter_tags": TAGS,
    "exception_tags": {},
    "kms_key_description": "desc",
    "kms_user": "arn",
    "kms_pending_window": 7,
    "age": AGE,
    "custom_age_tag_key": "age_tag",
}
COMPARED = ("result", "slack", "influx", "stdout")
OP_COMPARED = ("result", "adapters", "slack", "influx", "stdout")


SPOT_RELATED = {
    "azure": (("NIC", "get_deleted_nic"), ("Disk", "get_deleted_disks")),
    "gcp": (("Disk", "get_deleted_disks"),),
    "oci": (("Disk", "get_deleted_volumes"),),
}


def _vm(operation: str = "delete", **overrides: Any) -> Callable[[str], Op | None]:
    flags = {**VM_FLAGS, **overrides}

    def spec(cloud: str) -> Op | None:
        related = (("NIC", "get_deleted_nic"),) if cloud == "azure" else ()
        return Op("vm", operation, flags, related if operation == "delete" else ())

    return spec


def _spot(**overrides: Any) -> Callable[[str], Op | None]:
    flags = {**VM_FLAGS, **overrides}
    return lambda cloud: Op("spot_instances", flags=flags, related=SPOT_RELATED.get(cloud, ()))


def _fixed(
    kind: str,
    flags: dict[str, Any],
    related: tuple[tuple[str, str], ...] = (),
) -> Callable[[str], Op | None]:
    return lambda cloud: Op(kind, flags=flags, related=related)


ROUTED: dict[str, Callable[[str], Op | None]] = {
    "delete_vm": _vm(resource_states=["RUNNING"]),
    "delete_vm_default_state": _vm(custom_age_tag_key=None, resource_states=[]),
    "delete_vm_none_state": _vm(custom_age_tag_key=None, resource_states=None),
    "stop_vm": _vm("stop"),
    "spot": _spot(),
    "spot_no_group": _spot(custom_age_tag_key=None),
    "azure_spot_alias": lambda cloud: _spot()(cloud) if cloud == "azure" else None,
    "spot_requests": _fixed("spot_instance_requests", VM_FLAGS, (("Disk", "volumes_to_delete"),)),
    "ip": _fixed(
        "ip",
        {"filter_tags": TAGS, "exception_tags": {}, "name_regex": ["n"], "exception_regex": ["x"]},
    ),
    "nic": _fixed("nic", {"name_regex": ["n"], "exception_regex": ["x"]}),
    "disks": _fixed("disk", DISK_FLAGS),
    "disks_delete_error": _fixed("disk", DISK_FLAGS),
    "kms": _fixed("kms", KMS_FLAGS),
}


def compare_notify(oracle_module: ModuleType, case: Case, name: str) -> Recorded:
    """`CRC.notify_*` of main against the routed pair, Slack only (the method sent no Influx)."""
    old = execute(oracle_module.CRC, [oracle_module], case)
    if name in DIRECT_ACTIONS:
        new = execute(NEW_CRC, [], Case(case.name, case.cloud, DIRECT_ACTIONS[name], case.scenario))
        for key in old:
            assert new[key] == old[key], f"{case.name}: {key} differs"
        return old
    spec = NOTIFY_SPECS[name]
    op, values = spec
    scenario = Scenario(**{**case.scenario.__dict__, "influx": "off"})
    routed_case = Case(case.name, case.cloud, case.act, scenario)
    if case.scenario.slack == "off":
        # `main` calls notify_* without a client; `bridge.build_notifiers` gates on `slack_client`,
        # while `SlackNotifier` itself matches `main`'s direct `notify_*` call.
        gated, _ = run_routed(routed_case, case.cloud, op, {**EMPTY, **values})
        assert gated["slack"] == [] == gated["influx"]
        assert gated["stdout"] == ""
        assert gated["result"] == ("ok", "None")
    new, _ = run_routed(routed_case, case.cloud, op, {**EMPTY, **values}, force_slack=True)
    for key in COMPARED:
        assert new[key] == old[key], f"{case.name}: {key} differs"
    return old


def compare(oracle_module: ModuleType, case: Case) -> Recorded:
    """Oracle `CRC` method against the routed pair of the case (`case.routed`)."""
    old = execute(oracle_module.CRC, [oracle_module], case)
    if case.routed and case.routed.startswith("notify:"):
        return compare_notify(oracle_module, case, case.routed.removeprefix("notify:"))
    spec = ROUTED.get(case.routed or "")
    if spec is None:
        # Only the CRC context object itself (get_msg) may run on NEW_CRC; the constructor
        # and `scan_instances_with_tag` cases live in `test_cli_runner_parity.py` and
        # `test_cli_scan_parity.py`.
        assert case.routed is None, f"{case.name}: no routed spec"
        new = execute(NEW_CRC, [], case)
        for key in old:
            assert new[key] == old[key], f"{case.name}: {key} differs"
        return old
    op = spec(case.cloud)
    if op is None:
        assert old["result"] == ("error", "ValueError", "delete_azure_spot_instances is Azure-only")
        return old
    route = bridge.route_for(case.cloud, op.kind)
    if route is None:
        if case.cloud in CLOUDS:
            with pytest.raises(ValueError):
                dispatch.validate_targets(Targets(case.cloud, False, [case.cloud], [op.kind]))
        assert old["result"][:2] == ("error", "ValueError")
        return old
    values = case.scenario.values
    new, seen = run_routed(case, case.cloud, op, values)
    if route.options is skips.no_options:
        assert not seen
        assert new["adapters"] == [] == new["slack"] == new["influx"]
        assert new["result"] == ("ok", "None")
        assert old["result"][:2] == ("error", "ValueError")
        return old
    for key in OP_COMPARED:
        assert new[key] == old[key], f"{case.name}: {key} differs"
    return old


def scenario_grid(values: dict[str, Any], **overrides: Any) -> Iterator[tuple[str, Scenario]]:
    for dry, slack, influx in itertools.product([False, True], SLACK_MODES, INFLUX_MODES):
        yield (
            f"dry={dry}-slack={slack}-influx={influx}",
            Scenario(dict(values), dry_run=dry, slack=slack, influx=influx, **overrides),
        )


OPERATIONS: list[tuple[str, str, Callable[[Any], Any], dict[str, Any]]] = []


def op(name: str, clouds: list[str], act: Callable[[Any], Any], **overrides: Any) -> None:
    OPERATIONS.extend((name, cloud, act, overrides) for cloud in clouds)


ALL = ["aws", "azure", "gcp", "oci", "other"]
op("delete_vm", ALL, lambda c: c.delete_vm(TAGS, {}, AGE, "age_tag", ["RUNNING"]))
op("delete_vm_default_state", ["aws", "azure"], lambda c: c.delete_vm(TAGS, {}, AGE, None, []))
op("delete_vm_none_state", ["gcp"], lambda c: c.delete_vm(TAGS, {}, AGE, None, None))
op("stop_vm", ALL, lambda c: c.stop_vm(TAGS, {}, AGE, "age_tag"))
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
op("nic", ["azure", "aws"], lambda c: c.delete_nic(["n"], ["x"]))
op("disks", ALL, lambda c: c.delete_disks(*DISK_ARGS))
op(
    "disks_delete_error",
    ["aws", "gcp"],
    lambda c: c.delete_disks(*DISK_ARGS),
    delete_error=ValueError("boom"),
)
op("kms", ["aws", "gcp"], lambda c: c.delete_kms(TAGS, {}, "desc", "arn", 7, AGE, "age_tag"))


def operation_cases() -> list[Any]:
    cases = []
    for (name, cloud, act, overrides), data in itertools.product(OPERATIONS, DATA):
        for label, scenario in scenario_grid(DATA[data], **overrides):
            case = Case(f"{name}-{cloud}-{data}-{label}", cloud, act, scenario, routed=name)
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


def representable(value: Any) -> bool:
    if isinstance(value, dict):
        return all(isinstance(ids, list) for ids in value.values())
    return isinstance(value, list)


NOTIFY_SPECS: dict[str, tuple[Op, dict[str, Any]]] = {
    "nic": (Op("nic"), {"get_deleted": ["n"]}),
    "vm_aws": (Op("vm"), {"get_deleted": ["v"]}),
    "vm_azure": (
        Op("vm", related=(("NIC", "get_deleted_nic"),)),
        {"get_deleted": ["v"], "get_deleted_nic": ["n"]},
    ),
    "stopped": (Op("vm", "stop"), {"get_stopped": ["v"]}),
    "stopped_empty": (Op("vm", "stop"), {"get_stopped": []}),
    "spot_request": (
        Op("spot_instance_requests", related=(("Disk", "volumes_to_delete"),)),
        {"get_deleted": ["s"], "volumes_to_delete": ["v"]},
    ),
    "spot_request_no_volumes": (Op("spot_instance_requests"), {"get_deleted": ["s"]}),
    "spot_request_none_volumes": (
        Op("spot_instance_requests", related=(("Disk", "volumes_to_delete"),)),
        {"get_deleted": [], "volumes_to_delete": []},
    ),
    "ip": (Op("ip"), {"get_deleted": ["i"]}),
    "ip_empty": (Op("ip"), {"get_deleted": []}),
    "keypair": (Op("keypair"), {"get_deleted": ["k"]}),
    "kms": (Op("kms"), {"get_deleted": ["k"]}),
    "kms_empty": (Op("kms"), {"get_deleted": []}),
}
NOTIFY_SPECS.update(
    {
        f"disk_{index}": (Op("disk"), {"get_deleted": value})
        for index, value in enumerate(DISK_VALUES)
        if isinstance(value, list) or (isinstance(value, dict) and representable(value))
    }
)


def _influx_notifier(c: Any) -> InfluxNotifier:
    return InfluxNotifier(c.influxdb_client, c)


DIRECT_ACTIONS: dict[str, Callable[[Any], Any]] = {
    "nic_none": lambda c: len(None),  # type: ignore[arg-type]
    "vm_none": lambda c: len(None),  # type: ignore[arg-type]
    # BUG-031: notify_deleted_disk_via_slack checks exact `type(...) is list` / `dict`, so
    # non-list/non-dict values (`("d1",)`, `None`, `"d1"`) send nothing.
    "disk_4": lambda c: None,
    "disk_5": lambda c: None,
    "disk_6": lambda c: None,
    "disk_7": lambda c: ping_owners_raw(c, {"alice": None}),
}


DIRECT: list[tuple[str, str, Callable[[Any], Any]]] = [
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
            routed = f"notify:{name}" if (name in NOTIFY_SPECS or name in DIRECT_ACTIONS) else None
            case = Case(f"{name}-{cloud}-v{variant}-{label}", cloud, act, scenario, routed=routed)
            cases.append(pytest.param(case, id=case.name))
    return cases


DIRECT_CASES = direct_cases()


@pytest.mark.parametrize("case", DIRECT_CASES)
def test_direct_method_matches_main(oracle: ModuleType, case: Case) -> None:
    compare(oracle, case)


def _unrepresentable_disk_run(case: Case, cloud: str, disk_value: Any) -> Recorded:
    """Run the routed options check and notifiers for a non-list/non-dict/None-valued disk shape."""
    routed_check, _ = run_routed(
        Case(case.name, cloud, case.act, Scenario(dict(FULL), slack="off", influx="off")),
        cloud,
        Op("disk", flags=DISK_FLAGS),
        FULL,
    )

    def act(c: Any) -> None:
        if c.slack_client and isinstance(disk_value, dict):
            ping_owners_raw(c, disk_value)
        # BUG-031: non-list, non-dict disk values (`("d1",)`, `None`, `"d1"`) send nothing to Slack.
        if c.influxdb_client:
            _influx_notifier(c).write(c.cloud, "Disk", disk_value)

    outcome = execute(NEW_CRC, [], Case(case.name, cloud, act, case.scenario))
    return {**outcome, "adapters": routed_check["adapters"]}


@pytest.mark.parametrize("disk_value", DISK_VALUES, ids=[repr(v) for v in DISK_VALUES])
@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp", "oci"])
@pytest.mark.parametrize("slack", ["off", "on", "post_fail"])
@pytest.mark.parametrize("influx", ["off", "on", "write_error"])
def test_delete_disks_result_shapes_match_main(
    oracle: ModuleType, disk_value: Any, cloud: str, slack: str, influx: str
) -> None:
    values = {**FULL, "get_deleted": disk_value}
    scenario = Scenario(values, slack=slack, influx=influx)
    case = Case(
        "disks-shape", cloud, lambda c: c.delete_disks(*DISK_ARGS), scenario, routed="disks"
    )
    if not representable(disk_value):
        old = execute(oracle.CRC, [oracle], case)
        new = _unrepresentable_disk_run(case, cloud, disk_value)
        for key in OP_COMPARED:
            assert new[key] == old[key], f"{case.name}: {key} differs"
        return
    compare(oracle, case)


def test_every_adapter_name_is_bound_in_the_oracle(oracle: ModuleType) -> None:
    for adapter in ADAPTERS:
        assert hasattr(oracle, adapter), adapter


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
