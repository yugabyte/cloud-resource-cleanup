"""Table-driven tests for crc.core.policy: one table per catalogued variant."""

import datetime
import re
from typing import Any

import pytest

from crc.core import policy as p

UTC = datetime.UTC
T0 = datetime.datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


class FakeMap:
    """Dict-like object that is not a dict (stands in for GCP ScalarMapContainer)."""

    def __init__(self, data: dict[str, str]) -> None:
        self._data = data

    def items(self) -> Any:
        return self._data.items()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, {}),
        ([], {}),
        ({}, {}),
        ("text", {}),
        (5, {}),
        ([{"Key": "a", "Value": "1"}, {"Key": "b", "Value": "2"}], {"a": "1", "b": "2"}),
        ([{"Key": "a"}], {"a": ""}),
        ([{"Key": "a", "Value": None}], {"a": ""}),
        ([{"Key": "", "Value": "x"}, {"Value": "y"}], {}),
        ([{"Key": "a", "Value": "1"}, {"Key": "a", "Value": "2"}], {"a": "2"}),
        ([{"TagKey": "k", "TagValue": "v"}], {"k": "v"}),
        (({"Key": "t", "Value": "1"},), {"t": "1"}),
        (["junk", {"Key": "a", "Value": "1"}], {"a": "1"}),
        ({"a": "1", "b": None}, {"a": "1", "b": ""}),
        (FakeMap({"goog": "x"}), {"goog": "x"}),
    ],
)
def test_normalize_tags(raw: object, expected: dict[str, str]) -> None:
    assert p.normalize_tags(raw) == expected


def test_is_untagged() -> None:
    assert p.is_untagged({}) is True
    assert p.is_untagged({"a": ""}) is False


@pytest.mark.parametrize(
    ("tags", "mode", "has_notags", "expected"),
    [
        ({}, p.UntaggedMode.SKIP, False, True),
        ({}, p.UntaggedMode.SKIP, True, True),
        ({}, p.UntaggedMode.INCLUDE, False, False),
        ({}, p.UntaggedMode.INCLUDE, True, False),
        ({}, p.UntaggedMode.SKIP_UNLESS_NOTAGS, False, True),
        ({}, p.UntaggedMode.SKIP_UNLESS_NOTAGS, True, False),
        ({"a": "1"}, p.UntaggedMode.SKIP, False, False),
        ({"a": "1"}, p.UntaggedMode.SKIP_UNLESS_NOTAGS, False, False),
    ],
)
def test_skip_untagged(
    tags: dict[str, str], mode: p.UntaggedMode, has_notags: bool, expected: bool
) -> None:
    assert p.skip_untagged(tags, mode, has_notags=has_notags) is expected


TAGS = {"env": "ci", "owner": "bob"}

# (spec, any_key, all_keys); empty spec is the "no filter" case for the filter variants.
MATCH_TABLE: list[tuple[dict[str, list[str]], bool, bool]] = [
    ({}, False, True),
    ({"env": ["ci"]}, True, True),
    ({"env": ["prod"]}, False, False),
    ({"env": ["prod", "ci"]}, True, True),
    ({"env": []}, True, True),
    ({"missing": []}, False, False),
    ({"env": ["ci"], "owner": ["alice"]}, True, False),
    ({"env": ["ci"], "owner": ["bob"]}, True, True),
    ({"env": ["ci"], "missing": []}, True, False),
    ({"ENV": ["ci"]}, False, False),
    ({"env": ["CI"]}, False, False),
]


@pytest.mark.parametrize(("spec", "any_key", "all_keys"), MATCH_TABLE)
def test_matches_primitives(spec: dict[str, list[str]], any_key: bool, all_keys: bool) -> None:
    assert p.matches_any_key(TAGS, spec) is any_key
    assert p.matches_all_keys(TAGS, spec) is all_keys


