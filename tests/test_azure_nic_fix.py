"""BUG-111/BUG-122 (resource-group scope), BUG-113 (regex), BUG-114 (in use)."""

import types
from typing import Any

import pytest

from crc.azu.nic import AzureNicCleaner
from crc.core.cleaner import Operation, RunContext
from tests.helpers_azure_nic import NOW, FakeAzure, nic
from tests.helpers_wc_azu_engine import candidate_ids, engine_run

SDK_CALLS = ("list", "list_all", "begin_delete", "result")


def _ctx(*, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def _cleaner(azure: FakeAzure, name_regex: Any, exception_regex: Any = ()) -> AzureNicCleaner:
    return AzureNicCleaner(lambda: azure.client(None, "sub-1"), "rg", name_regex, exception_regex)


def _sdk(azure: FakeAzure) -> list[tuple[Any, ...]]:
    return [c for c in azure.calls if c[0] in SDK_CALLS]


def _engine(group: str, name_regex: Any, exception_regex: Any = (), *, dry_run: bool) -> Any:
    options = {
        "resource_group": group,
        "name_regex": name_regex,
        "exception_regex": list(exception_regex),
        "age": None if dry_run else {"days": 1},
    }
    return engine_run("nic", options, dry_run=dry_run, now=NOW)


def _in_use(**attrs: Any) -> types.SimpleNamespace:
    return types.SimpleNamespace(name="nic-a", virtual_machine=None, **attrs)


def test_only_the_configured_group_is_listed_and_acted_on_bug_111_bug_122(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    azure = FakeAzure([nic("nic-a")])
    azure.install(monkeypatch)
    outcome = _engine("rg-job", ["nic"], dry_run=False)
    assert ("list_all",) not in azure.calls
    assert _sdk(azure) == [
        ("list", "rg-job"),
        ("list", "rg-job"),
        ("begin_delete", "rg-job", "nic-a"),
        ("result", "nic-a"),
    ]
    assert candidate_ids(outcome) == ["nic-a"]


def test_group_comes_from_env_when_not_given_bug_111(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = FakeAzure([nic("nic-a")])
    azure.install(monkeypatch)
    _engine("", ["nic"], dry_run=True)
    assert ("list", "env-rg") in azure.calls


@pytest.mark.parametrize(
    ("pattern", "names", "expected"),
    [
        ("nic-.*", ["nic-1", "xnic"], ["nic-1"]),
        ("^nic", ["nic-1", "my-nic"], ["nic-1"]),
        ("yb-itest", ["yb-itest-1", "yb-itests-2", "other"], ["yb-itest-1", "yb-itests-2"]),
        ("a|b", ["a1", "b1", "c1"], ["a1", "b1"]),
    ],
)
def test_name_regex_is_a_regular_expression_bug_113(
    pattern: str, names: list[str], expected: list[str]
) -> None:
    plan = _cleaner(FakeAzure([nic(n) for n in names]), [pattern]).plan(_ctx(dry_run=True))
    assert [c.id for c in plan.candidates] == expected


def test_production_values_select_the_same_nics_bug_113() -> None:
    names = ["yb-itest-a", "yb-itests-b", "ui-auto-c", "ui-automation", "prod-nic", "yb-it"]
    plan = _cleaner(FakeAzure([nic(n) for n in names]), ["yb-itest", "yb-itests", "ui-auto"]).plan(
        _ctx(dry_run=True)
    )
    assert [c.id for c in plan.candidates] == [n for n in names if n not in {"prod-nic", "yb-it"}]


def test_exception_regex_is_a_regular_expression_bug_113() -> None:
    plan = _cleaner(FakeAzure([nic("nic-keep-1"), nic("nic-b")]), ["nic"], ["keep-\\d"]).plan(
        _ctx(dry_run=True)
    )
    assert [c.id for c in plan.candidates] == ["nic-b"]
    assert [(s.id, s.reason) for s in plan.skipped] == [("nic-keep-1", "matches exception_regex")]


def test_exception_literal_substring_still_excludes_bug_113() -> None:
    plan = _cleaner(FakeAzure([nic("x^nic-a"), nic("nic-b")]), ["nic"], ["^nic-a"]).plan(
        _ctx(dry_run=True)
    )
    assert [c.id for c in plan.candidates] == ["nic-b"]


@pytest.mark.parametrize(
    ("name_regex", "exception_regex"),
    [
        (["nic("], ()),
        (["nic"], ["[a-"]),
        ([], ["[a-"]),
        (["nic", "*bad"], ()),
        (["nic"], [""]),
        ("nic", ()),
        (["nic"], "x"),
        ([1], ()),
        (["nic"], [None]),
    ],
)
def test_invalid_patterns_raise_value_error_before_any_sdk_call_bug_113(
    name_regex: Any, exception_regex: Any
) -> None:
    azure = FakeAzure([nic("nic-a")])
    cleaner = _cleaner(azure, name_regex, exception_regex)
    with pytest.raises(ValueError, match=r"name_regex|exception_regex"):
        cleaner.plan(_ctx())
    assert azure.calls == []


def test_engine_invalid_pattern_raises_before_any_sdk_call_bug_113(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    azure = FakeAzure([nic("nic-a")])
    azure.install(monkeypatch)
    with pytest.raises(ValueError, match="Invalid name_regex pattern"):
        _engine("rg", ["nic("], dry_run=False)
    assert _sdk(azure) == []


def test_empty_string_entry_is_ignored_bug_113(caplog: pytest.LogCaptureFixture) -> None:
    azure = FakeAzure([nic("nic-a"), nic("other")])
    plan = _cleaner(azure, ["", "nic"]).plan(_ctx(dry_run=True))
    assert [c.id for c in plan.candidates] == ["nic-a"]
    azure = FakeAzure([nic("nic-a")])
    assert _cleaner(azure, [""]).plan(_ctx()).candidates == ()
    assert azure.calls == []
    assert "No Azure NICs to delete." in [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize(
    "attrs",
    [
        {"private_endpoint": {"id": "pe"}},
        {"private_link_service": {"id": "pls"}},
        {"private_endpoint": {"id": "pe"}, "private_link_service": {"id": "pls"}},
    ],
)
def test_nic_in_use_by_private_endpoint_or_link_service_is_kept_bug_114(
    attrs: dict[str, Any],
) -> None:
    azure = FakeAzure([_in_use(**attrs), nic("nic-b")])
    cleaner = _cleaner(azure, ["nic"])
    plan = cleaner.plan(_ctx())
    assert [c.id for c in plan.candidates] == ["nic-b"]
    assert [(s.id, s.reason) for s in plan.skipped] == [("nic-a", "in_use")]
    cleaner.apply(plan, _ctx())
    assert [c for c in azure.calls if c[0] == "begin_delete"] == [("begin_delete", "rg", "nic-b")]


def test_vm_attached_nic_is_in_use_and_missing_attributes_default_to_free_bug_114() -> None:
    azure = FakeAzure([nic("nic-a", {"id": "vm"}), _in_use(private_endpoint=None)])
    plan = _cleaner(azure, ["nic"]).plan(_ctx(dry_run=True))
    assert [(s.id, s.reason) for s in plan.skipped] == [("nic-a", "in_use")]
    assert [c.id for c in plan.candidates] == ["nic-a"]


def test_engine_keeps_nic_in_use_bug_114(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = FakeAzure([_in_use(private_endpoint={"id": "pe"})])
    azure.install(monkeypatch)
    assert candidate_ids(_engine("rg", ["nic"], dry_run=False)) == []
    assert [c for c in azure.calls if c[0] == "begin_delete"] == []
