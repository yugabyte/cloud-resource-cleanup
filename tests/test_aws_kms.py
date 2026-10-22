"""Unit tests for `crc.aws.kms`: read-only `plan()`, mutating `apply()`, adapter."""

import datetime
import json
from collections.abc import Iterable, Mapping
from typing import Any

import pytest
from botocore.exceptions import EndpointConnectionError

from crc.aws.kms import Kms, KmsCleaner, KmsRuntime, KmsSettings
from crc.core.cleaner import Operation, Plan, RunContext, Skipped

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
OLD = NOW - datetime.timedelta(days=30)
YOUNG = NOW - datetime.timedelta(hours=1)
USER = "arn:aws:iam::1:user/jenkins"
WINDOW = 9
MUTATING = {"schedule_key_deletion"}


class FakePaginator:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys

    def paginate(self) -> Iterable[Mapping[str, Any]]:
        return [{"Keys": [{"KeyId": k} for k in self.keys]}]


class FakeKms:
    def __init__(self, keys: dict[str, datetime.datetime], calls: list[tuple[str, ...]]) -> None:
        self.keys = keys
        self.calls = calls

    def get_paginator(self, operation_name: str) -> FakePaginator:
        self.calls.append(("get_paginator", operation_name))
        return FakePaginator(list(self.keys))

    def list_resource_tags(self, *, KeyId: str) -> Mapping[str, Any]:
        self.calls.append(("list_resource_tags", KeyId))
        return {"Tags": [{"TagKey": "team", "TagValue": "qa"}]}

    def describe_key(self, *, KeyId: str) -> Mapping[str, Any]:
        self.calls.append(("describe_key", KeyId))
        meta = {"KeyState": "Enabled", "Description": "ci key", "CreationDate": self.keys[KeyId]}
        return {"KeyMetadata": meta}

    def get_key_policy(self, *, KeyId: str, PolicyName: str) -> Mapping[str, Any]:
        self.calls.append(("get_key_policy", KeyId, PolicyName))
        return {"Policy": json.dumps({"Statement": [{"Principal": {"AWS": USER}}]})}

    def schedule_key_deletion(self, *, KeyId: str, PendingWindowInDays: int) -> Mapping[str, Any]:
        self.calls.append(("schedule_key_deletion", KeyId, str(PendingWindowInDays)))
        return {}


class Aws:
    def __init__(self, keys: dict[str, datetime.datetime]) -> None:
        self.keys = keys
        self.calls: list[tuple[str, ...]] = []
        self.error: Exception | None = None

    def client(self, region: str) -> FakeKms:
        self.calls.append(("client", region))
        if self.error is not None:
            raise self.error
        return FakeKms(self.keys, self.calls)


def context(*, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def cleaner(aws: Aws, **overrides: Any) -> KmsCleaner:
    params: dict[str, Any] = {
        "kms_key_description": "ci",
        "kms_user": USER,
        "kms_pending_window": WINDOW,
        "age": {"days": 1},
    }
    params.update(overrides)
    return KmsCleaner(aws.client, KmsSettings(**params))


def mutating(calls: list[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [c for c in calls if c[0] in MUTATING]


def test_plan_makes_no_mutating_call() -> None:
    aws = Aws({"old": OLD, "young": YOUNG})
    plan = cleaner(aws).plan(context())
    assert [c.id for c in plan.candidates] == ["old"]
    assert mutating(aws.calls) == []
    assert aws.calls[:2] == [("client", "us-west-2"), ("get_paginator", "list_keys")]


def test_plan_in_dry_run_logs_summary_and_does_not_mutate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    aws = Aws({"old": OLD})
    caplog.set_level("INFO")
    cleaner(aws).plan(context(dry_run=True))
    assert mutating(aws.calls) == []
    assert "List of AWS KMS (Total: 1) which will be deleted: ['old']" in caplog.messages


def test_plan_filters_by_pairs() -> None:
    aws = Aws({"old": OLD})
    assert cleaner(aws, filter_tags={"team": ["qa"]}).plan(context()).candidates
    assert not cleaner(aws, filter_tags={"team": ["dev"]}).plan(context()).candidates
    assert not cleaner(aws, filter_tags={"team": ["qa", "dev"]}).plan(context()).candidates


def test_plan_exception_tags_skip_the_key_through_keyerror() -> None:
    aws = Aws({"old": OLD})
    assert not cleaner(aws, exception_tags={"team": []}).plan(context()).candidates


def test_plan_unreachable_region_is_marked_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    aws = Aws({"old": OLD})
    aws.error = EndpointConnectionError(endpoint_url="https://kms.example")
    plan = cleaner(aws).plan(context())
    assert plan.candidates == ()
    assert [s.id for s in plan.skipped] == ["us-west-2"]
    assert "KMS cleanup skipped" in caplog.text


def test_apply_schedules_in_plan_order() -> None:
    aws = Aws({"a": OLD, "b": OLD})
    target = cleaner(aws)
    plan = target.plan(context())
    aws.calls.clear()
    result = target.apply(plan, context())
    assert result.succeeded == ("a", "b")
    assert mutating(aws.calls) == [
        ("schedule_key_deletion", "a", str(WINDOW)),
        ("schedule_key_deletion", "b", str(WINDOW)),
    ]


def test_apply_in_dry_run_does_not_mutate(caplog: pytest.LogCaptureFixture) -> None:
    aws = Aws({"a": OLD})
    target = cleaner(aws)
    plan = target.plan(context(dry_run=True))
    aws.calls.clear()
    result = target.apply(plan, context(dry_run=True))
    assert result.succeeded == ("a",)
    assert aws.calls == []


def test_apply_of_unreachable_plan_does_nothing(caplog: pytest.LogCaptureFixture) -> None:
    aws = Aws({})
    caplog.set_level("INFO")
    plan = Plan(skipped=(Skipped(id="us-west-2", reason="region unreachable"),))
    assert cleaner(aws).apply(plan, context()).succeeded == ()
    assert aws.calls == []
    assert caplog.messages == []


def test_apply_acts_only_on_the_given_plan() -> None:
    aws = Aws({"a": OLD, "b": OLD})
    assert cleaner(aws).apply(Plan(), context()).succeeded == ()
    assert aws.calls == []


def test_supports_only_delete() -> None:
    target = cleaner(Aws({}))
    assert target.supports(Operation.DELETE)
    assert not target.supports(Operation.STOP)


def test_adapter_accumulates_and_reports() -> None:
    aws = Aws({"a": OLD})
    runtime = KmsRuntime(client_factory=aws.client, clock=lambda: NOW)
    kms = Kms(False, {}, {}, "ci", USER, WINDOW, {"days": 1}, "", runtime)
    kms.delete()
    kms.delete()
    assert kms.get_deleted == ["a", "a"]
    assert kms.kms_keys_to_delete == ["a", "a"]
    assert len(mutating(aws.calls)) == 2 + 0


def test_adapter_dry_run_never_schedules() -> None:
    aws = Aws({"a": OLD})
    runtime = KmsRuntime(client_factory=aws.client, clock=lambda: NOW)
    kms = Kms(True, {}, {}, "ci", USER, WINDOW, {"days": 1}, "", runtime)
    kms.delete()
    assert kms.get_deleted == ["a"]
    assert mutating(aws.calls) == []
