"""Unit tests for `crc.oci.spot_vm`: `plan()` is read-only, `apply()` mutates."""

import datetime
import types
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from crc.core.cleaner import Operation, RunContext
from crc.core.registry import DEFAULT_REGISTRY
from crc.oci.spot_vm import SpotVM, SpotVmCleaner, SpotVmRuntime
from crc.oci.spot_vm.client import RegionClients
from crc.oci.spot_vm.decide import SpotVmSettings

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
MUTATING = ("terminate_instance", "delete_volume")


def inst(inst_id: str, hours: float = 100, tags: Mapping[str, str] | None = None) -> Any:
    return types.SimpleNamespace(
        id=inst_id,
        display_name=f"name-{inst_id}",
        lifecycle_state="RUNNING",
        freeform_tags={"env": "ci"} if tags is None else tags,
        defined_tags={},
        metadata={},
        preemptible_instance_config=object(),
        time_created=NOW - datetime.timedelta(hours=hours),
    )


class FakeClient:
    """One object playing both the compute and the block storage client."""

    def __init__(
        self,
        instances: Sequence[Any],
        attachments: Mapping[str, Sequence[str]] | None = None,
        volume_tags: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self.instances = list(instances)
        self.attachments = dict(attachments or {})
        self.volume_tags = dict(volume_tags or {})
        self.calls: list[tuple[str, ...]] = []

    def list_instances(self, compartment_id: str) -> Any:
        self.calls.append(("list_instances", compartment_id))
        return types.SimpleNamespace(data=self.instances)

    def list_volume_attachments(self, compartment_id: str, instance_id: str) -> Any:
        self.calls.append(("list_volume_attachments", instance_id))
        data = [
            types.SimpleNamespace(volume_id=v, lifecycle_state="ATTACHED")
            for v in self.attachments.get(instance_id, ())
        ]
        return types.SimpleNamespace(data=data)

    def get_instance(self, instance_id: str) -> Any:
        self.calls.append(("get_instance", instance_id))
        return types.SimpleNamespace(data=types.SimpleNamespace(lifecycle_state="TERMINATED"))

    def terminate_instance(self, instance_id: str, preserve_boot_volume: bool) -> None:
        self.calls.append(("terminate_instance", instance_id, str(preserve_boot_volume)))

    def get_volume(self, volume_id: str) -> Any:
        self.calls.append(("get_volume", volume_id))
        tags = self.volume_tags.get(volume_id, {})
        return types.SimpleNamespace(data=types.SimpleNamespace(freeform_tags=tags))

    def delete_volume(self, volume_id: str) -> None:
        self.calls.append(("delete_volume", volume_id))


def paginate(call: Any, **kwargs: Any) -> Any:
    return call(**kwargs)


def make_cleaner(client: FakeClient, **settings: Any) -> SpotVmCleaner:
    options: dict[str, Any] = {"filter_tags": {"env": ["ci"]}, "age": {"hours": 5}, **settings}
    return SpotVmCleaner(
        lambda region: RegionClients(client, client, "cmp"),
        lambda: ["r1"],
        SpotVmSettings(**options),
        paginate,
    )


def context(*, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def mutations(client: FakeClient) -> list[tuple[str, ...]]:
    return [c for c in client.calls if c[0] in MUTATING]


@pytest.mark.parametrize("dry_run", [False, True])
def test_plan_makes_no_mutating_call(dry_run: bool) -> None:
    client = FakeClient([inst("a")], {"a": ["v1"]})
    plan = make_cleaner(client).plan(context(dry_run=dry_run))
    assert [c.id for c in plan.candidates] == ["a"]
    assert mutations(client) == []


def test_plan_skips_young_untagged_and_unmatched() -> None:
    client = FakeClient([inst("young", hours=1), inst("bare", tags={}), inst("ok")])
    plan = make_cleaner(client).plan(context())
    assert [c.id for c in plan.candidates] == ["ok"]
    assert {s.id: s.reason for s in plan.skipped} == {
        "young": "not older than age",
        "bare": "does not match filter_tags",
    }


def test_plan_keeps_instance_when_attachment_listing_fails() -> None:
    client = FakeClient([inst("a")])

    def failing(compartment_id: str, instance_id: str) -> Any:
        raise RuntimeError("down")

    client.list_volume_attachments = failing  # type: ignore[method-assign]
    plan = make_cleaner(client).plan(context())
    assert plan.candidates == ()
    assert [s.reason for s in plan.skipped] == ["volume attachment list failed"]


def test_dry_run_plan_tag_checks_volumes_and_apply_returns_them() -> None:
    client = FakeClient([inst("a")], {"a": ["v1", "v2"]}, {"v1": {"keep": "1"}})
    cleaner = make_cleaner(client, exception_tags={"keep": ["1"]})
    plan = cleaner.plan(context(dry_run=True))
    assert plan.related["Disk"] == ("v2",)
    result = cleaner.apply(plan, context(dry_run=True))
    assert result.succeeded == ("name-a",)
    assert result.related["Disk"] == ("v2",)
    assert mutations(client) == []


def test_apply_terminates_waits_then_deletes_checked_volumes() -> None:
    client = FakeClient([inst("a")], {"a": ["v1", "v2"]}, {"v2": {"keep": "1"}})
    cleaner = make_cleaner(client, exception_tags={"keep": ["1"]})
    plan = cleaner.plan(context())
    assert mutations(client) == []
    result = cleaner.apply(plan, context())
    assert result.succeeded == ("name-a",)
    assert result.related["Disk"] == ("v1",)
    after_plan = client.calls[client.calls.index(("terminate_instance", "a", "False")) :]
    assert after_plan == [
        ("terminate_instance", "a", "False"),
        ("get_instance", "a"),
        ("get_volume", "v1"),
        ("delete_volume", "v1"),
        ("get_volume", "v2"),
    ]


def test_apply_without_leftovers_does_not_wait() -> None:
    client = FakeClient([inst("a")])
    cleaner = make_cleaner(client)
    cleaner.apply(cleaner.plan(context()), context())
    assert ("get_instance", "a") not in client.calls


def test_apply_isolates_a_failing_terminate() -> None:
    client = FakeClient([inst("a"), inst("b")])
    original = client.terminate_instance

    def flaky(instance_id: str, preserve_boot_volume: bool) -> None:
        if instance_id == "a":
            raise RuntimeError("busy")
        original(instance_id, preserve_boot_volume)

    client.terminate_instance = flaky  # type: ignore[method-assign]
    cleaner = make_cleaner(client)
    result = cleaner.apply(cleaner.plan(context()), context())
    assert result.succeeded == ("name-b",)
    assert [(f.id, f.error) for f in result.failed] == [("a", "busy")]


def test_only_delete_is_supported() -> None:
    cleaner = make_cleaner(FakeClient([]))
    assert cleaner.supports(Operation.DELETE)
    assert not cleaner.supports(Operation.STOP)


def test_registered_and_factory_requires_a_positive_age(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCI_PRIVATE_KEY_CONTENT", "not-a-key")
    factory = DEFAULT_REGISTRY.get("oci", "spot_instances")
    with pytest.raises(ValueError, match="age is required"):
        factory(RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW))
    cleaner = factory(
        RunContext(
            dry_run=True,
            operation=Operation.DELETE,
            options={"age": {"days": 1}},
            now=NOW,
        )
    )
    assert isinstance(cleaner, SpotVmCleaner)


def test_adapter_with_injected_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCI_PRIVATE_KEY_CONTENT", "not-a-key")
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    client = FakeClient([inst("a")], {"a": ["v1"]})
    runtime = SpotVmRuntime(
        clients_factory=lambda region: RegionClients(client, client, "cmp"),
        list_regions=lambda: ["r1"],
        pager=paginate,
        clock=lambda: NOW,
    )
    spot = SpotVM(False, {"env": ["ci"]}, {}, {"hours": 5}, "", {}, runtime)
    spot.delete()
    assert spot.get_deleted == ["name-a"]
    assert spot.get_deleted_volumes == ["v1"]
    assert spot.delete_count == 1
    assert mutations(client) == [("terminate_instance", "a", "False"), ("delete_volume", "v1")]
