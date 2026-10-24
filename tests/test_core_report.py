"""Report parity with crc.py: Slack texts, Influx points and payload, owner pings."""

import sys
from collections.abc import Iterator
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

from crc.core.cleaner import Candidate, Failure, Operation, Plan, Result
from crc.core.report import (
    DISK,
    KEYPAIR,
    KMS,
    NIC,
    SNAPSHOT,
    SPOT_INSTANCE_REQUEST,
    VM,
    InfluxPoint,
    Report,
    Section,
    SlackMessage,
    format_owner_ping,
    get_msg,
    influx_resource_name,
)
from tests.helpers_oracle import load_legacy_crc

IDS = ["i-1", "i-2"]


@pytest.fixture(scope="module")
def crc_cli() -> Iterator[ModuleType]:
    """Oracle crc.py (`tests/legacy/crc_main.txt`) loaded with frozen module oracles."""
    module = load_legacy_crc("crc_cli_under_test")
    yield module
    sys.modules.pop("crc_cli_under_test", None)


def make_crc(
    crc_cli: ModuleType, cloud: str = "aws", dry_run: bool = False, suffix: str | None = None
) -> tuple[Any, Mock, Mock]:
    slack, influx = Mock(), MagicMock()
    conn = {"bucket": "b", "resource_suffix": suffix}
    instance = crc_cli.CRC(
        cloud,
        dry_run,
        {},
        slack,
        influx,
        project_id="p",
        resource_group="rg",
        slack_channel="chan",
        influxdb_conn=conn,
    )
    return instance, slack, influx


def sent(slack: Mock) -> list[SlackMessage]:
    return [
        SlackMessage(c.kwargs["text"], c.kwargs.get("link_names", False))
        for c in slack.chat_postMessage.call_args_list
    ]


def written_lines(influx: MagicMock) -> list[str]:
    api = influx.write_api.return_value
    return [c.kwargs["record"].to_line_protocol() for c in api.write.call_args_list]


def line(cli: ModuleType, point: InfluxPoint) -> str:
    line_text: str = (
        cli.Point(point.measurement)
        .tag("resource", point.resource)
        .field("names", point.names)
        .field("count", point.count)
        .to_line_protocol()
    )
    return line_text


WORDS = {Operation.DELETE: "Deleted", Operation.STOP: "Stopped"}


@pytest.mark.parametrize(
    "setting",
    [(c, d) for c in ("aws", "azure", "gcp", "oci") for d in (False, True)],
)
@pytest.mark.parametrize(
    ("label", "operation"),
    [
        (VM, Operation.DELETE),
        (VM, Operation.STOP),
        (NIC, Operation.DELETE),
        (SNAPSHOT, Operation.DELETE),
        (SPOT_INSTANCE_REQUEST, Operation.DELETE),
    ],
)
@pytest.mark.parametrize("ids", [[], ["only"], ["a", "it's", 'q"x']])
def test_get_msg_matches_crc(
    crc_cli: ModuleType,
    setting: tuple[str, bool],
    label: str,
    operation: Operation,
    ids: list[str],
) -> None:
    cloud, dry_run = setting
    word = WORDS[operation]
    instance, _, _ = make_crc(crc_cli, cloud, dry_run)
    assert get_msg(cloud, label, operation, ids, dry_run) == instance.get_msg(label, word, ids)
    assert get_msg(cloud, label, operation, tuple(ids), dry_run) == instance.get_msg(
        label, word, ids
    )


