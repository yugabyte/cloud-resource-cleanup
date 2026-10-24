"""Unit tests of `crc.gcp.ip`: read-only `plan()`, mutating `apply()`, fakes only."""

import datetime
import logging
import types
from collections.abc import Iterable, Sequence
from typing import Any

import pytest

from crc.core.cleaner import Candidate, Operation, Plan, RunContext
from crc.core.registry import get
from crc.gcp.ip import AddressView, IpCleaner
from tests.helpers_wc_gcp_engine import deleted_ids, run_engine

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical
PROJECT = "proj-1"
TWO = 2


def ctx(*, dry_run: bool = False, **options: Any) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options=options, now=NOW)


def addr(name: str, status: str = "RESERVED") -> Any:
    return types.SimpleNamespace(name=name, status=status)


class FakeOperation:
    def __init__(self, log: list[tuple[str, ...]], address: str, error: Exception | None) -> None:
        self.log = log
        self.address = address
        self.error = error

    def result(self) -> object:
        self.log.append(("result", self.address))
        if self.error is not None:
            raise self.error
        return None


class FakeClient:
    def __init__(self, regions: dict[str, list[Any]]) -> None:
        self.regions = regions
        self.calls: list[tuple[str, ...]] = []

    def list_regions(self, project_id: str) -> Sequence[str]:
        self.calls.append(("list_regions", project_id))
        return list(self.regions)

    def list_addresses(self, project_id: str, region: str) -> Iterable[AddressView]:
        self.calls.append(("list_addresses", project_id, region))
        items: list[AddressView] = self.regions[region]
        return items

    def delete_address(self, project_id: str, region: str, address: str) -> FakeOperation:
        self.calls.append(("delete_address", project_id, region, address))
        return FakeOperation(self.calls, address, None)

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


def cleaner(
    client: FakeClient, filter_regex: Sequence[str] | None = None, exc: Sequence[str] | None = None
) -> IpCleaner:
    return IpCleaner(client, PROJECT, filter_regex, exc)


def test_plan_lists_and_never_mutates() -> None:
    client = FakeClient({"r1": [addr("a"), addr("b")], "r2": [addr("c")]})
    plan = cleaner(client).plan(ctx())
    assert [(c.id, c.region, c.state) for c in plan.candidates] == [
        ("a", "r1", "RESERVED"),
        ("b", "r1", "RESERVED"),
        ("c", "r2", "RESERVED"),
    ]
    assert client.names() == ["list_regions", "list_addresses", "list_addresses"]


