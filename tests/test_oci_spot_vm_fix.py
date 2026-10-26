"""BUG-255: a leftover volume is deleted only when it is owned by the terminated instance.

Owned means created at or after the instance's `time_created` and attached to no other instance.
`tests/test_oci_spot_vm.py` is hash-locked, so these tests live in their own file.
"""

import datetime
import types
from collections.abc import Sequence
from typing import Any

import pytest

from crc.oci.spot_vm import leftovers
from crc.oci.spot_vm.decide import SpotVmSettings

CREATED = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
HOUR = datetime.timedelta(hours=1)
SETTINGS = SpotVmSettings(
    filter_tags={}, exception_tags={"keep": ["1"]}, notags={}, age={"days": 1}
)


def attachment(instance_id: Any, state: str = "ATTACHED") -> Any:
    return types.SimpleNamespace(volume_id="v1", lifecycle_state=state, instance_id=instance_id)


class FakeCompute:
    def __init__(self, attachments: Sequence[Any] | Exception) -> None:
        self.attachments = attachments
        self.calls: list[dict[str, Any]] = []

    def list_volume_attachments(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.attachments, Exception):
            raise self.attachments
        return types.SimpleNamespace(data=self.attachments)


class FakeBlock:
    def __init__(self, created: Any, tags: Any = None) -> None:
        self.created = created
        self.tags = tags if tags is not None else {}
        self.calls: list[str] = []

    def get_volume(self, volume_id: str) -> Any:
        self.calls.append(volume_id)
        return types.SimpleNamespace(
            data=types.SimpleNamespace(freeform_tags=self.tags, time_created=self.created)
        )

    def delete_volume(self, volume_id: str) -> None:
        raise AssertionError("the volume check never deletes")


def owner_of(compute: FakeCompute, created: Any = CREATED) -> leftovers.VolumeOwner:
    return leftovers.VolumeOwner(
        compute.list_volume_attachments, "cmp", "inst-1", created, lambda call, **kw: call(**kw)
    )


@pytest.fixture
def skips(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    recorded: list[str] = []
    monkeypatch.setattr("crc.oci._logger.record_skip", recorded.append)
    return recorded


def check(block: FakeBlock, compute: FakeCompute, created: Any = CREATED) -> bool:
    owner = owner_of(compute, created)
    return leftovers.volume_passes_tag_check(block, "v1", SETTINGS, "r1", owner)


@pytest.mark.parametrize(
    "volume_created",
    [CREATED, CREATED + HOUR, CREATED + 1000 * HOUR],
    ids=["same-time", "after", "long-after"],
)
def test_volume_created_at_or_after_instance_is_deleted(
    volume_created: datetime.datetime, skips: list[str]
) -> None:
    compute = FakeCompute([attachment("inst-1")])
    assert check(FakeBlock(volume_created), compute)
    assert skips == []
    assert compute.calls == [{"compartment_id": "cmp", "volume_id": "v1"}]


def test_volume_without_other_attachment_is_deleted(skips: list[str]) -> None:
    assert check(FakeBlock(CREATED), FakeCompute([]))
    assert skips == []


def test_detached_attachment_of_another_instance_does_not_protect(skips: list[str]) -> None:
    compute = FakeCompute([attachment("inst-1"), attachment("other", "DETACHED")])
    assert check(FakeBlock(CREATED), compute)
    assert skips == []


def test_volume_created_before_instance_is_kept(skips: list[str]) -> None:
    compute = FakeCompute([attachment("inst-1")])
    assert not check(FakeBlock(CREATED - datetime.timedelta(seconds=1)), compute)
    assert skips == ["not_owned"]
    assert compute.calls == []


@pytest.mark.parametrize("state", ["ATTACHED", "ATTACHING", "DETACHING"])
def test_volume_attached_to_another_instance_is_kept(state: str, skips: list[str]) -> None:
    compute = FakeCompute([attachment("inst-1"), attachment("other", state)])
    assert not check(FakeBlock(CREATED + HOUR), compute)
    assert skips == ["not_owned"]


def test_attachment_without_instance_id_is_kept(skips: list[str]) -> None:
    assert not check(FakeBlock(CREATED), FakeCompute([attachment(None)]))
    assert skips == ["not_owned"]


@pytest.mark.parametrize(
    ("volume_created", "instance_created"),
    [
        (None, CREATED),
        ("2026-03-01", CREATED),
        (CREATED, None),
        (CREATED.replace(tzinfo=None), CREATED),
    ],
    ids=["volume-time-missing", "volume-time-not-a-datetime", "instance-time-missing", "naive"],
)
def test_uncertain_creation_time_keeps_the_volume(
    volume_created: Any, instance_created: Any, skips: list[str]
) -> None:
    assert not check(FakeBlock(volume_created), FakeCompute([]), instance_created)
    assert skips == ["not_owned"]


def test_failed_attachment_read_keeps_the_volume(
    skips: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    failures: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        "crc.oci._logger.record_failure",
        lambda r, res, err, **_: failures.append((r, res, str(err))),
    )
    assert not check(FakeBlock(CREATED), FakeCompute(RuntimeError("down")))
    assert skips == ["not_owned"]
    assert failures == [("r1", "v1", "down")]


def test_exception_tag_still_keeps_before_the_ownership_read(skips: list[str]) -> None:
    compute = FakeCompute([])
    assert not check(FakeBlock(CREATED, {"keep": "1"}), compute)
    assert skips == ["leftover_exception_tag"]
    assert compute.calls == []


def test_without_owner_only_tags_decide(skips: list[str]) -> None:
    block = FakeBlock(None)
    assert leftovers.volume_passes_tag_check(block, "v1", SETTINGS)
    assert skips == []


def test_checked_volumes_keeps_only_owned_ones(skips: list[str]) -> None:
    compute = FakeCompute([attachment("inst-1")])
    owner = owner_of(compute)
    old = FakeBlock(CREATED - HOUR)
    assert leftovers.checked_volumes(old, ["v1"], SETTINGS, "r1", owner) == []
    new = FakeBlock(CREATED)
    assert leftovers.checked_volumes(new, ["v1"], SETTINGS, "r1", owner) == ["v1"]
