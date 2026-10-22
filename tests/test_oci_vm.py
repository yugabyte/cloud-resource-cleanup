"""Unit tests for `crc.oci.vm`: `VmCleaner.plan()` is read-only, `apply()` mutates."""

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
from crc.oci.vm import VM, VmCleaner, VmRuntime, VmSettings, build_vm_cleaner
from crc.oci.vm.client import ComputeApi, OciComputeApi, SdkInstance

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
FILTER = {"env": ["ci"]}
MUTATING = ("terminate_instance", "stop_instance")


def inst(
    inst_id: str,
    hours: float = 100,
    state: str = "RUNNING",
    tags: Mapping[str, str] | None = None,
) -> Any:
    return types.SimpleNamespace(
        id=inst_id,
        display_name=f"name-{inst_id}",
        lifecycle_state=state,
        freeform_tags={"env": "ci"} if tags is None else tags,
        time_created=NOW - datetime.timedelta(hours=hours),
    )


class FakeApi:
    def __init__(
        self,
        instances: Sequence[Any] = (),
        failing: Mapping[str, Exception] | None = None,
    ) -> None:
        self.instances = list(instances)
        self.failing = dict(failing or {})
        self.calls: list[tuple[str, ...]] = []

    def list_instances(self) -> Sequence[SdkInstance]:
        self.calls.append(("list_instances",))
        return self.instances

    def terminate_instance(self, instance_id: str) -> None:
        self.calls.append(("terminate_instance", instance_id))
        if instance_id in self.failing:
            raise self.failing[instance_id]

    def stop_instance(self, instance_id: str) -> None:
        self.calls.append(("stop_instance", instance_id))
        if instance_id in self.failing:
            raise self.failing[instance_id]


def context(
    *, dry_run: bool = False, operation: Operation = Operation.DELETE, now: Any = NOW
) -> RunContext:
    return RunContext(dry_run=dry_run, operation=operation, options={}, now=now)


def cleaner(api: ComputeApi, settings: VmSettings | None = None) -> VmCleaner:
    chosen = settings or VmSettings(filter_tags=FILTER, age={"hours": 5})
    return VmCleaner(lambda region: api, lambda: ["r1"], chosen)


def test_plan_makes_no_mutating_call() -> None:
    api = FakeApi(instances=[inst("a"), inst("b")])
    plan = cleaner(api).plan(context())
    assert [c.id for c in plan.candidates] == ["a", "b"]
    assert [call for call in api.calls if call[0] in MUTATING] == []
    assert api.calls == [("list_instances",)]


@pytest.mark.parametrize("operation", [Operation.DELETE, Operation.STOP])
def test_plan_for_either_operation_makes_no_mutating_call(operation: Operation) -> None:
    api = FakeApi(instances=[inst("a")])
    plan = cleaner(api).plan(context(operation=operation, dry_run=True))
    assert [c.id for c in plan.candidates] == ["a"]
    assert [call for call in api.calls if call[0] in MUTATING] == []


def test_plan_candidates_carry_name_region_state_and_tags() -> None:
    plan = cleaner(FakeApi(instances=[inst("a")])).plan(context())
    (first,) = plan.candidates
    assert (first.id, first.name, first.region, first.state) == ("a", "name-a", "r1", "RUNNING")
    assert first.launch_time == NOW - datetime.timedelta(hours=100)
    assert dict(first.tags) == {"env": "ci"}


@pytest.mark.parametrize(
    ("instance", "reason"),
    [
        (inst("a", state="STOPPED"), "state is not allowed"),
        (inst("a", tags={"env": "dev"}), "does not match filter_tags"),
        (inst("a", hours=1), "not older than age"),
    ],
)
def test_plan_records_why_an_instance_is_kept(instance: Any, reason: str) -> None:
    plan = cleaner(FakeApi(instances=[instance])).plan(context())
    assert plan.candidates == ()
    assert [s.reason for s in plan.skipped] == [reason]


def test_plan_filter_needs_every_key() -> None:
    settings = VmSettings(filter_tags={"x": ["1"], "y": ["2"]})
    instances = [
        inst("a", tags={"x": "1", "y": "2"}),
        inst("b", tags={"x": "1"}),
        inst("c", tags={"y": "2"}),
    ]
    plan = cleaner(FakeApi(instances=instances), settings).plan(context())
    assert [c.id for c in plan.candidates] == ["a"]


