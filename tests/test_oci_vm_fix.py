"""Behaviour fixes of `crc.oci.vm`: BUG-158 (OKE / instance pool guard) and
BUG-162 (an error while evaluating one instance does not stop the run)."""

import datetime
import logging
import types
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from crc.core.cleaner import Operation, RunContext, Skipped
from crc.oci.vm import VmCleaner, VmSettings
from tests.helpers_ad_vm_engine import done_ids, run_vm

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
SETTINGS = VmSettings(filter_tags={}, age={"hours": 5})


def inst(
    inst_id: str,
    *,
    tags: Mapping[str, str] | None = None,
    defined_tags: Any = None,
    metadata: Any = None,
    created: Any = "old",
    state: str = "RUNNING",
) -> Any:
    when = NOW - datetime.timedelta(hours=100) if created == "old" else created
    return types.SimpleNamespace(
        id=inst_id,
        display_name=f"name-{inst_id}",
        lifecycle_state=state,
        freeform_tags={"env": "ci"} if tags is None else tags,
        defined_tags=defined_tags,
        metadata=metadata,
        time_created=when,
    )


class FakeApi:
    def __init__(self, instances: Sequence[Any]) -> None:
        self.instances = list(instances)
        self.calls: list[tuple[str, ...]] = []

    def list_instances(self) -> Sequence[Any]:
        self.calls.append(("list_instances",))
        return self.instances

    def terminate_instance(self, instance_id: str) -> None:
        self.calls.append(("terminate_instance", instance_id))

    def stop_instance(self, instance_id: str) -> None:
        self.calls.append(("stop_instance", instance_id))


class FakeRunLog:
    def __init__(self) -> None:
        self.skips: list[str] = []
        self.failures: list[tuple[str | None, str, str]] = []

    def skip(self, reason: str) -> None:
        self.skips.append(reason)

    def fail(
        self, region: str | None, resource_id: str, error: Any, *, logged: bool = False
    ) -> None:
        self.failures.append((region, resource_id, str(error)))

    def region(self, *args: Any) -> None: ...

    def region_skipped(self, *args: Any) -> None: ...

    def action(self, *args: Any) -> None: ...


@pytest.fixture
def run_log(monkeypatch: pytest.MonkeyPatch) -> FakeRunLog:
    fake = FakeRunLog()
    monkeypatch.setattr("crc.oci._logger.logger.current", lambda: fake)
    return fake


def context(operation: Operation = Operation.DELETE, *, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=operation, options={}, now=NOW)


def run_cleaner(
    instances: Sequence[Any],
    operation: Operation = Operation.DELETE,
    settings: VmSettings = SETTINGS,
) -> tuple[FakeApi, VmCleaner, Any]:
    api = FakeApi(instances)
    cleaner = VmCleaner(lambda region: api, lambda: ["r1"], settings)
    ctx = context(operation)
    plan = cleaner.plan(ctx)
    cleaner.apply(plan, ctx)
    return api, cleaner, plan


_ENGINE_SETTINGS = VmSettings(filter_tags={"env": ["ci"]}, age={"hours": 5})

MANAGED = [
    ("freeform-oke", {"env": "ci", "oke-cluster": "c1"}, None, None),
    ("freeform-oke-value", {"env": "ci", "pool": "OKE-nodes"}, None, None),
    ("freeform-instance-pool", {"env": "ci", "x": "instance_pool"}, None, None),
    ("defined-tag", {"env": "ci"}, {"Oracle-Tags": {"CreatedBy": "instancepool-1"}}, None),
    ("metadata", {"env": "ci"}, None, {"oke-native-pool-id": "ocid1.nodepool"}),
]


@pytest.mark.parametrize("operation", [Operation.DELETE, Operation.STOP])
@pytest.mark.parametrize(("name", "tags", "defined", "metadata"), MANAGED)
def test_bug_158_managed_instances_are_kept(
    name: str, tags: Any, defined: Any, metadata: Any, operation: Operation, run_log: FakeRunLog
) -> None:
    api, _, plan = run_cleaner(
        [
            inst("m", tags=tags, defined_tags=defined, metadata=metadata),
            inst("plain"),
        ],
        operation,
    )
    mutation = "terminate_instance" if operation is Operation.DELETE else "stop_instance"
    assert api.calls == [("list_instances",), (mutation, "plain")]
    assert [c.id for c in plan.candidates] == ["plain"]
    assert [(s.id, s.reason) for s in plan.skipped] == [("m", "OKE or instance pool member")]
    assert run_log.skips == ["managed"]


def test_bug_158_guard_applies_with_empty_filter_tags(run_log: FakeRunLog) -> None:
    api, _, _ = run_cleaner([inst("m", tags={"oke": "1"})], settings=VmSettings(age={}))
    assert api.calls == [("list_instances",)]
    assert run_log.skips == ["managed"]


