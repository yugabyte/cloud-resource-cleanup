"""Unit tests of `crc.gcp.disk`: read-only `plan()`, mutating `apply()`, fakes only."""

import datetime
import logging
import types
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pytest

from crc.core.cleaner import Candidate, Operation, Plan, RunContext
from crc.gcp.disk import Disk, DiskCleaner, DiskSettings, DiskView

UTC = datetime.UTC
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
PROJECT = "proj-1"
TWO = 2


def ctx(*, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def stamp(**delta: float) -> str:
    return (NOW - datetime.timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%S.%f%z")


def disk(
    name: str,
    *,
    days: float = 3,
    users: Sequence[str] = (),
    labels: Mapping[str, str] | None = None,
) -> Any:
    return types.SimpleNamespace(
        name=name,
        users=list(users),
        labels=dict(labels or {}),
        last_detach_timestamp=stamp(days=days),
    )


class FakeClient:
    def __init__(self, zones: dict[str, list[Any]], fail_on: str | None = None) -> None:
        self.zones = zones
        self.fail_on = fail_on
        self.calls: list[tuple[str, ...]] = []

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        self.calls.append(("list_zones", project_id))
        return [{"name": zone} for zone in self.zones]

    def list_disks(self, project_id: str, zone: str) -> Iterable[DiskView]:
        self.calls.append(("list_disks", project_id, zone))
        items: list[DiskView] = self.zones[zone]
        return items

    def delete_disk(self, project_id: str, zone: str, disk: str) -> object:
        self.calls.append(("delete_disk", project_id, zone, disk))
        if disk == self.fail_on:
            raise RuntimeError("denied")
        return object()

    def close(self) -> None:
        self.calls.append(("close",))

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


def settings(**overrides: Any) -> DiskSettings:
    values: dict[str, Any] = {
        "project_id": PROJECT,
        "filter_labels": None,
        "exception_labels": None,
        "exception_regex": None,
        "custom_age_tag_key": "",
        "detach_age": {"days": 1},
    }
    values.update(overrides)
    return DiskSettings(**values)


def cleaner(client: FakeClient, **overrides: Any) -> DiskCleaner:
    return DiskCleaner(client, settings(**overrides))


def test_plan_lists_and_never_mutates() -> None:
    client = FakeClient({"z1": [disk("a"), disk("b", users=["vm"])], "z2": [disk("c")]})
    plan = cleaner(client).plan(ctx())
    assert [(c.id, c.region) for c in plan.candidates] == [("a", "z1"), ("c", "z2")]
    assert client.calls == [
        ("list_zones", PROJECT),
        ("list_disks", PROJECT, "z1"),
        ("list_disks", PROJECT, "z2"),
        ("close",),
    ]
    assert "delete_disk" not in client.names()


def test_plan_in_dry_run_logs_summary_and_does_not_delete(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    client = FakeClient({"z": [disk("a")]})
    cleaner(client).plan(ctx(dry_run=True))
    assert [r.getMessage() for r in caplog.records] == [
        "List of GCP disk (Total: 1) which will be deleted: ['a']"
    ]
    assert "delete_disk" not in client.names()


def test_plan_outside_dry_run_logs_no_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    cleaner(FakeClient({"z": [disk("a")]})).plan(ctx())
    assert caplog.records == []


def test_plan_closes_the_client_when_listing_fails() -> None:
    class Broken(FakeClient):
        def list_disks(self, project_id: str, zone: str) -> Iterable[DiskView]:
            raise PermissionError("denied")

    client = Broken({"z": []})
    with pytest.raises(PermissionError, match="denied"):
        cleaner(client).plan(ctx())
    assert client.calls[-1] == ("close",)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, ["old", "tagged", "owned"]),
        ({"detach_age": {"days": 5}}, []),
        ({"detach_age": {}}, ["old", "tagged", "owned", "young"]),
        ({"filter_labels": {"env": ["qa"]}}, ["tagged"]),
        ({"filter_labels": {"env": []}}, ["tagged"]),
        ({"filter_labels": {"env": ["dev"]}}, []),
        ({"exception_labels": {"env": ["qa"]}}, ["old", "owned"]),
        ({"exception_regex": ["ta"]}, ["old", "owned"]),
        ({"exception_regex": ["ag"]}, ["old", "tagged", "owned"]),
        ({"custom_age_tag_key": "ttl"}, ["old", "tagged", "owned"]),
    ],
)
def test_plan_filters(overrides: dict[str, Any], expected: list[str]) -> None:
    client = FakeClient(
        {
            "z": [
                disk("old"),
                disk("tagged", labels={"env": "qa"}),
                disk("owned", labels={"owner": "alice"}),
                disk("young", days=0.5),
                disk("attached", users=["vm"]),
            ]
        }
    )
    plan = cleaner(client, **overrides).plan(ctx())
    assert [c.id for c in plan.candidates] == expected


def test_plan_custom_age_label_replaces_detach_age() -> None:
    client = FakeClient({"z": [disk("d", days=0.5, labels={"ttl": "{'hours': 1}"})]})
    plan = cleaner(client, custom_age_tag_key="ttl").plan(ctx())
    assert [c.id for c in plan.candidates] == ["d"]


