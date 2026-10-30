"""Behaviour fixes: BUG-074, BUG-013, BUG-077 and BUG-076."""

import types
from collections.abc import Iterator
from typing import Any

import pytest

from crc.azu.ip import AzureIpCleaner
from crc.core.cleaner import Operation, RunContext
from tests.helpers_azure_ip import NOW, FakeAzure, ip
from tests.helpers_wc_azu_engine import candidate_ids, engine_run

ENV = {"env": "test"}


def _context(*, dry_run: bool = True) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def _cleaner(azure: FakeAzure, group: str = "rg", **specs: Any) -> AzureIpCleaner:
    return AzureIpCleaner(
        lambda: azure.client(None, "sub-1"),
        group,
        specs.get("filter_tags"),
        specs.get("exception_tags"),
        specs.get("notags"),
    )


def _engine(
    group: str,
    *,
    dry_run: bool,
    filter_tags: Any = None,
    exception_tags: Any = None,
    notags: Any = None,
) -> Any:
    options = {
        "resource_group": group,
        "filter_tags": filter_tags,
        "exception_tags": exception_tags,
        "notags": notags,
        "age": None if dry_run else {"days": 1},
    }
    return engine_run("ip", options, dry_run=dry_run, now=NOW)


def _in_use_ip(name: str, **fields: Any) -> types.SimpleNamespace:
    base = {"name": name, "tags": ENV, "ip_configuration": None}
    return types.SimpleNamespace(**{**base, **fields})


class _GroupedOperations:
    """Fake whose `list` returns only the addresses of the requested group."""

    def __init__(self, groups: dict[str, list[Any]], calls: list[tuple[Any, ...]]) -> None:
        self.groups = groups
        self.calls = calls

    def list_all(self) -> Iterator[Any]:
        self.calls.append(("list_all",))
        raise AssertionError("a subscription-wide listing must never be used")

    def list(self, resource_group_name: str) -> Iterator[Any]:
        self.calls.append(("list", resource_group_name))
        return iter(self.groups.get(resource_group_name, []))

    def begin_delete(self, *, resource_group_name: str, public_ip_address_name: str) -> object:
        self.calls.append(("begin_delete", resource_group_name, public_ip_address_name))
        return types.SimpleNamespace()


def _grouped_client(
    groups: dict[str, list[Any]], calls: list[tuple[Any, ...]]
) -> types.SimpleNamespace:
    return types.SimpleNamespace(public_ip_addresses=_GroupedOperations(groups, calls))


def test_only_the_configured_group_is_listed_and_acted_on() -> None:
    """BUG-074: a same-named address in another group is never listed, so never acted on."""
    calls: list[tuple[Any, ...]] = []
    groups = {
        "yugabyte-rg-australiacentral": [ip("shared", ENV)],
        "rg-configured": [ip("mine", ENV)],
    }
    cleaner = AzureIpCleaner(
        lambda: _grouped_client(groups, calls), "rg-configured", None, None, None
    )
    plan = cleaner.plan(_context(dry_run=False))
    cleaner.apply(plan, _context(dry_run=False))
    assert calls == [
        ("list", "rg-configured"),
        ("begin_delete", "rg-configured", "mine"),
    ]


def test_same_name_in_other_group_is_not_deleted_in_configured_group() -> None:
    calls: list[tuple[Any, ...]] = []
    groups = {"other": [ip("shared", ENV)], "rg-configured": []}
    cleaner = AzureIpCleaner(
        lambda: _grouped_client(groups, calls), "rg-configured", None, None, None
    )
    plan = cleaner.plan(_context(dry_run=False))
    cleaner.apply(plan, _context(dry_run=False))
    assert [c for c in calls if c[0] == "begin_delete"] == []


