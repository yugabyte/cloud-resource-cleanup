# Copyright (c) Yugabyte, Inc.

"""Cleaner-based copy of the locked `tests/test_oci_spot_vm.py` (SAF-040, SAF-041, SAF-043,
SAF-044, SAF-045, SAF-046).

Same cases as the locked file, but they drive `SpotVmCleaner` (and the modules it is made of)
through the `Cleaner` contract instead of building the legacy `SpotVM` adapter. For the owner to
swap in and relock in `tests/archtest/safety_lock.json`.
"""

import types
from collections.abc import Mapping
from typing import Any

import pytest

from crc.core.cleaner import Operation, RunContext
from crc.oci.spot_vm import cleaner as spot_cleaner
from crc.oci.spot_vm import leftovers
from crc.oci.spot_vm.cleaner import SpotVmCleaner, build_spot_vm_cleaner
from crc.oci.spot_vm.client import RegionClients
from crc.oci.spot_vm.decide import (
    SpotVmSettings,
    is_managed_or_oke_node,
    is_preemptible,
    matches_filter_tags,
)
from tests.test_oci_spot_instances import NOW, FakeClient, inst, paginate


def settings(**values: Any) -> SpotVmSettings:
    options: dict[str, Any] = {
        "filter_tags": {"yb_task": ["itest"]},
        "age": {"days": 1},
        **values,
    }
    return SpotVmSettings(**options)


def instance(
    tags: Mapping[str, str] | None = None,
    preemptible: bool = True,
    defined_tags: Mapping[str, Any] | None = None,
) -> Any:
    item = inst(
        "ocid1.instance.oc1..test",
        hours=24 * 7,
        tags={"yb_task": "itest"} if tags is None else tags,
    )
    item.defined_tags = {} if defined_tags is None else defined_tags
    item.preemptible_instance_config = object() if preemptible else None
    return item


def cleaner(client: FakeClient, **values: Any) -> SpotVmCleaner:
    return SpotVmCleaner(
        lambda region: RegionClients(client, client, "cmp"),
        lambda: ["us-ashburn-1"],
        settings(**values),
        paginate,
    )


def context(*, dry_run: bool) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def attachment(volume_id: str, state: str = "ATTACHED") -> Any:
    return types.SimpleNamespace(volume_id=volume_id, lifecycle_state=state)


def spot(client: FakeClient, *, dry_run: bool = False) -> tuple[Any, Any]:
    spot_cleaner_ = cleaner(client, filter_tags={"yb_task": ["itest"]})
    plan = spot_cleaner_.plan(context(dry_run=dry_run))
    return spot_cleaner_, plan


def test_preemptible_config_present() -> None:
    assert is_preemptible(instance(preemptible=True))


def test_regular_instance_not_preemptible() -> None:
    assert not is_preemptible(instance(preemptible=False))


def test_oke_freeform_tag_skipped() -> None:
    assert is_managed_or_oke_node(instance(tags={"yb_task": "itest", "oke-cluster-id": "c1"}))


def test_instance_pool_defined_tag_skipped() -> None:
    pooled = instance(defined_tags={"Oracle-Tags": {"CreatedBy": "instancepool/pool-1"}})
    assert is_managed_or_oke_node(pooled)


def test_plain_itest_not_managed() -> None:
    assert not is_managed_or_oke_node(instance())


def test_plan_skips_oke_and_instance_pool_members() -> None:
    client = FakeClient([instance(tags={"yb_task": "itest", "oke-cluster-id": "c1"})])
    plan = cleaner(client).plan(context(dry_run=False))
    assert plan.candidates == ()
    assert [s.reason for s in plan.skipped] == ["OKE or instance pool member"]


def test_attached_volumes_collected_detached_skipped() -> None:
    compute = types.SimpleNamespace(
        list_volume_attachments=lambda **kwargs: types.SimpleNamespace(
            data=[
                attachment("vol-keep", "ATTACHED"),
                attachment("vol-gone", "DETACHED"),
                attachment("vol-detaching", "DETACHING"),
            ]
        )
    )
    ids = leftovers.attached_volume_ids(compute, "cmp", "ocid1.instance.oc1..test", paginate)
    assert ids == ["vol-keep"]


