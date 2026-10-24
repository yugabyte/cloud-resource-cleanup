"""The run-log lines (REGION, ACTION, SKIPPED, FAILED, RESULT) of every GCP cleaner.

Each test begins a run with a fake clock, drives a cleaner on a fake client and asserts the
exact log lines. Log text only: calls, results and what is deleted are covered by the unit and
parity tests of each module.
"""

import itertools
import logging
import types
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import pytest

from crc.core import logger
from crc.core.cleaner import Operation
from crc.gcp import _scan
from crc.gcp.ip import IpCleaner
from crc.gcp.spot_vm import SpotVMCleaner
from tests import test_gcp_disk as disks
from tests import test_gcp_ip as ips
from tests import test_gcp_spot_instances as spots
from tests import test_gcp_vm as vms
from tests.helpers_ad_vm_engine import run_vm
from tests.helpers_wc_gcp_engine import run_engine

SCAN_SECONDS = 7


@pytest.fixture(autouse=True)
def _scan_clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    ticks = itertools.cycle([0.0, float(SCAN_SECONDS)])
    monkeypatch.setattr(_scan, "time", types.SimpleNamespace(monotonic=lambda: next(ticks)))
    yield
    logger.finish()


def begin(resource: str, operation: str = "delete", *, dry_run: bool = False) -> None:
    logger.begin("gcp", resource, operation, dry_run, {}, clock=lambda: 0.0)