@pytest.mark.parametrize(("spec", "any_key", "all_keys"), MATCH_TABLE)
def test_filter_tags_any_and_all_keys(
    spec: dict[str, list[str]], any_key: bool, all_keys: bool
) -> None:
    assert p.filter_tags_any_key(TAGS, spec) is (any_key or not spec)
    assert p.filter_tags_all_keys(TAGS, spec) is all_keys
    assert p.filter_tags_any_key({}, spec) is (not spec)
    assert p.filter_tags_all_keys({}, spec) is (not spec)


@pytest.mark.parametrize(("spec", "any_key", "all_keys"), MATCH_TABLE)
def test_exception_and_notags(spec: dict[str, list[str]], any_key: bool, all_keys: bool) -> None:
    assert p.exception_tags_any(TAGS, spec) is any_key
    assert p.notags_all(TAGS, spec) is (bool(spec) and all_keys)
    assert p.exception_tags_any({}, spec) is False
    assert p.notags_all({}, spec) is False


@pytest.mark.parametrize(
    ("tags", "exception", "notags", "expected"),
    [
        (TAGS, {}, {}, False),
        (TAGS, {"env": ["ci"]}, {}, True),
        (TAGS, {"env": ["prod"]}, {}, False),
        (TAGS, {}, {"env": ["ci"], "owner": []}, True),
        (TAGS, {}, {"env": ["ci"], "owner": ["alice"]}, False),
        (TAGS, {}, {"env": ["ci"], "missing": []}, False),
        (TAGS, {"env": ["prod"]}, {"owner": ["bob"]}, True),
        ({}, {"env": ["ci"]}, {"env": []}, False),
    ],
)
def test_should_skip_by_tags(
    tags: dict[str, str],
    exception: dict[str, list[str]],
    notags: dict[str, list[str]],
    expected: bool,
) -> None:
    assert p.should_skip_by_tags(tags, exception, notags) is expected


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ({}, True),
        ({"env": ["c*"]}, True),
        ({"env": ["c?"]}, True),
        ({"env": ["*"]}, True),
        ({"env": ["p*", "c*"]}, True),
        ({"env": ["C*"]}, False),
        ({"env": ["prod"]}, False),
        ({"env": []}, True),
        ({"missing": []}, False),
        ({"env": ["c*"], "owner": ["a*"]}, False),
        ({"env": ["c*"], "owner": ["b*"]}, True),
    ],
)
def test_filter_tags_glob_all_keys(spec: dict[str, list[str]], expected: bool) -> None:
    assert p.filter_tags_glob_all_keys(TAGS, spec) is expected


@pytest.mark.parametrize(
    ("tags", "spec", "expected"),
    [
        (TAGS, {}, True),
        ({}, {}, True),
        (TAGS, {"env": ["ci"]}, True),
        (TAGS, {"env": ["ci"], "owner": ["bob"]}, True),
        (TAGS, {"env": ["ci", "prod"]}, False),
        (TAGS, {"env": ["prod"]}, False),
        (TAGS, {"env": []}, True),
        (TAGS, {"missing": []}, True),
        (TAGS, {"missing": ["x"]}, False),
        ({}, {"env": ["ci"]}, False),
        (TAGS, {"env": ["c*"]}, False),
    ],
)
def test_filter_tags_exact_pairs(
    tags: dict[str, str], spec: dict[str, list[str]], expected: bool
) -> None:
    assert p.filter_tags_exact_pairs(tags, spec) is expected


@pytest.mark.parametrize(
    ("exception", "expected"),
    [({}, False), ({"env": ["ci"]}, True), ({"env": ["x"]}, False), ({"owner": []}, True)],
)
def test_aws_eip_should_skip_ignores_notags(
    exception: dict[str, list[str]], expected: bool
) -> None:
    assert p.aws_eip_should_skip(TAGS, exception) is expected
    assert not hasattr(p, "aws_eip_should_skip_with_notags")


