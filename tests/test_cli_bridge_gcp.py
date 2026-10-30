"""Parity: the engine bridge against `main`'s frozen CLI and GCP adapters for the routed
kinds.

Each case builds the same fake GCP client data twice, runs `main`'s `CRC` on the oracles
(`tests/legacy/crc_main.txt` driving `tests/legacy/gcp_*.txt`, see
`tests/helpers_cl2_gcp_oracle.py`)
and once `dispatch._run_pair` through the route, with the same fake Slack and Influx clients, and
requires equal Slack calls (text and recipients), Influx points, console output, error and GCP
calls made. The oracles log no run-log lines (RUN START, REGION, ACTION, SKIPPED, ERRORS,
RESULT); the routed lines are compared with the lines the legacy handlers logged, frozen
in `tests/helpers_cl2_gcp_runlog.py`. Where the oracle differs because `main` had a bug fixed since,
the test asserts the difference against the oracle and names the BUG. Review found two
differences its tests missed (names versus ids in a dry run, missing per-region ACTION lines):
the dry run cases here compare both.
"""

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from crc.cli import bridge
from tests.helpers_cl2_gcp_diverge import DIVERGENCES
from tests.helpers_cl2_gcp_runlog import RUN_LOG
from tests.helpers_gcp_bridge import (
    FakeGcp,
    Outcome,
    address,
    attached_disk,
    disk,
    instance,
    run_pair,
)

TAGS = {"k": ["v"]}
AGE = {"days": 1}
BASE: dict[str, Any] = {"slack_channel": "chan", "influxdb": {"bucket": "b"}}


def ips() -> FakeGcp:
    return FakeGcp(
        {
            "r1": [address("a-1"), address("a-2"), address("b-1"), address("a-used", "IN_USE")],
            "r2": [address("a-3"), address("x-a-4")],
        }
    )


def disks() -> FakeGcp:
    return FakeGcp(
        {
            "z1": [
                disk("d1", labels={"owner": "alice@yugabyte.com", "ttl": "{'hours': 1}"}),
                disk("d2", users=["vm"]),
                disk("d3", labels={"keep": "yes"}),
                disk("d-notags", labels={"skip": "1"}),
                disk("d-ttl", labels={"ttl": "{'days': 99999}"}),
            ],
            "z2": [disk("d4", labels={"owner": "alice@yugabyte.com"}), disk("e5")],
        }
    )


def vms() -> FakeGcp:
    return FakeGcp(
        {
            "z1": [
                instance("vm1", labels={"k": "v", "ttl": "{'hours': 1}"}),
                instance("vm2", created="2999-01-01T00:00:00.000000+00:00"),
                instance("vm-exc", labels={"k": "v", "keep": "yes"}),
                instance("vm-notags", labels={"k": "v", "skip": "1"}),
            ],
            "z2": [instance("vm3"), instance("vm4", status="TERMINATED")],
        }
    )


def spots() -> FakeGcp:
    return FakeGcp(
        {
            "z1": [
                instance(
                    "vm1",
                    spot=True,
                    labels={"k": "v", "ttl": "{'hours': 1}"},
                    disks=[attached_disk("sd1"), attached_disk("sd-auto", auto_delete=True)],
                ),
                instance("vm2", spot=True, created="2999-01-01T00:00:00.000000+00:00"),
                instance("vm-std", spot=False),
                instance("vm-exc", spot=True, labels={"k": "v", "keep": "yes"}),
            ],
            "z2": [instance("vm3", spot=True, disks=[attached_disk("sd2", zone="z2")])],
        }
    )


DISK_GUARDS: dict[str, Any] = {
    "detach_age": AGE,
    "exception_tags": {"keep": ["yes"]},
    "notags": {"skip": ["1"]},
    "custom_age_tag_key": "ttl",
}
VM_GUARDS: dict[str, Any] = {
    "age": AGE,
    "filter_tags": TAGS,
    "exception_tags": {"keep": ["yes"]},
    "notags": {"skip": ["1"]},
    "custom_age_tag_key": "ttl",
}
CASES: dict[str, tuple[str, dict[str, Any], Callable[[], FakeGcp]]] = {
    "ip": ("ip", {"name_regex": ["a-"], "exception_regex": ["x-"], "age": AGE}, ips),
    "ip-match-all": ("ip", {"age": AGE}, ips),
    "disk": ("disk", {**DISK_GUARDS, "name_regex": ["d"]}, disks),
    "disk-owners": (
        "disk",
        {**DISK_GUARDS, "slack_notify_users": True, "slack_user_label": "owner"},
        disks,
    ),
    "disk-excluded": ("disk", {**DISK_GUARDS, "exception_regex": ["d"]}, disks),
    "vm-delete": ("vm", {**VM_GUARDS, "operation_type": "delete"}, vms),
    "vm-delete-states": (
        "vm",
        {"age": AGE, "operation_type": "delete", "resource_states": ["TERMINATED", "RUNNING"]},
        vms,
    ),
    "vm-stop": ("vm", {**VM_GUARDS, "operation_type": "stop"}, vms),
    "vm-nothing": (
        "vm",
        {"age": AGE, "filter_tags": {"other": ["v"]}, "operation_type": "delete"},
        vms,
    ),
    "spot": ("spot_instances", {**VM_GUARDS, "operation_type": "delete"}, spots),
    "spot-nothing": (
        "spot_instances",
        {"age": AGE, "filter_tags": {"other": ["v"]}, "operation_type": "delete"},
        spots,
    ),
}


