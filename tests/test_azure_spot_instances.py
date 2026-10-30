"""Focused tests of `AzureSpotVmCleaner.plan()` and `apply()` against fake SDK clients."""

import datetime

import pytest

from crc.azu.spot_vm import AzureSpotVmCleaner, RemovalOps, SpotVmConfig
from crc.core.cleaner import Operation, RunContext
from crc.core.registry import get
from tests.helpers_azure_spot import NOW, RG, FakeAzure, slot, vm

MUTATING = ("vm_begin_delete", "disk_begin_delete", "nic_begin_delete")


def _config(**changes: object) -> SpotVmConfig:
    values: dict[str, object] = {
        "resource_group": RG,
        "filter_tags": {"yb_task": ["itest"]},
        "exception_tags": {},
        "notags": {},
        "age": {"days": 1},
        "custom_age_tag_key": "custom_age",
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


def _names(calls: list[tuple[object, ...]]) -> list[object]:
    return [c[0] for c in calls]


def test_supports_delete_only() -> None:
    cleaner = _cleaner(FakeAzure())
    assert cleaner.supports(Operation.DELETE)
    assert not cleaner.supports(Operation.STOP)


def test_registered_for_azure_spot_instances() -> None:
    assert get("azure", "spot_instances").__name__ == "build_spot_vm_cleaner"


def test_plan_makes_no_mutating_call_in_a_real_run() -> None:
    azure = FakeAzure([vm("s1", os_disk=slot("os-a")), vm("s2")])
    plan = _cleaner(azure).plan(_ctx())
    assert [c.id for c in plan.candidates] == ["s1", "s2"]
    assert not set(_names(azure.calls)) & set(MUTATING)
    assert "disk_get" not in _names(azure.calls)
    assert plan.related == {}


def test_plan_dry_run_previews_disks_and_nic_without_mutating() -> None:
    azure = FakeAzure([vm("s1", os_disk=slot("os-a"))])
    plan = _cleaner(azure).plan(_ctx(dry_run=True))
    assert plan.related == {"NIC": ("s1-NIC",), "Disk": (f"{RG}/os-a",)}
    assert not set(_names(azure.calls)) & set(MUTATING)
    assert "disk_get" in _names(azure.calls)


def test_plan_records_skip_reasons() -> None:
    azure = FakeAzure(
        [vm("a", priority="Regular"), vm("b", tags={"x": "y"}), vm("c", age=datetime.timedelta(0))]
    )
    plan = _cleaner(azure).plan(_ctx())
    assert plan.candidates == ()
    assert [s.id for s in plan.skipped] == ["a", "b", "c"]


def test_plan_list_error_is_raised() -> None:
    azure = FakeAzure(list_error=RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        _cleaner(azure).plan(_ctx())


def test_apply_deletes_vm_then_disks_then_nic() -> None:
    azure = FakeAzure([vm("s1", os_disk=slot("os-a"))])
    cleaner = _cleaner(azure)
    result = cleaner.apply(cleaner.plan(_ctx()), _ctx())
    order = [c for c in _names(azure.calls) if c in (*MUTATING, "disk_get")]
    assert order == ["vm_begin_delete", "disk_get", "disk_begin_delete", "nic_begin_delete"]
    assert result.succeeded == ("s1",)
    assert result.related == {"NIC": ("s1-NIC",), "Disk": (f"{RG}/os-a",)}


def test_apply_isolates_a_failing_vm() -> None:
    azure = FakeAzure([vm("s1"), vm("s2")], script={("vm_begin", "s1"): [RuntimeError("x")]})

    def delete_vm(client: object, rg: str, name: str) -> None:
        if name == "s1":
            raise RuntimeError("vm failed")

    cleaner = AzureSpotVmCleaner(
        lambda: azure.compute("c", "s"),
        lambda: azure.network("c", "s"),
        _config(),
        RemovalOps(delete_vm=delete_vm),
    )
    result = cleaner.apply(cleaner.plan(_ctx()), _ctx())
    assert result.succeeded == ("s2",)
    assert [f.id for f in result.failed] == ["s1"]
    assert result.failed[0].error == "vm failed"


def test_apply_dry_run_makes_no_call() -> None:
    azure = FakeAzure([vm("s1")])
    cleaner = _cleaner(azure)
    plan = cleaner.plan(_ctx(dry_run=True))
    before = list(azure.calls)
    result = cleaner.apply(plan, _ctx(dry_run=True))
    assert azure.calls == before
    assert result.succeeded == ("s1",)


def test_apply_keeps_protected_leftover_disk() -> None:
    azure = FakeAzure([vm("s1", os_disk=slot("os-a"))], disk_tags={f"{RG}/os-a": {"keep": "true"}})
    cleaner = _cleaner(azure, exception_tags={"keep": ["true"]})
    result = cleaner.apply(cleaner.plan(_ctx()), _ctx())
    assert result.related["Disk"] == ()
    assert "disk_begin_delete" not in _names(azure.calls)


def test_requires_positive_age_in_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAzure().install(monkeypatch)
    ctx = RunContext(
        dry_run=True, operation=Operation.DELETE, options={"resource_group": RG, "age": {}}, now=NOW
    )
    with pytest.raises(ValueError, match="age is required"):
        get("azure", "spot_instances")(ctx)