def test_plan_applies_exception_tags_and_notags() -> None:
    settings = VmSettings(
        filter_tags={}, exception_tags={"keep": ["1"]}, notags={"x": [], "y": []}, age={"hours": 5}
    )
    instances = [
        inst("a", tags={"keep": "1"}),
        inst("b", tags={"x": "1", "y": "1"}),
        inst("c", tags={"x": "1"}),
    ]
    plan = cleaner(FakeApi(instances=instances), settings).plan(context())
    assert [c.id for c in plan.candidates] == ["c"]


def test_plan_uses_custom_age_tag() -> None:
    settings = VmSettings(filter_tags=FILTER, age={"days": 30}, custom_age_tag_key="ttl")
    keep = inst("a", hours=3, tags={"env": "ci"})
    drop = inst("b", hours=3, tags={"env": "ci", "ttl": "{'hours': 2}"})
    plan = cleaner(FakeApi(instances=[keep, drop]), settings).plan(context())
    assert [c.id for c in plan.candidates] == ["b"]


def test_plan_honours_the_allowed_states() -> None:
    settings = VmSettings(filter_tags=FILTER, instance_states=("STOPPED",))
    api = FakeApi(instances=[inst("a"), inst("b", state="STOPPED")])
    plan = cleaner(api, settings).plan(context())
    assert [c.id for c in plan.candidates] == ["b"]


def test_plan_reads_the_clock_from_the_context() -> None:
    later = NOW + datetime.timedelta(hours=10)
    settings = VmSettings(filter_tags=FILTER, age={"hours": 5})
    api = FakeApi(instances=[inst("a", hours=-3)])
    assert cleaner(api, settings).plan(context()).candidates == ()
    assert [c.id for c in cleaner(api, settings).plan(context(now=later)).candidates] == ["a"]


def test_plan_skips_region_on_connectivity_error(caplog: pytest.LogCaptureFixture) -> None:
    class Unreachable(FakeApi):
        def list_instances(self) -> Sequence[SdkInstance]:
            raise ConnectTimeout(Exception("timed out"))

    plan = cleaner(Unreachable()).plan(context())
    assert plan == Plan()
    assert "Region r1: skipped list_instances vm delete" in caplog.text


def test_plan_in_dry_run_logs_the_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    cleaner(FakeApi(instances=[inst("a")])).plan(context(dry_run=True))
    assert "List of OCI instances (Total: 1) which will be deleted: ['name-a']" in caplog.text


def test_plan_in_dry_run_for_stop_logs_the_stop_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    cleaner(FakeApi(instances=[inst("a")])).plan(context(dry_run=True, operation=Operation.STOP))
    assert "List of OCI instances (Total: 1) which will be stopped: ['name-a']" in caplog.text


def test_plan_outside_dry_run_does_not_log_the_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    cleaner(FakeApi(instances=[inst("a")])).plan(context())
    assert "OCI instances" not in caplog.text


def test_apply_terminates_instances_in_plan_order() -> None:
    api = FakeApi(instances=[inst("a"), inst("b")])
    subject = cleaner(api)
    plan = subject.plan(context())
    api.calls.clear()
    result = subject.apply(plan, context())
    assert api.calls == [("terminate_instance", "a"), ("terminate_instance", "b")]
    assert result.succeeded == ("name-a", "name-b")
    assert result.failed == ()


def test_apply_stops_instances_for_the_stop_operation() -> None:
    api = FakeApi(instances=[inst("a")])
    subject = cleaner(api)
    stop = context(operation=Operation.STOP)
    plan = subject.plan(stop)
    api.calls.clear()
    result = subject.apply(plan, stop)
    assert api.calls == [("stop_instance", "a")]
    assert result.succeeded == ("name-a",)


def test_apply_logs_a_failed_call_and_continues(caplog: pytest.LogCaptureFixture) -> None:
    api = FakeApi(instances=[inst("a"), inst("b")], failing={"a": RuntimeError("busy")})
    subject = cleaner(api)
    result = subject.apply(subject.plan(context()), context())
    assert result.succeeded == ("name-b",)
    assert [(f.id, f.error) for f in result.failed] == [("name-a", "busy")]
    assert "Error occurred while delete instance name-a: busy" in caplog.text


def test_apply_in_dry_run_calls_nothing() -> None:
    api = FakeApi(instances=[inst("a")])
    subject = cleaner(api)
    dry = context(dry_run=True)
    result = subject.apply(subject.plan(dry), dry)
    assert [call for call in api.calls if call[0] in MUTATING] == []
    assert result.succeeded == ("name-a",)