def test_get_msg_literal_text() -> None:
    assert get_msg("aws", VM, Operation.DELETE, ["a", "b"], False) == (
        "Deleted the following `2` aws VM(s):\n`['a', 'b']`"
    )
    assert get_msg("gcp", VM, Operation.STOP, [], True) == (
        "`Dry Run`: Will be Stopped: `0` gcp VM(s):\n`[]`"
    )


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    "case",
    [
        ("notify_deleted_vm_via_slack", "get_deleted", VM, Operation.DELETE, False),
        ("notify_stopped_vm_via_slack", "get_stopped", VM, Operation.STOP, False),
        ("notify_deleted_ip_via_slack", "get_deleted", "IP", Operation.DELETE, False),
        ("notify_deleted_keypair_via_slack", "get_deleted", KEYPAIR, Operation.DELETE, False),
        ("notify_deleted_nic_via_slack", "get_deleted", NIC, Operation.DELETE, False),
        ("notify_deleted_kms_via_slack", "get_deleted", KMS, Operation.DELETE, False),
        ("notify_deleted_disk_via_slack", "get_deleted", DISK, Operation.DELETE, True),
    ],
)
@pytest.mark.parametrize("ids", [[], IDS])
def test_single_section_notifications(
    crc_cli: ModuleType, dry_run: bool, case: tuple[str, str, str, Operation, bool], ids: list[str]
) -> None:
    notify, attr, label, operation, link_names = case
    instance, slack, _ = make_crc(crc_cli, "aws", dry_run)
    getattr(instance, notify)(SimpleNamespace(**{attr: ids}))
    report = Report.from_result("aws", label, Result(tuple(ids)), operation, dry_run)
    assert report.slack_messages() == tuple(sent(slack))
    assert report.slack_messages()[0].link_names is link_names


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("volumes", [[], ["vol-1", "vol-2"]])
def test_spot_request_notification(crc_cli: ModuleType, dry_run: bool, volumes: list[str]) -> None:
    instance, slack, influx = make_crc(crc_cli, "aws", dry_run)
    request = SimpleNamespace(get_deleted=IDS, volumes_to_delete=volumes)
    instance.notify_deleted_spot_request_via_slack(request)
    instance.write_influxdb(crc_cli.VMS, request.get_deleted)
    instance.write_influxdb(crc_cli.DISKS, request.volumes_to_delete)
    result = Result(tuple(IDS), related={DISK: tuple(volumes)})
    report = Report.from_result("aws", SPOT_INSTANCE_REQUEST, result, Operation.DELETE, dry_run)
    assert list(report.slack_messages()) == sent(slack)
    assert len(sent(slack)) == (2 if volumes else 1)
    assert [line(crc_cli, p) for p in report.influx_points()] == written_lines(influx)
    assert report.influx_payload() == {VM: IDS, DISK: volumes}


@pytest.mark.parametrize("cloud", ["azure", "gcp", "oci"])
@pytest.mark.parametrize("disks", [[], ["d1"]])
def test_spot_vm_notification_and_disk_message(
    crc_cli: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    cloud: str,
    disks: list[str],
) -> None:
    instance, slack, influx = make_crc(crc_cli, cloud, False)
    fake_spot = SimpleNamespace(
        delete=lambda: None,
        get_deleted=IDS,
        get_deleted_nic=["nic-1"],
        get_deleted_disks=disks,
        get_deleted_volumes=disks,
    )
    monkeypatch.setattr(crc_cli, "AZU_SpotVM", lambda *_a, **_kw: fake_spot)
    monkeypatch.setattr(crc_cli, "GCP_SpotVM", lambda *_a, **_kw: fake_spot)
    monkeypatch.setattr(crc_cli, "OCI_SpotVM", lambda *_a, **_kw: fake_spot)
    instance.delete_spot_instances({}, {}, {"days": 1}, "retention_age")
    related = {NIC: ("nic-1",), DISK: tuple(disks)} if cloud == "azure" else {DISK: tuple(disks)}
    result = Result(tuple(IDS), related=related)
    report = Report.from_result(cloud, VM, result, Operation.DELETE, False)
    assert list(report.slack_messages()) == sent(slack)
    assert [line(crc_cli, p) for p in report.influx_points()] == written_lines(influx)


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    ("vms", "nics"),
    [([], []), (["vm-1"], ["nic-1"])],
)
def test_azure_vm_nic_notification_and_influx_matches_crc(
    crc_cli: ModuleType, dry_run: bool, vms: list[str], nics: list[str]
) -> None:
    instance, slack, influx = make_crc(crc_cli, "azure", dry_run)
    vm = SimpleNamespace(get_deleted=vms, get_deleted_nic=nics)
    instance.notify_deleted_vm_via_slack(vm)
    instance.write_influxdb(crc_cli.VMS, vm.get_deleted)
    instance.write_influxdb(crc_cli.NICS, vm.get_deleted_nic)
    report = Report.from_result(
        "azure", VM, Result(tuple(vms), related={NIC: tuple(nics)}), Operation.DELETE, dry_run
    )
    assert list(report.slack_messages()) == sent(slack)
    assert [line(crc_cli, p) for p in report.influx_points()] == written_lines(influx)


@pytest.mark.parametrize("suffix", [None, "", "ci"])
@pytest.mark.parametrize("ids", [[], IDS])
def test_influx_matches_write_influxdb(
    crc_cli: ModuleType, suffix: str | None, ids: list[str]
) -> None:
    instance, _, influx = make_crc(crc_cli, "azure", False, suffix)
    instance.write_influxdb(VM, ids)
    instance.write_influxdb(NIC, ["n1"])
    report = Report.from_result(
        "azure", VM, Result(tuple(ids), related={NIC: ("n1",)}), Operation.DELETE, False
    )
    assert [line(crc_cli, p) for p in report.influx_points(suffix)] == written_lines(influx)
    expected_vm = f"{VM}_{suffix}" if suffix else VM
    assert report.influx_payload(suffix)[expected_vm] == ids


