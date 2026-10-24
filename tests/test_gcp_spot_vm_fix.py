"""BUG-228 (unlabelled Spot VM), BUG-229 (failed delete), BUG-242 (disk age)."""

import dataclasses
import datetime
import logging
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from crc.core import logger
from crc.gcp.spot_vm import SpotSettings, SpotVMCleaner
from crc.gcp.spot_vm.instances import reject_reason
from tests.helpers_ad_spot_engine import run_through_engine
from tests.test_gcp_spot_instances import (
    NOW,
    SETTINGS,
    FakeClient,
    context,
    instance,
    leftover,
    mutating,
)

STAMP = "%Y-%m-%dT%H:%M:%S.%f%z"
AGE_KEY = "retention"
CUSTOM = dataclasses.replace(SETTINGS, custom_age_tag_key=AGE_KEY)


@pytest.fixture(autouse=True)
def _end_run() -> Iterator[None]:
    yield
    logger.finish()


def ago(**delta: float) -> str:
    return (NOW - datetime.timedelta(**delta)).strftime(STAMP)


class DiskClient(FakeClient):
    """A client whose `get_disk` answers with a given disk resource."""

    def __init__(self, instances: list[Any], disk: Mapping[str, Any]) -> None:
        super().__init__(instances)
        self.disk = disk

    def get_disk(self, project_id: str, scope: str, location: str, disk: str) -> Mapping[str, Any]:
        self.calls.append(("get_disk", scope, location, disk))
        return self.disk


class OpClient(FakeClient):
    """A client whose instance delete returns `response` and whose polls return `polls`."""

    def __init__(self, instances: list[Any], response: Any, poll: Any) -> None:
        super().__init__(instances)
        self.response = response
        self.poll = poll

    def delete_instance(self, project_id: str, zone: str, instance: str) -> Mapping[str, Any]:
        self.calls.append(("delete_instance", zone, instance))
        return self.response  # type: ignore[no-any-return]

    def get_operation(
        self, project_id: str, scope: str, location: str, operation: str
    ) -> Mapping[str, Any]:
        self.calls.append(("get_operation", scope, location, operation))
        return self.poll  # type: ignore[no-any-return]


def clock() -> datetime.datetime:
    return NOW


# BUG-228


def test_bug_228_unlabelled_old_vm_is_kept_when_filter_is_empty() -> None:
    assert reject_reason(instance("u", labels={}), SETTINGS, clock) == "unlabelled"


def test_bug_228_labelled_old_vm_is_still_selected_when_filter_is_empty() -> None:
    assert reject_reason(instance("l"), SETTINGS, clock) is None


def test_bug_228_unlabelled_vm_fails_an_active_filter_as_before() -> None:
    settings = dataclasses.replace(SETTINGS, filter_labels={"team": ["ci"]})
    assert reject_reason(instance("u", labels={}), settings, clock) == "filter_mismatch"


def test_bug_228_young_unlabelled_vm_keeps_its_age_reason() -> None:
    young = instance("u", labels={})
    young.creation_timestamp = ago(hours=1)
    assert reject_reason(young, SETTINGS, clock) == "too_young"


def test_bug_228_plan_and_apply_never_delete_an_unlabelled_vm(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    client = FakeClient([instance("u", [leftover("d1")], labels={}), instance("l")])
    cleaner = SpotVMCleaner(client, SETTINGS)
    logger.begin("gcp", "spot_instances", "delete", False, {}, clock=lambda: 0.0)
    plan = cleaner.plan(context(dry_run=False))
    result = cleaner.apply(plan, context(dry_run=False))
    assert [c.id for c in plan.candidates] == ["l"]
    assert result.succeeded == ("l",)
    assert ("delete_instance", "z", "u") not in client.calls
    assert ("get_disk", "zone", "z", "d1") not in client.calls
    logger.finish()
    lines = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("SKIPPED") and "reason=unlabelled count=1" in m for m in lines)


def test_bug_228_engine_dry_run_lists_only_labelled_vms() -> None:
    client = FakeClient([instance("u", labels={}), instance("l")])
    outcome = run_through_engine(
        "gcp",
        "spot_instances",
        lambda _ctx: SpotVMCleaner(client, SETTINGS),
        context(dry_run=True),
    )
    assert [c.id for c in outcome.plan.candidates] == ["l"]


def test_bug_228_unlabelled_vm_with_exception_labels_set_is_kept() -> None:
    settings = dataclasses.replace(SETTINGS, exception_labels={"keep": ["yes"]})
    assert reject_reason(instance("u", labels={}), settings, clock) == "unlabelled"
    assert reject_reason(instance("p", labels={"keep": "yes"}), settings, clock) == "exception_tag"


# BUG-229


