"""Focused tests of the spot instance request Cleaner: plan() is read-only, apply() mutates."""

import logging

import pytest

from crc.aws.spot_instance_requests import SpotRequestCleaner, SpotRuntime
from crc.aws.spot_instance_requests.model import Waiter
from crc.core import policy, registry
from crc.core.cleaner import Operation, Plan, RunContext
from crc.core.report import DISK, SPOT_INSTANCE_REQUEST, VM, Report
from tests.test_aws_spot_instance_requests_parity import (
    NOW,
    Case,
    FakeAws,
    one,
    req,
    std,
    swept,
)

MUTATING = {
    "cancel_spot_instance_requests",
    "terminate_instances",
    "create_tags",
    "delete_tags",
    "delete_volume",
}
OPTIONS = {"filter_tags": {"yb_owner": ["qa"]}, "age": {"days": 1}}


def _setup(
    case: Case, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> tuple[FakeAws, SpotRequestCleaner, RunContext]:
    aws = FakeAws(case)
    monkeypatch.setattr("boto3.client", aws.client)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    ctx = RunContext(dry_run, Operation.DELETE, OPTIONS, NOW)
    cleaner = registry.get("aws", "spot_instance_requests")(ctx)
    assert isinstance(cleaner, SpotRequestCleaner)
    cleaner.runtime = SpotRuntime(
        client_factory=cleaner.runtime.client_factory,
        list_regions=lambda: list(case.regions),
        waiter=Waiter(aws.sleep, aws.monotonic),
    )
    return aws, cleaner, ctx


def _methods(aws: FakeAws) -> list[str]:
    return [call[1] for call in aws.calls]


@pytest.mark.parametrize("dry_run", [False, True])
def test_plan_makes_no_mutating_call(monkeypatch: pytest.MonkeyPatch, dry_run: bool) -> None:
    case = Case("x", one(std(requests=[req(), req("sir-2", None)])))
    aws, cleaner, ctx = _setup(case, monkeypatch, dry_run)
    cleaner.plan(ctx)
    assert not MUTATING & set(_methods(aws))


def test_plan_lists_candidates_and_regions(monkeypatch: pytest.MonkeyPatch) -> None:
    case = Case("x", one(std(requests=[req(), req(days=0.1, rid="young")])))
    _, cleaner, ctx = _setup(case, monkeypatch, False)
    plan = cleaner.plan(ctx)
    assert [c.id for c in plan.candidates] == ["sir-1"]
    assert plan.candidates[0].region == "us-west-2"
    assert plan.related == {}


def test_apply_runs_in_safety_order(monkeypatch: pytest.MonkeyPatch) -> None:
    aws, cleaner, ctx = _setup(Case("x", one(std())), monkeypatch, False)
    result = cleaner.apply(cleaner.plan(ctx), ctx)
    order = [m for m in _methods(aws) if m in MUTATING]
    assert order == [
        "create_tags",
        "cancel_spot_instance_requests",
        "terminate_instances",
        "delete_volume",
    ]
    assert result.succeeded == ("sir-1", "i-1")
    assert result.related == {DISK: ("vol-1",)}
    report = Report.from_result("aws", SPOT_INSTANCE_REQUEST, result, Operation.DELETE, False)
    assert report.influx_payload() == {VM: ["sir-1", "i-1"], DISK: ["vol-1"]}


def test_dry_run_plan_reports_and_apply_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    case = Case("x", one(std()), dry_run=True)
    aws, cleaner, ctx = _setup(case, monkeypatch, True)
    plan = cleaner.plan(ctx)
    assert [c.id for c in plan.candidates] == ["sir-1", "i-1"]
    assert plan.related == {DISK: ("vol-1",)}
    report = Report.from_plan("aws", SPOT_INSTANCE_REQUEST, plan, Operation.DELETE, True)
    assert report.influx_payload() == {VM: ["sir-1", "i-1"], DISK: ["vol-1"]}
    calls = len(aws.calls)
    result = cleaner.apply(plan, ctx)
    assert len(aws.calls) == calls
    assert result.succeeded == ("sir-1", "i-1")
    assert result.related == {DISK: ("vol-1",)}


def test_apply_sweeps_orphan_volumes(monkeypatch: pytest.MonkeyPatch) -> None:
    aws, cleaner, ctx = _setup(Case("x", one(swept())), monkeypatch, False)
    result = cleaner.apply(cleaner.plan(ctx), ctx)
    assert result.related[DISK] == ("vol-s",)
    assert "delete_volume" in _methods(aws)


def test_region_listing_failure_ends_the_run_quietly(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _, cleaner, ctx = _setup(Case("x", one(std())), monkeypatch, False)

    def broken() -> list[str]:
        raise RuntimeError("denied")

    cleaner.runtime = SpotRuntime(list_regions=broken)
    caplog.set_level(logging.INFO)
    plan = cleaner.plan(ctx)
    assert plan == Plan()
    cleaner.apply(plan, ctx)
    assert "Failed to list AWS regions for EC2: denied" in caplog.text
    assert "List of AWS SpotInstanceRequest" not in caplog.text


def test_factory_requires_a_positive_age() -> None:
    ctx = RunContext(False, Operation.DELETE, {"age": {"days": 0}}, NOW)
    with pytest.raises(ValueError, match="age"):
        registry.get("aws", "spot_instance_requests")(ctx)


def test_supports_delete_only() -> None:
    ctx = RunContext(False, Operation.DELETE, OPTIONS, NOW)
    cleaner = registry.get("aws", "spot_instance_requests")(ctx)
    assert cleaner.supports(Operation.DELETE)
    assert not cleaner.supports(Operation.STOP)


def test_cleaner_keeps_settings_and_runtime_attributes() -> None:
    ctx = RunContext(True, Operation.DELETE, {"filter_tags": {"a": ["b"]}, "age": {"days": 1}}, NOW)
    cleaner = registry.get("aws", "spot_instance_requests")(ctx)
    assert isinstance(cleaner, SpotRequestCleaner)
    assert cleaner.settings.age == {"days": 1}
    assert cleaner.settings.filter_tags == {"a": ["b"]}
    assert "prod" in policy.SENSITIVE_YB_TASK