def test_apply_logs_the_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    subject = cleaner(FakeApi())
    subject.apply(subject.plan(context()), context())
    assert [r.getMessage() for r in caplog.records] == [
        "No OCI instances to delete.",
        "List of OCI instances deleted: []",
    ]


@pytest.mark.parametrize("operation", [Operation.DELETE, Operation.STOP])
def test_supports_delete_and_stop(operation: Operation) -> None:
    assert cleaner(FakeApi()).supports(operation)


def test_cleaner_is_registered_for_oci_vm() -> None:
    assert DEFAULT_REGISTRY.get("oci", "vm") is build_vm_cleaner


def test_factory_builds_a_vm_cleaner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCI_PRIVATE_KEY_CONTENT", "not-a-key")
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    built = build_vm_cleaner(context())
    assert (built.cloud, built.kind) == ("oci", "vm")


def test_factory_reads_the_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCI_PRIVATE_KEY_CONTENT", "not-a-key")
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    options: dict[str, Any] = {
        "filter_tags": {"env": ["ci"]},
        "exception_tags": {"keep": []},
        "notags": {"x": []},
        "age": {"hours": 5},
        "custom_age_tag_key": "ttl",
        "instance_state": ["STOPPED"],
    }
    built = build_vm_cleaner(
        RunContext(dry_run=True, operation=Operation.DELETE, options=options, now=NOW)
    )
    assert isinstance(built, VmCleaner)
    assert built._settings == VmSettings(
        filter_tags={"env": ["ci"]},
        exception_tags={"keep": []},
        notags={"x": []},
        age={"hours": 5},
        custom_age_tag_key="ttl",
        instance_states=["STOPPED"],
    )
    stop_built = build_vm_cleaner(
        RunContext(dry_run=True, operation=Operation.STOP, options=options, now=NOW)
    )
    assert isinstance(stop_built, VmCleaner)
    assert stop_built._settings.instance_states == ("RUNNING",)


def test_adapter_runs_the_cleaner_with_injected_api(oci_env: None) -> None:
    api = FakeApi(instances=[inst("a"), inst("b", hours=1)])
    runtime = VmRuntime(
        api_factory=lambda region: api, list_regions=lambda: ["r1"], clock=lambda: NOW
    )
    vm = _vm(runtime)
    vm.delete()
    assert vm.get_deleted == ["name-a"]
    assert vm.get_stopped == []
    assert vm.delete_count == 1
    assert api.calls == [("list_instances",), ("terminate_instance", "a")]


def test_adapter_stop_fills_the_stop_list(oci_env: None) -> None:
    api = FakeApi(instances=[inst("a")])
    runtime = VmRuntime(
        api_factory=lambda region: api, list_regions=lambda: ["r1"], clock=lambda: NOW
    )
    vm = _vm(runtime)
    vm.stop()
    assert vm.get_stopped == ["name-a"]
    assert vm.get_deleted == []
    assert vm.stopped_count == 1
    assert api.calls[-1] == ("stop_instance", "a")


@pytest.fixture
def oci_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCI_PRIVATE_KEY_CONTENT", "not-a-key")
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)


def _vm(runtime: VmRuntime) -> VM:
    return VM(False, FILTER, {}, {"hours": 5}, "", {}, runtime)


def test_sdk_api_pages_through_every_result_page() -> None:
    pages = {None: (["a"], "p2"), "p2": (["b"], None)}

    class Compute:
        def list_instances(self, compartment_id: str, page: str | None = None) -> Any:
            data, token = pages[page]
            headers = {} if token is None else {"opc-next-page": token}
            return oci.response.Response(200, headers, data, None)

    api = OciComputeApi(Compute(), "comp")
    listed: Any = api.list_instances()
    assert listed == ["a", "b"]


def test_sdk_api_maps_terminate_and_stop_to_the_compute_calls() -> None:
    seen: list[tuple[Any, ...]] = []

    class Compute:
        def terminate_instance(self, instance_id: str) -> None:
            seen.append(("terminate_instance", instance_id))

        def instance_action(self, instance_id: str, action: str) -> None:
            seen.append(("instance_action", instance_id, action))

    api = OciComputeApi(Compute(), "comp")
    api.terminate_instance("a")
    api.stop_instance("b")
    assert seen == [("terminate_instance", "a"), ("instance_action", "b", "STOP")]
