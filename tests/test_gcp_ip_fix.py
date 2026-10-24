"""BUG-001 (exception_regex excludes), BUG-063 (per-address delete errors are
recorded and the loop goes on), BUG-064 (wait for the delete operation). Fakes only."""

import datetime
import logging
import types
from collections.abc import Iterable, Sequence
from typing import Any

import pytest
from google.api_core.exceptions import PermissionDenied, ServiceUnavailable
from google.auth.exceptions import DefaultCredentialsError

from crc.core import logger
from crc.core.cleaner import Operation, RunContext
from crc.gcp.ip import AddressView, IpCleaner, OperationView
from tests.helpers_wc_gcp_engine import deleted_ids, run_engine

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical
PROJECT = "proj-1"
THREE = 3


def _make(kind: type[Exception], message: str) -> Exception:
    return kind(message)


def addr(name: str, status: str = "RESERVED") -> Any:
    return types.SimpleNamespace(name=name, status=status)


class Op:
    def __init__(self, calls: list[tuple[str, ...]], name: str, error: Exception | None) -> None:
        self.calls = calls
        self.name = name
        self.error = error

    def result(self) -> object:
        self.calls.append(("result", self.name))
        if self.error is not None:
            raise self.error
        return None


class Client:
    def __init__(
        self,
        regions: dict[str, list[Any]],
        start_errors: dict[str, Exception] | None = None,
        wait_errors: dict[str, Exception] | None = None,
    ) -> None:
        self.regions = regions
        self.start_errors = start_errors or {}
        self.wait_errors = wait_errors or {}
        self.calls: list[tuple[str, ...]] = []

    def list_regions(self, project_id: str) -> Sequence[str]:
        return list(self.regions)

    def list_addresses(self, project_id: str, region: str) -> Iterable[AddressView]:
        items: list[AddressView] = self.regions[region]
        return items

    def delete_address(self, project_id: str, region: str, address: str) -> OperationView:
        self.calls.append(("delete", address))
        if address in self.start_errors:
            raise self.start_errors[address]
        return Op(self.calls, address, self.wait_errors.get(address))


def ctx(*, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)


@pytest.mark.parametrize(
    ("filter_regex", "exception_regex", "expected"),
    [
        (None, ["keep"], ["ip-a", "ip-b"]),
        ([], ["keep", "ip-a"], ["ip-b"]),
        (["ip-"], ["ip-b"], ["ip-a"]),
        (["keep"], ["keep"], []),
        (None, ["zzz"], ["ip-a", "ip-b", "keep-c"]),
        (None, [], ["ip-a", "ip-b", "keep-c"]),
        (None, None, ["ip-a", "ip-b", "keep-c"]),
    ],
)
def test_bug_001_exception_regex_excludes_matching_names(
    filter_regex: list[str] | None, exception_regex: list[str] | None, expected: list[str]
) -> None:
    client = Client({"r": [addr("ip-a"), addr("ip-b"), addr("keep-c"), addr("keep-d", "IN_USE")]})
    plan = IpCleaner(client, PROJECT, filter_regex, exception_regex).plan(ctx())
    assert [c.id for c in plan.candidates] == expected
    assert client.calls == []


def test_bug_001_excluded_names_are_skipped_with_reason() -> None:
    client = Client({"r": [addr("ip-a"), addr("keep-c")]})
    plan = IpCleaner(client, PROJECT, None, ["keep"]).plan(ctx())
    assert [(s.id, s.reason) for s in plan.skipped] == [("keep-c", "exception_regex")]


@pytest.mark.parametrize(
    ("name_regex", "exception_regex", "expected"),
    [
        (["itest", "itests", "ui-auto"], None, ["itest-1", "ui-auto-2"]),
        (["itest", "itests", "ui-auto"], [], ["itest-1", "ui-auto-2"]),
        (["yb-stress-auto"], None, ["yb-stress-auto-1"]),
        (["yb-stress-auto"], [], ["yb-stress-auto-1"]),
    ],
)
def test_bug_001_production_selection_without_exception_regex_is_unchanged(
    name_regex: list[str], exception_regex: list[str] | None, expected: list[str]
) -> None:
    client = Client(
        {
            "r": [
                addr("itest-1"),
                addr("ui-auto-2"),
                addr("yb-stress-auto-1"),
                addr("other"),
                addr("itest-3", "IN_USE"),
            ]
        }
    )
    plan = IpCleaner(client, PROJECT, name_regex, exception_regex).plan(ctx())
    assert [c.id for c in plan.candidates] == expected