def test_bug_158_guard_comes_after_the_state_check(run_log: FakeRunLog) -> None:
    _, _, plan = run_cleaner([inst("m", tags={"oke": "1"}, state="STOPPED")])
    assert [s.reason for s in plan.skipped] == ["state is not allowed"]
    assert run_log.skips == ["state"]


def test_bug_158_dry_run_lists_no_managed_instance(run_log: FakeRunLog) -> None:
    api = FakeApi([inst("m", tags={"oke": "1"}), inst("plain")])
    cleaner = VmCleaner(lambda region: api, lambda: ["r1"], SETTINGS)
    plan = cleaner.plan(context(dry_run=True))
    assert [c.id for c in plan.candidates] == ["plain"]


def test_bug_158_instances_without_defined_tags_or_metadata_still_match(
    run_log: FakeRunLog,
) -> None:
    plain = types.SimpleNamespace(
        id="a",
        display_name="a",
        lifecycle_state="RUNNING",
        freeform_tags={"env": "ci"},
        time_created=NOW - datetime.timedelta(hours=100),
    )
    api, _, _ = run_cleaner([plain])
    assert api.calls == [("list_instances",), ("terminate_instance", "a")]


def test_bug_158_engine_skips_managed_instances(run_log: FakeRunLog) -> None:
    api = FakeApi([inst("m", metadata={"oke-native-pool-id": "x"}), inst("plain")])
    cleaner = VmCleaner(lambda region: api, lambda: ["r1"], _ENGINE_SETTINGS, engine=True)
    outcome = run_vm("oci", cleaner)
    assert api.calls == [("list_instances",), ("terminate_instance", "plain")]
    assert done_ids(outcome) == ["name-plain"]


BROKEN = [
    ("missing-created-time", {"created": None}),
    ("naive-created-time", {"created": datetime.datetime(2026, 1, 1)}),  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical
]


@pytest.mark.parametrize("operation", [Operation.DELETE, Operation.STOP])
@pytest.mark.parametrize(("name", "overrides"), BROKEN)
def test_bug_162_broken_instance_is_kept_and_loop_continues(
    name: str, overrides: Any, operation: Operation, run_log: FakeRunLog
) -> None:
    api, _, plan = run_cleaner(
        [inst("a"), inst("bad", **overrides), inst("c")],
        operation,
    )
    mutation = "terminate_instance" if operation is Operation.DELETE else "stop_instance"
    assert api.calls == [("list_instances",), (mutation, "a"), (mutation, "c")]
    assert [c.id for c in plan.candidates] == ["a", "c"]
    (skipped,) = plan.skipped
    assert isinstance(skipped, Skipped)
    assert (skipped.id, skipped.reason) == ("bad", "evaluation failed")
    ((region, resource_id, _),) = run_log.failures
    assert (region, resource_id) == ("r1", "bad")
    assert run_log.skips == []


def test_bug_162_failure_is_logged_as_an_error(
    caplog: pytest.LogCaptureFixture, run_log: FakeRunLog
) -> None:
    caplog.set_level(logging.DEBUG)
    run_cleaner([inst("bad", created=None)])
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].getMessage().startswith("Error occurred while evaluating instance name-bad: ")


def test_bug_162_malformed_max_age_keeps_every_instance(
    monkeypatch: pytest.MonkeyPatch, run_log: FakeRunLog
) -> None:
    monkeypatch.setenv("MAX_AGE", "2")
    api, _, _ = run_cleaner([inst("a"), inst("b")], settings=VmSettings(age={"days": 1}))
    assert api.calls == [("list_instances",)]
    assert [f[1] for f in run_log.failures] == ["a", "b"]


def test_bug_162_failure_in_one_region_does_not_stop_the_next(run_log: FakeRunLog) -> None:
    apis = {"r1": FakeApi([inst("bad", created=None)]), "r2": FakeApi([inst("ok")])}
    cleaner = VmCleaner(lambda region: apis[region], lambda: ["r1", "r2"], SETTINGS)
    ctx = context()
    cleaner.apply(cleaner.plan(ctx), ctx)
    assert apis["r1"].calls == [("list_instances",)]
    assert apis["r2"].calls == [("list_instances",), ("terminate_instance", "ok")]


def test_bug_162_engine_carries_on_and_names_only_healthy_instances(
    run_log: FakeRunLog,
) -> None:
    api = FakeApi([inst("a"), inst("bad", created=None), inst("c")])
    cleaner = VmCleaner(lambda region: api, lambda: ["r1"], _ENGINE_SETTINGS, engine=True)
    outcome = run_vm("oci", cleaner)
    assert done_ids(outcome) == ["name-a", "name-c"]
    assert [f[1] for f in run_log.failures] == ["bad"]