def test_engine_lists_configured_group(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = FakeAzure([ip("a", ENV)])
    azure.install(monkeypatch)
    _engine("rg-x", dry_run=True)
    assert ("list", "rg-x") in azure.calls
    assert ("list_all",) not in azure.calls


def test_group_from_environment_is_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = FakeAzure([ip("a", ENV)])
    azure.install(monkeypatch)
    _engine("", dry_run=True)
    assert ("list", "env-rg") in azure.calls


@pytest.mark.parametrize("group", ["", "   "])
def test_empty_resource_group_fails_closed_before_any_listing(group: str) -> None:
    azure = FakeAzure([ip("a", ENV)])
    with pytest.raises(ValueError, match="resource group"):
        _cleaner(azure, group=group).plan(_context(dry_run=False))
    assert azure.calls == []


@pytest.mark.parametrize(
    ("specs", "expected"),
    [
        ({"exception_tags": {"env": []}}, ["a"]),
        ({"notags": {"env": []}}, ["a"]),
        ({}, ["a", "b"]),
    ],
    ids=["exception", "notags", "none"],
)
def test_untagged_ip_never_raises(specs: dict[str, Any], expected: list[str]) -> None:
    """BUG-013: `tags is None` is an address with no tags."""
    azure = FakeAzure([ip("a", None), ip("b", ENV)])
    plan = _cleaner(azure, **specs).plan(_context())
    assert [c.id for c in plan.candidates] == expected


def test_untagged_ip_with_exception_tags_is_selected_and_tagged_one_kept() -> None:
    azure = FakeAzure([ip("a", None), ip("b", ENV)])
    plan = _cleaner(azure, exception_tags={"env": []}).plan(_context())
    assert [c.id for c in plan.candidates] == ["a"]
    assert [(s.id, s.reason) for s in plan.skipped] == [("b", "matches exception_tags or notags")]


def test_untagged_ip_with_notags_is_selected() -> None:
    azure = FakeAzure([ip("a", None)])
    plan = _cleaner(azure, notags={"env": []}).plan(_context())
    assert [c.id for c in plan.candidates] == ["a"]
    assert plan.candidates[0].tags == {}


def test_untagged_ip_excluded_by_filter_does_not_abort_the_run() -> None:
    """BUG-077: earlier deletes are no longer followed by a TypeError."""
    azure = FakeAzure([ip("a", ENV), ip("b", None), ip("c", ENV)])
    cleaner = _cleaner(
        azure, filter_tags={"env": []}, exception_tags={"zzz": []}, notags={"zzz": []}
    )
    plan = cleaner.plan(_context(dry_run=False))
    cleaner.apply(plan, _context(dry_run=False))
    deletes = [c for c in azure.calls if c[0] == "begin_delete"]
    assert deletes == [("begin_delete", "rg", "a"), ("begin_delete", "rg", "c")]
    assert [(s.id, s.reason) for s in plan.skipped] == [("b", "no filter_tags match")]


@pytest.mark.parametrize(
    "field",
    ["ip_configuration", "nat_gateway", "linked_public_ip_address", "service_public_ip_address"],
)
def test_ip_in_use_is_kept_with_reason_in_use(field: str) -> None:
    """BUG-076: any in-use field set keeps the address."""
    azure = FakeAzure([_in_use_ip("busy", **{field: {"id": "x"}}), _in_use_ip("free")])
    plan = _cleaner(azure).plan(_context(dry_run=False))
    assert [c.id for c in plan.candidates] == ["free"]
    assert [(s.id, s.reason) for s in plan.skipped] == [("busy", "in_use")]


def test_sdk_model_without_the_newer_in_use_fields_still_works() -> None:
    bare = types.SimpleNamespace(name="a", tags=ENV, ip_configuration=None)
    plan = _cleaner(FakeAzure([bare])).plan(_context())
    assert [c.id for c in plan.candidates] == ["a"]


def test_model_without_ip_configuration_still_raises_before_any_delete() -> None:
    azure = FakeAzure([types.SimpleNamespace(name="a", tags=ENV)])
    with pytest.raises(AttributeError, match="ip_configuration"):
        _cleaner(azure).plan(_context(dry_run=False))
    assert [c for c in azure.calls if c[0] == "begin_delete"] == []


def test_in_use_ip_is_never_deleted_by_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = FakeAzure([_in_use_ip("nat", nat_gateway={"id": "g"}), _in_use_ip("free")])
    azure.install(monkeypatch)
    assert candidate_ids(_engine("rg", dry_run=False)) == ["free"]
    assert [c for c in azure.calls if c[0] == "begin_delete"] == [("begin_delete", "rg", "free")]


@pytest.mark.parametrize(
    ("filter_tags", "matching_tags"),
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
                    "test",
                    "testing",
                    "manual-testing",
                    "manual_testing",
                    "Test",
                    "internal_testing",
                ],
                "crc": ["gke"],
            },
            {"crc": "gke"},
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
            {"yb_task": "stress-chaos"},
        ),
    ],
    ids=["yba-crc-cleanup", "db-stress-tests-cleanup"],
)
def test_production_jenkins_filter_tags_selection(
    monkeypatch: pytest.MonkeyPatch,
    filter_tags: dict[str, list[str]],
    matching_tags: dict[str, str],
) -> None:
    azure = FakeAzure(
        [
            ip("match-unused", matching_tags),
            ip("match-attached", matching_tags, config={"id": "nic"}),
            types.SimpleNamespace(
                name="match-nat",
                tags=matching_tags,
                ip_configuration=None,
                nat_gateway={"id": "ng"},
            ),
            ip("untagged", None),
            ip("other-task", {"yb_task": "production"}),
        ]
    )
    azure.install(monkeypatch)
    outcome = _engine(
        "rg-prod", dry_run=False, filter_tags=filter_tags, exception_tags={}, notags={}
    )
    assert candidate_ids(outcome) == ["match-unused"]
    assert ("list", "rg-prod") in azure.calls
    assert ("list_all",) not in azure.calls
    assert [c for c in azure.calls if c[0] == "begin_delete"] == [
        ("begin_delete", "rg-prod", "match-unused")
    ]