def test_plan_uses_the_context_clock() -> None:
    client = FakeClient({"z": [disk("d", days=3)]})
    later = RunContext(
        dry_run=False,
        operation=Operation.DELETE,
        options={},
        now=NOW - datetime.timedelta(days=3),
    )
    assert cleaner(client).plan(later).candidates == ()


def test_plan_groups_by_owner_when_slack_is_on() -> None:
    client = FakeClient(
        {
            "z": [
                disk("a", labels={"owner": "alice"}),
                disk("b"),
                disk("c", labels={"owner": "alice"}),
            ]
        }
    )
    plan = cleaner(client, slack_notify_users=True, slack_user_label="owner").plan(ctx())
    assert plan.by_owner == {"alice": ("a", "c"), "not_tagged": ("b",)}


def test_plan_has_no_owner_groups_without_slack() -> None:
    plan = cleaner(FakeClient({"z": [disk("a")]})).plan(ctx())
    assert plan.by_owner is None


def test_apply_deletes_in_plan_order_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = FakeClient({"z1": [disk("a"), disk("b")], "z2": [disk("c")]})
    c = cleaner(client)
    plan = c.plan(ctx())
    client.calls.clear()
    caplog.clear()
    result = c.apply(plan, ctx())
    assert result.succeeded == ("a", "b", "c")
    assert client.calls == [
        ("delete_disk", PROJECT, "z1", "a"),
        ("delete_disk", PROJECT, "z1", "b"),
        ("delete_disk", PROJECT, "z2", "c"),
        ("close",),
    ]
    assert [r.getMessage() for r in caplog.records] == [
        "Deleting disk a",
        "Deleting disk b",
        "Deleting disk c",
        "number of GCP disks deleted: 3",
        "List of GCP disk deleted: ['a', 'b', 'c']",
    ]


def test_apply_skips_a_failed_delete_and_continues(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = FakeClient({"z": [disk("a"), disk("b")]}, fail_on="a")
    c = cleaner(client)
    result = c.apply(c.plan(ctx()), ctx())
    assert result.succeeded == ("b",)
    assert result.failed == ()
    assert "Error occurred while deleting disk a: denied" in [
        r.getMessage() for r in caplog.records
    ]
    assert client.names().count("delete_disk") == TWO


def test_apply_with_nothing_logs_the_empty_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    result = cleaner(FakeClient({})).apply(Plan(), ctx())
    assert result.succeeded == ()
    assert [r.getMessage() for r in caplog.records] == [
        "No GCP disk to delete.",
        "number of GCP disks deleted: 0",
        "List of GCP disk deleted: []",
    ]


def test_apply_in_dry_run_deletes_nothing() -> None:
    client = FakeClient({"z": [disk("a")]})
    c = cleaner(client)
    plan = c.plan(ctx(dry_run=True))
    client.calls.clear()
    c.apply(plan, ctx(dry_run=True))
    assert "delete_disk" not in client.names()


def test_apply_groups_by_owner_and_skips_failed() -> None:
    client = FakeClient(
        {
            "z": [
                disk("a", labels={"owner": "alice"}),
                disk("b"),
                disk("c", labels={"owner": "bob"}),
            ]
        },
        fail_on="c",
    )
    c = cleaner(client, slack_notify_users=True, slack_user_label="owner")
    result = c.apply(c.plan(ctx()), ctx())
    assert result.by_owner == {"alice": ("a",), "not_tagged": ("b",)}
    assert result.succeeded == ("a", "b")


def test_apply_uses_the_zone_of_each_candidate() -> None:
    client = FakeClient({})
    plan = Plan(candidates=(Candidate(id="a", region="zone-9"),))
    cleaner(client).apply(plan, ctx())
    assert client.calls[0] == ("delete_disk", PROJECT, "zone-9", "a")


def test_supports_only_delete() -> None:
    c = cleaner(FakeClient({}))
    assert c.supports(Operation.DELETE)
    assert not c.supports(Operation.STOP)


def test_disk_adapter_keeps_main_attributes_and_result_shapes() -> None:
    args: dict[str, Any] = {
        "dry_run": False,
        "project_id": PROJECT,
        "filter_labels": {},
        "exception_labels": {},
        "age": 1,
        "custom_age_tag_key": "",
        "detach_age": {"days": 1},
        "notags": {},
        "name_regex": [],
        "exception_regex": [],
        "slack_user_label": "owner",
    }
    plain = Disk(**args, slack_notify_users=False)
    grouped = Disk(**args, slack_notify_users=True)
    assert plain.get_deleted == []
    assert grouped.get_deleted == {}
    assert plain.count == 0
    assert Disk.default_disk_state == ["Unattached"]


def test_disk_adapter_deletes_through_the_injected_client() -> None:
    client = FakeClient({"z": [disk("a"), disk("b", users=["vm"])]})
    adapter = Disk(
        False, PROJECT, {}, {}, 1, "", {"days": 1}, {}, [], [], False, "", client, lambda: NOW
    )
    adapter.delete()
    assert adapter.get_deleted == ["a"]
    assert client.calls == [
        ("list_zones", PROJECT),
        ("list_disks", PROJECT, "z"),
        ("delete_disk", PROJECT, "z", "a"),
        ("close",),
    ]
