"""Registry: decorator registration, lookups, duplicates and isolation."""

import datetime

import pytest

from crc.core import registry
from crc.core.cleaner import Operation, Plan, Result, RunContext
from crc.core.registry import DuplicateCleanerError, Registry, UnknownCleanerError

CONTEXT = RunContext(True, Operation.DELETE, {}, datetime.datetime(2026, 1, 1))  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical


class StubCleaner:
    def __init__(self, context: RunContext, cloud: str = "aws", kind: str = "disk") -> None:
        self.context = context
        self.cloud = cloud
        self.kind = kind

    def supports(self, operation: Operation) -> bool:
        return True

    def plan(self, context: RunContext) -> Plan:
        return Plan()

    def apply(self, plan: Plan, context: RunContext) -> Result:
        return Result()


def test_register_and_get_returns_the_factory() -> None:
    reg = Registry()

    @reg.register("aws", "disk")
    class AwsDisk(StubCleaner):
        pass

    factory = reg.get("aws", "disk")
    assert factory is AwsDisk
    cleaner = factory(CONTEXT)
    assert isinstance(cleaner, AwsDisk)
    assert cleaner.context is CONTEXT


def test_register_accepts_plain_functions() -> None:
    reg = Registry()

    @reg.register("gcp", "ip")
    def build(context: RunContext) -> StubCleaner:
        return StubCleaner(context, "gcp", "ip")

    assert reg.get("gcp", "ip")(CONTEXT).kind == "ip"
    assert build(CONTEXT).cloud == "gcp"


def test_listing_is_sorted() -> None:
    reg = Registry()
    for cloud, kind in [("oci", "vm"), ("aws", "vm"), ("aws", "disk"), ("aws", "ip")]:
        reg.register(cloud, kind)(StubCleaner)
    assert reg.clouds() == ("aws", "oci")
    assert reg.kinds_for("aws") == ("disk", "ip", "vm")
    assert reg.kinds_for("oci") == ("vm",)


def test_duplicate_registration_raises_and_keeps_first() -> None:
    reg = Registry()

    class First(StubCleaner):
        pass

    class Second(StubCleaner):
        pass

    reg.register("aws", "disk")(First)
    with pytest.raises(DuplicateCleanerError, match="cloud='aws' kind='disk'"):
        reg.register("aws", "disk")(Second)
    assert reg.get("aws", "disk") is First


def test_same_kind_on_other_cloud_is_not_a_duplicate() -> None:
    reg = Registry()
    reg.register("aws", "disk")(StubCleaner)
    reg.register("azure", "disk")(StubCleaner)
    assert reg.clouds() == ("aws", "azure")


def test_unknown_lookups_raise_with_context() -> None:
    reg = Registry()
    reg.register("aws", "disk")(StubCleaner)
    with pytest.raises(UnknownCleanerError, match=r"kind='vm'.*known kinds for 'aws': \['disk'\]"):
        reg.get("aws", "vm")
    with pytest.raises(UnknownCleanerError, match=r"cloud='gcp'.*known clouds: \['aws'\]"):
        reg.get("gcp", "disk")
    with pytest.raises(UnknownCleanerError, match="cloud='gcp'"):
        reg.kinds_for("gcp")


def test_empty_registry() -> None:
    reg = Registry()
    assert reg.clouds() == ()
    with pytest.raises(UnknownCleanerError, match=r"known clouds: \[\]"):
        reg.get("aws", "disk")


@pytest.mark.parametrize(("cloud", "kind"), [("", "disk"), ("aws", ""), ("", "")])
def test_empty_names_rejected(cloud: str, kind: str) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        Registry().register(cloud, kind)


def test_registries_are_isolated() -> None:
    first, second = Registry(), Registry()
    first.register("aws", "disk")(StubCleaner)
    assert second.clouds() == ()
    second.register("aws", "disk")(StubCleaner)


def test_default_registry_functions_use_default_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered_before = registry.DEFAULT_REGISTRY.clouds()
    isolated = Registry()
    monkeypatch.setattr(registry, "DEFAULT_REGISTRY", isolated)

    @registry.register("aws", "keypair")
    class Keypair(StubCleaner):
        pass

    assert registry.get("aws", "keypair") is Keypair
    assert registry.kinds_for("aws") == ("keypair",)
    assert registry.clouds() == ("aws",)
    with pytest.raises(DuplicateCleanerError):
        registry.register("aws", "keypair")(StubCleaner)
    monkeypatch.undo()
    assert registry.DEFAULT_REGISTRY is not isolated
    assert registry.DEFAULT_REGISTRY.clouds() == registered_before
