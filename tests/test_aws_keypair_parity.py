"""Parity: `crc.aws.keypairs.KeyPairs` against main's version (`tests/legacy/aws_keypairs.txt`).

Both run against the same fake `boto3.client`; SDK calls (in order), results, log records,
stdout and raised errors must be identical for every case.
"""

import datetime
import logging
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError

from crc.aws.keypairs import KeyPairRuntime, KeyPairs, build_keypair_cleaner
from crc.core.cleaner import Operation, RunContext
from tests.helpers_log_fix import expected_logs
from tests.log_fix_aws_misc import RULES

ORACLE = Path(__file__).parent / "legacy" / "aws_keypairs.txt"
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)
NOW_UTC = NOW.astimezone(datetime.UTC)
TWO = 2


def kp(name: str | None, hours: float | None = 100, **extra: Any) -> dict[str, Any]:
    pair: dict[str, Any] = dict(extra)
    if name is not None:
        pair["KeyName"] = name
    if hours is not None:
        pair["CreateTime"] = NOW_UTC - datetime.timedelta(hours=hours)
    return pair


def conn_error() -> Exception:
    error: Exception = EndpointConnectionError(endpoint_url="https://ec2.example")
    return error


def client_error(operation: str) -> Exception:
    error: Exception = ClientError(
        {"Error": {"Code": "AuthFailure", "Message": "denied"}}, operation
    )
    return error


@dataclass
class Case:
    id: str
    regions: Mapping[str, Any]
    name_regex: Any = field(default_factory=list)
    exception_regex: Any = field(default_factory=list)
    age: Any = field(default_factory=lambda: {"hours": 5})
    dry_run: bool = False
    delete_errors: Mapping[tuple[str, str], Exception] = field(default_factory=dict)
    max_age: str | None = None
    regions_error: Exception | None = None


def _many(prefix: str, count: int) -> list[dict[str, Any]]:
    return [kp(f"{prefix}{i}", 50 + i) for i in range(count)]