NAMES = [
    # patterns, text, search_name, search_exc, match_exc, substring_filter, nic
    ([], "web-1", True, False, False, True, False),
    (["web"], "web-1", True, True, True, True, True),
    (["eb"], "web-1", True, True, False, True, True),
    (["^web"], "web-1", True, True, True, False, False),
    (["db"], "web-1", False, False, False, False, False),
    (["WEB"], "web-1", False, False, False, False, False),
    (["db", "web"], "web-1", True, True, True, True, True),
    (["w.b"], "web-1", True, True, True, False, False),
]


@pytest.mark.parametrize("row", NAMES)
def test_name_and_regex_variants(row: tuple[Any, ...]) -> None:
    patterns, text, s_name, s_exc, m_exc, sub_filter, nic = row
    assert p.name_regex_search_matches(patterns, text) is s_name
    assert p.exception_regex_search_matches(patterns, text) is s_exc
    assert p.exception_regex_match_matches(patterns, text) is m_exc
    assert p.filter_substring_matches(patterns, text) is sub_filter
    assert p.nic_name_excluded(patterns, text) is nic


def test_regex_search_vs_match_and_none_text() -> None:
    assert p.exception_regex_search_matches(["b-1"], "web-1") is True
    assert p.exception_regex_match_matches(["b-1"], "web-1") is False
    assert p.name_regex_search_matches(["x"], None) is False
    assert p.name_regex_search_matches(["^$"], None) is True
    assert p.exception_regex_search_matches(["^$"], None) is True


def test_dead_policy_helpers_removed() -> None:
    assert not hasattr(p, "gcp_ip_exception_excludes")
    assert not hasattr(p, "nic_name_included")


def test_invalid_regex_behaviour() -> None:
    with pytest.raises(re.error):
        p.name_regex_search_matches(["("], "x")
    with pytest.raises(ValueError, match=r"Invalid name_regex pattern '\('"):
        p.compile_patterns(["a", "("], "name_regex")
    assert [c.pattern for c in p.compile_patterns(["a", "b"], "x")] == ["a", "b"]
    assert p.compile_patterns([], "x") == ()


@pytest.mark.parametrize(
    ("state", "allowed", "exact", "disk_sub", "vm_sub"),
    [
        ("running", ["running"], True, True, True),
        ("Unattached", ["Unattached"], True, True, True),
        ("Attached", ["Unattached"], False, False, False),
        ("attached", ["Unattached"], False, True, False),  # substring quirk of Azure disk
        ("VM running", ["running"], False, False, True),
        ("running", ["VM running"], False, True, False),
        ("VM deallocated", ["running"], False, False, False),
        ("running", [], False, False, False),
    ],
)
def test_state_variants(
    state: str, allowed: list[str], exact: bool, disk_sub: bool, vm_sub: bool
) -> None:
    assert p.state_in(state, allowed) is exact
    assert p.state_is_substring_of_any(state, allowed) is disk_sub
    assert p.status_contains_any_allowed(state, allowed) is vm_sub


def test_state_in_none_and_generators() -> None:
    assert p.state_in(None, ["a"]) is False
    assert p.state_in("a", (s for s in ["a"])) is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", False),
        ("   ", False),
        ("prod", True),
        ("PROD", True),
        (" Production ", True),
        ("infra", True),
        ("infrastructure", True),
        ("ipsec", True),
        ("vpn", True),
        ("my-vpn-box", True),
        ("test/prod/x", True),
        ("a_infra_b", True),
        ("prod x", True),
        ("prod-east", True),
        ("prod_east", True),
        ("production-eu", True),
        ("product", False),
        ("nonprod", False),
        ("preprod", False),
        ("ci", False),
        ("infrared", False),
    ],
)
def test_is_sensitive_yb_task(value: str, expected: bool) -> None:
    assert p.is_sensitive_yb_task(value) is expected


def test_sensitive_set() -> None:
    assert frozenset({"infrastructure", "infra", "ipsec", "vpn", "prod", "production"}) == (
        p.SENSITIVE_YB_TASK
    )


