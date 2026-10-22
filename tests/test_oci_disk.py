"""Unit tests for `crc.oci.disk`: `DiskCleaner.plan()` is read-only, `apply()` mutates."""

import datetime
import logging
import types
from collections.abc import Mapping, Sequence
from typing import Any

import oci
import pytest
from oci.exceptions import ConnectTimeout

from crc.core.cleaner import Operation, Plan, RunContext
from crc.core.registry import DEFAULT_REGISTRY
from crc.oci.disk import DiskCleaner, DiskSettings, build_disk_cleaner
from crc.oci.disk.api import OciVolumeApi, SdkAttachment, SdkVolume, VolumeApi

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
FILTER = {"env": ["ci"]}
MUTATING = ("delete_volume", "delete_boot_volume")


def vol(
    vol_id: str,
    hours: float = 100,
    state: str = "AVAILABLE",
    tags: Mapping[str, str] | None = None,
) -> Any:
    return types.SimpleNamespace(
        id=vol_id,
        display_name=f"name-{vol_id}",
        lifecycle_state=state,
        freeform_tags={"env": "ci"} if tags is None else tags,
        time_created=NOW - datetime.timedelta(hours=hours),
    )


def att(volume_id: str, state: str = "ATTACHED") -> Any:
    return types.SimpleNamespace(
        lifecycle_state=state, volume_id=volume_id, boot_volume_id=volume_id
    )


class FakeApi:
    def __init__(
        self,
        volumes: Sequence[Any] = (),
        boot_volumes: Sequence[Any] = (),
        attachments: Sequence[Any] = (),
        boot_attachments: Sequence[Any] = (),
        failing: Mapping[str, Exception] | None = None,
    ) -> None:
        self.volumes = list(volumes)
        self.boot_volumes = list(boot_volumes)
        self.attachments = list(attachments)
        self.boot_attachments = list(boot_attachments)
        self.failing = dict(failing or {})
        self.calls: list[tuple[str, ...]] = []

    def list_volumes(self) -> Sequence[SdkVolume]:
        self.calls.append(("list_volumes",))
        return self.volumes

    def list_boot_volumes(self) -> Sequence[SdkVolume]:
        self.calls.append(("list_boot_volumes",))
        return self.boot_volumes

    def list_volume_attachments(self) -> Sequence[SdkAttachment]:
        self.calls.append(("list_volume_attachments",))
        return self.attachments

    def list_availability_domains(self) -> Sequence[str]:
        self.calls.append(("list_availability_domains",))
        return ["AD-1"]

    def list_boot_volume_attachments(self, availability_domain: str) -> Sequence[SdkAttachment]:
        self.calls.append(("list_boot_volume_attachments", availability_domain))
        return self.boot_attachments

    def delete_volume(self, volume_id: str) -> None:
        self.calls.append(("delete_volume", volume_id))
        if volume_id in self.failing:
            raise self.failing[volume_id]

    def delete_boot_volume(self, volume_id: str) -> None:
        self.calls.append(("delete_boot_volume", volume_id))
        if volume_id in self.failing:
            raise self.failing[volume_id]