CASES = [
    Case("dry-run", {"us-west-2": [kp("a"), kp("b", 1)]}, dry_run=True),
    Case("delete", {"us-west-2": [kp("a"), kp("b", 1)], "eu-west-1": [kp("c")]}),
    Case("empty-name-regex-deletes-all-old", {"us-west-2": [kp("x"), kp("y"), kp("z", 2)]}),
    Case("name-regex-none", {"us-west-2": [kp("a")]}, name_regex=None),
    Case(
        "name-regex-search",
        {"us-west-2": [kp("perftest_1"), kp("qa_2"), kp("prod")]},
        name_regex=["perf", "^qa_"],
    ),
    Case("name-regex-no-match", {"us-west-2": [kp("a")]}, name_regex=["zzz"]),
    Case("name-regex-case-sensitive", {"us-west-2": [kp("Perf")]}, name_regex=["perf"]),
    Case("exception-regex", {"us-west-2": [kp("keep_me"), kp("drop")]}, exception_regex=["keep"]),
    Case("exception-regex-none", {"us-west-2": [kp("a")]}, exception_regex=None),
    Case(
        "exception-wins-over-name",
        {"us-west-2": [kp("qa_keep")]},
        name_regex=["qa_"],
        exception_regex=["keep"],
    ),
    Case(
        "exception-checked-when-name-misses",
        {"us-west-2": [kp("a")]},
        name_regex=["zzz"],
        exception_regex=["a"],
    ),
    Case(
        "exception-duplicates",
        {"us-west-2": [kp("ab"), kp("cd")]},
        exception_regex=["a", "a", "b", "z"],
    ),
    Case("invalid-name-regex", {"us-west-2": [kp("a")]}, name_regex=["("]),
    Case("invalid-exception-regex", {"us-west-2": [kp("a")]}, exception_regex=["("]),
    Case(
        "invalid-regex-after-earlier-region-deleted",
        {"us-west-2": [kp("a")], "eu-west-1": [kp("b")]},
        name_regex=["a", "[b"],
    ),
    Case("untagged-missing-keys", {"us-west-2": [kp(None), kp("n", None), {}, kp("ok")]}),
    Case("untagged-only-missing-keys", {"us-west-2": [kp(None), kp("n", None)]}),
    Case("age-equal-hours", {"us-west-2": [kp("a", 5)]}),
    Case("age-just-under-hours", {"us-west-2": [kp("a", 4.99)]}),
    Case("age-just-over-hours", {"us-west-2": [kp("a", 5.01)]}),
    Case("age-days", {"us-west-2": [kp("a", 48), kp("b", 47.9)]}, age={"days": 2}),
    Case(
        "age-days-and-hours",
        {"us-west-2": [kp("a", 30), kp("b", 29.9)]},
        age={"days": 1, "hours": 6},
    ),
    Case("age-int-days", {"us-west-2": [kp("a", 25), kp("b", 23)]}, age=1),
    Case("age-empty-matches-all", {"us-west-2": [kp("a", 0.1)]}, age={}),
    Case("age-none-matches-all", {"us-west-2": [kp("a", 0.1)]}, age=None),
    Case("age-true-is-one-day", {"us-west-2": [kp("a", 25), kp("b", 1)]}, age=True),
    Case("age-unknown-keys-keep-all", {"us-west-2": [kp("a", 9999)]}, age={"weeks": 1}),
    Case(
        "max-age-shortens",
        {"us-west-2": [kp("a", 3), kp("b", 1)]},
        age={"days": 30},
        max_age="{'hours': 2}",
    ),
    Case("max-age-empty-dict", {"us-west-2": [kp("a", 3)]}, age={"days": 1}, max_age="{}"),
    Case("max-age-not-a-dict", {"us-west-2": [kp("a", 3)]}, age={"days": 1}, max_age="2"),
    Case("create-time-in-the-future", {"us-west-2": [kp("a", -10)]}),
    Case("naive-create-time", {"us-west-2": [{"KeyName": "a", "CreateTime": NOW}]}),
    Case("empty-region", {"us-west-2": [], "eu-west-1": []}),
    Case("no-regions", {}),
    Case("missing-keypairs-key", {"us-west-2": {}}),
    Case(
        "describe-connection-error-skips-region",
        {"us-west-2": conn_error(), "eu-west-1": [kp("c")]},
    ),
    Case(
        "describe-timeout-skips-region",
        {
            "us-west-2": ConnectTimeoutError(endpoint_url="https://ec2.example"),
            "eu-west-1": [kp("c")],
        },
    ),
    Case(
        "describe-client-error-propagates",
        {"us-west-2": [kp("a")], "eu-west-1": client_error("DescribeKeyPairs")},
    ),
    Case("describe-unexpected-error", {"us-west-2": ValueError("boom")}),
    Case(
        "delete-connection-error-mid-region",
        {"us-west-2": _many("k", 4), "eu-west-1": [kp("c")]},
        delete_errors={("us-west-2", "k2"): conn_error()},
    ),
    Case(
        "delete-client-error-propagates",
        {"us-west-2": _many("k", 4), "eu-west-1": [kp("c")]},
        delete_errors={("us-west-2", "k1"): client_error("DeleteKeyPair")},
    ),
    Case(
        "delete-error-in-dry-run-never-called",
        {"us-west-2": [kp("a")]},
        dry_run=True,
        delete_errors={("us-west-2", "a"): client_error("DeleteKeyPair")},
    ),
    Case("duplicate-names-in-region", {"us-west-2": [kp("a"), kp("a", 200), kp("b")]}),
    Case("same-name-two-regions", {"us-west-2": [kp("a")], "eu-west-1": [kp("a")]}),
    Case("many-keys-set-order", {"us-west-2": _many("key-", 25), "eu-west-1": _many("kk-", 9)}),
    Case("many-keys-dry-run", {"us-west-2": _many("key-", 25)}, dry_run=True),
    Case(
        "describe-regions-client-error-propagates",
        {"us-west-2": [kp("a")]},
        regions_error=client_error("DescribeRegions"),
    ),
    Case(
        "describe-regions-connection-error-falls-back",
        {"us-west-2": [kp("a")]},
        regions_error=conn_error(),
    ),
]