@pytest.mark.parametrize(
    ("response", "poll"),
    [
        ({}, {"status": "DONE"}),
        ({"name": ""}, {"status": "DONE"}),
        ({"name": "op-a"}, {"status": "DONE", "error": {"code": 1}}),
        ({"name": "op-a"}, {"status": "RUNNING"}),
    ],
    ids=["no-name", "empty-name", "operation-error", "never-done"],
)
def test_bug_229_a_delete_that_does_not_complete_is_failed_not_deleted(
    response: Any, poll: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    settings = dataclasses.replace(SETTINGS, wait_sec=2, poll_sec=0)
    client = OpClient([instance("a", [leftover("d1")])], response, poll)
    cleaner = SpotVMCleaner(client, settings)
    logger.begin("gcp", "spot_instances", "delete", False, {}, clock=lambda: 0.0)
    result = cleaner.apply(cleaner.plan(context(dry_run=False)), context(dry_run=False))
    logger.finish()
    assert result.succeeded == ()
    assert result.related["Disk"] == ()
    assert ("get_disk", "zone", "z", "d1") not in client.calls
    assert mutating(client) == [("delete_instance", "z", "a")]
    lines = [r.getMessage() for r in caplog.records]
    assert any("FAILED cloud=gcp resource=spot_instances region=z id=a" in m for m in lines)
    assert any("ACTION" in m and "deleted" in m for m in lines) is False
    assert any("failed=1" in m for m in lines if m.startswith("RESULT"))


def test_bug_229_engine_does_not_record_a_failed_delete() -> None:
    client = OpClient([instance("a", [leftover("d1")])], {}, {"status": "DONE"})
    outcome = run_through_engine(
        "gcp",
        "spot_instances",
        lambda _ctx: SpotVMCleaner(client, SETTINGS),
        context(dry_run=False),
    )
    assert outcome.result is not None
    assert outcome.result.succeeded == ()
    assert outcome.result.related["Disk"] == ()


# BUG-242


def disk_resource(created: Any, **labels: str) -> dict[str, Any]:
    return {"labels": dict(labels), "creationTimestamp": created}


def run(
    disk: Mapping[str, Any], settings: SpotSettings = CUSTOM, *, dry_run: bool = False
) -> tuple[list[str], DiskClient]:
    client = DiskClient([instance("a", [leftover("d1")])], disk)
    cleaner = SpotVMCleaner(client, settings)
    plan = cleaner.plan(context(dry_run=dry_run))
    if dry_run:
        return list(plan.related["Disk"]), client
    result = cleaner.apply(plan, context(dry_run=False))
    assert result.succeeded == ("a",)
    return list(result.related["Disk"]), client


def test_bug_242_disk_younger_than_its_custom_age_is_kept() -> None:
    disks, client = run(disk_resource(ago(days=3), **{AGE_KEY: "{'days': 10}"}))
    assert disks == []
    assert ("delete_disk", "zone", "z", "d1") not in client.calls


def test_bug_242_disk_older_than_its_custom_age_is_deleted() -> None:
    disks, client = run(disk_resource(ago(days=3), **{AGE_KEY: "{'days': 2}"}))
    assert disks == ["p/z/d1"]
    assert ("delete_disk", "zone", "z", "d1") in client.calls


def test_bug_242_custom_age_replaces_the_default_age_for_the_disk() -> None:
    disks, _ = run(disk_resource(ago(hours=5), **{AGE_KEY: "{'hours': 1}"}))
    assert disks == ["p/z/d1"]


@pytest.mark.parametrize("value", ["", "garbage", "{'days': 0}", "[1]"])
def test_bug_242_invalid_custom_age_keeps_the_disk(value: str) -> None:
    disks, client = run(disk_resource(ago(days=30), **{AGE_KEY: value}))
    assert disks == []
    assert ("delete_disk", "zone", "z", "d1") not in client.calls


@pytest.mark.parametrize("created", [None, "", "yesterday", 42])
def test_bug_242_unreadable_disk_age_keeps_a_disk_with_a_custom_age(created: Any) -> None:
    disks, client = run(disk_resource(created, **{AGE_KEY: "{'days': 1}"}))
    assert disks == []
    assert ("delete_disk", "zone", "z", "d1") not in client.calls


def test_bug_242_disk_without_the_age_label_is_deleted_as_before() -> None:
    disks, _ = run(disk_resource(None, other="x"))
    assert disks == ["p/z/d1"]
    disks, _ = run({"labels": {}})
    assert disks == ["p/z/d1"]


def test_bug_242_no_custom_age_key_ignores_a_label_named_like_it() -> None:
    disks, _ = run(disk_resource(ago(days=1), **{AGE_KEY: "{'days': 99}"}), SETTINGS)
    assert disks == ["p/z/d1"]


def test_bug_242_dry_run_honours_the_disk_custom_age() -> None:
    young, _ = run(disk_resource(ago(days=1), **{AGE_KEY: "{'days': 5}"}), dry_run=True)
    old, _ = run(disk_resource(ago(days=9), **{AGE_KEY: "{'days': 5}"}), dry_run=True)
    assert young == []
    assert old == ["p/z/d1"]


def test_bug_242_exception_labels_still_win_over_the_age() -> None:
    settings = dataclasses.replace(CUSTOM, exception_labels={"keep": ["yes"]})
    disks, _ = run(disk_resource(ago(days=30), keep="yes", **{AGE_KEY: "{'days': 1}"}), settings)
    assert disks == []


def test_bug_242_skip_is_counted_and_logged_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    logger.begin("gcp", "spot_instances", "delete", True, {}, clock=lambda: 0.0)
    run(disk_resource(ago(days=1), **{AGE_KEY: "{'days': 5}"}), dry_run=True)
    logger.finish()
    records = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert (
        logging.DEBUG,
        "Skipping leftover Spot disk p/z/d1: disk_too_young",
    ) in records
    assert any(
        lvl == logging.INFO and "SKIPPED" in m and "reason=disk_too_young count=1" in m
        for lvl, m in records
    )


def test_bug_242_engine_honours_the_disk_custom_age() -> None:
    client = DiskClient(
        [instance("a", [leftover("d1")])],
        disk_resource(ago(days=1), **{AGE_KEY: "{'days': 5}"}),
    )
    outcome = run_through_engine(
        "gcp",
        "spot_instances",
        lambda _ctx: SpotVMCleaner(client, CUSTOM),
        context(dry_run=False),
    )
    assert outcome.result is not None
    assert outcome.result.succeeded == ("a",)
    assert outcome.result.related["Disk"] == ()
    assert ("delete_disk", "zone", "z", "d1") not in client.calls
