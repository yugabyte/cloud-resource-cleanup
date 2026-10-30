"""Unit tests for `crc.aws.snapshot.SnapshotCleaner`: read-only plan(), mutating apply()."""

import datetime
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from botocore.exceptions import EndpointConnectionError

from crc.aws.snapshot import SnapshotCleaner, SnapshotClient
from crc.core import engine
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, RunContext
from crc.core.registry import Registry
from crc.core.selection import Selection

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
AGE_DAYS = 90


def snap(snap_id: str, days: float = 100, **extra: Any) -> dict[str, Any]:
    return {"SnapshotId": snap_id, "StartTime": NOW - datetime.timedelta(days=days), **extra}


class FakeClient:
    def __init__(
        self,
        snaps: Sequence[Mapping[str, Any]],
        images: Sequence[Mapping[str, Any]] = (),
        fail: Mapping[str, Exception] | None = None,
    ) -> None:
        self.snaps = snaps
        self.images = images
        self.fail = fail or {}
        self.calls: list[tuple[str, Any]] = []

    def describe_snapshots(self, *, OwnerIds: Sequence[str]) -> Mapping[str, Any]:
        self.calls.append(("describe_snapshots", tuple(OwnerIds)))
        return {"Snapshots": self.snaps}

    def describe_images(self, *, Owners: Sequence[str]) -> Mapping[str, Any]:
        self.calls.append(("describe_images", tuple(Owners)))
        return {"Images": self.images}

    def delete_snapshot(self, *, SnapshotId: str) -> Mapping[str, Any]:
        self.calls.append(("delete_snapshot", SnapshotId))
        if SnapshotId in self.fail:
            raise self.fail[SnapshotId]
        return {}


def make(
    clients: Mapping[str, FakeClient],
    age_days: int = 90,
    exception_tags: Mapping[str, Sequence[str]] | None = None,
) -> SnapshotCleaner:
    def factory(region: str) -> SnapshotClient:
        client: SnapshotClient = clients[region]
        return client

    return SnapshotCleaner(factory, lambda: list(clients), age_days, exception_tags)