def test_plan_in_dry_run_logs_summary_and_does_not_delete(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    client = FakeClient({"r": [addr("a")]})
    cleaner(client).plan(ctx(dry_run=True))
    assert [r.getMessage() for r in caplog.records] == [
        "List of GCP IPs (Total: 1) which will be deleted: ['a']"
    ]
    assert "delete_address" not in client.names()


def test_plan_outside_dry_run_logs_no_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    cleaner(FakeClient({"r": [addr("a")]})).plan(ctx())
    assert caplog.records == []


@pytest.mark.parametrize(
    ("filter_regex", "exception_regex", "expected"),
    [
        (None, None, ["ip-a", "keep-b"]),
        ([], [], ["ip-a", "keep-b"]),
        (["ip-"], None, ["ip-a"]),
        (["ip-", "keep"], None, ["ip-a", "keep-b"]),
        (["zzz"], None, []),
        (None, ["keep"], ["ip-a"]),
        (None, ["zzz"], ["ip-a", "keep-b"]),
        (["ip-"], ["ip-a"], []),
        (["keep"], ["ip-"], ["keep-b"]),
    ],
)
def test_plan_filters(
    filter_regex: list[str] | None, exception_regex: list[str] | None, expected: list[str]
) -> None:
    client = FakeClient({"r": [addr("ip-a"), addr("keep-b"), addr("in-use", "IN_USE")]})
    plan = cleaner(client, filter_regex, exception_regex).plan(ctx())
    assert [c.id for c in plan.candidates] == expected


def test_plan_records_skip_reasons() -> None:
    client = FakeClient({"r": [addr("a", "IN_USE"), addr("b"), addr("c")]})
    plan = cleaner(client, ["b", "c"], ["c"]).plan(ctx())
    assert [(s.id, s.reason) for s in plan.skipped] == [
        ("a", "status is not RESERVED"),
        ("c", "exception_regex"),
    ]
    assert [c.id for c in plan.candidates] == ["b"]


def test_plan_propagates_listing_errors() -> None:
    class Broken(FakeClient):
        def list_regions(self, project_id: str) -> Sequence[str]:
            raise PermissionError("denied")

    with pytest.raises(PermissionError, match="denied"):
        cleaner(Broken({})).plan(ctx())


def test_apply_deletes_in_plan_order_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    client = FakeClient({"r1": [addr("a"), addr("b")], "r2": [addr("c")]})
    c = cleaner(client)
    plan = c.plan(ctx())
    client.calls.clear()
    caplog.clear()
    result = c.apply(plan, ctx())
    assert result.succeeded == ("a", "b", "c")
    assert client.calls == [
        ("delete_address", PROJECT, "r1", "a"),
        ("result", "a"),
        ("delete_address", PROJECT, "r1", "b"),
        ("result", "b"),
        ("delete_address", PROJECT, "r2", "c"),
        ("result", "c"),
    ]
    assert [r.getMessage() for r in caplog.records] == [
        "Deleting IP address: a",
        "Deleting IP address: b",
        "Deleting IP address: c",
        "number of GCP IPs deleted: 3",
        "List of GCP IPs deleted: ['a', 'b', 'c']",
    ]


def test_apply_acts_only_on_the_plan() -> None:
    client = FakeClient({"r": [addr("a"), addr("b")]})
    plan = Plan(candidates=(Candidate(id="b", name="b", region="r"),))
    result = cleaner(client).apply(plan, ctx())
    assert result.succeeded == ("b",)
    assert client.calls == [("delete_address", PROJECT, "r", "b"), ("result", "b")]


def test_apply_in_dry_run_deletes_nothing() -> None:
    client = FakeClient({})
    plan = Plan(candidates=(Candidate(id="a", name="a", region="r"),))
    cleaner(client).apply(plan, ctx(dry_run=True))
    assert client.calls == []


def test_apply_unexpected_error_propagates_without_summary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Failing(FakeClient):
        def delete_address(self, project_id: str, region: str, address: str) -> FakeOperation:
            raise RuntimeError("quota")

    caplog.set_level(logging.WARNING)
    plan = Plan(candidates=(Candidate(id="a", name="a", region="r"),))
    with pytest.raises(RuntimeError, match="quota"):
        cleaner(Failing({})).apply(plan, ctx())
    assert caplog.records == []


def test_supports_delete_only() -> None:
    c = cleaner(FakeClient({}))
    assert c.supports(Operation.DELETE)
    assert not c.supports(Operation.STOP)


def test_factory_reads_options_and_requires_project() -> None:
    built = get("gcp", "ip")(
        ctx(project_id=PROJECT, name_regex=["a"], exception_regex=["b"], age={"days": 1})
    )
    assert isinstance(built, IpCleaner)
    with pytest.raises(ValueError, match="project_id is required"):
        get("gcp", "ip")(ctx())


def test_engine_uses_injected_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("google.cloud.compute_v1.RegionsClient", lambda: pytest.fail("SDK used"))
    client = FakeClient({"r1": [addr("a")], "r2": [addr("b")]})
    outcome = run_engine(cleaner(client, ["a"], []), "ip")
    assert deleted_ids(outcome) == ["a"]
    assert client.names().count("delete_address") == 1


def test_engine_dry_run_lists_without_deleting() -> None:
    client = FakeClient({"r": [addr("a"), addr("b")]})
    outcome = run_engine(cleaner(client, [], []), "ip", dry_run=True)
    assert outcome.result is None
    assert deleted_ids(outcome) == ["a", "b"]
    assert "delete_address" not in client.names()
    assert len(deleted_ids(outcome)) == TWO