def apply_scenario(client: FakeGcp, scenario: str) -> None:
    """The fault of a scenario name (the keys of `RUN_LOG`) injected into the fake client."""
    fault, _, argument = scenario.partition(":")
    if fault == "fail_delete":
        client.fail_delete = argument
    elif fault == "fail_list_first":
        client.fail_list = next(iter(client.data))
    elif fault == "fail_list_last":
        client.fail_list = list(client.data)[-1]
    elif fault == "fail_zones":
        client.fail_zones = True
    elif fault == "fail_close":
        client.fail_close = True
    elif fault == "fail_close_after_delete":
        client.fail_close_after_delete = True


def run_both(  # noqa: PLR0913 - the case, the three switches and the scenario
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: str,
    *,
    dry_run: bool,
    slack: bool,
    influx: bool,
    scenario: str = "",
) -> tuple[Outcome, Outcome]:
    """The oracle outcome and the routed one; the oracle logs no run-log lines, so its `run_log`
    is the frozen lines of the legacy run of the same case."""
    kind, flags, make = CASES[case]
    outcomes = []
    for routed in (False, True):
        client = make()
        apply_scenario(client, scenario)
        outcomes.append(
            run_pair(
                monkeypatch,
                caplog,
                kind,
                {**BASE, **flags, "dry_run": dry_run},
                client,
                routed=routed,
                slack=slack,
                influx=influx,
            )
        )
    key = (case, dry_run, slack, influx, scenario)
    return replace(outcomes[0], run_log=list(RUN_LOG[key]), key=key), outcomes[1]


def by_kind(lines: list[str]) -> dict[str, list[str]]:
    kinds: dict[str, list[str]] = {}
    for line in lines:
        kinds.setdefault(line.split(" ")[0], []).append(line)
    return kinds


FIELDS = ("slack", "influx", "stdout", "error", "gcp_calls")


def view(outcome: Outcome, field: str, dry_run: bool) -> Any:
    """The GCP calls of a real run are compared as a set of calls (order of deletes may differ)."""
    value = getattr(outcome, field)
    return sorted(value) if field == "gcp_calls" and not dry_run else value


def assert_equal(old: Outcome, new: Outcome) -> None:
    """`old` is the oracle run with the frozen run-log lines of the legacy run.

    The routed run lists every region before it logs ACTION lines (BUG-400, and `plan()` of the
    Cleaners logs a dry run's ACTION lines after its REGION lines), so the REGION and ACTION
    lines are compared per kind, each in order; every other line keeps its place. Slack calls,
    Influx points, console output, error and GCP calls equal the oracle's, except for the fields
    of a case in `DIVERGENCES` (a bug fixed since `main`): exactly those fields differ from the
    oracle, and the routed run equals the frozen values."""
    assert old.key is not None
    dry_run = old.key[1]
    assert by_kind(new.run_log) == by_kind(old.run_log)
    assert (new.run_log[0], new.run_log[-1]) == (old.run_log[0], old.run_log[-1])
    bugs, frozen = DIVERGENCES.get(old.key, ((), {}))
    differing = {f for f in FIELDS if view(old, f, dry_run) != view(new, f, dry_run)}
    assert differing == set(frozen), bugs
    for field, value in frozen.items():
        assert view(new, field, dry_run) == value, (field, bugs)


