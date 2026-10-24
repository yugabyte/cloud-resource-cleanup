"""Parity: the Elastic IP Cleaner, run through `crc.core.engine`, against main's `ElasticIPs`
(`tests/legacy/aws_elastic_ips.txt`).

Both run against the same fake `boto3.client`; results, errors, stdout, describe and release
calls and log records must match for every case. The one allowed difference: the cleaner lists
every region before it releases, so a later listing error stops the run before any release
(fewer releases, never more). BUG-011 cases assert the fixed outcome exactly.
"""

import datetime
import logging
import types
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError

from crc.aws.elastic_ips import (
    ElasticIpCleaner,
    build_elastic_ip_cleaner,
)
from crc.core import engine
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import Registry, get
from crc.core.selection import Selection
from tests.helpers_wc_aws1_engine import deleted_ids, run_cleaner

ORACLE = Path(__file__).parent / "legacy" / "aws_elastic_ips.txt"
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical
TWO = 2


def tags(mapping: Mapping[str, str]) -> list[dict[str, str]]:
    return [{"Key": k, "Value": v} for k, v in mapping.items()]


def eip(ip: str, tag_list: Any = "absent", alloc: str | None = "", **extra: Any) -> dict[str, Any]:
    address: dict[str, Any] = {
        "PublicIp": ip,
        "AllocationId": f"eipalloc-{ip}" if alloc == "" else alloc,
    }
    if alloc is None:
        del address["AllocationId"]
    if not (isinstance(tag_list, str) and tag_list == "absent"):
        address["Tags"] = tag_list
    address.update(extra)
    return address


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
    filter_tags: Any = field(default_factory=dict)
    exception_tags: Any = field(default_factory=dict)
    notags: Any = field(default_factory=dict)
    dry_run: bool = False
    release_errors: Mapping[tuple[str, str], Exception] = field(default_factory=dict)
    regions_error: Exception | None = None
    fails_in_plan: bool = False
    diverges: str | None = None


ENV = tags({"env": "test", "owner": "qa"})
PROD = tags({"env": "prod"})


def _many(prefix: str, count: int) -> list[dict[str, Any]]:
    return [eip(f"{prefix}{i}", ENV) for i in range(count)]