def context(*, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def cleaner(api: VolumeApi, settings: DiskSettings | None = None) -> DiskCleaner:
    chosen = settings or DiskSettings(filter_tags=FILTER, age={"hours": 5})
    return DiskCleaner(lambda region: api, lambda: ["r1"], chosen)


def test_plan_makes_no_mutating_call() -> None:
    api = FakeApi(volumes=[vol("a")], boot_volumes=[vol("b")])
    plan = cleaner(api).plan(context())
    assert [c.id for c in plan.candidates] == ["a", "b"]
    assert [call for call in api.calls if call[0] in MUTATING] == []


def test_plan_lists_in_main_order() -> None:
    api = FakeApi(volumes=[vol("a")], boot_volumes=[vol("b")])
    cleaner(api).plan(context())
    assert [call[0] for call in api.calls] == [
        "list_volumes",
        "list_boot_volumes",
        "list_volume_attachments",
        "list_availability_domains",
        "list_boot_volume_attachments",
    ]


def test_plan_candidates_carry_name_region_kind_and_tags() -> None:
    plan = cleaner(FakeApi(volumes=[vol("a")], boot_volumes=[vol("b")])).plan(context())
    first, second = plan.candidates
    assert (first.name, first.region, first.extra["kind"]) == ("name-a", "r1", "disk")
    assert second.extra["kind"] == "boot disk"
    assert dict(first.tags) == {"env": "ci"}


@pytest.mark.parametrize(
    ("volume", "reason"),
    [
        (vol("a", state="TERMINATED"), "state is not AVAILABLE"),
        (vol("a", tags={"env": "dev"}), "does not match filter_tags"),
        (vol("a", hours=1), "not older than age"),
    ],
)
def test_plan_records_why_a_volume_is_kept(volume: Any, reason: str) -> None:
    plan = cleaner(FakeApi(volumes=[volume])).plan(context())
    assert plan.candidates == ()
    assert [s.reason for s in plan.skipped] == [reason]


def test_plan_keeps_attached_volumes() -> None:
    api = FakeApi(volumes=[vol("a"), vol("b")], attachments=[att("a"), att("b", "DETACHED")])
    plan = cleaner(api).plan(context())
    assert [c.id for c in plan.candidates] == ["b"]
    assert [(s.id, s.reason) for s in plan.skipped] == [("a", "attached")]


def test_plan_keeps_attached_boot_volumes() -> None:
    api = FakeApi(boot_volumes=[vol("a"), vol("b")], boot_attachments=[att("a", "ATTACHING")])
    plan = cleaner(api).plan(context())
    assert [c.id for c in plan.candidates] == ["b"]


def test_plan_applies_exception_tags_and_notags() -> None:
    settings = DiskSettings(
        filter_tags={}, exception_tags={"keep": ["1"]}, notags={"x": [], "y": []}, age={"hours": 5}
    )
    volumes = [
        vol("a", tags={"keep": "1"}),
        vol("b", tags={"x": "1", "y": "1"}),
        vol("c", tags={"x": "1"}),
    ]
    plan = cleaner(FakeApi(volumes=volumes), settings).plan(context())
    assert [c.id for c in plan.candidates] == ["c"]


def test_plan_uses_custom_age_tag() -> None:
    settings = DiskSettings(filter_tags=FILTER, age={"days": 30}, custom_age_tag_key="ttl")
    keep = vol("a", hours=3, tags={"env": "ci"})
    drop = vol("b", hours=3, tags={"env": "ci", "ttl": "{'hours': 2}"})
    plan = cleaner(FakeApi(volumes=[keep, drop]), settings).plan(context())
    assert [c.id for c in plan.candidates] == ["b"]


def test_plan_skips_region_on_connectivity_error(caplog: pytest.LogCaptureFixture) -> None:
    class Unreachable(FakeApi):
        def list_volumes(self) -> Sequence[SdkVolume]:
            raise ConnectTimeout(Exception("timed out"))

    api = Unreachable()
    plan = cleaner(api).plan(context())
    assert plan == Plan()
    assert "Region r1: skipped list volumes/boot volumes disk delete" in caplog.text


def test_plan_in_dry_run_logs_the_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    cleaner(FakeApi(volumes=[vol("a")])).plan(context(dry_run=True))
    assert "List of OCI disk (Total: 1) which will be deleted: ['name-a']" in caplog.text


def test_plan_outside_dry_run_does_not_log_the_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    cleaner(FakeApi(volumes=[vol("a")])).plan(context())
    assert "OCI disk" not in caplog.text


def test_apply_deletes_volumes_then_boot_volumes_in_plan_order() -> None:
    api = FakeApi(volumes=[vol("a"), vol("b")], boot_volumes=[vol("c")])
    subject = cleaner(api)
    plan = subject.plan(context())
    api.calls.clear()
    result = subject.apply(plan, context())
    assert api.calls == [
        ("delete_volume", "a"),
        ("delete_volume", "b"),
        ("delete_boot_volume", "c"),
    ]
    assert result.succeeded == ("name-a", "name-b", "name-c")
    assert result.failed == ()


def test_apply_logs_a_failed_delete_and_continues(caplog: pytest.LogCaptureFixture) -> None:
    api = FakeApi(volumes=[vol("a"), vol("b")], failing={"a": RuntimeError("busy")})
    subject = cleaner(api)
    result = subject.apply(subject.plan(context()), context())
    assert result.succeeded == ("name-b",)
    assert [(f.id, f.error) for f in result.failed] == [("name-a", "busy")]
    assert "Error occurred while deleting disk name-a: busy" in caplog.text


def test_apply_in_dry_run_deletes_nothing() -> None:
    api = FakeApi(volumes=[vol("a")])
    subject = cleaner(api)
    result = subject.apply(subject.plan(context(dry_run=True)), context(dry_run=True))
    assert [call for call in api.calls if call[0] in MUTATING] == []
    assert result.succeeded == ("name-a",)


def test_apply_logs_the_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    subject = cleaner(FakeApi())
    subject.apply(subject.plan(context()), context())
    assert [r.getMessage() for r in caplog.records] == [
        "No OCI disk to delete.",
        "number of OCI disks deleted: 0",
        "List of OCI disk deleted: []",
    ]


def test_supports_delete_only() -> None:
    subject = cleaner(FakeApi())
    assert subject.supports(Operation.DELETE)
    assert not subject.supports(Operation.STOP)


def test_cleaner_is_registered_for_oci_disk() -> None:
    assert DEFAULT_REGISTRY.get("oci", "disk") is build_disk_cleaner


def test_factory_builds_a_disk_cleaner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCI_PRIVATE_KEY_CONTENT", "not-a-key")
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    built = build_disk_cleaner(context())
    assert (built.cloud, built.kind) == ("oci", "disk")


def test_sdk_api_pages_through_every_result_page(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {None: (["a"], "p2"), "p2": (["b"], None)}

    class Blockstorage:
        def list_volumes(self, compartment_id: str, page: str | None = None) -> Any:
            data, token = pages[page]
            headers = {} if token is None else {"opc-next-page": token}
            return oci.response.Response(200, headers, data, None)

    api = OciVolumeApi(object(), Blockstorage(), "comp", lambda: [])
    listed: Any = api.list_volumes()
    assert listed == ["a", "b"]


def test_sdk_api_scopes_boot_attachments_to_the_domain() -> None:
    seen: list[Mapping[str, str]] = []

    class Compute:
        def list_boot_volume_attachments(self, **kwargs: str) -> Any:
            seen.append(kwargs)
            return oci.response.Response(200, {}, [], None)

    api = OciVolumeApi(Compute(), object(), "comp", lambda: ["AD-1"])
    assert api.list_availability_domains() == ["AD-1"]
    api.list_boot_volume_attachments("AD-1")
    assert seen == [{"availability_domain": "AD-1", "compartment_id": "comp"}]
