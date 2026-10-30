"""Cleaner-based copy of `tests/test_azu_spot_vm.py` (hash-locked, never edited).

The locked file drives the `SpotVM` adapter through its private methods; these tests cover the
same SAF cases through `AzureSpotVmCleaner` (`plan()` / `apply()` on a fake Azure SDK) and the
pure disk helpers it uses, for the owner to swap in and relock later:

- SAF-038: leftover (detach) disks are collected, an empty delete option means detach, and the
  VM is deleted before its leftover disks.
- SAF-041: a positive age is required.
- SAF-042: an untagged Spot VM is always skipped.
- SAF-044: a disk with an exception tag is kept, a disk of another resource group is not touched.
"""

import pytest

from crc.azu.spot_vm import AzureSpotVmCleaner, RemovalOps, SpotVmConfig
from crc.azu.spot_vm.disks import (
    detached_disk_refs,
    disk_name_from_id,
    disk_ref_from_id,
    disk_slots,
)
from crc.core.cleaner import Operation, RunContext
from crc.core.registry import get
from tests.helpers_azure_spot import NOW, RG, FakeAzure, disk_id, slot, vm

MUTATING = ("vm_begin_delete", "disk_begin_delete", "nic_begin_delete")


def _config(**changes: object) -> SpotVmConfig:
    values: dict[str, object] = {
        "resource_group": RG,
        "filter_tags": {"yb_task": ["itest"]},
        "exception_tags": {},
        "notags": {},
        "age": {"days": 1},
        "custom_age_tag_key": None,
    }
    values.update(changes)
    return SpotVmConfig(**values)  # type: ignore[arg-type]


def _cleaner(azure: FakeAzure, **changes: object) -> AzureSpotVmCleaner:
    def delete_vm(client: object, rg: str, name: str) -> None:
        azure.calls.append(("vm_begin_delete", rg, name))

    def delete_nic(client: object, rg: str, name: str, dry_run: bool, nics: list[str]) -> None:
        azure.calls.append(("nic_begin_delete", rg, f"{name}-NIC", dry_run))
        nics.append(f"{name}-NIC")

    return AzureSpotVmCleaner(
        lambda: azure.compute("c", "s"),
        lambda: azure.network("c", "s"),
        _config(**changes),
        RemovalOps(delete_vm=delete_vm, delete_nic=delete_nic),
    )


def _ctx(dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def _names(azure: FakeAzure) -> list[object]:
    return [c[0] for c in azure.calls]


def test_saf_038_collects_detach_os_and_data_disks() -> None:
    machine = vm(
        "spot-1",
        os_disk=slot("os-disk", "Detach"),
        data_disks=[
            slot("data-1", "Detach"),
            slot("data-dot", "Delete"),
            slot(None, "Detach"),
        ],
    )
    refs = detached_disk_refs("spot-1", disk_slots(machine), RG)
    assert [name for _, name, _ in refs] == ["os-disk", "data-1"]


def test_saf_038_empty_delete_option_is_detach_default() -> None:
    machine = vm("spot-1", os_disk=slot("os", ""), data_disks=[slot("d1", "Delete")])
    refs = detached_disk_refs("spot-1", disk_slots(machine), RG)
    assert [name for _, name, _ in refs] == ["os"]


def test_saf_038_ephemeral_os_disk_without_managed_id_is_skipped() -> None:
    machine = vm("spot-1", os_disk=slot(None, ""))
    assert detached_disk_refs("spot-1", disk_slots(machine), RG) == []


def test_saf_038_disk_name_from_managed_id() -> None:
    assert disk_name_from_id(disk_id("vol-a")) == "vol-a"
    assert disk_name_from_id(None) is None


def test_saf_038_disk_ref_parses_another_resource_group() -> None:
    assert disk_ref_from_id(disk_id("vol-a", "other-rg"), RG) == ("other-rg", "vol-a", True)


def test_saf_038_apply_removes_the_leftover_disks_after_the_vm() -> None:
    azure = FakeAzure([vm("spot-1", os_disk=slot("os-detach"))])
    cleaner = _cleaner(azure)
    result = cleaner.apply(cleaner.plan(_ctx()), _ctx())
    order = [c for c in _names(azure) if c in MUTATING]
    assert order == ["vm_begin_delete", "disk_begin_delete", "nic_begin_delete"]
    assert result.succeeded == ("spot-1",)
    assert result.related["Disk"] == (f"{RG}/os-detach",)


def test_saf_038_dry_run_records_leftover_disks_without_deleting_the_vm() -> None:
    azure = FakeAzure([vm("spot-1", os_disk=slot("os-detach"))])
    plan = _cleaner(azure).plan(_ctx(dry_run=True))
    assert [c.id for c in plan.candidates] == ["spot-1"]
    assert plan.related["Disk"] == (f"{RG}/os-detach",)
    assert not set(_names(azure)) & set(MUTATING)


def test_saf_041_requires_a_positive_age(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAzure().install(monkeypatch)
    context = RunContext(
        dry_run=True, operation=Operation.DELETE, options={"resource_group": RG, "age": {}}, now=NOW
    )
    with pytest.raises(ValueError, match="age is required"):
        get("azure", "spot_instances")(context)


def test_saf_042_untagged_spot_vm_is_always_skipped() -> None:
    no_tags = vm("no-tags")
    no_tags.tags = None
    azure = FakeAzure([vm("untagged", tags={}), no_tags])
    plan = _cleaner(azure, filter_tags={}).plan(_ctx())
    assert plan.candidates == ()
    assert [s.id for s in plan.skipped] == ["untagged", "no-tags"]


def test_saf_044_exception_tag_on_the_disk_keeps_it() -> None:
    azure = FakeAzure(
        [vm("spot-1", os_disk=slot("os-detach"))],
        disk_tags={f"{RG}/os-detach": {"keep": "true"}},
    )
    cleaner = _cleaner(azure, exception_tags={"keep": ["true"]})
    result = cleaner.apply(cleaner.plan(_ctx()), _ctx())
    assert result.succeeded == ("spot-1",)
    assert result.related["Disk"] == ()
    assert "disk_begin_delete" not in _names(azure)


def test_saf_044_disk_in_another_resource_group_is_not_touched() -> None:
    azure = FakeAzure([vm("spot-1", os_disk=slot("os-detach", rg="other-rg"))])
    cleaner = _cleaner(azure)
    result = cleaner.apply(cleaner.plan(_ctx()), _ctx())
    assert result.related["Disk"] == ()
    assert "disk_begin_delete" not in _names(azure)
    assert "disk_get" not in _names(azure)
