"""Cleaner contract: a fake cleaner proves plan/apply, and the DTOs are immutable."""

import dataclasses
import datetime
from collections.abc import Mapping
from typing import Any

import pytest

from crc.core.cleaner import (
    Candidate,
    Cleaner,
    Failure,
    Operation,
    Plan,
    Result,
    RunContext,
    Skipped,
    candidate_ids,
)

NOW = datetime.datetime(2026, 1, 10, 12, 0, tzinfo=datetime.UTC)


def make_context(
    *, dry_run: bool = False, operation: Operation = Operation.DELETE, **options: Any
) -> RunContext:
    return RunContext(dry_run=dry_run, operation=operation, options=options, now=NOW)


class FakeCloudClient:
    """Stands in for an SDK client and records every call."""

    def __init__(self, resources: list[Candidate]) -> None:
        self.resources = resources
        self.calls: list[tuple[str, str]] = []

    def list_resources(self) -> list[Candidate]:
        self.calls.append(("list", ""))
        return list(self.resources)

    def delete_resource(self, resource_id: str) -> None:
        self.calls.append(("delete", resource_id))
        if resource_id == "bad":
            raise RuntimeError("boom")


class FakeCleaner:
    cloud = "fake"
    kind = "thing"

    def __init__(self, client: FakeCloudClient) -> None:
        self._client = client

    def supports(self, operation: Operation) -> bool:
        return operation is Operation.DELETE

    def plan(self, context: RunContext) -> Plan:
        keep: list[Candidate] = []
        skipped: list[Skipped] = []
        floor = context.now - datetime.timedelta(days=1)
        for item in self._client.list_resources():
            if item.launch_time is not None and item.launch_time <= floor:
                keep.append(item)
            else:
                skipped.append(Skipped(item.id, "younger than age floor", item.name))
        return Plan(tuple(keep), tuple(skipped))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        done: list[str] = []
        failed: list[Failure] = []
        for item in plan.candidates:
            try:
                self._client.delete_resource(item.id)
            except RuntimeError as error:
                failed.append(Failure(item.id, str(error)))
            else:
                done.append(item.id)
        return Result(tuple(done), tuple(failed), {"NIC": tuple(f"{i}-nic" for i in done)})


def candidate(resource_id: str, age_days: float) -> Candidate:
    return Candidate(
        id=resource_id,
        name=f"name-{resource_id}",
        region="r1",
        state="running",
        instance_type="t1",
        launch_time=NOW - datetime.timedelta(days=age_days),
        tags={"owner": "me"},
    )


def make_cleaner() -> tuple[FakeCleaner, FakeCloudClient]:
    client = FakeCloudClient([candidate("old", 3), candidate("bad", 5), candidate("new", 0.1)])
    return FakeCleaner(client), client


def test_fake_satisfies_protocol() -> None:
    cleaner: Cleaner = FakeCleaner(FakeCloudClient([]))
    assert (cleaner.cloud, cleaner.kind) == ("fake", "thing")
    assert cleaner.supports(Operation.DELETE)
    assert not cleaner.supports(Operation.STOP)


def test_plan_is_read_only_and_uses_context_clock() -> None:
    cleaner, client = make_cleaner()
    plan = cleaner.plan(make_context(dry_run=True))
    assert candidate_ids(plan.candidates) == ("old", "bad")
    assert [s.id for s in plan.skipped] == ["new"]
    assert plan.skipped[0].reason == "younger than age floor"
    assert client.calls == [("list", "")]

    later = RunContext(True, Operation.DELETE, {}, NOW + datetime.timedelta(days=2))
    assert candidate_ids(cleaner.plan(later).candidates) == ("old", "bad", "new")


def test_apply_reports_success_failure_and_related() -> None:
    cleaner, client = make_cleaner()
    context = make_context()
    result = cleaner.apply(cleaner.plan(context), context)
    assert result.succeeded == ("old",)
    assert result.failed == (Failure("bad", "boom"),)
    assert result.related == {"NIC": ("old-nic",)}
    assert [call for call in client.calls if call[0] == "delete"] == [
        ("delete", "old"),
        ("delete", "bad"),
    ]