def test_attachment_list_failure_returns_none() -> None:
    def boom(**kwargs: Any) -> Any:
        raise RuntimeError("boom")

    compute = types.SimpleNamespace(list_volume_attachments=boom)
    assert (
        leftovers.attached_volume_ids(compute, "cmp", "ocid1.instance.oc1..test", paginate) is None
    )


def test_volume_exception_tags_skip_delete() -> None:
    client = FakeClient([], volume_tags={"vol-1": {"keep": "true"}})
    protected = settings(exception_tags={"keep": ["true"]})
    assert not leftovers.volume_passes_tag_check(client, "vol-1", protected)


def test_dry_run_plan_records_volume_without_api_call() -> None:
    client = FakeClient([instance()], {"ocid1.instance.oc1..test": ["vol-keep"]})
    _, plan = spot(client, dry_run=True)
    assert plan.related == {"Disk": ("vol-keep",)}
    assert [c for c in client.calls if c[0] in ("delete_volume", "terminate_instance")] == []


def test_engine_built_dry_run_plan_records_volume_without_api_call() -> None:
    """The factory builds the cleaner with `engine=True`; its dry run must not mutate either."""
    client = FakeClient([instance()], {"ocid1.instance.oc1..test": ["vol-keep"]})
    engine_cleaner = SpotVmCleaner(
        lambda region: RegionClients(client, client, "cmp"),
        lambda: ["us-ashburn-1"],
        settings(),
        paginate,
        engine=True,
    )
    plan = engine_cleaner.plan(context(dry_run=True))
    assert plan.related == {"Disk": ("vol-keep",)}
    assert [c.id for c in plan.candidates] == [c.name for c in plan.candidates]
    assert [c for c in client.calls if c[0] in ("delete_volume", "terminate_instance")] == []


def test_filter_requires_tags_on_instance() -> None:
    assert not matches_filter_tags(settings(), instance(tags={}))
    assert matches_filter_tags(settings(), instance(tags={"yb_task": "itest"}))


def test_empty_filter_allows_untagged() -> None:
    assert matches_filter_tags(settings(filter_tags={}), instance(tags={}))
    assert matches_filter_tags(settings(filter_tags={}), instance(tags={"yb_task": "itest"}))


def test_requires_positive_age(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(spot_cleaner, "Base", lambda: object())
    context_ = RunContext(
        dry_run=True,
        operation=Operation.DELETE,
        options={"filter_tags": {"yb_task": ["itest"]}, "age": {}},
        now=NOW,
    )
    with pytest.raises(ValueError, match="age"):
        build_spot_vm_cleaner(context_)


def test_delete_terminates_waits_then_deletes_volume() -> None:
    client = FakeClient([instance()], {"ocid1.instance.oc1..test": ["vol-keep"]})
    spot_cleaner_, plan = spot(client)
    result = spot_cleaner_.apply(plan, context(dry_run=False))
    order = [
        c[0]
        for c in client.calls
        if c[0] in ("terminate_instance", "get_instance", "delete_volume")
    ]
    assert order == ["terminate_instance", "get_instance", "delete_volume"]
    assert result.succeeded == ("name-ocid1.instance.oc1..test",)
    assert result.related == {"Disk": ("vol-keep",)}


def test_skip_terminate_when_attachment_list_fails() -> None:
    client = FakeClient([instance()])

    def failing(compartment_id: str, instance_id: str = "", volume_id: str = "") -> Any:
        raise RuntimeError("down")

    client.list_volume_attachments = failing  # type: ignore[method-assign]
    spot_cleaner_, plan = spot(client)
    result = spot_cleaner_.apply(plan, context(dry_run=False))
    assert plan.candidates == ()
    assert not any(call[0] == "terminate_instance" for call in client.calls)
    assert result.succeeded == ()


def test_delete_still_tries_volumes_when_wait_fails() -> None:
    client = FakeClient([instance()], {"ocid1.instance.oc1..test": ["vol-keep"]})

    def failing_poll(instance_id: str) -> Any:
        raise RuntimeError("poll down")

    client.get_instance = failing_poll  # type: ignore[method-assign]
    spot_cleaner_, plan = spot(client)
    spot_cleaner_.apply(plan, context(dry_run=False))
    order = [c[0] for c in client.calls if c[0] in ("terminate_instance", "delete_volume")]
    assert order == ["terminate_instance", "delete_volume"]