@pytest.mark.parametrize("influx", [False, True], ids=["no-influx", "influx"])
@pytest.mark.parametrize("slack", [False, True], ids=["no-slack", "slack"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["real", "dry"])
@pytest.mark.parametrize("case", list(CASES))
def test_routed_run_equals_legacy_handler(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: str,
    dry_run: bool,
    slack: bool,
    influx: bool,
) -> None:
    old, new = run_both(monkeypatch, caplog, case, dry_run=dry_run, slack=slack, influx=influx)
    assert_equal(old, new)
    assert old.run_log and old.run_log[0].startswith("RUN START")
    assert old.run_log[-1].startswith("RESULT")
    if slack:
        assert old.slack
    if influx:
        assert old.influx
    if dry_run:
        assert not [c for c in new.gcp_calls if c[0].startswith(("delete", "stop"))]
        assert not [c for c in old.gcp_calls if c[0].startswith(("delete", "stop"))]
        assert any(line.startswith("ACTION") for line in old.run_log) == (
            case not in ("vm-nothing", "spot-nothing")
        )


@pytest.mark.parametrize("dry_run", [False, True], ids=["real", "dry"])
@pytest.mark.parametrize("case", ["ip", "disk", "vm-delete", "vm-stop", "spot"])
def test_one_failed_delete_is_recorded_and_the_run_goes_on(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: str,
    dry_run: bool,
) -> None:
    fail = {"ip": "a-2", "disk": "d1", "vm-delete": "vm1", "vm-stop": "vm1", "spot": "vm1"}[case]

    old, new = run_both(
        monkeypatch,
        caplog,
        case,
        dry_run=dry_run,
        slack=True,
        influx=True,
        scenario=f"fail_delete:{fail}",
    )
    assert_equal(old, new)
    assert old.error is None
    assert dry_run or any(line.startswith("ERRORS") for line in old.run_log)


@pytest.mark.parametrize("dry_run", [False, True], ids=["real", "dry"])
@pytest.mark.parametrize("case", ["ip", "disk", "vm-delete", "spot"])
def test_first_zone_listing_error_gives_the_same_run(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: str,
    dry_run: bool,
) -> None:
    old, new = run_both(
        monkeypatch,
        caplog,
        case,
        dry_run=dry_run,
        slack=True,
        influx=True,
        scenario="fail_list_first",
    )
    assert_equal(old, new)


@pytest.mark.parametrize("dry_run", [False, True], ids=["real", "dry"])
@pytest.mark.parametrize("case", ["ip", "disk", "vm-delete", "vm-stop", "spot"])
def test_project_listing_error_raises_the_same_error_after_the_same_report(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: str,
    dry_run: bool,
) -> None:
    """`disk` reports (an empty list) before it raises, as `delete_disks` did; the others raise
    without a Slack or Influx message."""

    old, new = run_both(
        monkeypatch, caplog, case, dry_run=dry_run, slack=True, influx=True, scenario="fail_zones"
    )
    assert_equal(old, new)
    assert old.error is not None
    assert bool(old.slack) == (case == "disk")


def test_owner_pings_with_a_project_listing_error_differ_known(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Known difference (see the BUG rows): `_ReportThenRaise` rebuilds a plan without
    owner groups, so with `--slack_notify_users` the error path posts an empty disk list where
    `delete_disks` only looked up the user groups. The error and the Influx point count agree."""

    old, new = run_both(
        monkeypatch,
        caplog,
        "disk-owners",
        dry_run=False,
        slack=True,
        influx=True,
        scenario="fail_zones",
    )
    assert new.run_log == old.run_log
    assert new.error == old.error
    assert old.slack == [("usergroups_list",)]
    assert [call[0] for call in new.slack] == ["chat_postMessage"]
    assert new.influx[0][3]["count"] == old.influx[0][3]["count"] == 0


@pytest.mark.parametrize("case", ["ip", "disk", "vm-delete", "spot"])
def test_later_listing_error_deletes_fewer_never_more(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, case: str
) -> None:
    """The one allowed difference (BUG-400): `plan()` lists every region before any delete."""

    old, new = run_both(
        monkeypatch,
        caplog,
        case,
        dry_run=False,
        slack=False,
        influx=False,
        scenario="fail_list_last",
    )
    deletes = ("delete_address", "delete_disk", "delete_instance")
    old_deleted = [c for c in old.gcp_calls if c[0] in deletes]
    new_deleted = [c for c in new.gcp_calls if c[0] in deletes]
    assert set(new_deleted) <= set(old_deleted)
    assert len(new_deleted) <= len(old_deleted)
    if case == "ip":
        assert [line for line in old.run_log if line.startswith("ACTION")]
        assert not [line for line in new.run_log if line.startswith("ACTION")]  # BUG-400
        assert new.error == old.error == "RuntimeError: listing denied"
    else:
        assert_equal(old, new)


@pytest.mark.parametrize("dry_run", [False, True], ids=["real", "dry"])
def test_disk_close_error_reports_partial_succeeded_before_reraising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, dry_run: bool
) -> None:
    old, new = run_both(
        monkeypatch,
        caplog,
        "disk",
        dry_run=dry_run,
        slack=True,
        influx=True,
        scenario="fail_close" if dry_run else "fail_close_after_delete",
    )
    assert_equal(old, new)
    assert old.error == "RuntimeError: close failed"
    assert old.influx[0][3]["names"] == "['d1', 'd-notags', 'd4', 'e5']"
    assert new.influx[0][3]["names"] == "['d1', 'd4']"  # BUG-005, BUG-006 (see DIVERGENCES)


@pytest.mark.parametrize("case", ["ip", "ip-match-all"])
def test_ip_dry_run_logs_each_region_then_its_actions_as_legacy(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, case: str
) -> None:
    old, new = run_both(monkeypatch, caplog, case, dry_run=True, slack=False, influx=False)
    assert new.run_log == old.run_log
    kinds = [line.split()[0] for line in new.run_log]
    assert kinds.index("ACTION") < len(kinds) - 1 - kinds[::-1].index("REGION")


def test_route_table_routes_every_kind() -> None:
    assert sorted(bridge.TABLES["gcp"]) == [
        "disk",
        "ip",
        "spot_instance_requests",
        "spot_instances",
        "vm",
    ]
    assert bridge.route_for("gcp", "spot_instances") is not None
