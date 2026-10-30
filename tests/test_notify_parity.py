"""Parity: the frozen `main` CRC notify code (oracle) against the bridge notifiers.

The legacy side is `tests.helpers_oracle.load_legacy_crc()`: `tests/legacy/crc_main.txt`, the
Slack and Influx code of `CRC` as it was on `main`. The routed side is what production runs:
`crc.cli.bridge.build_notifiers` gives `SlackNotifier` / `InfluxNotifier`, and `crc.core.engine`
builds the report from a cleaner's plan and result (`tests/helpers_cl2_notify.py`). The
fake-client calls, the printed text and the raised errors must be equal. Where the oracle
accepts input that the engine cannot produce (an id list that is `None` or a tuple, a value of
an owner group without `len()`), the oracle outcome is asserted against frozen data instead.
"""

import contextlib
import io
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

from crc.core.cleaner import Candidate, Operation, Plan, Result, RunContext
from crc.core.engine import run as run_engine
from crc.core.registry import Registry
from crc.core.report import (
    DISK,
    KEYPAIR,
    KMS,
    NIC,
    SPOT_INSTANCE_REQUEST,
    VM,
    Report,
    Section,
    get_msg,
)
from crc.core.selection import Selection
from crc.notify.influx import InfluxConfig, InfluxNotifier
from crc.notify.slack import SlackNotifier
from tests.helpers_cl2_notify import Outcome, normalise, routed_call, routed_run
from tests.helpers_oracle import load_legacy_crc

NOW = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
CLOUDS = ["aws", "azure", "gcp", "oci"]


@pytest.fixture(scope="module")
def legacy() -> Iterator[ModuleType]:
    """`main`'s `CRC` (tests/legacy/crc_main.txt) with its adapters bound to their oracles."""
    yield load_legacy_crc("legacy_crc_notify_parity")


def make(
    module: ModuleType,
    cloud: str,
    dry_run: bool,
    channel: str | None = "chan",
    suffix: str | None = None,
    conn: bool = True,
) -> tuple[Any, Mock, MagicMock]:
    slack, influx = Mock(), MagicMock()
    instance = module.CRC(
        cloud,
        dry_run,
        {},
        slack,
        influx,
        project_id="p",
        resource_group="rg",
        slack_channel=channel,
        influxdb_conn={"bucket": "bkt", "resource_suffix": suffix} if conn else None,
    )
    return instance, slack, influx


def run(
    module: ModuleType,
    call: Callable[[Any], Any],
    setup: Callable[[Mock], None] | None = None,
    **options: Any,
) -> Outcome:
    options.setdefault("cloud", "aws")
    options.setdefault("dry_run", False)
    instance, slack, influx = make(module, **options)
    if setup is not None:
        setup(slack)
    stdout = io.StringIO()
    error: tuple[str, str] | None = None
    with contextlib.redirect_stdout(stdout):
        try:
            call(instance)
        except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
            error = (type(e).__name__, str(e))
    return slack.mock_calls, normalise(influx), stdout.getvalue(), error


def resource(**attrs: Any) -> SimpleNamespace:
    return SimpleNamespace(**attrs)