def test_dry_run_is_plan_without_apply() -> None:
    cleaner, client = make_cleaner()
    cleaner.plan(make_context(dry_run=True))
    assert all(kind != "delete" for kind, _ in client.calls)


def test_operation_values_match_cli() -> None:
    assert [op.value for op in Operation] == ["delete", "stop"]
    assert Operation("stop") is Operation.STOP
    with pytest.raises(ValueError, match="start"):
        Operation("start")


def test_defaults() -> None:
    assert Plan() == Plan((), ())
    assert Plan().related == {}
    assert Plan().by_owner is None
    assert Result() == Result((), (), {})
    assert Result().by_owner is None
    one = Candidate(id="x")
    assert (one.name, one.region, one.state, one.instance_type) == ("", "", "", "")
    assert one.launch_time is None
    assert one.tags == {}
    assert one.extra == {}


@pytest.mark.parametrize(
    "instance",
    [
        Candidate(id="a"),
        Skipped("a", "why"),
        Plan(),
        Failure("a", "e"),
        Result(),
        make_context(),
    ],
)
def test_dataclasses_are_frozen(instance: Any) -> None:
    field_name = dataclasses.fields(instance)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(instance, field_name, "changed")
    # frozen + slots: a new attribute raises AttributeError or, on some CPython
    # versions, TypeError from the generated __setattr__.
    with pytest.raises((AttributeError, TypeError)):
        instance.brand_new = 1


def test_mappings_are_copied_and_read_only() -> None:
    tags = {"k": "v"}
    extra: dict[str, Any] = {"zone": "z1"}
    options: dict[str, Any] = {"age": {"days": 1}}
    related: dict[str, tuple[str, ...]] = {"Disk": ("d1",)}
    by_owner: dict[str, tuple[str, ...]] = {"alice": ("d1", "d2")}
    one = Candidate(id="a", tags=tags, extra=extra)
    context = RunContext(False, Operation.DELETE, options, NOW)
    plan = Plan(related=related, by_owner=by_owner)
    result = Result(related=related, by_owner=by_owner)
    tags["k"] = "changed"
    extra["zone"] = "changed"
    options["age"] = "changed"
    related["Disk"] = ("d2",)
    by_owner["alice"] = ("d3",)
    assert one.tags == {"k": "v"}
    assert one.extra == {"zone": "z1"}
    assert context.options == {"age": {"days": 1}}
    assert plan.related == {"Disk": ("d1",)}
    assert plan.by_owner == {"alice": ("d1", "d2")}
    assert result.related == {"Disk": ("d1",)}
    assert result.by_owner == {"alice": ("d1", "d2")}
    assert plan.by_owner is not None
    assert result.by_owner is not None
    for frozen in (
        one.tags,
        one.extra,
        context.options,
        plan.related,
        plan.by_owner,
        result.related,
        result.by_owner,
    ):
        assert isinstance(frozen, Mapping)
        with pytest.raises(TypeError):
            frozen["new"] = 1  # type: ignore[index]


def test_sequences_become_tuples() -> None:
    plan = Plan(candidates=[Candidate(id="a")], skipped=[Skipped("b", "r")])  # type: ignore[arg-type]
    result = Result(succeeded=["a"], failed=[Failure("b", "e")])  # type: ignore[arg-type]
    assert isinstance(plan.candidates, tuple)
    assert isinstance(plan.skipped, tuple)
    assert isinstance(result.succeeded, tuple)
    assert isinstance(result.failed, tuple)


def test_context_carries_now_and_options() -> None:
    context = make_context(dry_run=True, operation=Operation.STOP, age={"days": 2})
    assert context.now == NOW
    assert context.dry_run is True
    assert context.operation is Operation.STOP
    assert context.options["age"] == {"days": 2}
