"""Cleaner-based copy of the locked safety cases of `tests/test_aws_spot_instance_requests.py`.

The locked file builds the `SpotInstanceRequests` adapter; these tests drive `SpotRequestCleaner`
through `crc.core.engine.run` (or `plan()` for the read-only cases) on the recording fake AWS of
`tests/test_aws_spot_instance_requests_parity.py`. Covered: SAF-007, 008, 011, 026, 027, 028, 029,
030, 031, 032, 033 and 035.
"""

from typing import Any

import pytest

from crc.aws.spot_instance_requests import SpotRequestCleaner, SpotRuntime
from crc.aws.spot_instance_requests.model import (
    LEFTOVER_FROM_TAG,
    LEFTOVER_INVALID_VALUE,
    LEFTOVER_SCOPE_PREFIX,
    SpotSettings,
    Waiter,
)
from crc.aws.spot_instance_requests.rules import scope_tags_for_volume
from crc.core import engine, registry
from crc.core.cleaner import Operation, RunContext
from crc.core.registry import Registry
from crc.core.selection import Selection
from tests.test_aws_spot_instance_requests_cleaner import MUTATING, OPTIONS, _methods, _setup
from tests.test_aws_spot_instance_requests_parity import (
    LEFT,
    NOW,
    QA,
    Case,
    FakeAws,
    Spec,
    ce,
    conn,
    inst,
    one,
    req,
    std,
    swept,
    tagged,
    vol,
)
from tests.test_aws_spot_instance_requests_parity import FakeEc2 as FakeSpotEc2


def mutations(aws: FakeAws) -> list[tuple[str, dict[str, Any]]]:
    return [(c[1], c[2]) for c in aws.calls if c[1] in MUTATING]


MARKER_TAGS = [{"Key": LEFTOVER_FROM_TAG}, {"Key": LEFTOVER_SCOPE_PREFIX + "yb_owner"}]
LEFTOVER_TAGS = [
    {"Key": LEFTOVER_FROM_TAG, "Value": "sir-1"},
    {"Key": LEFTOVER_SCOPE_PREFIX + "yb_owner", "Value": "qa"},
]


def plan_ids(case: Case, monkeypatch: pytest.MonkeyPatch, **options: Any) -> list[str]:
    ctx = RunContext(True, Operation.DELETE, {**OPTIONS, **options}, NOW)
    aws = FakeAws(case)
    monkeypatch.setattr("boto3.client", aws.client)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    cleaner = registry.get("aws", "spot_instance_requests")(ctx)
    cleaner.runtime = _runtime(cleaner, case, aws)  # type: ignore[attr-defined]
    return [c.id for c in cleaner.plan(ctx).candidates]


def _runtime(cleaner: Any, case: Case, aws: FakeAws) -> Any:
    return SpotRuntime(
        client_factory=cleaner.runtime.client_factory,
        list_regions=lambda: list(case.regions),
        waiter=Waiter(aws.sleep, aws.monotonic),
    )


def run_engine(
    case: Case, monkeypatch: pytest.MonkeyPatch, *, dry_run: bool
) -> tuple[FakeAws, engine.RunOutcome]:
    aws = FakeAws(case)
    monkeypatch.setattr("boto3.client", aws.client)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    options = {
        "filter_tags": case.filter_tags,
        "exception_tags": case.exception_tags,
        "notags": case.notags,
        "age": case.age,
        "custom_age_tag_key": case.custom,
    }
    ctx = RunContext(dry_run, Operation.DELETE, options, NOW)
    cleaner = registry.get("aws", "spot_instance_requests")(ctx)
    cleaner.runtime = _runtime(cleaner, case, aws)  # type: ignore[attr-defined]
    local = Registry()
    local.register("aws", "spot_instance_requests")(lambda context: cleaner)
    selection = Selection(items=(("aws", "spot_instance_requests"),))
    return aws, engine.run(selection, ctx, local)[0]