def context(dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def test_plan_lists_and_never_deletes() -> None:
    client = FakeClient([snap("a"), snap("b", 1)])
    plan = make({"r1": client}).plan(context())
    assert [c.id for c in plan.candidates] == ["a"]
    assert [(s.id, s.reason) for s in plan.skipped] == [("b", "newer than age_days")]
    assert client.calls == [("describe_snapshots", ("self",)), ("describe_images", ("self",))]


def test_plan_candidates_carry_region_and_start_time() -> None:
    plan = make({"r1": FakeClient([snap("a")]), "r2": FakeClient([snap("b")])}).plan(context())
    assert [(c.id, c.region) for c in plan.candidates] == [("a", "r1"), ("b", "r2")]
    assert plan.candidates[0].launch_time == NOW - datetime.timedelta(days=100)


def test_plan_skips_ami_backed_and_tagged_snapshots() -> None:
    client = FakeClient(
        [snap("a"), snap("b"), snap("c", Tags=[{"Key": "keep", "Value": "1"}])],
        images=[{"BlockDeviceMappings": [{"Ebs": {"SnapshotId": "b"}}]}],
    )
    plan = make({"r": client}, exception_tags={"keep": []}).plan(context())
    assert [c.id for c in plan.candidates] == ["a"]
    assert {s.id: s.reason for s in plan.skipped} == {
        "b": "used by AMI",
        "c": "has an exception tag",
    }


def test_plan_age_cutoff_is_inclusive() -> None:
    plan = make({"r": FakeClient([snap("edge", 90)])}).plan(context())
    assert [c.id for c in plan.candidates] == ["edge"]


def test_plan_skips_region_on_connectivity_error(caplog: pytest.LogCaptureFixture) -> None:
    class Down(FakeClient):
        def describe_snapshots(self, *, OwnerIds: Sequence[str]) -> Mapping[str, Any]:
            raise EndpointConnectionError(endpoint_url="https://ec2.example")

    caplog.set_level(logging.WARNING)
    plan = make({"r1": Down([]), "r2": FakeClient([snap("b")])}).plan(context())
    assert [c.id for c in plan.candidates] == ["b"]
    assert any("skipped snapshot cleanup" in r.getMessage() for r in caplog.records)


def test_plan_invalid_age_fails_before_listing() -> None:
    client = FakeClient([snap("a")])
    cleaner = make({"r": client}, age_days=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        cleaner.plan(context())
    assert client.calls == []


def test_apply_deletes_planned_snapshots_in_order() -> None:
    client = FakeClient([snap("a"), snap("b")])
    cleaner = make({"r": client})
    result = cleaner.apply(cleaner.plan(context()), context())
    assert result.succeeded == ("a", "b")
    assert [c for c in client.calls if c[0] == "delete_snapshot"] == [
        ("delete_snapshot", "a"),
        ("delete_snapshot", "b"),
    ]


def test_apply_dry_run_makes_no_call() -> None:
    client = FakeClient([snap("a")])
    cleaner = make({"r": client})
    result = cleaner.apply(cleaner.plan(context(dry_run=True)), context(dry_run=True))
    assert result.succeeded == ("a",)
    assert all(c[0] != "delete_snapshot" for c in client.calls)


def test_apply_logs_and_swallows_delete_errors(caplog: pytest.LogCaptureFixture) -> None:
    client = FakeClient([snap("a"), snap("b")], fail={"a": ValueError("busy")})
    cleaner = make({"r": client})
    caplog.set_level(logging.INFO)
    result = cleaner.apply(cleaner.plan(context()), context())
    assert result.succeeded == ("b",)
    assert [(f.id, f.error) for f in result.failed] == [("a", "busy")]
    messages = [r.getMessage() for r in caplog.records]
    assert "Failed to delete snapshot a: busy" in messages
    assert "Deleted snapshot b" in messages


def test_apply_reuses_the_planned_client() -> None:
    created: list[str] = []
    client = FakeClient([snap("a")])

    def factory(region: str) -> SnapshotClient:
        created.append(region)
        return client

    cleaner = SnapshotCleaner(factory, lambda: ["r"], 90, None)
    cleaner.apply(cleaner.plan(context()), context())
    assert created == ["r"]


def test_apply_of_foreign_candidate_builds_its_client() -> None:
    client = FakeClient([])
    cleaner = make({"r": client})
    result = cleaner.apply(Plan(candidates=(Candidate(id="x", region="r"),)), context())
    assert result.succeeded == ("x",)
    assert client.calls == [("delete_snapshot", "x")]


def test_supports_only_delete() -> None:
    cleaner = make({})
    assert cleaner.supports(Operation.DELETE)
    assert not cleaner.supports(Operation.STOP)


def _engine_run(cleaner: SnapshotCleaner, dry_run: bool = False) -> list[str]:
    registry = Registry()
    built: Cleaner = cleaner
    registry.register("aws", "snapshot")(lambda ctx: built)
    (outcome,) = engine.run(Selection((("aws", "snapshot"),)), context(dry_run), registry)
    assert outcome.result is not None or dry_run
    return (
        list(outcome.result.succeeded)
        if outcome.result
        else [c.id for c in outcome.plan.candidates]
    )


def test_engine_runs_the_cleaner_with_injected_clients() -> None:
    client = FakeClient([snap("a"), snap("b", 1)])
    assert _engine_run(make({"r": client}, AGE_DAYS)) == ["a"]
    assert client.calls[-1] == ("delete_snapshot", "a")


def test_engine_lists_before_deleting_and_skips_the_region_on_error() -> None:
    client = FakeClient([snap("a"), {"SnapshotId": "b"}])
    assert _engine_run(make({"r": client})) == []
    assert [c for c in client.calls if c[0] == "delete_snapshot"] == []
