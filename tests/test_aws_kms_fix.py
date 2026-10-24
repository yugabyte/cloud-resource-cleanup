"""BUG-007, BUG-008, BUG-014 and BUG-055 on `crc.aws.kms` (BUG-015 left open)."""

import datetime
import json
from collections.abc import Iterable, Mapping
from typing import Any

from crc.aws.kms import KmsCleaner, KmsSettings
from crc.core import policy
from crc.core.cleaner import Operation, RunContext
from tests.helpers_wc_aws1_engine import deleted_ids, run_cleaner

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
CREATED = NOW - datetime.timedelta(days=3)
USER = "arn:aws:iam::1:user/jenkins"


class Paginator:
    def __init__(self, ids: list[str]) -> None:
        self.ids = ids

    def paginate(self) -> Iterable[Mapping[str, Any]]:
        return [{"Keys": [{"KeyId": k} for k in self.ids]}]


class FakeKms:
    def __init__(self, tags: Mapping[str, list[dict[str, str]]], policies: Mapping[str, str]):
        self.tags = tags
        self.policies = policies
        self.calls: list[tuple[str, str]] = []

    def get_paginator(self, operation_name: str) -> Paginator:
        return Paginator(list(self.tags))

    def list_resource_tags(self, *, KeyId: str) -> Mapping[str, Any]:
        return {"Tags": self.tags[KeyId]}

    def describe_key(self, *, KeyId: str) -> Mapping[str, Any]:
        self.calls.append(("describe_key", KeyId))
        meta = {"KeyState": "Enabled", "Description": "ci key", "CreationDate": CREATED}
        return {"KeyMetadata": meta}

    def get_key_policy(self, *, KeyId: str, PolicyName: str) -> Mapping[str, Any]:
        return {"Policy": self.policies.get(KeyId) or _policy(USER)}

    def schedule_key_deletion(self, *, KeyId: str, PendingWindowInDays: int) -> Mapping[str, Any]:
        self.calls.append(("schedule_key_deletion", KeyId))
        return {}


def _policy(*principals: str) -> str:
    return json.dumps({"Statement": [{"Principal": {"AWS": p}} for p in principals]})


def _tags(**tags: str) -> list[dict[str, str]]:
    return [{"TagKey": k, "TagValue": v} for k, v in tags.items()]


def _plan(
    tags: Mapping[str, list[dict[str, str]]],
    policies: Mapping[str, str] | None = None,
    **settings: Any,
) -> tuple[list[str], FakeKms]:
    fake = FakeKms(tags, policies or {})
    values: dict[str, Any] = {"kms_key_description": "ci", "kms_user": USER, "age": {"days": 1}}
    values.update(settings)
    cleaner = KmsCleaner(lambda region: fake, KmsSettings(**values))
    context = RunContext(dry_run=False, operation=Operation.DELETE, options={}, now=NOW)
    plan = cleaner.plan(context)
    return [c.id for c in plan.candidates], fake


def test_bug_014_several_values_match_any_of_them() -> None:
    tags = {"k1": _tags(a="1"), "k2": _tags(a="2"), "k3": _tags(a="3"), "k4": []}
    selected, _ = _plan(tags, filter_tags={"a": ["1", "2"]})
    assert selected == ["k1", "k2"]


def test_bug_014_all_filter_keys_still_required() -> None:
    tags = {"k1": _tags(a="1", b="x"), "k2": _tags(a="1", b="y"), "k3": _tags(a="1")}
    selected, _ = _plan(tags, filter_tags={"a": ["1", "2"], "b": ["x"]})
    assert selected == ["k1"]


def test_bug_007_empty_value_list_requires_the_key() -> None:
    tags = {"k1": _tags(a="anything"), "k2": _tags(a=""), "k3": _tags(z="1"), "k4": []}
    selected, _ = _plan(tags, filter_tags={"a": []})
    assert selected == ["k1", "k2"]