CASES = [
    Case("dry-run", {"us-west-2": [eip("1.1.1.1", ENV), eip("2.2.2.2", PROD)]}, dry_run=True),
    Case("release", {"us-west-2": [eip("1.1.1.1", ENV)], "eu-west-1": [eip("3.3.3.3", ENV)]}),
    Case(
        "no-filter-includes-every-tagged",
        {"us-west-2": [eip("1.1.1.1", ENV), eip("2.2.2.2", PROD)]},
    ),
    Case("filter-none", {"us-west-2": [eip("1.1.1.1", ENV)]}, filter_tags=None),
    Case(
        "filter-key-only",
        {"us-west-2": [eip("1.1.1.1", ENV), eip("2.2.2.2", tags({"x": "y"}))]},
        filter_tags={"env": []},
    ),
    Case(
        "filter-key-and-value",
        {"us-west-2": [eip("1.1.1.1", ENV), eip("2.2.2.2", PROD)]},
        filter_tags={"env": ["prod", "stage"]},
    ),
    Case(
        "filter-any-key-matches",
        {"us-west-2": [eip("1.1.1.1", ENV), eip("2.2.2.2", PROD)]},
        filter_tags={"owner": ["qa"], "env": ["prod"]},
    ),
    Case(
        "filter-case-sensitive",
        {"us-west-2": [eip("1.1.1.1", tags({"env": "Test"}))]},
        filter_tags={"env": ["test"]},
    ),
    Case("filter-no-match", {"us-west-2": [eip("1.1.1.1", ENV)]}, filter_tags={"zzz": []}),
    Case("filter-value-no-match", {"us-west-2": [eip("1.1.1.1", ENV)]}, filter_tags={"env": ["x"]}),
    Case(
        "untagged-skipped",
        {"us-west-2": [eip("1.1.1.1"), eip("2.2.2.2", ENV)]},
    ),
    Case("untagged-with-filter", {"us-west-2": [eip("1.1.1.1")]}, filter_tags={"env": []}),
    Case("empty-tag-list-no-filter-included", {"us-west-2": [eip("1.1.1.1", [])]}),
    Case(
        "empty-tag-list-with-filter", {"us-west-2": [eip("1.1.1.1", [])]}, filter_tags={"env": []}
    ),
    Case(
        "associated-skipped",
        {"us-west-2": [eip("1.1.1.1", ENV, NetworkInterfaceId="eni-1"), eip("2.2.2.2", ENV)]},
    ),
    Case(
        "exception-key-only",
        {"us-west-2": [eip("1.1.1.1", ENV), eip("2.2.2.2", PROD)]},
        exception_tags={"owner": []},
    ),
    Case(
        "exception-key-and-value",
        {"us-west-2": [eip("1.1.1.1", ENV), eip("2.2.2.2", PROD)]},
        exception_tags={"env": ["prod"]},
    ),
    Case(
        "exception-wins-over-filter",
        {"us-west-2": [eip("1.1.1.1", ENV)]},
        filter_tags={"env": []},
        exception_tags={"owner": ["qa"]},
    ),
    Case(
        "exception-second-tag",
        {"us-west-2": [eip("1.1.1.1", tags({"a": "1", "keep": "yes"}))]},
        exception_tags={"keep": []},
    ),
    Case(
        "exception-value-mismatch",
        {"us-west-2": [eip("1.1.1.1", ENV)]},
        exception_tags={"env": ["x"]},
    ),
    Case("exception-none", {"us-west-2": [eip("1.1.1.1", ENV)]}, exception_tags=None),
    Case("exception-empty-tag-list", {"us-west-2": [eip("1.1.1.1", [])]}, exception_tags={"a": []}),
    Case(
        "notags-raises-type-error",
        {"us-west-2": [eip("1.1.1.1", ENV)]},
        notags={"env": []},
        diverges="BUG-011",
    ),
    Case(
        "notags-raises-in-first-region-skips-later-regions",
        {"us-west-2": [eip("1.1.1.1", ENV)], "eu-west-1": [eip("2.2.2.2", ENV)]},
        notags={"zzz": []},
        diverges="BUG-011",
    ),
    Case(
        "notags-dry-run-raises",
        {"us-west-2": [eip("1.1.1.1", ENV)]},
        notags={"env": []},
        dry_run=True,
        diverges="BUG-011",
    ),
    Case(
        "notags-exception-hit-on-first-tag-skips",
        {"us-west-2": [eip("1.1.1.1", ENV)]},
        notags={"env": []},
        exception_tags={"env": []},
    ),
    Case(
        "notags-exception-hit-on-second-tag-raises",
        {"us-west-2": [eip("1.1.1.1", ENV)]},
        notags={"env": []},
        exception_tags={"owner": []},
        diverges="BUG-011",
    ),
    Case(
        "notags-empty-tag-list-never-skips", {"us-west-2": [eip("1.1.1.1", [])]}, notags={"a": []}
    ),
    Case("notags-on-untagged-ignored", {"us-west-2": [eip("1.1.1.1")]}, notags={"a": []}),
    Case(
        "notags-on-associated-ignored",
        {"us-west-2": [eip("1.1.1.1", ENV, NetworkInterfaceId="e")]},
        notags={"a": []},
    ),
    Case("notags-none", {"us-west-2": [eip("1.1.1.1", ENV)]}, notags=None),
    Case(
        "duplicate-public-ip-last-allocation-wins",
        {"us-west-2": [eip("1.1.1.1", ENV, "a1"), eip("2.2.2.2", ENV), eip("1.1.1.1", ENV, "a2")]},
    ),
    Case(
        "duplicate-match-in-one-eip",
        {"us-west-2": [eip("1.1.1.1", ENV)]},
        filter_tags={"env": [], "owner": []},
    ),
    Case(
        "same-ip-two-regions",
        {"us-west-2": [eip("1.1.1.1", ENV)], "eu-west-1": [eip("1.1.1.1", ENV)]},
    ),
    Case("empty-region", {"us-west-2": [], "eu-west-1": []}),
    Case("no-regions", {}),
    Case("missing-addresses-key", {"us-west-2": {}}, fails_in_plan=True),
    Case(
        "describe-connection-error-skips-region",
        {"us-west-2": conn_error(), "eu-west-1": [eip("3.3.3.3", ENV)]},
    ),
    Case(
        "describe-timeout-skips-region",
        {
            "us-west-2": ConnectTimeoutError(endpoint_url="https://ec2.example"),
            "eu-west-1": [eip("3.3.3.3", ENV)],
        },
    ),
    Case(
        "describe-client-error-propagates",
        {"us-west-2": [eip("1.1.1.1", ENV)], "eu-west-1": client_error("DescribeAddresses")},
        fails_in_plan=True,
    ),
    Case("describe-unexpected-error", {"us-west-2": ValueError("boom")}, fails_in_plan=True),
    Case(
        "release-connection-error-mid-region-loses-record",
        {"us-west-2": _many("10.0.0.", 4), "eu-west-1": [eip("3.3.3.3", ENV)]},
        release_errors={("us-west-2", "eipalloc-10.0.0.2"): conn_error()},
    ),
    Case(
        "release-client-error-propagates",
        {"us-west-2": _many("10.0.0.", 4), "eu-west-1": [eip("3.3.3.3", ENV)]},
        release_errors={("us-west-2", "eipalloc-10.0.0.1"): client_error("ReleaseAddress")},
    ),
    Case(
        "release-error-in-dry-run-never-called",
        {"us-west-2": [eip("1.1.1.1", ENV)]},
        dry_run=True,
        release_errors={("us-west-2", "eipalloc-1.1.1.1"): client_error("ReleaseAddress")},
    ),
    Case("many-ips-order", {"us-west-2": _many("10.1.0.", 25), "eu-west-1": _many("10.2.0.", 9)}),
    Case("many-ips-dry-run", {"us-west-2": _many("10.1.0.", 25)}, dry_run=True),
    Case("missing-allocation-id", {"us-west-2": [eip("1.1.1.1", ENV, None)]}),
    Case(
        "missing-allocation-id-not-matching-filter",
        {"us-west-2": [eip("1.1.1.1", ENV, None)]},
        filter_tags={"zzz": []},
    ),
    Case(
        "missing-allocation-id-then-tag-without-key",
        {"us-west-2": [eip("1.1.1.1", [*ENV, {"Value": "v"}], None)]},
        filter_tags={"env": []},
    ),
    Case(
        "missing-allocation-id-then-tag-without-value",
        {"us-west-2": [eip("1.1.1.1", [*ENV, {"Key": "owner"}], None)]},
        filter_tags={"env": [], "owner": ["qa"]},
    ),
    Case("missing-public-ip", {"us-west-2": [{"AllocationId": "a", "Tags": ENV}]}),
    Case("tag-without-key-no-filter", {"us-west-2": [eip("1.1.1.1", [{"Value": "v"}])]}),
    Case(
        "tag-without-key-with-filter",
        {"us-west-2": [eip("1.1.1.1", [{"Value": "v"}])]},
        filter_tags={"a": []},
    ),
    Case(
        "tag-without-key-with-exception",
        {"us-west-2": [eip("1.1.1.1", [{"Value": "v"}])]},
        exception_tags={"a": []},
    ),
    Case("tags-none-no-filter-included", {"us-west-2": [eip("1.1.1.1", None)]}),
    Case(
        "tags-none-with-filter-raises", {"us-west-2": [eip("1.1.1.1", None)]}, filter_tags={"a": []}
    ),
    Case(
        "tag-key-without-value-key-only-filter",
        {"us-west-2": [eip("1.1.1.1", [{"Key": "a"}])]},
        filter_tags={"a": []},
    ),
    Case(
        "tag-key-without-value-value-filter-raises",
        {"us-west-2": [eip("1.1.1.1", [{"Key": "a"}])]},
        filter_tags={"a": ["v"]},
    ),
    Case(
        "tag-key-without-value-exception-value-filter-raises",
        {"us-west-2": [eip("1.1.1.1", [{"Key": "a"}])]},
        exception_tags={"a": ["v"]},
    ),
    Case(
        "describe-regions-client-error-propagates",
        {"us-west-2": [eip("1.1.1.1", ENV)]},
        regions_error=client_error("DescribeRegions"),
    ),
    Case(
        "describe-regions-connection-error-falls-back",
        {"us-west-2": [eip("1.1.1.1", ENV)]},
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

    def describe_addresses(self) -> Any:
        self.aws.calls.append(("describe_addresses", self.region))
        outcome = self.aws.case.regions[self.region]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome if isinstance(outcome, dict) else {"Addresses": outcome}

    def release_address(self, *, AllocationId: str) -> dict[str, Any]:
        self.aws.calls.append(("release_address", self.region, AllocationId))
        error = self.aws.case.release_errors.get((self.region, AllocationId))
        if error is not None:
            raise error
        return {"ResponseMetadata": {}}


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
    module = types.ModuleType("legacy_aws_elastic_ips")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    return module


def _legacy(case: Case) -> Any:
    return _load_oracle().ElasticIPs(
        case.dry_run, case.filter_tags, case.exception_tags, case.notags
    )


def _setup(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeAws:
    aws = FakeAws(case)
    monkeypatch.setattr("boto3.client", aws.client)
    monkeypatch.setattr("boto3.session.Session", aws.session)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    caplog.clear()
    capsys.readouterr()
    return aws


def _run(
    build: Callable[[Case], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    aws = _setup(case, monkeypatch, caplog, capsys)
    cleaner = build(case)
    error: tuple[str, str] | None = None
    try:
        cleaner.delete()
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    deleted = list(cleaner.get_deleted)
    count = cleaner.count
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(aws.calls, deleted, count, logs, capsys.readouterr().out, error)


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


BUG_011_OLD = ("TypeError", "'bool' object is not iterable")

_DESCRIBE_W2 = [
    ("client", "ec2", "us-west-2"),
    ("describe_regions", "us-west-2"),
    ("client", "ec2", "us-west-2"),
    ("describe_addresses", "us-west-2"),
]
_KEPT_OUTCOME = Outcome(
    calls=_DESCRIBE_W2,
    deleted=[],
    count=0,
    logs=[
        ("root", logging.INFO, "Retrieved list of regions: ['us-west-2']"),
        ("root", logging.WARNING, "number of AWS Elastic IPs deleted: 0"),
        ("root", logging.WARNING, "List of AWS Elastic IPs deleted: []"),
        ("root", logging.INFO, "count of items in deleted_ips: 0"),
    ],
    stdout="",
    error=None,
)

# BUG-011: notags no longer raises; exact new Outcome (calls, deleted, count, logs,
# stdout, error) for each diverging case (the oracle still raises the TypeError,
# asserted in `test_bug_011_notags_no_longer_raises`).
FIXED_MAIN: Mapping[str, Outcome] = {
    "notags-raises-type-error": _KEPT_OUTCOME,
    "notags-raises-in-first-region-skips-later-regions": Outcome(
        calls=[
            *_DESCRIBE_W2,
            ("release_address", "us-west-2", "eipalloc-1.1.1.1"),
            ("client", "ec2", "eu-west-1"),
            ("describe_addresses", "eu-west-1"),
            ("release_address", "eu-west-1", "eipalloc-2.2.2.2"),
        ],
        deleted=["1.1.1.1", "2.2.2.2"],
        count=TWO,
        logs=[
            ("root", logging.INFO, "Retrieved list of regions: ['us-west-2', 'eu-west-1']"),
            ("root", logging.INFO, "Deleted IP: 1.1.1.1"),
            ("root", logging.INFO, "Deleted IP: 2.2.2.2"),
            ("root", logging.WARNING, "number of AWS Elastic IPs deleted: 2"),
            (
                "root",
                logging.WARNING,
                "List of AWS Elastic IPs deleted: ['1.1.1.1', '2.2.2.2']",
            ),
            ("root", logging.INFO, "count of items in deleted_ips: 2"),
        ],
        stdout="",
        error=None,
    ),
    "notags-dry-run-raises": Outcome(
        calls=_DESCRIBE_W2,
        deleted=[],
        count=0,
        logs=[
            ("root", logging.INFO, "Retrieved list of regions: ['us-west-2']"),
            (
                "root",
                logging.WARNING,
                "List of AWS Elastic IPs (Total: 0) which will be deleted: []",
            ),
            ("root", logging.INFO, "count of items in deleted_ips: 0"),
        ],
        stdout="",
        error=None,
    ),
    "notags-exception-hit-on-second-tag-raises": _KEPT_OUTCOME,
}


_REGIONS_LOG = ("root", logging.INFO, "Retrieved list of regions: ['us-west-2']")
_KEPT_ENGINE = Outcome(
    calls=_DESCRIBE_W2,
    deleted=[],
    count=0,
    logs=[
        _REGIONS_LOG,
        ("root", logging.WARNING, "number of AWS Elastic IPs deleted: 0"),
        ("root", logging.WARNING, "List of AWS Elastic IPs deleted: []"),
    ],
    stdout="",
    error=None,
)

# The same BUG-011 outcomes through the engine: every region is listed before the first
# release, the adapter's trailing `count of items` line is gone.
ENGINE_FIXED: Mapping[str, Outcome] = {
    "notags-raises-type-error": _KEPT_ENGINE,
    "notags-exception-hit-on-second-tag-raises": _KEPT_ENGINE,
    "notags-dry-run-raises": Outcome(
        calls=_DESCRIBE_W2,
        deleted=[],
        count=0,
        logs=[
            _REGIONS_LOG,
            (
                "root",
                logging.WARNING,
                "List of AWS Elastic IPs (Total: 0) which will be deleted: []",
            ),
        ],
        stdout="",
        error=None,
    ),
    "notags-raises-in-first-region-skips-later-regions": Outcome(
        calls=[
            *_DESCRIBE_W2,
            ("client", "ec2", "eu-west-1"),
            ("describe_addresses", "eu-west-1"),
            ("client", "ec2", "us-west-2"),
            ("release_address", "us-west-2", "eipalloc-1.1.1.1"),
            ("client", "ec2", "eu-west-1"),
            ("release_address", "eu-west-1", "eipalloc-2.2.2.2"),
        ],
        deleted=["1.1.1.1", "2.2.2.2"],
        count=TWO,
        logs=[
            ("root", logging.INFO, "Retrieved list of regions: ['us-west-2', 'eu-west-1']"),
            ("root", logging.INFO, "Deleted IP: 1.1.1.1"),
            ("root", logging.INFO, "Deleted IP: 2.2.2.2"),
            ("root", logging.WARNING, "number of AWS Elastic IPs deleted: 2"),
            (
                "root",
                logging.WARNING,
                "List of AWS Elastic IPs deleted: ['1.1.1.1', '2.2.2.2']",
            ),
        ],
        stdout="",
        error=None,
    ),
}


def _run_cleaner(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    aws = _setup(case, monkeypatch, caplog, capsys)
    ctx = RunContext(
        dry_run=case.dry_run,
        operation=Operation.DELETE,
        options={
            "filter_tags": case.filter_tags,
            "exception_tags": case.exception_tags,
            "notags": case.notags,
        },
        now=NOW,
    )
    registry = Registry()
    registry.register("aws", "ip")(build_elastic_ip_cleaner)
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    try:
        (outcome,) = engine.run(Selection((("aws", "ip"),)), ctx, registry)
        deleted = deleted_ids(outcome)
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(aws.calls, deleted, len(deleted), logs, capsys.readouterr().out, error)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_engine_dry_run_never_releases(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dry = Case(**{**case.__dict__, "dry_run": True})
    old = _run(_legacy, dry, monkeypatch, caplog, capsys)
    new = _run_cleaner(dry, monkeypatch, caplog, capsys)
    assert not [c for c in new.calls if c[0] == "release_address"]
    assert not [c for c in old.calls if c[0] == "release_address"]
    if case.diverges:
        assert old.error == BUG_011_OLD
        return
    assert new.error == old.error
    assert new.stdout == old.stdout
    if new.error is None:
        assert new.deleted == old.deleted


def _without_client(calls: Sequence[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    return [c for c in calls if c[0] != "client"]


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
    if case.diverges:
        assert old.error == BUG_011_OLD
        old = FIXED_MAIN[case.id]
    assert new.error == old.error
    assert new.stdout == old.stdout
    old_releases = [c for c in old.calls if c[0] == "release_address"]
    new_releases = [c for c in new.calls if c[0] == "release_address"]
    if case.fails_in_plan:
        assert new_releases == []
        assert [c for c in new.calls if c[0] == "describe_addresses"] == [
            c for c in old.calls if c[0] == "describe_addresses"
        ]
        if len(case.regions) <= 1:
            assert new.logs == old.logs[:-1]
            assert _without_client(new.calls) == _without_client(old.calls)
        return
    if new.error is None:
        assert new.deleted == old.deleted
        assert [c for c in new.calls if c[0] == "describe_addresses"] == [
            c for c in old.calls if c[0] == "describe_addresses"
        ]
        assert new_releases == old_releases
        assert Counter(new.logs) == Counter(old.logs[:-1])
        if len(case.regions) <= 1:
            assert new.logs == old.logs[:-1]
            assert _without_client(new.calls) == _without_client(old.calls)
    else:
        assert new_releases == old_releases
        assert new.logs == old.logs[:-1]
        if len(case.regions) <= 1:
            assert _without_client(new.calls) == _without_client(old.calls)


def _find(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("notags-exception-hit-on-first-tag-skips", lambda o: o.error is None and o.deleted == []),
    ("notags-empty-tag-list-never-skips", lambda o: o.deleted == ["1.1.1.1"]),
    ("notags-on-untagged-ignored", lambda o: o.deleted == []),
    ("untagged-skipped", lambda o: o.deleted == ["2.2.2.2"]),
    ("empty-tag-list-no-filter-included", lambda o: o.deleted == ["1.1.1.1"]),
    ("tags-none-no-filter-included", lambda o: o.deleted == ["1.1.1.1"]),
    ("associated-skipped", lambda o: o.deleted == ["2.2.2.2"]),
    ("filter-any-key-matches", lambda o: o.deleted == ["1.1.1.1", "2.2.2.2"]),
    (
        "duplicate-public-ip-last-allocation-wins",
        lambda o: (
            ("release_address", "us-west-2", "a2") in o.calls
            and o.deleted == ["1.1.1.1", "2.2.2.2"]
        ),
    ),
    (
        "release-connection-error-mid-region-loses-record",
        lambda o: o.error is None and o.deleted == ["3.3.3.3"],
    ),
    ("release-client-error-propagates", lambda o: o.error is not None and o.deleted == []),
    ("describe-connection-error-skips-region", lambda o: o.deleted == ["3.3.3.3"]),
    ("describe-regions-client-error-propagates", lambda o: o.error is not None and o.deleted == []),
    ("describe-regions-connection-error-falls-back", lambda o: o.deleted == ["1.1.1.1"]),
    ("tag-without-key-with-filter", lambda o: o.error == ("KeyError", "'Key'")),
    ("tag-key-without-value-value-filter-raises", lambda o: o.error == ("KeyError", "'Value'")),
    (
        "tag-key-without-value-exception-value-filter-raises",
        lambda o: o.error == ("KeyError", "'Value'"),
    ),
    ("tags-none-with-filter-raises", lambda o: o.error is not None and o.error[0] == "TypeError"),
    ("missing-allocation-id", lambda o: o.error == ("KeyError", "'AllocationId'")),
    (
        "missing-allocation-id-then-tag-without-key",
        lambda o: o.error == ("KeyError", "'AllocationId'"),
    ),
    (
        "missing-allocation-id-then-tag-without-value",
        lambda o: o.error == ("KeyError", "'AllocationId'"),
    ),
    ("filter-case-sensitive", lambda o: o.deleted == []),
]


@pytest.mark.parametrize("case_id", sorted(FIXED_MAIN), ids=sorted(FIXED_MAIN))
def test_bug_011_notags_no_longer_raises(
    case_id: str,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find(case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    assert old.error == BUG_011_OLD
    assert new.error is None
    assert new == ENGINE_FIXED[case_id]


@pytest.mark.parametrize(("case_id", "check"), QUIRKS, ids=[q[0] for q in QUIRKS])
def test_quirk_is_preserved(
    case_id: str,
    check: Callable[[Outcome], bool],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find(case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    assert check(old), "oracle no longer shows the quirk"
    assert check(new)


SKIP_TAGS: Sequence[Any] = [
    ENV,
    [],
    PROD,
    tags({"owner": "qa", "env": "test"}),
    [{"Value": "v"}],
    [{"Key": "owner"}],
]
SKIP_SPECS: Sequence[tuple[Any, Any]] = [
    ({}, {}),
    (None, None),
    ({"env": []}, {}),
    ({"owner": ["qa"]}, {}),
    ({"owner": ["x"]}, {}),
    ({}, {"env": []}),
    ({"env": []}, {"env": []}),
]


# BUG-011: (SKIP_SPECS index, SKIP_TAGS index) -> new outcome where the oracle raises the
# TypeError. notags {"env": []} keeps (skips) an address that has an `env` tag; a tag
# without `Value` is read for notags and raises KeyError, as for AWS VMs.
_BUG_011_SKIP: Mapping[tuple[int, int], tuple[str, Any]] = {
    (5, 0): ("ok", True),
    (5, 2): ("ok", True),
    (5, 3): ("ok", True),
    (5, 5): ("KeyError", "'Value'"),
    (6, 3): ("ok", True),
    (6, 5): ("KeyError", "'Value'"),
}


def _skip_outcome(decide: Callable[[Any], bool], tag_list: Any) -> tuple[str, Any]:
    try:
        return ("ok", decide(tag_list))
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        return (type(e).__name__, str(e))


@pytest.mark.parametrize("tag_list", SKIP_TAGS, ids=range(len(SKIP_TAGS)))
@pytest.mark.parametrize("spec", SKIP_SPECS, ids=range(len(SKIP_SPECS)))
def test_should_skip_instance_matches_main(
    spec: tuple[Any, Any], tag_list: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    exception_tags, notags = spec
    old = _load_oracle().ElasticIPs(False, {}, exception_tags, notags)
    ctx = RunContext(
        dry_run=False,
        operation=Operation.DELETE,
        options={"filter_tags": {}, "exception_tags": exception_tags, "notags": notags},
        now=NOW,
    )
    new = build_elastic_ip_cleaner(ctx)
    assert isinstance(new, ElasticIpCleaner)
    old_outcome = _skip_outcome(old._should_skip_instance, tag_list)
    new_outcome = _skip_outcome(new.should_skip, tag_list)
    if old_outcome == BUG_011_OLD:
        assert notags
        assert new_outcome == _BUG_011_SKIP[(SKIP_SPECS.index(spec), SKIP_TAGS.index(tag_list))]
    else:
        assert new_outcome == old_outcome


def test_registered_factory_builds_cleaner() -> None:
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    cleaner: Cleaner = get("aws", "ip")(ctx)
    assert isinstance(cleaner, ElasticIpCleaner)
    assert get("aws", "ip") is build_elastic_ip_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("aws", "ip")


def test_runtime_injection_replaces_boto3(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    case = _find("release")
    aws = FakeAws(case)
    monkeypatch.setattr("boto3.client", lambda *a, **k: pytest.fail("boto3 used"))
    cleaner = ElasticIpCleaner(
        lambda region: FakeEc2(aws, region), lambda: list(case.regions), {}, {}, {}
    )
    outcome = run_cleaner("aws", "ip", cleaner)
    assert deleted_ids(outcome) == ["1.1.1.1", "3.3.3.3"]
