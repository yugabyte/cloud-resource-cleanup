"""Focused unit tests of `crc.aws.vpc.VpcCleaner`: `plan()` is read-only, `apply()` mutates."""

import datetime
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from crc.aws.vpc import VpcCleaner
from crc.core.cleaner import Operation, Plan, RunContext
from tests.test_aws_vpc_parity import (
    ENV,
    MUTATING,
    PROD,
    W2,
    Case,
    FakeAws,
    FakeClient,
    FakeResource,
    V,
    conn_error,
    full,
)

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)
TWO_VPCS = 2


def _context(*, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def _cleaner(
    aws: FakeAws,
    *,
    filter_tags: Mapping[str, Sequence[str]] | None = None,
    exception_tags: Mapping[str, Sequence[str]] | None = None,
    notags: Mapping[str, Sequence[str]] | None = None,
) -> VpcCleaner:
    return VpcCleaner(
        client_factory=lambda region: FakeClient(aws, region),
        resource_factory=lambda region: FakeResource(aws, region),
        list_regions=lambda: list(aws.case.regions),
        filter_tags=filter_tags,
        exception_tags=exception_tags,
        notags=notags,
    )


def _aws(regions: Mapping[str, Any], **kwargs: Any) -> FakeAws:
    return FakeAws(Case("unit", regions, **kwargs))


def _mutating(aws: FakeAws) -> list[tuple[Any, ...]]:
    return [c for c in aws.calls if c[0] in MUTATING]


def test_supports_delete_only() -> None:
    cleaner = _cleaner(_aws({}))
    assert cleaner.supports(Operation.DELETE)
    assert not cleaner.supports(Operation.STOP)


def test_plan_makes_no_mutating_call() -> None:
    aws = _aws({W2: [full("vpc-1"), full("vpc-2")]})
    plan = _cleaner(aws).plan(_context())
    assert [c.id for c in plan.candidates] == ["vpc-1", "vpc-2"]
    assert _mutating(aws) == []
    assert {c[0] for c in aws.calls} <= {"vpcs.filter"}


def test_plan_candidates_carry_region_and_unprotected_flag() -> None:
    aws = _aws({W2: [full("vpc-1")]})
    (candidate,) = _cleaner(aws).plan(_context()).candidates
    assert (candidate.id, candidate.name, candidate.region) == ("vpc-1", "vpc-1", W2)
    assert dict(candidate.extra) == {"protected": False}


def test_plan_sends_the_server_side_tag_filter() -> None:
    aws = _aws({W2: []})
    _cleaner(aws, filter_tags={"env": ["a", "b"], "owner": []}).plan(_context())
    (call,) = aws.calls
    assert call == (
        "vpcs.filter",
        W2,
        str(
            [
                {"Name": "tag:env", "Values": ["a", "b"]},
                {"Name": "tag:owner", "Values": []},
            ]
        ),
    )


def test_plan_marks_protected_vpcs_but_still_lists_them() -> None:
    aws = _aws({W2: [full("vpc-1", PROD), full("vpc-2", ENV)]})
    plan = _cleaner(aws, exception_tags={"env": ["prod"]}).plan(_context())
    assert [c.id for c in plan.candidates] == ["vpc-1", "vpc-2"]
    assert [c.extra["protected"] for c in plan.candidates] == [True, False]
    assert [s.id for s in plan.skipped] == ["vpc-1"]


def test_plan_in_dry_run_does_not_evaluate_protection(caplog: pytest.LogCaptureFixture) -> None:
    aws = _aws({W2: [full("vpc-1")]})
    plan = _cleaner(aws, exception_tags={"env": []}).plan(_context(dry_run=True))
    assert [c.id for c in plan.candidates] == ["vpc-1"]
    assert plan.skipped == ()


def test_plan_real_vpc_with_exception_tags_raises_before_any_mutation() -> None:
    aws = _aws({W2: [full("vpc-1")]})
    with pytest.raises(TypeError, match="not iterable"):
        _cleaner(aws, exception_tags={"env": []}).plan(_context())
    assert _mutating(aws) == []


def test_plan_skips_region_on_connectivity_error() -> None:
    aws = _aws({W2: conn_error(), "eu-west-1": [full("vpc-3")]})
    plan = _cleaner(aws).plan(_context())
    assert [(c.id, c.region) for c in plan.candidates] == [("vpc-3", "eu-west-1")]


def test_plan_logs_dry_run_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    _cleaner(_aws({W2: [V("vpc-1")]})).plan(_context(dry_run=True))
    assert [r.getMessage() for r in caplog.records] == [
        "List of AWS VPCs (Total: 1) which will be deleted: ['vpc-1']"
    ]


def test_apply_tears_down_in_order_and_deletes_the_vpc_last() -> None:
    aws = _aws({W2: [full("vpc-1")]})
    cleaner = _cleaner(aws)
    plan = cleaner.plan(_context())
    result = cleaner.apply(plan, _context())
    assert result.succeeded == ("vpc-1",)
    names = [c[0] for c in _mutating(aws)]
    assert names[0] == "associate_with_vpc"
    assert names[-1] == "delete_vpc"
    order = [
        "detach_internet_gateway",
        "gw.delete",
        "rta.delete",
        "rt.delete",
        "instance.terminate",
        "delete_vpc_endpoints",
        "sg.delete",
        "peering.delete",
        "netacl.delete",
        "interface.delete",
        "subnet.delete",
        "delete_vpc",
    ]
    firsts = [names.index(name) for name in order]
    assert firsts == sorted(firsts)


def test_apply_in_dry_run_calls_nothing() -> None:
    aws = _aws({W2: [full("vpc-1")]}, dry_run=True)
    cleaner = _cleaner(aws)
    plan = cleaner.plan(_context(dry_run=True))
    calls_after_plan = list(aws.calls)
    result = cleaner.apply(plan, _context(dry_run=True))
    assert result.succeeded == ("vpc-1",)
    assert aws.calls == calls_after_plan


def test_apply_skips_protected_vpcs_but_reports_them() -> None:
    aws = _aws({W2: [full("vpc-1", PROD), full("vpc-2", ENV)]})
    cleaner = _cleaner(aws, exception_tags={"env": ["prod"]})
    plan = cleaner.plan(_context())
    result = cleaner.apply(plan, _context())
    assert result.succeeded == ("vpc-1", "vpc-2")
    deleted = [c for c in aws.calls if c[0] == "delete_vpc"]
    assert deleted == [("delete_vpc", W2, "vpc-2")]


def test_apply_logs_main_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    aws = _aws({W2: [V("vpc-1"), V("vpc-2")]})
    cleaner = _cleaner(aws)
    cleaner.apply(cleaner.plan(_context()), _context())
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert messages[-2:] == [
        "Number of AWS VPCs deleted: 2",
        "List of AWS VPCs deleted: ['vpc-1', 'vpc-2']",
    ]


def test_apply_connectivity_error_skips_region_and_keeps_ids() -> None:
    aws = _aws(
        {W2: [full("vpc-1")], "eu-west-1": [full("vpc-3")]},
        errors={("sg.delete", W2, "sg-vpc-1-a"): conn_error()},
    )
    cleaner = _cleaner(aws)
    result = cleaner.apply(cleaner.plan(_context()), _context())
    assert result.succeeded == ("vpc-1", "vpc-3")
    assert ("delete_vpc", W2, "vpc-1") not in aws.calls
    assert ("delete_vpc", "eu-west-1", "vpc-3") in aws.calls


def test_apply_empty_plan_logs_zero(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    result = _cleaner(_aws({})).apply(Plan(), _context())
    assert result.succeeded == ()
    assert caplog.records[0].getMessage() == "Number of AWS VPCs deleted: 0"
