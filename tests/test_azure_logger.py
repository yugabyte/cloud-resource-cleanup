"""LOG-AZU: the Azure cleaners report to `crc.core.logger` (REGION, ACTION, SKIPPED, FAILED).

Azure has no regions: one REGION line per resource group, `region=<resource group>`. Every case
runs the adapter `crc.py` uses under a begun run and asserts the exact new lines; the tag dumps
(BUG-358) and the per-leftover-disk skip lines are DEBUG with unchanged text.
"""

import logging
import re
from collections.abc import Iterator
from typing import Any

import pytest

from crc.azu import _logger
from crc.azu.disk import AzureDiskCleaner, Disk, DiskSettings
from crc.azu.ip import IP
from crc.azu.nic import NIC, AzureNicCleaner
from crc.azu.spot_vm import SpotVM
from crc.azu.vm import VM, VmCleaner, VmSettings
from crc.core import logger
from crc.core.cleaner import Operation, RunContext
from tests import helpers_azure_disk as hd
from tests import helpers_azure_ip as hi
from tests import helpers_azure_nic as hn
from tests import helpers_azure_spot as hs
from tests import helpers_azure_vm as hv

_PREFIXES = ("RUN START", "REGION", "ACTION", "SKIPPED", "ERRORS", "FAILED", "RESULT")
ENV = {"env": ["ci"]}


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        self.now += 2
        return self.now


@pytest.fixture(autouse=True)
def _caplog_info(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    caplog.set_level(logging.DEBUG)
    logger.finish()
    yield
    logger.finish()


def _begin(resource: str, *, dry_run: bool) -> None:
    logger.begin("azure", resource, "delete", dry_run, {}, clock=_Clock())


def _lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith(_PREFIXES)]
    return [re.sub(r"seconds=\d+", "seconds=S", line) for line in lines]


# ----- disk ---------------------------------------------------------------------------------


def _disks(monkeypatch: pytest.MonkeyPatch) -> hd.FakeAzure:
    azure = hd.FakeAzure(
        [
            hd.disk("a", {"env": "ci"}),
            hd.disk("b", {"env": "ci"}, state="Reserved"),
            hd.disk("c", {"other": "x"}),
            hd.disk("d", {"env": "ci"}),
        ]
    )
    azure.install(monkeypatch)
    return azure


def _disk(clock_now: Any = hd.NOW) -> Disk:
    adapter = Disk("rg", False, {"env": ["ci"]}, None, None, None, None)
    adapter.clock = lambda: clock_now
    return adapter