class FakeEc2:
    def __init__(self, aws: "FakeAws", region: str) -> None:
        self.aws = aws
        self.region = region

    def describe_regions(self) -> dict[str, Any]:
        self.aws.calls.append(("describe_regions", self.region))
        if self.aws.case.regions_error is not None:
            raise self.aws.case.regions_error
        return {"Regions": [{"RegionName": r} for r in self.aws.case.regions]}

    def describe_key_pairs(self) -> Any:
        self.aws.calls.append(("describe_key_pairs", self.region))
        outcome = self.aws.case.regions[self.region]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome if isinstance(outcome, dict) else {"KeyPairs": outcome}

    def delete_key_pair(self, *, KeyName: str) -> dict[str, Any]:
        self.aws.calls.append(("delete_key_pair", self.region, KeyName))
        error = self.aws.case.delete_errors.get((self.region, KeyName))
        if error is not None:
            raise error
        return {"Return": True, "KeyPairId": f"key-{self.region}-{KeyName}"}


class FakeAws:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []

    def client(self, service: str, region_name: str | None = None) -> FakeEc2:
        self.calls.append(("client", service, region_name))
        assert region_name is not None
        return FakeEc2(self, region_name)

    def session(self) -> Any:
        self.calls.append(("session",))
        return types.SimpleNamespace(get_available_regions=lambda service: list(self.case.regions))


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[str] | None
    count: int | None
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_aws_keypairs")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    module.__dict__["datetime"] = types.SimpleNamespace(
        datetime=types.SimpleNamespace(now=lambda: NOW)
    )
    return module


def _legacy(case: Case) -> Any:
    return _load_oracle().KeyPairs(case.dry_run, case.name_regex, case.exception_regex, case.age)


def _new(case: Case) -> Any:
    runtime = KeyPairRuntime(clock=lambda: NOW)
    return KeyPairs(case.dry_run, case.name_regex, case.exception_regex, case.age, runtime)