def test_bug_064_operation_is_awaited_before_the_name_is_recorded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    client = Client({"r": [addr("a"), addr("b")]})
    cleaner = IpCleaner(client, PROJECT, None, None)
    result = cleaner.apply(cleaner.plan(ctx()), ctx())
    assert client.calls == [("delete", "a"), ("result", "a"), ("delete", "b"), ("result", "b")]
    assert result.succeeded == ("a", "b")
    assert result.failed == ()


def test_bug_064_operation_failure_is_not_recorded_as_deleted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    client = Client(
        {"r": [addr("a"), addr("b"), addr("c")]},
        wait_errors={"b": _make(ServiceUnavailable, "operation failed")},
    )
    cleaner = IpCleaner(client, PROJECT, None, None)
    result = cleaner.apply(cleaner.plan(ctx()), ctx())
    assert result.succeeded == ("a", "c")
    assert [(f.id, f.error) for f in result.failed] == [("b", "503 operation failed")]
    messages = [r.getMessage() for r in caplog.records]
    assert "Deleting IP address: b" not in messages
    assert "Deleting IP address: a" in messages
    assert "List of GCP IPs deleted: ['a', 'c']" in messages


def test_bug_064_operation_timeout_is_recorded_and_the_loop_continues() -> None:
    client = Client({"r": [addr("a"), addr("b")]}, wait_errors={"a": TimeoutError("too slow")})
    cleaner = IpCleaner(client, PROJECT, None, None)
    result = cleaner.apply(cleaner.plan(ctx()), ctx())
    assert result.succeeded == ("b",)
    assert [f.id for f in result.failed] == ["a"]


def test_bug_063_start_error_is_recorded_in_the_run_log_and_the_loop_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    client = Client(
        {"r1": [addr("a"), addr("b")], "r2": [addr("c")]},
        start_errors={"a": _make(PermissionDenied, "denied")},
    )
    cleaner = IpCleaner(client, PROJECT, None, None)
    logger.begin("gcp", "ip", "delete", False, {}, clock=lambda: 0.0)
    result = cleaner.apply(cleaner.plan(ctx()), ctx())
    logger.finish()
    assert result.succeeded == ("b", "c")
    assert [(f.id, f.error) for f in result.failed] == [("a", "403 denied")]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == [
        "Error occurred while deleting IP address a: 403 denied",
        "ERRORS count=1",
        "FAILED cloud=gcp resource=ip region=r1 id=a error=PermissionDenied: 403 denied",
    ]
    assert ("delete", "c") in client.calls


def test_bug_063_deleted_addresses_stay_recorded_when_a_later_one_fails(
    quiet: None,
) -> None:
    client = Client(
        {"r1": [addr("a"), addr("b"), addr("c")], "r2": [addr("d")]},
        start_errors={"c": _make(ServiceUnavailable, "unreachable")},
    )
    deleted = deleted_ids(run_engine(IpCleaner(client, PROJECT, [], []), "ip"))
    assert deleted == ["a", "b", "d"]
    assert len(deleted) == THREE


def test_bug_063_summary_is_logged_after_a_failure(
    quiet: None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    client = Client(
        {"r": [addr("a"), addr("b")]}, start_errors={"a": _make(PermissionDenied, "denied")}
    )
    run_engine(IpCleaner(client, PROJECT, [], []), "ip")
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING] == [
        "number of GCP IPs deleted: 1",
        "List of GCP IPs deleted: ['b']",
    ]


def test_bug_063_dry_run_never_calls_delete(quiet: None) -> None:
    client = Client({"r": [addr("a")]}, start_errors={"a": _make(PermissionDenied, "denied")})
    outcome = run_engine(IpCleaner(client, PROJECT, [], []), "ip", dry_run=True)
    assert deleted_ids(outcome) == ["a"]
    assert client.calls == []


def test_bug_063_credentials_errors_are_recorded_per_address() -> None:
    client = Client(
        {"r": [addr("a"), addr("b")]},
        start_errors={"a": _make(DefaultCredentialsError, "no credentials")},
    )
    cleaner = IpCleaner(client, PROJECT, None, None)
    result = cleaner.apply(cleaner.plan(ctx()), ctx())
    assert result.succeeded == ("b",)
    assert [f.id for f in result.failed] == ["a"]


def test_programming_errors_still_propagate() -> None:
    client = Client({"r": [addr("a")]}, start_errors={"a": RuntimeError("bug")})
    cleaner = IpCleaner(client, PROJECT, None, None)
    with pytest.raises(RuntimeError, match="bug"):
        cleaner.apply(cleaner.plan(ctx()), ctx())
