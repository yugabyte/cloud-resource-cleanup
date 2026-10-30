"""BUG-011: `notags` on Elastic IPs keeps addresses instead of raising."""

import datetime
from collections.abc import Mapping
from typing import Any

import pytest

from crc.aws.elastic_ips import ElasticIpCleaner
from crc.core.cleaner import Operation, RunContext
from tests.helpers_wc_aws1_engine import deleted_ids, run_cleaner

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical


def address(ip: str, **tags: str) -> dict[str, Any]:
    return {
        "PublicIp": ip,
        "AllocationId": f"alloc-{ip}",
        "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
    }


class FakeEc2:
    def __init__(self, addresses: list[dict[str, Any]], released: list[str]) -> None:
        self.addresses = addresses
        self.released = released

    def describe_addresses(self) -> Mapping[str, Any]:
        return {"Addresses": self.addresses}

    def release_address(self, *, AllocationId: str) -> Mapping[str, Any]:
        self.released.append(AllocationId)
        return {}


def build(
    addresses: list[dict[str, Any]],
    released: list[str],
    *,
    filter_tags: Mapping[str, list[str]] | None = None,
    exception_tags: Mapping[str, list[str]] | None = None,
    notags: Mapping[str, list[str]] | None = None,
) -> ElasticIpCleaner:
    return ElasticIpCleaner(
        lambda region: FakeEc2(addresses, released),
        lambda: ["r1"],
        filter_tags,
        exception_tags,
        notags,
    )


def context(*, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def planned(cleaner: ElasticIpCleaner) -> list[str]:
    return [c.id for c in cleaner.plan(context()).candidates]


def test_notags_key_present_keeps_address() -> None:
    cleaner = build([address("1.1.1.1", keep="yes")], [], notags={"keep": []})
    plan = cleaner.plan(context())
    assert [c.id for c in plan.candidates] == []
    assert [(s.id, s.reason) for s in plan.skipped] == [("1.1.1.1", "matches exception_tags")]


def test_notags_value_must_match() -> None:
    addresses = [address("1.1.1.1", keep="yes"), address("2.2.2.2", keep="no")]
    cleaner = build(addresses, [], notags={"keep": ["yes"]})
    assert planned(cleaner) == ["2.2.2.2"]


def test_notags_needs_every_key_to_keep() -> None:
    addresses = [address("1.1.1.1", a="1"), address("2.2.2.2", a="1", b="2")]
    cleaner = build(addresses, [], notags={"a": [], "b": []})
    assert planned(cleaner) == ["1.1.1.1"]


def test_notags_not_matching_still_selected_by_filter() -> None:
    cleaner = build([address("1.1.1.1", env="test")], [], filter_tags={"env": []}, notags={"x": []})
    assert planned(cleaner) == ["1.1.1.1"]


def test_notags_and_exception_tags_both_keep() -> None:
    addresses = [
        address("1.1.1.1", a="1"),
        address("2.2.2.2", b="1"),
        address("3.3.3.3", c="1"),
    ]
    cleaner = build(addresses, [], exception_tags={"a": []}, notags={"b": []})
    assert planned(cleaner) == ["3.3.3.3"]


def test_notags_apply_releases_only_unkept() -> None:
    released: list[str] = []
    addresses = [address("1.1.1.1", keep="y"), address("2.2.2.2", env="t")]
    cleaner = build(addresses, released, notags={"keep": []})
    result = cleaner.apply(cleaner.plan(context()), context())
    assert result.succeeded == ("2.2.2.2",)
    assert released == ["alloc-2.2.2.2"]


def test_notags_dry_run_releases_nothing() -> None:
    released: list[str] = []
    cleaner = build([address("1.1.1.1", env="t")], released, notags={"x": []})
    plan = cleaner.plan(context(dry_run=True))
    cleaner.apply(plan, context(dry_run=True))
    assert released == []


def test_notags_tag_without_value_fails_closed() -> None:
    bad = {"PublicIp": "1.1.1.1", "AllocationId": "a", "Tags": [{"Key": "k"}]}
    released: list[str] = []
    cleaner = build([bad], released, notags={"k": []})
    with pytest.raises(KeyError):
        cleaner.plan(context())
    assert released == []


def test_engine_run_with_notags_does_not_raise() -> None:
    released: list[str] = []
    addresses = [address("1.1.1.1", keep="y"), address("2.2.2.2", env="t")]
    cleaner = ElasticIpCleaner(
        lambda region: FakeEc2(addresses, released), lambda: ["r1"], {}, {}, {"keep": []}
    )
    outcome = run_cleaner("aws", "ip", cleaner)
    assert deleted_ids(outcome) == ["2.2.2.2"]
    assert released == ["alloc-2.2.2.2"]


@pytest.mark.parametrize(
    ("filter_tags", "expected"),
    [
        (
            {
                "yb_task": [
                    "bug_bash_22_9_2023",
                    "uitest",
                    "itest",
                    "itests",
                    "itest-system",
                    "db-cross-functional",
                    "itest-keep-universe",
                    "itest-system-keep-universe",
                    "db-cross-functional-keep-universe",
                    "dev-itest",
                    "dev-uitest",
                    "uitest-keep-universe",
                    "dev-uitest-keep-universe",
                    "test",
                    "testing",
                    "manual-testing",
                    "manual_testing",
                    "Test",
                    "internal_testing",
                ],
                "crc": ["gke"],
            },
            ["1.1.1.1", "2.2.2.2"],
        ),
        (
            {
                "yb_task": [
                    "stress",
                    "stress-dev",
                    "stress-chaos",
                    "stress-lru-cdc",
                    "stress-smoke-cdc",
                    "smoke-core",
                    "stress-smoke-core",
                    "stress-sqlancer",
                    "stress-lru-core",
                    "stress-scale",
                ]
            },
            ["3.3.3.3"],
        ),
    ],
)
def test_production_jenkins_filter_tags_selection_unchanged(
    filter_tags: Mapping[str, list[str]], expected: list[str]
) -> None:
    addresses = [
        address("1.1.1.1", yb_task="itest"),
        address("2.2.2.2", crc="gke"),
        address("3.3.3.3", yb_task="stress"),
        address("4.4.4.4", yb_task="production"),
    ]
    cleaner = build(addresses, [], filter_tags=filter_tags)
    assert planned(cleaner) == expected