NOTIFY_CASES: dict[str, tuple[str, SimpleNamespace]] = {
    "vm": (
        "notify_deleted_vm_via_slack",
        resource(get_deleted=["vm-1", "vm-2"], get_deleted_nic=["nic-1"]),
    ),
    "stopped": ("notify_stopped_vm_via_slack", resource(get_stopped=["vm-1"])),
    "spot-with-volumes": (
        "notify_deleted_spot_request_via_slack",
        resource(get_deleted=["sir-1"], volumes_to_delete=["v-1", "v-2"]),
    ),
    "spot-no-volumes": (
        "notify_deleted_spot_request_via_slack",
        resource(get_deleted=["sir-1"], volumes_to_delete=[]),
    ),
    "spot-no-attribute": (
        "notify_deleted_spot_request_via_slack",
        resource(get_deleted=["sir-1"]),
    ),
    "ip": ("notify_deleted_ip_via_slack", resource(get_deleted=["1.1.1.1"])),
    "keypair": ("notify_deleted_keypair_via_slack", resource(get_deleted=["k-1"])),
    "nic": (
        "notify_deleted_nic_via_slack",
        resource(get_deleted=["from-get-deleted"], get_deleted_nic=["from-get-deleted-nic"]),
    ),
    "kms": ("notify_deleted_kms_via_slack", resource(get_deleted=["key-1"])),
    "disk-list": ("notify_deleted_disk_via_slack", resource(get_deleted=["v-1", "v-2"])),
    "disk-empty-list": ("notify_deleted_disk_via_slack", resource(get_deleted=[])),
    "disk-tuple": ("notify_deleted_disk_via_slack", resource(get_deleted=("v-1",))),
    "disk-none": ("notify_deleted_disk_via_slack", resource(get_deleted=None)),
    "empty-vm": ("notify_deleted_vm_via_slack", resource(get_deleted=[])),
    "vm-without-ids": ("notify_deleted_vm_via_slack", resource(get_deleted=None)),
}

# The engine kind, operation, ids, and the related NIC ids of an Azure VM, that the cleaner
# result of each case carries. `None`: the engine cannot produce that input (see UNROUTED).
RoutedSpec = tuple[str, Operation, list[str], dict[str, list[str]]]
ROUTED_CASES: dict[str, RoutedSpec | None] = {
    "vm": ("vm", Operation.DELETE, ["vm-1", "vm-2"], {NIC: ["nic-1"]}),
    "stopped": ("vm", Operation.STOP, ["vm-1"], {}),
    "spot-with-volumes": (
        "spot_instance_requests",
        Operation.DELETE,
        ["sir-1"],
        {DISK: ["v-1", "v-2"]},
    ),
    "spot-no-volumes": ("spot_instance_requests", Operation.DELETE, ["sir-1"], {DISK: []}),
    "spot-no-attribute": ("spot_instance_requests", Operation.DELETE, ["sir-1"], {DISK: []}),
    "ip": ("ip", Operation.DELETE, ["1.1.1.1"], {}),
    "keypair": ("keypair", Operation.DELETE, ["k-1"], {}),
    "nic": ("nic", Operation.DELETE, ["from-get-deleted"], {}),
    "kms": ("kms", Operation.DELETE, ["key-1"], {}),
    "disk-list": ("disk", Operation.DELETE, ["v-1", "v-2"], {}),
    "disk-empty-list": ("disk", Operation.DELETE, [], {}),
    "disk-tuple": None,
    "disk-none": None,
    "empty-vm": ("vm", Operation.DELETE, [], {NIC: []}),
    "vm-without-ids": None,
}

# Oracle outcome of the inputs the engine cannot produce: a disk id list that is neither a list
# nor a dict is silently not notified; a VM id list of `None` raises in `len()`.
UNROUTED: dict[str, Outcome] = {
    "disk-tuple": ([], [], "", None),
    "disk-none": ([], [], "", None),
    "vm-without-ids": ([], [], "", ("TypeError", "object of type 'NoneType' has no len()")),
}


def routed_notify(case: str, cloud: str, **options: Any) -> Outcome:
    """The routed outcome of a NOTIFY_CASES case: Slack only, as the oracle notify_* is."""
    spec = ROUTED_CASES[case]
    assert spec is not None
    kind, operation, ids, related = spec
    return routed_run(
        cloud,
        kind,
        operation=operation,
        succeeded=ids,
        related={} if kind == "vm" and cloud != "azure" else related,
        influx=False,
        **options,
    )