@pytest.mark.parametrize(
    ("tags", "disk_value", "disk", "spot"),
    [
        ({}, "", False, False),
        ({"yb_task": "prod"}, "prod", True, True),
        ({"yb-task": "prod"}, "prod", True, True),
        ({"YB_TASK": "prod"}, "prod", True, False),
        ({"Yb-Task": " vpn "}, "vpn", True, False),
        ({"yb_task": "ci"}, "ci", False, False),
        ({"yb_task": "ci", "yb-task": "prod"}, "ci", False, True),
        ({"yb-task": "prod", "yb_task": "ci"}, "prod", True, True),
        ({"yb_task": ""}, "", False, False),
        ({"task": "prod"}, "", False, False),
    ],
)
def test_yb_task_variants(tags: dict[str, str], disk_value: str, disk: bool, spot: bool) -> None:
    assert p.aws_disk_yb_task(tags) == disk_value
    assert p.aws_disk_has_sensitive_yb_task(tags) is disk
    assert p.aws_spot_has_sensitive_yb_task(tags) is spot


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({}, False),
        ({"kubernetes.io/cluster/x": "owned"}, True),
        ({"ebs.csi.aws.com/cluster": "true"}, True),
        ({"KubernetesCluster": "x"}, True),
        ({"CSIVolumeName": "pvc"}, True),
        ({"kubernetescluster": "x"}, False),
        ({"my.kubernetes.io/x": "1"}, False),
        ({"Name": "vol"}, False),
    ],
)
def test_aws_kubernetes_volume(tags: dict[str, str], expected: bool) -> None:
    assert p.aws_is_kubernetes_volume(tags) is expected
    assert p.aws_filter_wants_kubernetes({k: [] for k in tags}) is expected


@pytest.mark.parametrize(
    ("tags", "filter_tags", "expected"),
    [
        ({"kubernetes.io/x": "1"}, {}, True),
        ({"kubernetes.io/x": "1"}, {"Name": ["a"]}, True),
        ({"kubernetes.io/x": "1"}, {"kubernetes.io/x": []}, False),
        ({"kubernetes.io/x": "1"}, {"KubernetesCluster": ["c"]}, False),
        ({"Name": "a"}, {}, False),
    ],
)
def test_aws_disk_skips_kubernetes(
    tags: dict[str, str], filter_tags: dict[str, list[str]], expected: bool
) -> None:
    assert p.aws_disk_skips_kubernetes(tags, filter_tags) is expected


@pytest.mark.parametrize(
    ("labels", "metadata", "expected"),
    [
        ({}, [], False),
        ({"goog-gke-node": ""}, [], True),
        ({}, [("created-by", "projects/p/zones/z/instanceGroupManagers/mig-1")], True),
        ({}, [("created-by", "projects/p/zones/z/instances/i")], False),
        ({}, [("other", "instanceGroupManagers/x")], False),
        ({}, [("Created-By", "instanceGroupManagers/x")], False),
        ({"other": "x"}, [("a", "b"), ("created-by", "instanceGroupManagers")], True),
    ],
)
def test_gcp_managed_or_gke(
    labels: dict[str, str], metadata: list[tuple[str, str]], expected: bool
) -> None:
    assert p.gcp_is_managed_or_gke_node(labels, metadata) is expected
    assert p.gcp_is_managed_or_gke_node(labels, iter(metadata)) is expected