@pytest.mark.parametrize("age", [{}, {"days": 0}, None])
def test_saf_007_011_027_a_positive_age_is_required(age: Any) -> None:
    ctx = RunContext(
        False, Operation.DELETE, {"filter_tags": {"yb_owner": ["qa"]}, "age": age}, NOW
    )
    with pytest.raises(ValueError):
        registry.get("aws", "spot_instance_requests")(ctx)


@pytest.mark.parametrize("bad", ["{'day': 30}", "never", "30d", "{days: 30}", "{'days': 30"])
def test_saf_008_an_invalid_or_unparseable_custom_age_tag_skips_the_request(
    bad: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tags = [*QA, {"Key": "retention", "Value": bad}]
    case = Case("x", one(std(requests=[req(tags=tags)])))
    assert plan_ids(case, monkeypatch, custom_age_tag_key="retention") == []


def test_saf_026_tags_are_matched_on_the_request_not_the_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case(
        "x", one(std(requests=[req(instance="i-untagged")], instances={"i-untagged": inst()}))
    )
    assert plan_ids(case, monkeypatch) == ["sir-1", "i-untagged"]


def test_saf_026_untagged_requests_are_skipped_even_without_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case("x", one(std(requests=[req(tags=[])])))
    assert plan_ids(case, monkeypatch, filter_tags={}) == []


def test_saf_029_a_cancelled_request_with_an_instance_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case("x", one(std(requests=[req(state="cancelled", instance="i-1")])))
    assert plan_ids(case, monkeypatch) == ["sir-1", "i-1"]


def test_saf_029_a_cancelled_request_without_an_instance_is_not_planned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case("x", one(std(requests=[req(state="cancelled", instance=None)])))
    assert plan_ids(case, monkeypatch) == []


def test_saf_028_cancel_comes_before_terminate_and_leftover_volume_is_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aws, outcome = run_engine(Case("x", one(std())), monkeypatch, dry_run=False)
    assert mutations(aws) == [
        ("create_tags", {"Resources": ["vol-1"], "Tags": LEFTOVER_TAGS}),
        ("cancel_spot_instance_requests", {"SpotInstanceRequestIds": ["sir-1"]}),
        ("terminate_instances", {"InstanceIds": ["i-1"]}),
        ("delete_volume", {"VolumeId": "vol-1"}),
    ]
    assert outcome.result is not None
    assert outcome.result.succeeded == ("sir-1", "i-1")
    assert outcome.result is not None
    assert outcome.result.related["Disk"] == ("vol-1",)


def test_saf_028_a_failed_cancel_does_not_terminate_a_persistent_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied = ce("UnauthorizedOperation", "CancelSpotInstanceRequests")
    spec = std(errors={"cancel_spot_instance_requests": denied})
    aws, outcome = run_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    assert "terminate_instances" not in _methods(aws)
    assert outcome.result is not None
    assert outcome.result.succeeded == ()
    assert outcome.result.related["Disk"] == ()


def test_saf_028_a_failed_volume_tag_does_not_terminate(monkeypatch: pytest.MonkeyPatch) -> None:
    denied = ce("UnauthorizedOperation", "CreateTags")
    aws, outcome = run_engine(
        Case("x", one(std(errors={"create_tags": denied}))), monkeypatch, dry_run=False
    )
    methods = _methods(aws)
    assert "terminate_instances" not in methods
    assert "cancel_spot_instance_requests" not in methods
    assert "delete_volume" not in methods
    assert mutations(aws)[1:] == [
        ("delete_tags", {"Resources": ["vol-1"], "Tags": MARKER_TAGS}),
    ]
    assert outcome.result is not None
    assert outcome.result.succeeded == ()


def test_saf_029_an_already_cancelled_request_skips_cancel_then_terminates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case("x", one(std(requests=[req(state="cancelled", instance="i-1")])))
    aws, outcome = run_engine(case, monkeypatch, dry_run=False)
    methods = _methods(aws)
    assert "cancel_spot_instance_requests" not in methods
    assert methods.count("terminate_instances") == 1
    assert ("delete_volume", {"VolumeId": "vol-1"}) in mutations(aws)
    assert outcome.result is not None
    assert "i-1" in outcome.result.succeeded


def test_saf_035_a_cancelled_request_whose_instance_is_gone_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = std(
        requests=[req(state="cancelled", instance="i-1")],
        instances={"i-1": inst(state="terminated", dot_false=())},
        volumes={},
    )
    aws, outcome = run_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    assert not MUTATING & set(_methods(aws))
    assert outcome.result is not None
    assert outcome.result.succeeded == ()


def test_saf_035_retry_already_gone_volume_is_not_recounted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = Spec([req(state="cancelled")], {"i-1": inst("shutting-down", ("vol-1",))}, {})
    aws, outcome = run_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    assert "delete_volume" not in _methods(aws)
    assert outcome.result is not None
    assert outcome.result.succeeded == ()
    assert outcome.result.related["Disk"] == ()


def test_saf_030_scope_tags_overwrite_existing_prefix_keys() -> None:
    settings = SpotSettings({"yb_owner": ["qa"]}, {}, {}, {"days": 1}, None)
    tags1 = scope_tags_for_volume(settings, "sir-1", [{"Key": "yb_owner", "Value": "qa"}])
    assert {"Key": LEFTOVER_SCOPE_PREFIX + "yb_owner", "Value": "qa"} in tags1
    tags2 = scope_tags_for_volume(settings, "sir-1", [{"Key": "yb_owner", "Value": "qa-new"}])
    assert {"Key": LEFTOVER_SCOPE_PREFIX + "yb_owner", "Value": "qa-new"} in tags2


def test_saf_030_tags_bdm_volumes_when_instance_is_shutting_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = Spec(
        [req(state="cancelled", rtype="one-time")],
        {"i-1": inst("shutting-down", ("vol-1",))},
        {"vol-1": vol("available", attached=None)},
    )
    aws, outcome = run_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    methods = _methods(aws)
    assert methods.count("create_tags") == 1
    assert "terminate_instances" not in methods
    assert ("delete_volume", {"VolumeId": "vol-1"}) in mutations(aws)
    assert outcome.result is not None
    assert outcome.result.related["Disk"] == ("vol-1",)


def test_saf_030_retry_uses_tagged_leftover_volumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = Spec(
        [req(state="cancelled", rtype="one-time")],
        {"i-1": inst("terminated", ())},
        {"vol-1": vol("available", tagged("sir-1"), attached=None)},
    )
    aws, outcome = run_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    assert "terminate_instances" not in _methods(aws)
    assert "delete_volume" in _methods(aws)
    assert outcome.result is not None
    assert outcome.result.succeeded == ("sir-1",)
    assert outcome.result.related["Disk"] == ("vol-1",)


def test_saf_031_volume_exception_tag_skips_leftover_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = std(volumes={"vol-1": vol(tags=[{"Key": "autoclean", "Value": "false"}])})
    case = Case("x", one(spec), exception_tags={"autoclean": ["false"]})
    aws, outcome = run_engine(case, monkeypatch, dry_run=False)
    assert "delete_volume" not in _methods(aws)
    assert outcome.result is not None
    assert outcome.result.related["Disk"] == ()


def test_saf_031_volume_sensitive_yb_task_skips_leftover_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = std(volumes={"vol-1": vol(tags=[{"Key": "yb_task", "Value": "infrastructure"}])})
    aws, outcome = run_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    assert "delete_volume" not in _methods(aws)
    assert outcome.result is not None
    assert outcome.result.related["Disk"] == ()


def test_saf_031_sweep_skips_volume_own_exception_on_live_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tags = [{"Key": LEFTOVER_FROM_TAG, "Value": "sir-live"}, {"Key": "keep", "Value": "true"}]
    spec = swept("sir-live", tags=tags, requests=[req("sir-live", None)])
    case = Case("x", one(spec), exception_tags={"keep": ["true"]})
    aws, _ = run_engine(case, monkeypatch, dry_run=False)
    assert "delete_volume" not in _methods(aws)


def test_saf_032_terminate_failure_clears_leftover_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = std(errors={"terminate_instances": ce("OperationNotPermitted", "TerminateInstances")})
    aws, _ = run_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    methods = _methods(aws)
    assert methods.count("create_tags") == 1
    assert methods.count("delete_tags") == 1
    assert ("delete_tags", {"Resources": ["vol-1"], "Tags": MARKER_TAGS}) in mutations(aws)
    assert "delete_volume" not in methods


def test_saf_032_cancel_botocore_error_invalidates_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = std(errors={"cancel_spot_instance_requests": conn()})
    aws, _ = run_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    methods = _methods(aws)
    assert "terminate_instances" not in methods
    assert methods.count("delete_tags") == 1
    assert ("delete_tags", {"Resources": ["vol-1"], "Tags": MARKER_TAGS}) in mutations(aws)


def test_saf_032_terminate_failure_writes_sentinel_when_delete_tags_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = std(
        errors={
            "terminate_instances": ce("OperationNotPermitted", "TerminateInstances"),
            "delete_tags": ce("UnauthorizedOperation", "DeleteTags"),
        }
    )
    aws, _ = run_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    create_calls = [c for c in aws.calls if c[1] == "create_tags"]
    assert len(create_calls) == 1 + 1 + 1
    assert create_calls[1][2]["Tags"] == [
        {"Key": LEFTOVER_SCOPE_PREFIX + "yb_owner", "Value": LEFTOVER_INVALID_VALUE}
    ]
    assert create_calls[-1][2]["Tags"] == [
        {"Key": LEFTOVER_FROM_TAG, "Value": LEFTOVER_INVALID_VALUE}
    ]


def test_saf_033_botocore_terminate_error_invalidates_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = std(errors={"terminate_instances": conn()})
    aws, _ = run_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    methods = _methods(aws)
    assert methods.count("delete_tags") == 1
    assert "delete_volume" not in methods


def test_saf_033_botocore_terminate_error_keeps_marker_if_shutting_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = std(
        volumes={"vol-1": vol("available")},
        errors={"terminate_instances": conn()},
        terminate_error_state="shutting-down",
    )
    aws, _ = run_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    methods = _methods(aws)
    assert "delete_tags" not in methods
    assert methods.count("delete_volume") == 1
    assert ("delete_volume", {"VolumeId": "vol-1"}) in mutations(aws)


def test_saf_033_botocore_terminate_unknown_state_invalidates_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = std(errors={"terminate_instances": conn()})
    aws, cleaner, ctx = _setup(Case("x", one(spec)), monkeypatch, False)
    plan = cleaner.plan(ctx)
    aws.case.regions["us-west-2"].errors["describe_instances"] = ce(
        "UnauthorizedOperation", "DescribeInstances"
    )
    # First describe_instances in apply_region succeeds, second (after terminate error) fails:
    calls = 0
    real_client = cleaner.runtime.client_factory("us-west-2")
    real_describe = real_client.describe_instances

    def describe_twice(*, InstanceIds: list[str]) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            aws.case.regions["us-west-2"].errors.pop("describe_instances", None)
            out = real_describe(InstanceIds=InstanceIds)
            aws.case.regions["us-west-2"].errors["describe_instances"] = ce(
                "UnauthorizedOperation", "DescribeInstances"
            )
            return out
        return real_describe(InstanceIds=InstanceIds)

    monkeypatch.setattr(real_client, "describe_instances", describe_twice)
    cleaner._clients["us-west-2"] = real_client
    cleaner.apply(plan, ctx)
    methods = _methods(aws)
    assert methods.count("delete_tags") == 1
    assert "delete_volume" not in methods


def test_the_registered_cleaner_is_the_spot_request_cleaner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, cleaner, _ = _setup(Case("x", one(std())), monkeypatch, True)
    assert isinstance(cleaner, SpotRequestCleaner)


def test_persistent_and_one_time_requests_are_both_planned(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = [req("sir-p", "i-p", "persistent"), req("sir-o", "i-o", "one-time")]
    instances = {"i-p": inst(dot_false=()), "i-o": inst(dot_false=())}
    case = Case("x", one(Spec(requests, instances, {})))
    assert plan_ids(case, monkeypatch) == ["sir-p", "sir-o", "i-p", "i-o"]


def test_young_requests_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    case = Case("x", one(std(requests=[req(days=2)])), age={"days": 10})
    assert plan_ids(case, monkeypatch, age={"days": 10}) == []


def test_a_failed_describe_instances_aborts_before_cancel_or_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied = ce("UnauthorizedOperation", "DescribeInstances")
    aws, _ = run_engine(
        Case("x", one(std(errors={"describe_instances": denied}))), monkeypatch, dry_run=False
    )
    assert mutations(aws) == []


def test_a_create_tags_timeout_invalidates_both_volumes(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = std(
        instances={"i-1": inst(dot_false=("vol-a", "vol-b"))},
        volumes={"vol-a": vol(), "vol-b": vol()},
    )
    real_create = FakeSpotEc2.create_tags

    def flaky(self: FakeSpotEc2, *, Resources: list[str], Tags: list[dict[str, str]]) -> Any:
        if Resources == ["vol-b"]:
            self._rec("create_tags", Resources=Resources, Tags=Tags)
            raise conn()
        return real_create(self, Resources=Resources, Tags=Tags)

    monkeypatch.setattr(FakeSpotEc2, "create_tags", flaky)
    aws, _ = run_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    cleared = {c[2]["Resources"][0] for c in aws.calls if c[1] == "delete_tags"}
    assert cleared == {"vol-a", "vol-b"}
    assert "cancel_spot_instance_requests" not in _methods(aws)


def test_a_dry_run_lists_the_leftover_volume_without_mutating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aws, outcome = run_engine(Case("x", one(std())), monkeypatch, dry_run=True)
    assert mutations(aws) == []
    assert outcome.plan.related["Disk"] == ("vol-1",)


def test_filter_tags_honour_globs(monkeypatch: pytest.MonkeyPatch) -> None:
    tags = [*QA, {"Key": "yb_task", "Value": "itest-123"}]
    case = Case("x", one(std(requests=[req(tags=tags)])))
    assert plan_ids(case, monkeypatch, filter_tags={"yb_task": ["itest*"]}) == ["sir-1", "i-1"]


def test_the_sweep_rejects_the_terminate_failed_sentinel(monkeypatch: pytest.MonkeyPatch) -> None:
    case = Case("x", one(swept(tags=[{"Key": LEFT, "Value": LEFTOVER_INVALID_VALUE}])))
    aws, _ = run_engine(case, monkeypatch, dry_run=False)
    assert mutations(aws) == []
    lookups = [c for c in aws.calls if c[1] == "describe_spot_instance_requests"]
    assert [c[2].get("SpotInstanceRequestIds") for c in lookups] == [None]


def test_the_sweep_skips_another_owner_and_in_use_volumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = [{"Key": LEFT, "Value": "sir-x"}, {"Key": "yb_owner", "Value": "prod"}]
    spec = Spec(
        [],
        volumes={
            "vol-other": vol("available", other, 7, None),
            "vol-busy": vol("in-use", tagged("sir-gone"), 10, "i-9"),
        },
    )
    aws, _ = run_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    assert mutations(aws) == []


def test_the_sweep_skips_a_live_untagged_request(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = swept("sir-live", requests=[req("sir-live", None, tags=[])])
    aws, _ = run_engine(Case("x", one(spec), filter_tags={}), monkeypatch, dry_run=False)
    assert "delete_volume" not in _methods(aws)


def test_the_sweep_skips_a_young_live_request(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = swept("sir-live", requests=[req("sir-live", None, days=2)])
    case = Case("x", one(spec), age={"days": 10})
    aws, _ = run_engine(case, monkeypatch, dry_run=False)
    assert "delete_volume" not in _methods(aws)


def test_the_sweep_deletes_a_tagged_orphan_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    aws, outcome = run_engine(Case("x", one(swept())), monkeypatch, dry_run=False)
    assert ("delete_volume", {"VolumeId": "vol-s"}) in mutations(aws)
    assert outcome.result is not None
    assert outcome.result.related["Disk"] == ("vol-s",)