def assert_notify_matches(
    legacy: ModuleType, case: str, setup: Callable[[Mock], None] | None = None, **options: Any
) -> Outcome:
    method, obj = NOTIFY_CASES[case]
    old = run(legacy, lambda crc: getattr(crc, method)(obj), setup, **options)
    if ROUTED_CASES[case] is None:
        assert old == UNROUTED[case]
        return old
    new = routed_notify(case, setup=setup, **options)
    assert new == old
    return new


@pytest.mark.parametrize("channel", ["chan", "#chan", None])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("cloud", CLOUDS)
@pytest.mark.parametrize("case", sorted(NOTIFY_CASES))
def test_notify_methods_match_legacy(
    legacy: ModuleType,
    case: str,
    cloud: str,
    dry_run: bool,
    channel: str | None,
) -> None:
    assert_notify_matches(legacy, case, cloud=cloud, dry_run=dry_run, channel=channel)


@pytest.mark.parametrize("case", sorted(NOTIFY_CASES))
def test_notify_methods_match_legacy_when_slack_fails(legacy: ModuleType, case: str) -> None:
    def fail(slack: Mock) -> None:
        slack.chat_postMessage.side_effect = RuntimeError("slack down")

    assert_notify_matches(legacy, case, fail, cloud="azure")


def test_quirks_are_visible_in_the_outcome(legacy: ModuleType) -> None:
    """Azure VM sends VM names as NIC list (BUG-028), `#chan` becomes `##chan` (BUG-034)."""
    calls, _, _, _ = assert_notify_matches(legacy, "vm", cloud="azure", channel="#chan")
    texts = [c.kwargs["text"] for c in calls]
    assert texts == [
        "Deleted the following `2` azure VM(s):\n`['vm-1', 'vm-2']`",
        "Deleted the following `2` azure NIC(s):\n`['vm-1', 'vm-2']`",
    ]
    assert {c.kwargs["channel"] for c in calls} == {"##chan"}