def test_influx_is_written_for_empty_and_dry_run(crc_cli: ModuleType) -> None:
    instance, slack, influx = make_crc(crc_cli, "gcp", True)
    instance.notify_deleted_vm_via_slack(SimpleNamespace(get_deleted=[]))
    instance.write_influxdb(VM, [])
    report = Report.from_result("gcp", VM, Result(), Operation.DELETE, True)
    assert report.influx_points() == (InfluxPoint("gcp", "VM", "[]", 0),)
    assert [line(crc_cli, p) for p in report.influx_points()] == written_lines(influx)
    assert report.influx_payload() == {"VM": []}
    assert list(report.slack_messages()) == sent(slack)


def test_influx_payload_literal_and_merge() -> None:
    report = Report(
        "oci",
        Operation.DELETE,
        False,
        (Section(VM, ("a",)), Section(DISK, ("d1",)), Section(DISK, ("d2",))),
    )
    assert report.influx_payload("x") == {"VM_x": ["a"], "Disk_x": ["d1", "d2"]}
    assert [p.count for p in report.influx_points("x")] == [1, 1, 1]


@pytest.mark.parametrize(("suffix", "expected"), [(None, VM), ("", VM), ("s", "VM_s")])
def test_influx_resource_name(suffix: str | None, expected: str) -> None:
    assert influx_resource_name(VM, suffix) == expected


def test_failures_do_not_change_messages() -> None:
    failed = Result(("a",), (Failure("b", "denied"),))
    report = Report.from_result("aws", DISK, failed, Operation.DELETE, False)
    assert report.failures == (Failure("b", "denied"),)
    assert report.slack_messages() == (
        SlackMessage("Deleted the following `1` aws Disk(s):\n`['a']`", True),
    )


def test_from_plan_uses_candidate_ids_and_related_for_dry_run(crc_cli: ModuleType) -> None:
    instance, slack, influx = make_crc(crc_cli, "aws", True)
    request = SimpleNamespace(get_deleted=["i-1", "i-2"], volumes_to_delete=["vol-1"])
    instance.notify_deleted_spot_request_via_slack(request)
    instance.write_influxdb(crc_cli.VMS, request.get_deleted)
    instance.write_influxdb(crc_cli.DISKS, request.volumes_to_delete)
    plan = Plan(
        (Candidate(id="i-1"), Candidate(id="i-2")),
        related={DISK: ("vol-1",)},
    )
    report = Report.from_plan("aws", SPOT_INSTANCE_REQUEST, plan, Operation.DELETE, True)
    assert list(report.slack_messages()) == sent(slack)
    assert [line(crc_cli, p) for p in report.influx_points()] == written_lines(influx)


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("owners", [{}, {"owner": IDS}])
def test_owner_grouped_disk_report_matches_crc(
    crc_cli: ModuleType, dry_run: bool, owners: dict[str, list[str]]
) -> None:
    instance, slack, influx = make_crc(crc_cli, "gcp", dry_run)
    instance.slack_lookup_user_by_email = Mock(return_value="not_found")
    instance.get_user_groups_list = Mock(return_value=[])
    disk = SimpleNamespace(get_deleted=owners)
    instance.notify_deleted_disk_via_slack(disk)
    instance.write_influxdb(DISK, disk.get_deleted)
    grouped = {k: tuple(v) for k, v in owners.items()}
    report = Report.from_result("gcp", DISK, Result(by_owner=grouped), Operation.DELETE, dry_run)
    assert report.slack_messages() == ()
    assert report.by_owner == grouped
    pings = [
        format_owner_ping(report, DISK, owner, ids, None)
        for owner, ids in (report.by_owner or {}).items()
    ]
    assert [m.text for m in sent(slack)] == pings
    assert [line(crc_cli, p) for p in report.influx_points()] == written_lines(influx)


def test_report_is_frozen() -> None:
    report = Report.from_result("aws", VM, Result(("a",)), Operation.DELETE, False)
    with pytest.raises(AttributeError):
        report.cloud = "gcp"  # type: ignore[misc]
    assert isinstance(report.sections, tuple)


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    ("mention", "lookup", "groups"),
    [
        (None, "not_found", []),
        ("<!subteam^G1>", "not_found", [{"handle": "owner", "id": "G1"}]),
        ("<@U1>", "U1", []),
    ],
)
def test_owner_ping_matches_ping_on_slack(
    crc_cli: ModuleType,
    dry_run: bool,
    mention: str | None,
    lookup: str,
    groups: list[dict[str, str]],
) -> None:
    instance, slack, _ = make_crc(crc_cli, "aws", dry_run)
    instance.slack_lookup_user_by_email = Mock(return_value=lookup)
    instance.get_user_groups_list = Mock(return_value=groups)
    slack.conversations_open.return_value = {"channel": {"id": "D1"}}
    instance.ping_on_slack("Disk", "Deleted", {"owner": IDS})
    report = Report("aws", Operation.DELETE, dry_run, ())
    expected = format_owner_ping(report, "Disk", "owner", IDS, mention)
    assert [m.text for m in sent(slack)] == [expected]