def run_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The run-log lines (everything except per-resource diagnostics), in order."""
    prefixes = ("REGION ", "ACTION ", "SKIPPED ", "ERRORS ", "FAILED ", "RESULT ")
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(prefixes)]


def result(resource: str, op: str, dry: str, counts: str) -> str:
    return f"RESULT cloud=gcp resource={resource} operation={op} dry_run={dry} {counts}"


def region(resource: str, name: str, scanned: int, selected: int) -> str:
    return (
        f"REGION cloud=gcp resource={resource} region={name} scanned={scanned} "
        f"selected={selected} seconds={SCAN_SECONDS}"
    )


def action(kind: str, resource: str, where: str, ident: str) -> str:
    return f"ACTION {kind} cloud=gcp resource={resource} region={where} id={ident} name={ident}"


def skipped(resource: str, reason: str, count: int) -> str:
    return f"SKIPPED cloud=gcp resource={resource} reason={reason} count={count}"


def test_vm_delete_logs_region_actions_skips_and_failure(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = vms.FakeClient(
        {
            "z1": [vms.inst("a"), vms.inst("young", days=0.1), vms.inst("off", status="STOPPED")],
            "z2": [vms.inst("bad"), vms.inst("b")],
        },
        fail_on="bad",
    )
    cleaner = vms.cleaner(client)
    begin("vm")
    plan = cleaner.plan(vms.ctx())
    cleaner.apply(plan, vms.ctx())
    logger.finish()
    assert run_lines(caplog) == [
        region("vm", "z1", 3, 1),
        region("vm", "z2", 2, 2),
        action("deleted", "vm", "z1", "a"),
        action("deleted", "vm", "z2", "b"),
        skipped("vm", "state", 1),
        skipped("vm", "too_young", 1),
        "ERRORS count=1",
        "FAILED cloud=gcp resource=vm region=z2 id=bad error=RuntimeError: boom",
        result(
            "vm",
            "delete",
            "false",
            "selected=2 done=2 failed=1 skipped=2 regions=2 regions_skipped=0 duration_s=0",
        ),
    ]


def test_vm_stop_dry_run_logs_would_stop_from_plan(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    cleaner = vms.cleaner(vms.FakeClient({"z": [vms.inst("a"), vms.inst("b")]}))
    begin("vm", "stop", dry_run=True)
    cleaner.plan(vms.ctx(dry_run=True, operation=Operation.STOP))
    logger.finish()
    assert run_lines(caplog) == [
        region("vm", "z", 2, 2),
        action("would_stop", "vm", "z", "a"),
        action("would_stop", "vm", "z", "b"),
        result(
            "vm",
            "stop",
            "true",
            "selected=2 done=0 failed=0 skipped=0 regions=1 regions_skipped=0 duration_s=0",
        ),
    ]


def test_vm_engine_stop_and_dry_run_delete(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = vms.FakeClient({"z": [vms.inst("a")]})
    begin("vm", "stop")
    run_vm("gcp", vms.cleaner(client, age=1), operation=Operation.STOP)
    logger.finish()
    begin("vm", dry_run=True)
    run_vm("gcp", vms.cleaner(client, age=1), dry_run=True)
    logger.finish()
    lines = run_lines(caplog)
    assert action("stopped", "vm", "z", "a") in lines
    assert action("would_delete", "vm", "z", "a") in lines


def test_vm_label_dump_is_debug_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    cleaner = vms.cleaner(vms.FakeClient({"z": [vms.inst("a", labels={"k": "v"})]}))
    list(cleaner.plan_zone("z", lambda: vms.NOW))
    assert "{'k': 'v'}" not in [r.getMessage() for r in caplog.records]
    caplog.set_level(logging.DEBUG)
    list(cleaner.plan_zone("z", lambda: vms.NOW))
    dump = [r for r in caplog.records if r.getMessage() == "{'k': 'v'}"]
    assert [r.levelno for r in dump] == [logging.DEBUG]


def test_disk_logs_regions_skips_actions_and_failure(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = disks.FakeClient(
        {
            "z1": [disks.disk("a"), disks.disk("att", users=["vm"]), disks.disk("new", days=0.1)],
            "z2": [disks.disk("bad"), disks.disk("keep", labels={"k": "v"})],
        },
        fail_on="bad",
    )
    cleaner = disks.cleaner(client, exception_labels={"k": ["v"]})
    begin("disk")
    cleaner.apply(cleaner.plan(disks.ctx()), disks.ctx())
    logger.finish()
    assert run_lines(caplog) == [
        region("disk", "z1", 3, 1),
        region("disk", "z2", 2, 1),
        action("deleted", "disk", "z1", "a"),
        skipped("disk", "attached", 1),
        skipped("disk", "exception_tag", 1),
        skipped("disk", "too_young", 1),
        "ERRORS count=1",
        "FAILED cloud=gcp resource=disk region=z2 id=bad error=RuntimeError: denied",
        result(
            "disk",
            "delete",
            "false",
            "selected=1 done=1 failed=1 skipped=3 regions=2 regions_skipped=0 duration_s=0",
        ),
    ]


def test_disk_dry_run_logs_would_delete_and_never_done(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    cleaner = disks.cleaner(disks.FakeClient({"z": [disks.disk("a")]}))
    begin("disk", dry_run=True)
    cleaner.plan(disks.ctx(dry_run=True))
    logger.finish()
    assert run_lines(caplog) == [
        region("disk", "z", 1, 1),
        action("would_delete", "disk", "z", "a"),
        result(
            "disk",
            "delete",
            "true",
            "selected=1 done=0 failed=0 skipped=0 regions=1 regions_skipped=0 duration_s=0",
        ),
    ]


def test_disk_exception_regex_and_missing_detach_reasons(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    never = disks.disk("never")
    never.last_detach_timestamp = ""
    client = disks.FakeClient({"z": [disks.disk("tmp-1"), never]})
    cleaner = disks.cleaner(client, exception_regex=["tmp-"])
    begin("disk", dry_run=True)
    list(cleaner.plan_zone("z", lambda: disks.NOW))
    logger.finish()
    lines = run_lines(caplog)
    assert skipped("disk", "exception_regex", 1) in lines
    assert skipped("disk", "never_detached", 1) in lines


def test_disk_engine_failure_is_recorded(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = disks.FakeClient({"z": [disks.disk("bad")]}, fail_on="bad")
    begin("disk")
    run_engine(disks.cleaner(client), "disk", now=disks.NOW)
    logger.finish()
    lines = run_lines(caplog)
    assert "FAILED cloud=gcp resource=disk region=z id=bad error=RuntimeError: denied" in lines


def test_ip_logs_regions_actions_and_skips(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client = ips.FakeClient(
        {"r1": [ips.addr("ip-a"), ips.addr("other"), ips.addr("ip-used", "IN_USE")], "r2": []}
    )
    cleaner = ips.cleaner(client, ["ip-"])
    begin("ip")
    cleaner.apply(cleaner.plan(ips.ctx()), ips.ctx())
    logger.finish()
    assert run_lines(caplog) == [
        region("ip", "r1", 3, 1),
        region("ip", "r2", 0, 0),
        action("deleted", "ip", "r1", "ip-a"),
        skipped("ip", "filter_mismatch", 1),
        skipped("ip", "state", 1),
        result(
            "ip",
            "delete",
            "false",
            "selected=1 done=1 failed=0 skipped=2 regions=2 regions_skipped=0 duration_s=0",
        ),
    ]


def test_ip_dry_run_cleaner_and_engine(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = ips.FakeClient({"r": [ips.addr("a")]})
    begin("ip", dry_run=True)
    IpCleaner(client, ips.PROJECT, None, None).plan(ips.ctx(dry_run=True))
    run_engine(IpCleaner(client, ips.PROJECT, None, None), "ip", dry_run=True)
    logger.finish()
    assert run_lines(caplog).count(action("would_delete", "ip", "r", "a")) == 2  # noqa: PLR2004


def test_ip_delete_error_is_recorded_and_still_raises(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = ips.FakeClient({"r": [ips.addr("a")]})

    def boom(project_id: str, region: str, address: str) -> ips.FakeOperation:
        raise RuntimeError("quota")

    client.delete_address = boom  # type: ignore[method-assign]
    begin("ip")
    with pytest.raises(RuntimeError, match="quota"):
        run_engine(IpCleaner(client, ips.PROJECT, None, None), "ip")
    logger.finish()
    assert "FAILED cloud=gcp resource=ip region=r id=a error=RuntimeError: quota" in run_lines(
        caplog
    )


def test_ip_address_dump_is_debug_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    ips.cleaner(ips.FakeClient({"r": [ips.addr("a")]})).plan_region("r")
    assert not [r for r in caplog.records if r.getMessage().startswith("namespace(")]
    caplog.set_level(logging.DEBUG)
    ips.cleaner(ips.FakeClient({"r": [ips.addr("a")]})).plan_region("r")
    dumps = [r for r in caplog.records if r.getMessage().startswith("namespace(")]
    assert [r.levelno for r in dumps] == [logging.DEBUG]


def spot_cleaner(client: Any) -> SpotVMCleaner:
    return SpotVMCleaner(client, spots.SETTINGS)


def test_spot_real_run_logs_region_action_and_skipped(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    young = spots.instance("young")
    young.creation_timestamp = spots.NOW.strftime("%Y-%m-%dT%H:%M:%S.%f%z")
    plain = spots.instance("plain")
    plain.scheduling = types.SimpleNamespace(preemptible=False, provisioning_model="")
    client = spots.FakeClient([spots.instance("a"), young, plain])
    cleaner = spot_cleaner(client)
    begin("spot_instances")
    cleaner.apply(cleaner.plan(spots.context(dry_run=False)), spots.context(dry_run=False))
    logger.finish()
    assert run_lines(caplog) == [
        region("spot_instances", "z", 3, 1),
        action("deleted", "spot_instances", "z", "a"),
        skipped("spot_instances", "not_spot", 1),
        skipped("spot_instances", "too_young", 1),
        result(
            "spot_instances",
            "delete",
            "false",
            "selected=1 done=1 failed=0 skipped=2 regions=1 regions_skipped=0 duration_s=0",
        ),
    ]


def test_spot_dry_run_leftover_disk_skip_is_debug_and_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    client = spots.FakeClient([spots.instance("a", [spots.leftover("d1")])])
    client.get_disk = lambda *args: {"labels": {"keep": "yes"}}  # type: ignore[method-assign]
    settings = replace(spots.SETTINGS, exception_labels={"keep": ["yes"]})
    begin("spot_instances", dry_run=True)
    SpotVMCleaner(client, settings).plan(spots.context(dry_run=True))
    logger.finish()
    skip_records = [r for r in caplog.records if r.getMessage().startswith("Skipping leftover")]
    assert [r.levelno for r in skip_records] == [logging.DEBUG]
    lines = run_lines(caplog)
    assert action("would_delete", "spot_instances", "z", "a") in lines
    assert skipped("spot_instances", "disk_exception_tag", 1) in lines


def test_spot_listing_error_counts_a_skipped_region(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = spots.FakeClient([])

    def broken(project_id: str, zone: str) -> Any:
        raise ConnectionError("down")

    client.list_instances = broken  # type: ignore[method-assign]
    begin("spot_instances")
    spot_cleaner(client).plan(spots.context(dry_run=False))
    logger.finish()
    assert run_lines(caplog) == [
        result(
            "spot_instances",
            "delete",
            "false",
            "selected=0 done=0 failed=0 skipped=0 regions=0 regions_skipped=1 duration_s=0",
        ),
    ]
    assert any(
        r.levelno == logging.ERROR and "failed listing" in r.getMessage() for r in caplog.records
    )


def test_spot_failed_instance_delete_is_recorded(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = spots.FakeClient([spots.instance("a")])

    def boom(project_id: str, zone: str, name: str) -> Any:
        raise RuntimeError("nope")

    client.delete_instance = boom  # type: ignore[method-assign,assignment]
    cleaner = spot_cleaner(client)
    begin("spot_instances")
    cleaner.apply(cleaner.plan(spots.context(dry_run=False)), spots.context(dry_run=False))
    logger.finish()
    lines = run_lines(caplog)
    assert (
        "FAILED cloud=gcp resource=spot_instances region=z id=a error=RuntimeError: nope" in lines
    )
    assert not [line for line in lines if line.startswith("ACTION")]


def test_cleaners_log_nothing_extra_without_a_run(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    vms.cleaner(vms.FakeClient({"z": [vms.inst("a")]})).plan(vms.ctx())
    disks.cleaner(disks.FakeClient({"z": [disks.disk("a")]})).plan(disks.ctx())
    ips.cleaner(ips.FakeClient({"r": [ips.addr("a")]})).plan(ips.ctx())
    assert run_lines(caplog) == []