@pytest.mark.parametrize("operation", ["Deleted", "Stopped"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("cloud", CLOUDS)
@pytest.mark.parametrize("ids", [[], ["a"], ["a", "b"], None])
def test_get_msg_matches_legacy(
    legacy: ModuleType,
    operation: str,
    dry_run: bool,
    cloud: str,
    ids: list[str] | None,
) -> None:
    old = run(
        legacy,
        lambda crc: print(crc.get_msg("Disk", operation, ids)),
        cloud=cloud,
        dry_run=dry_run,
    )
    if ids is None:
        assert old == ([], [], "", ("TypeError", "object of type 'NoneType' has no len()"))
        return
    op = Operation.DELETE if operation == "Deleted" else Operation.STOP
    text = get_msg(cloud, "Disk", op, ids, dry_run)
    assert old == ([], [], text + "\n", None)


def lookup_setup(
    user: str | None, groups: list[dict[str, str]] | None, post_fails: bool, open_fails: bool
) -> Callable[[Mock], None]:
    def setup(slack: Mock) -> None:
        def lookup(email: str) -> Any:
            if user is None or email.startswith("bob"):
                raise RuntimeError("users_not_found")
            return {"user": {"id": user}}

        slack.users_lookupByEmail.side_effect = lookup
        if groups is None:
            slack.usergroups_list.side_effect = RuntimeError("no scope")
        else:
            slack.usergroups_list.return_value = {"usergroups": groups}
        slack.conversations_open.return_value = {"channel": {"id": "D1"}}
        if open_fails:
            slack.conversations_open.side_effect = RuntimeError("cannot open")
        if post_fails:
            slack.chat_postMessage.side_effect = RuntimeError("slack down")

    return setup


GROUPS = [{"handle": "bob", "id": "S1"}, {"handle": "carol", "id": "S2"}]
OWNERS = {"alice": ["v-1", "v-2"], "bob": ["v-3"], "carol": []}


def routed_ping(setup: Callable[[Mock], None], cloud: str = "aws", **options: Any) -> Outcome:
    """Owner pings of a disk run: the engine builds the by-owner report, Slack only."""
    return routed_run(cloud, "disk", by_owner=OWNERS, setup=setup, influx=False, **options)


def assert_ping_matches(
    legacy: ModuleType, setup: Callable[[Mock], None], cloud: str = "aws", **options: Any
) -> Outcome:
    old = run(
        legacy,
        lambda crc: crc.ping_on_slack("Disk", "Deleted", OWNERS),
        setup,
        cloud=cloud,
        **options,
    )
    new = routed_ping(setup, cloud, **options)
    assert new == old
    return new


SCENARIOS = [
    ("U1", GROUPS, False, False),
    ("U1", GROUPS, True, False),
    ("U1", GROUPS, False, True),
    ("U1", None, False, False),
    (None, GROUPS, False, False),
    (None, GROUPS, True, False),
    (None, [], False, False),
    (None, None, False, False),
]


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("cloud", ["aws", "gcp"])
def test_ping_on_slack_matches_legacy(
    legacy: ModuleType,
    cloud: str,
    dry_run: bool,
    scenario: tuple[str | None, list[dict[str, str]] | None, bool, bool],
) -> None:
    assert_ping_matches(legacy, lookup_setup(*scenario), cloud, dry_run=dry_run)


@pytest.mark.parametrize("channel", ["chan", "#chan", None])
@pytest.mark.parametrize("groups", [GROUPS, None])
def test_dict_disks_match_legacy(
    legacy: ModuleType,
    channel: str | None,
    groups: list[dict[str, str]] | None,
) -> None:
    setup = lookup_setup(None, groups, False, False)
    obj = resource(get_deleted=OWNERS)
    old = run(legacy, lambda crc: crc.notify_deleted_disk_via_slack(obj), setup, channel=channel)
    assert routed_ping(setup, channel=channel) == old


def test_ping_failure_modes_are_the_documented_quirks(legacy: ModuleType) -> None:
    broken = lookup_setup(None, None, False, False)
    _, _, out, error = assert_ping_matches(legacy, broken)
    assert error == ("TypeError", "string indices must be integers, not 'str'")
    assert "Something Went Wront! Could not get user_groups.\n" in out
    _, _, _, error = assert_ping_matches(legacy, lookup_setup("U1", GROUPS, False, True))
    assert error == ("RuntimeError", "cannot open")


@pytest.mark.parametrize(
    ("operated", "expected", "routed_expected"),
    [
        (
            {"alice": ["v-1"], "bob": None},
            ("TypeError", "object of type 'NoneType' has no len()"),
            ("TypeError", "'NoneType' object is not iterable"),
        ),
        (
            {"alice": ["v-1"], "bob": 3},
            ("TypeError", "object of type 'int' has no len()"),
            ("TypeError", "'int' object is not iterable"),
        ),
        (
            ["v-1"],
            ("AttributeError", "'list' object has no attribute 'keys'"),
            ("AttributeError", "'list' object has no attribute 'items'"),
        ),
    ],
)
def test_ping_on_slack_bad_input_matches_legacy(
    legacy: ModuleType,
    operated: Any,
    expected: tuple[str, str],
    routed_expected: tuple[str, str],
) -> None:
    """Oracle: groups are fetched, then every value is measured, before any owner is pinged.

    The engine cannot hand such a value to the notifier: a cleaner's `Plan` / `Result` freezes
    every owner group into a tuple first, so the routed run fails there, before any Slack call.
    """
    setup = lookup_setup("U1", GROUPS, False, False)
    calls, _, out, error = run(
        legacy, lambda crc: crc.ping_on_slack("Disk", "Deleted", operated), setup
    )
    assert error == expected
    assert [c[0] for c in calls] == ["usergroups_list"]
    assert out == ""
    routed_calls, _, routed_out, routed_error = routed_run(
        "aws", "disk", by_owner=operated, setup=setup, influx=False
    )
    assert routed_error == routed_expected
    assert routed_calls == []
    assert routed_out == ""


def test_slack_and_user_lookups_match_legacy(legacy: ModuleType) -> None:
    for setup in (lookup_setup("U1", GROUPS, False, False), lookup_setup(None, None, False, False)):
        old = run(legacy, lambda crc: print(crc.slack_lookup_user_by_email("a@b.c")), setup)
        new = routed_call(lambda n, _crc: print(n["slack"].lookup_user_by_email("a@b.c")), setup)
        assert new == old
        old = run(legacy, lambda crc: print(crc.get_user_groups_list()), setup)
        new = routed_call(lambda n, _crc: print(n["slack"].user_groups()), setup)
        assert new == old


def test_ping_uses_overridden_lookups() -> None:
    def ping(notifiers: dict[str, Any], crc: Any) -> None:
        report = Report(
            "aws", Operation.DELETE, False, (Section(DISK, ()),), by_owner={"alice": ("v-1",)}
        )
        notifiers["slack"].ping_owners(
            report,
            "Disk",
            Mock(return_value="not_found"),
            Mock(return_value=[{"handle": "alice", "id": "S9"}]),
        )

    calls, _, _, error = routed_call(ping)
    assert error is None
    assert "<!subteam^S9>" in calls[0].kwargs["text"]


INFLUX_ARGS: list[tuple[str, Any]] = [
    ("VM", ["i-1", "i-2"]),
    ("Disk", []),
    ("Disk", {"alice": ["v-1"], "bob": []}),
    ("NIC", None),
    ("Keypair", ("a",)),
]


@pytest.mark.parametrize("args", INFLUX_ARGS)
@pytest.mark.parametrize("suffix", [None, "", "jenkins"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("cloud", CLOUDS)
def test_write_influxdb_matches_legacy(
    legacy: ModuleType,
    cloud: str,
    dry_run: bool,
    suffix: str | None,
    args: tuple[str, Any],
) -> None:
    old = run(
        legacy, lambda crc: crc.write_influxdb(*args), cloud=cloud, dry_run=dry_run, suffix=suffix
    )
    new = routed_call(
        lambda n, _crc: n["influx"].write(cloud, *args),
        cloud=cloud,
        dry_run=dry_run,
        suffix=suffix,
    )
    assert new == old


def test_write_influxdb_without_connection_matches_legacy(legacy: ModuleType) -> None:
    """BUG-030: no bucket or suffix without `influxdb_conn`; the catch-all prints it."""
    old = run(legacy, lambda crc: crc.write_influxdb("VM", ["a"]), conn=False)
    new = routed_call(lambda n, _crc: n["influx"].write("aws", "VM", ["a"]), conn=False)
    assert new == old
    _, influx, out, error = new
    assert error is None
    assert "has no attribute 'resource_suffix'" in out
    assert [c[0] for c in influx] == ["write_api"]


def test_write_influxdb_failure_matches_legacy(legacy: ModuleType) -> None:
    def instance_call(crc: Any) -> None:
        crc.influxdb_client.write_api.return_value.write.side_effect = RuntimeError("down")
        crc.write_influxdb("VM", ["a"])

    def routed(notifiers: dict[str, Any], crc: Any) -> None:
        crc.influxdb_client.write_api.return_value.write.side_effect = RuntimeError("down")
        notifiers["influx"].write("aws", "VM", ["a"])

    old = run(legacy, instance_call)
    new = routed_call(routed)
    assert new == old
    assert new[2] == "Unable to push data to influxDB: down\n"


def report_for(cloud: str, dry_run: bool, operation: Operation, sections: list[Section]) -> Report:
    return Report(cloud, operation, dry_run, tuple(sections))


SECTION_CASES: dict[str, tuple[Operation, Callable[[SimpleNamespace], list[Section]]]] = {
    "vm": (Operation.DELETE, lambda o: [Section(VM, tuple(o.get_deleted))]),
    "stopped": (Operation.STOP, lambda o: [Section(VM, tuple(o.get_stopped))]),
    "spot-with-volumes": (
        Operation.DELETE,
        lambda o: [
            Section(SPOT_INSTANCE_REQUEST, tuple(o.get_deleted)),
            Section(DISK, tuple(o.volumes_to_delete), notify_empty=False),
        ],
    ),
    "spot-no-volumes": (
        Operation.DELETE,
        lambda o: [
            Section(SPOT_INSTANCE_REQUEST, tuple(o.get_deleted)),
            Section(DISK, (), notify_empty=False),
        ],
    ),
    "ip": (Operation.DELETE, lambda o: [Section("IP", tuple(o.get_deleted))]),
    "keypair": (Operation.DELETE, lambda o: [Section(KEYPAIR, tuple(o.get_deleted))]),
    "nic": (Operation.DELETE, lambda o: [Section(NIC, tuple(o.get_deleted))]),
    "kms": (Operation.DELETE, lambda o: [Section(KMS, tuple(o.get_deleted))]),
    "disk-list": (
        Operation.DELETE,
        lambda o: [Section(DISK, tuple(o.get_deleted), link_names=True)],
    ),
    "empty-vm": (Operation.DELETE, lambda o: [Section(VM, ())]),
}


SECTION_PARAMS = [
    (case, cloud)
    for case in sorted(SECTION_CASES)
    for cloud in CLOUDS
    if not (cloud == "azure" and case in {"vm", "empty-vm"})
]


def run_slack_notifier(
    report: Report,
    channel: str | None = "chan",
    setup: Callable[[Mock], None] | None = None,
) -> tuple[list[Any], str, tuple[str, str] | None]:
    slack = Mock()
    if setup is not None:
        setup(slack)
    context = RunContext(report.dry_run, report.operation, {}, NOW)
    stdout = io.StringIO()
    error: tuple[str, str] | None = None
    with contextlib.redirect_stdout(stdout):
        try:
            SlackNotifier(slack, channel).send(report, context)  # type: ignore[arg-type]
        except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
            error = (type(e).__name__, str(e))
    return slack.mock_calls, stdout.getvalue(), error


@pytest.mark.parametrize("channel", ["chan", "#chan", None])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(("case", "cloud"), SECTION_PARAMS)
def test_slack_notifier_matches_legacy(
    legacy: ModuleType,
    case: str,
    cloud: str,
    dry_run: bool,
    channel: str | None,
) -> None:
    operation, sections = SECTION_CASES[case]
    method, obj = NOTIFY_CASES[case]
    old = run(
        legacy,
        lambda crc: getattr(crc, method)(obj),
        cloud=cloud,
        dry_run=dry_run,
        channel=channel,
    )
    report = report_for(cloud, dry_run, operation, sections(obj))
    calls, out, error = run_slack_notifier(report, channel)
    assert (calls, out, error) == (old[0], old[2], old[3])


@pytest.mark.parametrize(("case", "cloud"), SECTION_PARAMS)
def test_slack_notifier_matches_legacy_when_slack_fails(
    legacy: ModuleType, case: str, cloud: str
) -> None:
    operation, sections = SECTION_CASES[case]
    method, obj = NOTIFY_CASES[case]

    def fail(slack: Mock) -> None:
        slack.chat_postMessage.side_effect = RuntimeError("slack down")

    old = run(legacy, lambda crc: getattr(crc, method)(obj), fail, cloud=cloud)
    report = report_for(cloud, False, operation, sections(obj))
    calls, out, error = run_slack_notifier(report, "chan", fail)
    assert (calls, out, error) == (old[0], old[2], old[3])


@pytest.mark.parametrize("post_fails", [False, True])
@pytest.mark.parametrize("channel", ["chan", "#chan", None])
@pytest.mark.parametrize("dry_run", [False, True])
def test_slack_notifier_matches_legacy_azure_vm(
    legacy: ModuleType,
    dry_run: bool,
    channel: str | None,
    post_fails: bool,
) -> None:
    method, obj = NOTIFY_CASES["vm"]

    def setup(slack: Mock) -> None:
        if post_fails:
            slack.chat_postMessage.side_effect = RuntimeError("slack down")

    old = run(
        legacy,
        lambda crc: getattr(crc, method)(obj),
        setup,
        cloud="azure",
        dry_run=dry_run,
        channel=channel,
    )
    sections = [Section(VM, ("vm-1", "vm-2")), Section(NIC, ("nic-1",), slack_ids=("vm-1", "vm-2"))]
    report = report_for("azure", dry_run, Operation.DELETE, sections)
    calls, out, error = run_slack_notifier(report, channel, setup)
    assert (calls, out, error) == (old[0], old[2], old[3])


@pytest.mark.parametrize(
    "scenario",
    [
        ("U1", GROUPS, False, False),
        ("U1", GROUPS, True, False),
        ("U1", GROUPS, False, True),
        ("U1", None, False, False),
        (None, GROUPS, False, False),
        (None, GROUPS, True, False),
        (None, [], False, False),
        (None, None, False, False),
    ],
)
@pytest.mark.parametrize("channel", ["chan", "#chan", None])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("cloud", ["aws", "gcp"])
def test_slack_notifier_owner_pings_match_legacy(
    legacy: ModuleType,
    cloud: str,
    dry_run: bool,
    channel: str | None,
    scenario: tuple[str | None, list[dict[str, str]] | None, bool, bool],
) -> None:
    setup = lookup_setup(*scenario)
    old = run(
        legacy,
        lambda crc: crc.ping_on_slack("Disk", "Deleted", OWNERS),
        setup,
        cloud=cloud,
        dry_run=dry_run,
        channel=channel,
    )
    grouped = {k: tuple(v) for k, v in OWNERS.items()}
    report = Report(cloud, Operation.DELETE, dry_run, (Section(DISK, ()),), by_owner=grouped)
    calls, out, error = run_slack_notifier(report, channel, setup)
    assert (calls, out, error) == (old[0], old[2], old[3])


@pytest.mark.parametrize("write_fails", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("suffix", [None, "jenkins"])
def test_influx_notifier_matches_legacy_writes(
    legacy: ModuleType,
    dry_run: bool,
    suffix: str | None,
    write_fails: bool,
) -> None:
    """The three writes of a spot VM delete, as `crc.py` issues them one by one."""

    def writes(crc: Any) -> None:
        if write_fails:
            crc.influxdb_client.write_api.return_value.write.side_effect = RuntimeError("down")
        crc.write_influxdb("VM", ["i-1"])
        crc.write_influxdb("NIC", [])
        crc.write_influxdb("Disk", ["v-1", "v-2"])

    old = run(legacy, writes, cloud="aws", dry_run=dry_run, suffix=suffix)
    influx = MagicMock()
    if write_fails:
        influx.write_api.return_value.write.side_effect = RuntimeError("down")
    sections = (Section(VM, ("i-1",)), Section(NIC, ()), Section(DISK, ("v-1", "v-2")))
    report = Report("aws", Operation.DELETE, dry_run, sections)
    context = RunContext(dry_run, Operation.DELETE, {}, NOW)
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        InfluxNotifier(influx, InfluxConfig("bkt", suffix)).send(report, context)
    assert (normalise(influx), stdout.getvalue()) == (old[1], old[2])


@pytest.mark.parametrize("post_fails", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_engine_with_notifiers_matches_legacy_section_and_owner_flows(
    legacy: ModuleType, dry_run: bool, post_fails: bool
) -> None:
    """Engine wiring with SlackNotifier(fail_open=False) and InfluxNotifier(fail_open=True)."""

    def setup(slack: Mock) -> None:
        lookup_setup("U1", GROUPS, post_fails, False)(slack)

    def legacy_flow(crc: Any) -> None:
        vm = resource(get_deleted=["vm-1"], get_deleted_nic=["nic-1"])
        crc.notify_deleted_vm_via_slack(vm)
        crc.write_influxdb("VM", vm.get_deleted)
        crc.write_influxdb("NIC", vm.get_deleted_nic)

    old = run(legacy, legacy_flow, setup, cloud="azure", dry_run=dry_run, suffix="ci")

    registry = Registry()

    @registry.register("azure", "vm")
    class AzureVmCleaner:
        cloud = "azure"
        kind = "vm"

        def __init__(self, _ctx: RunContext) -> None:
            pass

        def supports(self, op: Operation) -> bool:
            return op is Operation.DELETE

        def plan(self, _ctx: RunContext) -> Plan:
            return Plan((Candidate("vm-1"),), related={NIC: ("nic-1",)})

        def apply(self, _plan: Plan, _ctx: RunContext) -> Result:
            return Result(("vm-1",), related={NIC: ("nic-1",)})

    slack, influx = Mock(), MagicMock()
    setup(slack)
    notifiers = (SlackNotifier(slack, "chan"), InfluxNotifier(influx, InfluxConfig("bkt", "ci")))
    ctx = RunContext(dry_run, Operation.DELETE, {}, NOW)
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        run_engine(Selection((("azure", "vm"),)), ctx, registry, notifiers)
    assert (slack.mock_calls, normalise(influx), stdout.getvalue(), None) == old
    assert (
        routed_run(
            "azure",
            "vm",
            dry_run=dry_run,
            succeeded=["vm-1"],
            related={NIC: ["nic-1"]},
            setup=setup,
            suffix="ci",
        )
        == old
    )


@pytest.mark.parametrize(
    "scenario",
    [
        ("U1", GROUPS, True, False),
        ("U1", GROUPS, False, True),
        (None, None, False, False),
    ],
)
def test_engine_with_notifiers_matches_legacy_on_disk_owner_pings(
    legacy: ModuleType,
    scenario: tuple[str | None, list[dict[str, str]] | None, bool, bool],
) -> None:
    """chat_postMessage errors print and continue to Influx; BUG-041/042 raise before Influx."""
    setup = lookup_setup(*scenario)

    def legacy_disk_flow(crc: Any) -> None:
        disk = resource(get_deleted=OWNERS)
        crc.notify_deleted_disk_via_slack(disk)
        crc.write_influxdb("Disk", disk.get_deleted)

    old = run(legacy, legacy_disk_flow, setup, cloud="gcp")

    registry = Registry()
    grouped = {k: tuple(v) for k, v in OWNERS.items()}

    @registry.register("gcp", "disk")
    class GcpDiskCleaner:
        cloud = "gcp"
        kind = "disk"

        def __init__(self, _ctx: RunContext) -> None:
            pass

        def supports(self, op: Operation) -> bool:
            return op is Operation.DELETE

        def plan(self, _ctx: RunContext) -> Plan:
            return Plan(by_owner=grouped)

        def apply(self, _plan: Plan, _ctx: RunContext) -> Result:
            return Result(by_owner=grouped)

    slack, influx = Mock(), MagicMock()
    setup(slack)
    notifiers = (SlackNotifier(slack, "chan"), InfluxNotifier(influx, InfluxConfig("bkt")))
    ctx = RunContext(False, Operation.DELETE, {}, NOW)
    stdout = io.StringIO()
    error: tuple[str, str] | None = None
    with contextlib.redirect_stdout(stdout):
        try:
            run_engine(Selection((("gcp", "disk"),)), ctx, registry, notifiers)
        except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
            error = (type(e).__name__, str(e))
    assert (slack.mock_calls, normalise(influx), stdout.getvalue(), error) == old
    assert routed_run("gcp", "disk", by_owner=OWNERS, setup=setup) == old