@pytest.mark.parametrize(
    ("freeform", "defined", "metadata", "expected"),
    [
        (None, None, None, False),
        ({}, {}, {}, False),
        ({"name": "web"}, {}, {}, False),
        ({"oke-cluster": "x"}, {}, {}, True),
        ({"a": "OKE"}, {}, {}, True),
        ({"a": "instancepool"}, {}, {}, True),
        ({"a": "x_instance_pool"}, {}, {}, True),
        ({"a": "instance-pool-1"}, {}, {}, True),
        ({}, {"ns": {"oke": "1"}}, {}, True),
        ({}, {"oke-ns": {"k": "v"}}, {}, True),
        ({}, {"oke-ns": "not-a-dict"}, {}, False),
        ({}, {}, {"oke.cluster": "x"}, True),
        ({}, {}, ["oke"], False),
        ({}, {}, None, False),
        ({"a": "broker"}, {}, {}, True),
    ],
)
def test_oci_managed_or_oke(freeform: Any, defined: Any, metadata: Any, expected: bool) -> None:
    assert p.oci_is_managed_or_oke_node(freeform, defined, metadata) is expected


@pytest.mark.parametrize(
    ("custom", "default", "expected"),
    [
        (None, {"days": 2}, {"days": 2}),
        ({}, {"days": 2}, {"days": 2}),
        ({"hours": 3}, {"days": 2}, {"hours": 3}),
    ],
)
def test_effective_age(
    custom: dict[str, int] | None, default: dict[str, int], expected: dict[str, int]
) -> None:
    assert p.effective_age(custom, default) == expected


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (2, datetime.timedelta(days=2)),
        ({"days": 1, "hours": 2}, datetime.timedelta(hours=26)),
        ({"hours": 5}, datetime.timedelta(hours=5)),
        ({}, datetime.timedelta(0)),
    ],
)
def test_age_to_timedelta(age: Any, expected: datetime.timedelta) -> None:
    assert p.age_to_timedelta(age) == expected


@pytest.mark.parametrize(
    ("age", "detach", "retention", "expected"),
    [
        ({"days": 3}, {"days": 1}, None, {"days": 3}),
        ({"days": 3}, {}, {"hours": 1}, {"hours": 1}),
        ({"days": 3}, {"days": 9}, {"days": 2}, {"days": 2}),
        ({}, {"days": 1}, None, {"days": 1}),
        ({}, {"days": 1}, {"hours": 1}, {"days": 1}),
        ({}, {"hours": 1}, {"days": 2}, {"days": 2}),
        ({}, {}, {"days": 2}, {"days": 2}),
        ({}, {}, None, {}),
    ],
)
def test_aws_disk_creation_floor(
    age: dict[str, int],
    detach: dict[str, int],
    retention: dict[str, int] | None,
    expected: dict[str, int],
) -> None:
    assert p.aws_disk_creation_floor(age, detach, retention) == expected


@pytest.mark.parametrize(
    ("created_delta", "floor", "expected"),
    [
        (datetime.timedelta(days=2), {"days": 2}, True),
        (datetime.timedelta(days=2) - datetime.timedelta(seconds=1), {"days": 2}, False),
        (datetime.timedelta(days=5), {"hours": 1}, True),
        (datetime.timedelta(days=5), {}, False),
        (datetime.timedelta(days=5), {"days": 0}, False),
        (datetime.timedelta(0), {"hours": 1}, False),
    ],
)
def test_is_older_than_floor(
    created_delta: datetime.timedelta, floor: dict[str, int], expected: bool
) -> None:
    assert p.is_older_than_floor(T0 - created_delta, T0, floor) is expected


@pytest.mark.parametrize(
    ("start_delta", "days", "expected"),
    [
        (datetime.timedelta(days=3), 2, True),
        (datetime.timedelta(days=2), 2, True),
        (datetime.timedelta(days=2) - datetime.timedelta(seconds=1), 2, False),
        (datetime.timedelta(0), 1, False),
        (datetime.timedelta(0), 0, True),
    ],
)
def test_snapshot_old_enough(start_delta: datetime.timedelta, days: int, expected: bool) -> None:
    assert p.snapshot_old_enough(T0 - start_delta, T0, days) is expected


def test_package_exports_policy_symbols() -> None:
    from crc import core

    for name in ("normalize_tags", "filter_tags_all_keys", "UntaggedMode", "SENSITIVE_YB_TASK"):
        assert name in core.__all__
        assert getattr(core, name) is getattr(p, name)