def test_bug_007_matches_the_vm_semantics() -> None:
    spec = {"a": [], "b": ["1", "2"]}
    for present in ({"a": "x", "b": "2"}, {"a": "x"}, {"b": "1"}, {}):
        tags = {"k1": _tags(**present)}
        selected, _ = _plan(tags, filter_tags=spec)
        assert bool(selected) == policy.filter_tags_all_keys(present, spec)


def test_bug_015_left_open_any_tagged_key_is_skipped_with_exception_tags() -> None:
    tags = {"k1": _tags(autoclean="false"), "k2": _tags(crc="gke"), "k3": _tags(crc="x"), "k4": []}
    exceptions = {"autoclean": ["false", "False"], "crc": ["gke"]}
    selected, fake = _plan(tags, exception_tags=exceptions)
    assert selected == ["k4"]
    assert [c[1] for c in fake.calls if c[0] == "describe_key"] == ["k4"]


def test_bug_008_custom_age_tag_is_not_reached_when_exception_tags_skip() -> None:
    tags = {"k1": _tags(max_retention_age="{'hours': 1}")}
    selected, _ = _plan(
        tags, age={"days": 5}, custom_age_tag_key="max_retention_age", exception_tags={"a": []}
    )
    assert selected == []


def test_bug_008_custom_age_tag_is_read_from_tagkey() -> None:
    tags = {
        "k1": _tags(max_retention_age="{'hours': 1}"),
        "k2": _tags(max_retention_age="{'days': 9}"),
    }
    selected, _ = _plan(tags, age={"days": 5}, custom_age_tag_key="max_retention_age")
    assert selected == ["k1"]
    selected, _ = _plan(tags, age={"days": 5})
    assert selected == []


def test_bug_008_custom_age_tag_longer_than_age_keeps_the_key() -> None:
    tags = {"k1": _tags(max_retention_age="{'days': 100}")}
    selected, _ = _plan(tags, age={"days": 1}, custom_age_tag_key="max_retention_age")
    assert selected == []


def test_bug_008_bad_custom_age_tag_keeps_the_key() -> None:
    # BUG-004: a present but empty or unparseable age tag keeps the key.
    tags = {"k1": _tags(max_retention_age="nope"), "k2": _tags(max_retention_age="")}
    selected, _ = _plan(tags, age={"days": 1}, custom_age_tag_key="max_retention_age")
    assert selected == []


def test_bug_055_skipped_key_is_never_scheduled() -> None:
    mixed = json.dumps(
        {"Statement": [{"Principal": {"AWS": USER}}, {"Principal": {"Service": "logs"}}]}
    )
    tags: dict[str, list[dict[str, str]]] = {"bad": [], "good": []}
    fake = FakeKms(tags, {"bad": mixed})
    settings = KmsSettings({}, {}, "ci", USER, 7, {"days": 1}, "")
    outcome = run_cleaner("aws", "kms", KmsCleaner(lambda region: fake, settings), now=NOW)
    assert deleted_ids(outcome) == ["good"]
    assert [c for c in fake.calls if c[0] == "schedule_key_deletion"] == [
        ("schedule_key_deletion", "good")
    ]


def test_bug_055_policy_with_only_user_statements_still_selects() -> None:
    selected, _ = _plan({"k1": []}, {"k1": _policy(USER, "other")})
    assert selected == ["k1"]


def test_production_command_shape_selects_untagged_old_key() -> None:
    spec = {
        "autoclean": ["false", "False", "FALSE"],
        "auto_clean": ["false", "False", "FALSE"],
        "crc": ["gke"],
    }
    tags = {"k1": _tags(auto_clean="FALSE"), "k2": _tags(owner="qa"), "k3": []}
    selected, _ = _plan(
        tags,
        exception_tags=spec,
        age={"days": 2},
        custom_age_tag_key="max_retention_age",
        kms_key_description="ci",
    )
    assert selected == ["k3"]