def test_disk_real_run_reports_region_actions_and_skips(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _disks(monkeypatch)
    _begin("disk", dry_run=False)
    _disk().delete()
    logger.finish()
    assert _lines(caplog) == [
        "RUN START cloud=azure resource=disk operation=delete dry_run=false",
        "ACTION deleted cloud=azure resource=disk region=rg id=a name=a",
        "ACTION deleted cloud=azure resource=disk region=rg id=d name=d",
        "REGION cloud=azure resource=disk region=rg scanned=4 selected=2 seconds=S",
        "SKIPPED cloud=azure resource=disk reason=filter_tags count=1",
        "SKIPPED cloud=azure resource=disk reason=state count=1",
        "RESULT cloud=azure resource=disk operation=delete dry_run=false selected=2 done=2 "
        "failed=0 skipped=2 regions=1 regions_skipped=0 duration_s=2",
    ]


def test_disk_dry_run_logs_would_delete_and_done_is_zero(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    azure = _disks(monkeypatch)
    adapter = Disk("rg", True, {"env": ["ci"]}, None, None, None, None)
    _begin("disk", dry_run=True)
    adapter.delete()
    logger.finish()
    lines = _lines(caplog)
    assert lines[1:3] == [
        "ACTION would_delete cloud=azure resource=disk region=rg id=a name=a",
        "ACTION would_delete cloud=azure resource=disk region=rg id=d name=d",
    ]
    assert "selected=2 done=0 failed=0 skipped=2" in lines[-1]
    assert [c for c in azure.calls if c[0] == "begin_delete"] == []


def test_disk_tag_dump_is_debug_with_unchanged_text(caplog: pytest.LogCaptureFixture) -> None:
    azure = hd.FakeAzure([hd.disk("a", {"env": "ci"})])
    cleaner = AzureDiskCleaner(
        lambda: azure.client(None, "sub"), "rg", DiskSettings(filter_tags={"env": ["ci"]})
    )
    cleaner.decide(hd.disk("a", {"env": "ci"}), hd.NOW)
    dumps = [r for r in caplog.records if r.getMessage() == "{'env': 'ci'}"]
    assert [r.levelno for r in dumps] == [logging.DEBUG]


def test_disk_cleaner_plan_dry_run_logs_each_action_once(caplog: pytest.LogCaptureFixture) -> None:
    azure = hd.FakeAzure([hd.disk("a", {"env": "ci"})])
    cleaner = AzureDiskCleaner(
        lambda: azure.client(None, "sub"), "rg", DiskSettings(filter_tags={"env": ["ci"]})
    )
    context = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=hd.NOW)
    _begin("disk", dry_run=True)
    plan = cleaner.plan(context)
    cleaner.apply(plan, context)
    logger.finish()
    assert [line for line in _lines(caplog) if line.startswith("ACTION")] == [
        "ACTION would_delete cloud=azure resource=disk region=rg id=a name=a"
    ]


# ----- ip -----------------------------------------------------------------------------------


def test_ip_real_and_dry_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    azure = hi.FakeAzure(
        [hi.ip("a", {"env": "ci"}), hi.ip("b", {"env": "ci"}, config={"id": "x"}), hi.ip("c", {})]
    )
    azure.install(monkeypatch)
    _begin("ip", dry_run=False)
    IP("rg", False, {"env": ["ci"]}, {}, {}).delete()
    logger.finish()
    assert _lines(caplog)[1:] == [
        "ACTION deleted cloud=azure resource=ip region=rg id=a name=a",
        "REGION cloud=azure resource=ip region=rg scanned=3 selected=1 seconds=S",
        "SKIPPED cloud=azure resource=ip reason=attached count=1",
        "SKIPPED cloud=azure resource=ip reason=filter_tags count=1",
        "RESULT cloud=azure resource=ip operation=delete dry_run=false selected=1 done=1 "
        "failed=0 skipped=2 regions=1 regions_skipped=0 duration_s=2",
    ]
    caplog.clear()
    _begin("ip", dry_run=True)
    IP("rg", True, {"env": ["ci"]}, {}, {}).delete()
    logger.finish()
    lines = _lines(caplog)
    assert lines[1] == "ACTION would_delete cloud=azure resource=ip region=rg id=a name=a"
    assert "selected=1 done=0" in lines[-1]


# ----- nic ----------------------------------------------------------------------------------


def test_nic_failure_after_three_attempts_is_listed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    boom = RuntimeError("busy")
    azure = hn.FakeAzure(
        [hn.nic("nic-a"), hn.nic("nic-b"), hn.nic("nic-c", "vm")],
        begin_script={"nic-b": [boom, boom, boom]},
    )
    azure.install(monkeypatch)
    _begin("nic", dry_run=False)
    adapter = NIC("rg", False, ["nic"], None)
    adapter.delete()
    logger.finish()
    assert adapter.get_deleted == ["nic-a"]
    assert _lines(caplog)[1:] == [
        "ACTION deleted cloud=azure resource=nic region=rg id=nic-a name=nic-a",
        "REGION cloud=azure resource=nic region=rg scanned=3 selected=2 seconds=S",
        "SKIPPED cloud=azure resource=nic reason=attached count=1",
        "ERRORS count=1",
        "FAILED cloud=azure resource=nic region=rg id=nic-b error=RuntimeError: busy",
        "RESULT cloud=azure resource=nic operation=delete dry_run=false selected=1 done=1 "
        "failed=1 skipped=1 regions=1 regions_skipped=0 duration_s=2",
    ]
    errors = [r.levelno for r in caplog.records if r.getMessage().startswith(("ERRORS", "FAILED"))]
    assert errors == [logging.ERROR, logging.ERROR]


def test_nic_dry_run_and_no_double_action_from_plan_and_apply(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    azure = hn.FakeAzure([hn.nic("nic-a")])
    azure.install(monkeypatch)
    _begin("nic", dry_run=True)
    NIC("rg", True, ["nic"], None).delete()
    logger.finish()
    assert "ACTION would_delete cloud=azure resource=nic region=rg id=nic-a name=nic-a" in _lines(
        caplog
    )
    caplog.clear()
    cleaner = AzureNicCleaner(lambda: azure.client(None, "s"), "rg", ["nic"], None)
    context = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=hn.NOW)
    _begin("nic", dry_run=True)
    cleaner.apply(cleaner.plan(context), context)
    logger.finish()
    assert [x for x in _lines(caplog) if x.startswith("ACTION")] == [
        "ACTION would_delete cloud=azure resource=nic region=rg id=nic-a name=nic-a"
    ]


# ----- vm -----------------------------------------------------------------------------------


def _vm_adapter(*, dry_run: bool) -> VM:
    return VM("rg", dry_run, ENV, {}, {"hours": 1}, "", {}, clock=lambda: hv.NOW)


def test_vm_delete_reports_skips_and_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    azure = hv.FakeAzure(
        [
            hv.vm("vm-a"),
            hv.vm("vm-young", hours=0.1),
            hv.vm("vm-off", statuses=hv.STOPPED),
            hv.vm("vm-bad", statuses=RuntimeError("view failed")),
            hv.vm("vm-other", tags={"x": "y"}),
        ]
    )
    azure.install(monkeypatch)
    _begin("vm", dry_run=False)
    _vm_adapter(dry_run=False).delete()
    logger.finish()
    assert _lines(caplog)[1:] == [
        "ACTION deleted cloud=azure resource=vm region=rg id=vm-a name=vm-a",
        "REGION cloud=azure resource=vm region=rg scanned=5 selected=1 seconds=S",
        "SKIPPED cloud=azure resource=vm reason=filter_tags count=1",
        "SKIPPED cloud=azure resource=vm reason=state count=1",
        "SKIPPED cloud=azure resource=vm reason=too_young count=1",
        "ERRORS count=1",
        "FAILED cloud=azure resource=vm region=rg id=vm-bad error=RuntimeError: view failed",
        "RESULT cloud=azure resource=vm operation=delete dry_run=false selected=1 done=1 "
        "failed=1 skipped=3 regions=1 regions_skipped=0 duration_s=2",
    ]


def test_vm_stop_and_dry_run_kinds(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    hv.FakeAzure([hv.vm("vm-a")]).install(monkeypatch)
    _begin("vm", dry_run=False)
    _vm_adapter(dry_run=False).stop()
    logger.finish()
    assert "ACTION stopped cloud=azure resource=vm region=rg id=vm-a name=vm-a" in _lines(caplog)
    caplog.clear()
    _begin("vm", dry_run=True)
    _vm_adapter(dry_run=True).stop()
    logger.finish()
    lines = _lines(caplog)
    assert "ACTION would_stop cloud=azure resource=vm region=rg id=vm-a name=vm-a" in lines
    assert "selected=1 done=0" in lines[-1]


def test_vm_nic_failure_after_24_attempts_is_listed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    boom = RuntimeError("nic busy")
    azure = hv.FakeAzure([hv.vm("vm-a")], scripts={"nic_begin": {"vm-a-NIC": [boom] * 24}})
    azure.install(monkeypatch)
    _begin("vm", dry_run=False)
    _vm_adapter(dry_run=False).delete()
    logger.finish()
    lines = _lines(caplog)
    assert (
        "FAILED cloud=azure resource=vm region=rg id=vm-a-NIC "
        "error=NIC delete failed after 24 attempts" in lines
    )


def test_vm_failed_delete_counts_as_selected_without_action(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    boom = RuntimeError("delete failed")
    azure = hv.FakeAzure([hv.vm("vm-a")], scripts={"vm_begin": {"vm-a": [boom]}})
    azure.install(monkeypatch)
    _begin("vm", dry_run=False)
    _vm_adapter(dry_run=False).delete()
    logger.finish()
    lines = _lines(caplog)
    assert not [x for x in lines if x.startswith("ACTION")]
    assert "REGION cloud=azure resource=vm region=rg scanned=1 selected=1 seconds=S" in lines
    assert "FAILED cloud=azure resource=vm region=rg id=vm-a error=RuntimeError: delete failed" in (
        lines
    )
    assert "selected=0 done=0 failed=1 skipped=0" in lines[-1]


def test_vm_tag_dump_is_debug_with_unchanged_text(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    azure = hv.FakeAzure([])
    azure.install(monkeypatch)
    cleaner = VmCleaner(
        lambda: azure.compute_client(None, "s"),
        lambda: azure.network_client(None, "s"),
        "rg",
        VmSettings(filter_tags=ENV, age={"hours": 1}),
    )
    cleaner.decide(hv.vm("vm-a"), lambda: hv.NOW)
    dumps = [r for r in caplog.records if r.getMessage() == "{'env': 'ci'}"]
    assert [r.levelno for r in dumps] == [logging.DEBUG]


# ----- spot vm ------------------------------------------------------------------------------


def _spot(*, dry_run: bool) -> SpotVM:
    return SpotVM(hs.RG, dry_run, None, None, {"days": 1}, None, None, clock=lambda: hs.NOW)


def test_spot_real_run_reports_region_action_skips_and_failures(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    boom = RuntimeError("delete denied")
    azure = hs.FakeAzure(
        [
            hs.vm("s1"),
            hs.vm("s2"),
            hs.vm("s-young", age=None),
            hs.vm("s-run"),
            hs.vm("s-reg", priority="Regular"),
        ],
        power={
            "s1": hs.DEALLOCATED,
            "s2": hs.DEALLOCATED,
            "s-young": hs.DEALLOCATED,
            "s-run": ["Provisioning succeeded", "VM running"],
        },
        script={("vm_begin", "s2"): [boom]},
    )
    azure.install(monkeypatch)
    _begin("spot_instances", dry_run=False)
    _spot(dry_run=False).delete()
    logger.finish()
    lines = _lines(caplog)
    region = f"REGION cloud=azure resource=spot_instances region={hs.RG} scanned=5 selected=2"
    assert f"{region} seconds=S" in lines
    assert (
        f"ACTION deleted cloud=azure resource=spot_instances region={hs.RG} id=s1 name=s1" in lines
    )
    assert "SKIPPED cloud=azure resource=spot_instances reason=no_creation_time count=1" in lines
    assert "SKIPPED cloud=azure resource=spot_instances reason=not_spot count=1" in lines
    assert "SKIPPED cloud=azure resource=spot_instances reason=state count=1" in lines
    assert "ERRORS count=1" in lines
    assert any(line.startswith("FAILED") and "id=s2 error=RuntimeError:" in line for line in lines)
    assert "selected=1 done=1 failed=1 skipped=3 regions=1" in lines[-1]


def test_spot_dry_run_and_leftover_disk_skips_are_debug(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    azure = hs.FakeAzure(
        [hs.vm("s1", os_disk=hs.slot("disk-1", rg="other-rg"))],
        power={"s1": hs.DEALLOCATED},
    )
    azure.install(monkeypatch)
    _begin("spot_instances", dry_run=True)
    _spot(dry_run=True).delete()
    logger.finish()
    lines = _lines(caplog)
    assert (
        f"ACTION would_delete cloud=azure resource=spot_instances region={hs.RG} id=s1 name=s1"
        in lines
    )
    assert "selected=1 done=0" in lines[-1]
    skips = [r for r in caplog.records if r.getMessage().startswith("Skipping leftover Spot disk")]
    assert [r.levelno for r in skips] == [logging.DEBUG]
    assert (
        "SKIPPED cloud=azure resource=spot_instances "
        "reason=leftover_disk_outside_resource_group count=1" in lines
    )


# ----- helpers ------------------------------------------------------------------------------


def test_reason_slug_maps_known_reasons_and_slugs_the_rest() -> None:
    assert _logger.reason_slug("not older than the age threshold") == "too_young"
    assert _logger.reason_slug("Disk: State!!") == "disk_state"
    assert _logger.reason_slug("???") == "other"


def test_no_run_begun_logs_nothing_extra(caplog: pytest.LogCaptureFixture) -> None:
    _logger.region_done("rg", 1, 1, _logger.started())
    _logger.acted("deleted", "rg", "x")
    _logger.skip("too young")
    _logger.failed("rg", "x", "boom")
    assert _lines(caplog) == []