def _run(
    build: Callable[[Case], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    aws = FakeAws(case)
    monkeypatch.setattr("boto3.client", aws.client)
    monkeypatch.setattr("boto3.session.Session", aws.session)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    if case.max_age is None:
        monkeypatch.delenv("MAX_AGE", raising=False)
    else:
        monkeypatch.setenv("MAX_AGE", case.max_age)
    caplog.clear()
    capsys.readouterr()
    cleaner = build(case)
    error: tuple[str, str] | None = None
    try:
        cleaner.delete()
    except Exception as e:
        error = (type(e).__name__, str(e))
    deleted = list(cleaner.get_deleted)
    count = cleaner.count
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(aws.calls, deleted, count, logs, capsys.readouterr().out, error)


LOG_BUGS = {"BUG-356"}


@pytest.fixture(autouse=False)
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert new.calls == old.calls
    assert new.error == old.error
    assert new.deleted == old.deleted
    assert new.count == old.count
    assert new.logs == expected_logs(old.logs, LOG_BUGS, rules=RULES)
    assert new.stdout == old.stdout


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("empty-name-regex-deletes-all-old", lambda o: o.deleted is not None and len(o.deleted) == TWO),
    ("untagged-missing-keys", lambda o: o.deleted == ["ok"]),
    ("age-true-is-one-day", lambda o: o.deleted == ["a"]),
    ("age-unknown-keys-keep-all", lambda o: o.deleted == []),
    ("max-age-empty-dict", lambda o: o.deleted == []),
    ("max-age-shortens", lambda o: o.deleted == ["a"]),
    ("age-empty-matches-all", lambda o: o.deleted == ["a"]),
    ("invalid-name-regex", lambda o: o.error is not None and o.error[0] == "error"),
    ("max-age-not-a-dict", lambda o: o.error is not None),
    ("naive-create-time", lambda o: o.error is not None and o.error[0] == "TypeError"),
    (
        "delete-client-error-propagates",
        lambda o: o.error is not None and "k1" not in (o.deleted or []),
    ),
    (
        "delete-connection-error-mid-region",
        lambda o: o.error is None and "k2" not in (o.deleted or []),
    ),
    (
        "invalid-regex-after-earlier-region-deleted",
        lambda o: o.error is not None and o.deleted == ["a"],
    ),
    ("describe-connection-error-skips-region", lambda o: o.deleted == ["c"]),
    ("describe-regions-client-error-propagates", lambda o: o.error is not None and o.deleted == []),
    ("describe-regions-connection-error-falls-back", lambda o: o.deleted == ["a"]),
    ("exception-checked-when-name-misses", lambda o: o.deleted == []),
    ("duplicate-names-in-region", lambda o: sorted(o.deleted or []) == ["a", "b"]),
]


def _run_cleaner(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    aws = FakeAws(case)
    monkeypatch.setattr("boto3.client", aws.client)
    monkeypatch.setattr("boto3.session.Session", aws.session)
    if case.max_age is None:
        monkeypatch.delenv("MAX_AGE", raising=False)
    else:
        monkeypatch.setenv("MAX_AGE", case.max_age)
    caplog.clear()
    capsys.readouterr()
    ctx = RunContext(
        dry_run=case.dry_run,
        operation=Operation.DELETE,
        options={
            "name_regex": case.name_regex,
            "exception_regex": case.exception_regex,
            "age": case.age,
        },
        now=NOW,
    )
    cleaner = build_keypair_cleaner(ctx)
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    try:
        plan = cleaner.plan(ctx)
        if case.dry_run:
            deleted = [c.id for c in plan.candidates]
        else:
            deleted = list(cleaner.apply(plan, ctx).succeeded)
    except Exception as e:
        error = (type(e).__name__, str(e))
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(aws.calls, deleted, len(deleted), logs, capsys.readouterr().out, error)


_INTERLEAVED_ERROR_CASES = frozenset(
    {
        "describe-client-error-propagates",
        "invalid-regex-after-earlier-region-deleted",
    }
)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_cleaner_protocol_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    assert new.error == old.error
    assert new.stdout == old.stdout
    if case.id in _INTERLEAVED_ERROR_CASES:
        assert [c for c in new.calls if c[0] == "delete_key_pair"] == []
        return
    if new.error is None:
        assert new.deleted == old.deleted
    if len(case.regions) <= 1 and new.error is None:
        assert new.logs == expected_logs(old.logs[:-1], LOG_BUGS, rules=RULES)
        assert [c for c in new.calls if c[0] != "client"] == [
            c for c in old.calls if c[0] != "client"
        ]


@pytest.mark.parametrize(("case_id", "check"), QUIRKS, ids=[q[0] for q in QUIRKS])
def test_quirk_is_preserved(
    case_id: str,
    check: Callable[[Outcome], bool],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = next(c for c in CASES if c.id == case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert check(old), "oracle no longer shows the quirk"
    assert check(new)


@pytest.mark.parametrize("case_id", ["exception-regex", "age-days"])
def test_per_keypair_skip_lines_are_debug(
    case_id: str,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """BUG-356: the oracle logs the per-keypair skip lines at INFO, the new code at DEBUG."""
    case = next(c for c in CASES if c.id == case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert old.logs != new.logs
    assert new.logs == expected_logs(old.logs, LOG_BUGS, rules=RULES)
    skip_lines = [r for r in new.logs if "exception_regex" in r[2] or "not old enough" in r[2]]
    assert skip_lines
    assert {r[1] for r in skip_lines} == {logging.DEBUG}
    assert {r[1] for r in old.logs if r[2].startswith("Keypair ")} == {logging.INFO}
