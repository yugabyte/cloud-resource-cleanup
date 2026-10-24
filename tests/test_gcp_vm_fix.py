"""Behaviour-fix tests of `crc.gcp.vm`: BUG-185 (wait for the zone operation) and
BUG-186 (an error on one instance or zone is recorded and the run goes on). Fakes only."""

import datetime
import logging
import types
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any

import pytest

from crc.core import logger
from crc.core.cleaner import Candidate, Operation, Plan, RunContext
from crc.gcp.vm import InstanceView, VmCleaner, VmSettings
from tests.helpers_ad_vm_engine import done_ids, run_vm

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
PROJECT = "proj-1"
THREE = 3


def stamp(days: float) -> str:
    return (NOW - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.%f%z")


def inst(name: str, created: Any = "old") -> Any:
    ts = stamp(3) if created == "old" else created
    return types.SimpleNamespace(name=name, status="RUNNING", labels={}, creation_timestamp=ts)


class Client:
    """Fake client; `states` are the operation states returned by successive polls."""

    def __init__(
        self,
        zones: Mapping[str, Any],
        states: Sequence[Mapping[str, Any] | Exception] = ({"status": "DONE"},),
        response: object = None,
    ) -> None:
        self.zones = zones
        self.states = list(states)
        self.response = {"name": "op-1"} if response is None else response
        self.calls: list[tuple[str, ...]] = []

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        return [{"name": zone} for zone in self.zones]

    def list_instances(self, project_id: str, zone: str) -> Iterable[InstanceView]:
        self.calls.append(("list_instances", zone))
        outcome = self.zones[zone]
        if isinstance(outcome, Exception):
            raise outcome
        return self._iterate(outcome)

    @staticmethod
    def _iterate(outcome: Sequence[Any]) -> Iterator[Any]:
        for item in outcome:
            if isinstance(item, Exception):
                raise item
            yield item

    def delete_instance(self, project_id: str, zone: str, instance: str) -> object:
        self.calls.append(("delete", zone, instance))
        return self.response

    def stop_instance(self, project_id: str, zone: str, instance: str) -> object:
        self.calls.append(("stop", zone, instance))
        return self.response

    def get_zone_operation(self, project_id: str, zone: str, operation: str) -> Mapping[str, Any]:
        self.calls.append(("poll", zone, operation))
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        if isinstance(state, Exception):
            raise state
        return state


def cleaner(client: Client, **kw: Any) -> VmCleaner:
    settings = VmSettings(
        project_id=PROJECT,
        filter_labels=None,
        exception_labels=None,
        notags=None,
        age={"days": 1},
        custom_age_tag_key="",
        **{"wait_sec": 5.0, "poll_sec": 0.0, **kw},
    )
    return VmCleaner(client, settings)


def ctx(operation: Operation = Operation.DELETE, *, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=operation, options={}, now=NOW)


def one() -> Plan:
    return Plan(candidates=(Candidate(id="a", name="a", region="z"),))


@pytest.fixture
def run_log() -> Iterator[logger.RunLog]:
    run = logger.begin("gcp", "vm", "delete", False, {})
    yield run
    logger.finish()


def failed_ids(run: logger.RunLog) -> list[tuple[str | None, str]]:
    return [(f.region, f.resource_id) for f in run._failures]


@pytest.mark.parametrize("operation", [Operation.DELETE, Operation.STOP])
def test_instance_is_recorded_only_after_the_operation_is_done(operation: Operation) -> None:
    client = Client({}, states=[{"status": "RUNNING"}, {"status": "RUNNING"}, {"status": "DONE"}])
    result = cleaner(client).apply(one(), ctx(operation))
    assert result.succeeded == ("a",)
    verb = "delete" if operation is Operation.DELETE else "stop"
    assert client.calls == [(verb, "z", "a"), ("poll", "z", "op-1")] + [("poll", "z", "op-1")] * 2


@pytest.mark.parametrize(
    "states",
    [
        [{"status": "DONE", "error": {"errors": [{"code": "BAD"}]}}],
        [RuntimeError("poll failed")],
        [{"status": "RUNNING"}],
    ],
    ids=["operation-error", "poll-error", "timeout"],
)
@pytest.mark.parametrize("operation", [Operation.DELETE, Operation.STOP])
def test_a_failed_or_unfinished_operation_is_not_recorded(
    operation: Operation,
    states: list[Any],
    run_log: logger.RunLog,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    client = Client({}, states=states)
    result = cleaner(client, wait_sec=0.05, poll_sec=0.01).apply(one(), ctx(operation))
    assert result.succeeded == ()
    assert failed_ids(run_log) == [("z", "a")]
    assert any(
        m.startswith(f"Error occurred while {operation.value} instance a:") for m in caplog.messages
    )
    assert "No GCP instances to" in " ".join(caplog.messages)


def test_a_response_without_an_operation_name_is_not_recorded(run_log: logger.RunLog) -> None:
    client = Client({}, response={})
    assert cleaner(client).apply(one(), ctx()).succeeded == ()
    assert [c[0] for c in client.calls] == ["delete"]
    assert failed_ids(run_log) == [("z", "a")]


def test_dry_run_neither_acts_nor_polls() -> None:
    client = Client({})
    assert cleaner(client).apply(one(), ctx(dry_run=True)).succeeded == ("a",)
    assert client.calls == []


def test_the_poll_line_is_debug(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client = Client({}, states=[{"status": "RUNNING"}, {"status": "DONE"}])
    assert cleaner(client).apply(one(), ctx()).succeeded == ("a",)
    waits = [r.levelno for r in caplog.records if r.getMessage().startswith("Waiting for GCP")]
    assert waits == [logging.DEBUG]


def test_later_instances_are_still_processed_after_an_unfinished_operation() -> None:
    client = Client({}, states=[{"status": "DONE", "error": {"x": 1}}, {"status": "DONE"}])
    plan = Plan(candidates=tuple(Candidate(id=n, name=n, region="z") for n in "ab"))
    assert cleaner(client).apply(plan, ctx()).succeeded == ("b",)


def test_engine_does_not_record_a_failed_delete() -> None:
    client = Client({"z": [inst("a")]}, states=[{"status": "DONE", "error": {"x": 1}}])
    outcome = run_vm("gcp", cleaner(client))
    assert done_ids(outcome) == []


def test_an_instance_error_is_recorded_and_the_next_instance_is_evaluated(
    run_log: logger.RunLog, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    client = Client({"z": [inst("bad", created="not-a-time"), inst("good")]})
    c = cleaner(client)
    plan = c.plan(ctx())
    assert [cand.id for cand in plan.candidates] == ["good"]
    assert failed_ids(run_log) == [("z", "bad")]
    prefix = "Error occurred while evaluating instance bad:"
    assert any(m.startswith(prefix) for m in caplog.messages)
    assert c.apply(plan, ctx()).succeeded == ("good",)


def test_an_instance_without_a_name_is_recorded_with_a_placeholder(
    run_log: logger.RunLog,
) -> None:
    broken = types.SimpleNamespace(status="RUNNING", creation_timestamp=stamp(3))
    client = Client({"z": [broken]})
    assert cleaner(client).plan(ctx()).candidates == ()
    assert failed_ids(run_log) == [("z", "?")]


def test_a_zone_listing_error_is_recorded_and_the_zone_is_skipped(
    run_log: logger.RunLog, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    client = Client({"z1": PermissionError("denied"), "z2": [inst("b")]})
    plan = cleaner(client).plan(ctx())
    assert [(cand.region, cand.id) for cand in plan.candidates] == [("z2", "b")]
    assert failed_ids(run_log) == [("z1", "-")]
    assert run_log._regions_skipped == 1
    assert "Zone z1: failed listing GCP instances: denied" in caplog.messages


def test_an_error_while_iterating_a_zone_keeps_what_was_listed_and_skips_the_rest(
    run_log: logger.RunLog,
) -> None:
    client = Client({"z1": [inst("a"), ConnectionError("down"), inst("never")], "z2": [inst("b")]})
    plan = cleaner(client).plan(ctx())
    assert [cand.id for cand in plan.candidates] == ["a", "b"]
    assert failed_ids(run_log) == [("z1", "-")]


def test_engine_continues_with_the_next_zone_after_a_listing_error() -> None:
    client = Client({"z1": RuntimeError("boom"), "z2": [inst("b")]})
    outcome = run_vm("gcp", cleaner(client))
    assert done_ids(outcome) == ["b"]
    assert [c for c in client.calls if c[0] == "delete"] == [("delete", "z2", "b")]


def test_the_zone_list_itself_still_raises() -> None:
    class Denied(Client):
        def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
            raise PermissionError("denied")

    with pytest.raises(PermissionError, match="denied"):
        cleaner(Denied({})).plan(ctx())
