"""Parity: the snapshot Cleaner, run through `crc.core.engine`, against main's `Snapshot`
(`tests/legacy/aws_snapshot.txt`).

Both run against the same fake `boto3.client`; SDK calls, results, log records, stdout and raised
errors must match for every case. The one allowed difference: the Cleaner lists every region
before it deletes, so only the interleaving of delete calls and delete log lines with the listing
differs from main, and a listing error stops the run before any delete.
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

from crc.aws.snapshot import SnapshotCleaner, build_snapshot_cleaner
from crc.aws.snapshot.protection import used_by_amis
from crc.core import registry
from crc.core.cleaner import Operation, RunContext
from tests.helpers_log_fix import expected_logs, expected_outcome
from tests.helpers_wc_aws2_engine import run_through_engine
from tests.log_fix_aws_misc import RULES

ORACLE = Path(__file__).parent / "legacy" / "aws_snapshot.txt"
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
TWO = 2
DELETE_PREFIXES = ("Deleted snapshot", "Failed to delete snapshot")


def snap(
    snap_id: str | None,
    days: float | None = 100,
    tags: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    item: dict[str, Any] = dict(extra)
    if snap_id is not None:
        item["SnapshotId"] = snap_id
    if days is not None:
        item["StartTime"] = NOW - datetime.timedelta(days=days)
    if tags is not None:
        item["Tags"] = tags
    return item


def tag(key: str, value: str = "") -> dict[str, str]:
    return {"Key": key, "Value": value}


def image(*snap_ids: str | None, **extra: Any) -> dict[str, Any]:
    mappings: list[dict[str, Any]] = [{"Ebs": {"SnapshotId": s}} for s in snap_ids]
    return {"BlockDeviceMappings": mappings, **extra}


def conn_error() -> Exception:
    error: Exception = EndpointConnectionError(endpoint_url="https://ec2.example")
    return error


def client_error(operation: str) -> Exception:
    error: Exception = ClientError(
        {"Error": {"Code": "AuthFailure", "Message": "denied"}}, operation
    )
    return error


@dataclass
class Region:
    snaps: Any = field(default_factory=list)
    images: Any = field(default_factory=list)


@dataclass
class Case:
    id: str
    regions: Mapping[str, Region]
    age_days: Any = 90
    exception_tags: Any = None
    dry_run: bool = False
    delete_errors: Mapping[tuple[str, str], Exception] = field(default_factory=dict)
    regions_error: Exception | None = None


def _many(prefix: str, count: int) -> list[dict[str, Any]]:
    return [snap(f"{prefix}{i}", 100 + i) for i in range(count)]


def one(*snaps: dict[str, Any], images: Any = None, region: str = "us-west-2") -> dict[str, Region]:
    return {region: Region(list(snaps), images if images is not None else [])}


CASES = [
    Case("dry-run", one(snap("a"), snap("b", 1)), dry_run=True),
    Case(
        "delete",
        {"us-west-2": Region([snap("a"), snap("b", 1)]), "eu-west-1": Region([snap("c")])},
    ),
    Case("age-equal-cutoff", one(snap("a", 90))),
    Case("age-just-over", one(snap("a", 90.00001))),
    Case("age-just-under", one(snap("a", 89.99999))),
    Case("age-future-start", one(snap("a", -3))),
    Case("age-days-zero-deletes-all-past", one(snap("a", 0.001), snap("b", -0.001)), age_days=0),
    Case("age-days-negative", one(snap("a", -0.5), snap("b", -2)), age_days=-1),
    Case("age-days-float", one(snap("a", 2), snap("b", 1)), age_days=1.5),
    Case("age-days-true", one(snap("a", 2), snap("b", 0.5)), age_days=True),
    Case("age-days-none-fails-before-any-call", one(snap("a")), age_days=None),
    Case("age-days-string", one(snap("a")), age_days="90"),
    Case("age-days-overflow", one(snap("a")), age_days=10**12),
    Case("naive-start-time", one({"SnapshotId": "a", "StartTime": datetime.datetime(2020, 1, 1)})),  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical
    Case("start-time-string", one({"SnapshotId": "a", "StartTime": "2020-01-01"})),
    Case("missing-start-time", one(snap("a"), snap("b", None))),
    Case("missing-snapshot-id", one(snap("a"), snap(None), snap("c"))),
    Case("missing-snapshots-key", {"us-west-2": Region({})}),
    Case("short-age-keeps-young", one(snap("a", 5), snap("b", 20)), age_days=10),
    Case(
        "exception-key-only",
        one(snap("a", tags=[tag("keep")]), snap("b", tags=[tag("other")])),
        exception_tags={"keep": []},
    ),
    Case(
        "exception-value-match",
        one(snap("a", tags=[tag("env", "prod")]), snap("b", tags=[tag("env", "dev")])),
        exception_tags={"env": ["prod"]},
    ),
    Case(
        "exception-value-case-sensitive",
        one(snap("a", tags=[tag("env", "PROD")])),
        exception_tags={"env": ["prod"]},
    ),
    Case(
        "exception-values-none-means-any",
        one(snap("a", tags=[tag("env", "x")])),
        exception_tags={"env": None},
    ),
    Case(
        "exception-value-is-string-substring",
        one(snap("a", tags=[tag("env", "pro")]), snap("b", tags=[tag("env", "zzz")])),
        exception_tags={"env": "production"},
    ),
    Case(
        "exception-first-key-logged",
        one(snap("a", tags=[tag("b"), tag("a")])),
        exception_tags={"a": [], "b": []},
    ),
    Case(
        "exception-second-key-logged",
        one(snap("a", tags=[tag("b")])),
        exception_tags={"a": [], "b": []},
    ),
    Case("exception-tags-none", one(snap("a", tags=[tag("keep")])), exception_tags=None),
    Case("exception-tags-empty", one(snap("a", tags=[tag("keep")])), exception_tags={}),
    Case("untagged-deleted", one(snap("a"), snap("b", tags=[])), exception_tags={"keep": []}),
    Case("tags-none", one({**snap("a"), "Tags": None}), exception_tags={"keep": []}),
    Case(
        "tag-missing-value-raises",
        one(snap("a", tags=[{"Key": "k"}])),
        exception_tags={"keep": []},
    ),
    Case(
        "tag-missing-key-raises",
        one(snap("a", tags=[{"Value": "v"}])),
        exception_tags={"keep": []},
    ),
    Case(
        "tag-malformed-ignored-without-exception-tags",
        one(snap("a", tags=[{"Key": "k"}])),
    ),
    Case(
        "tag-malformed-on-young-snapshot-not-read",
        one(snap("a", 1, tags=[{"Key": "k"}])),
        exception_tags={"keep": []},
    ),
    Case(
        "tag-malformed-after-deleted-snapshot",
        one(snap("a"), snap("b", tags=[{"Key": "k"}]), snap("c")),
        exception_tags={"keep": []},
    ),
    Case("tags-is-dict", one(snap("a", tags={"keep": "x"})), exception_tags={"keep": []}),
    Case("exception-tags-not-a-dict", one(snap("a", tags=[tag("keep")])), exception_tags=["keep"]),
    Case(
        "exception-tags-duplicate-tag-last-wins",
        one(snap("a", tags=[tag("env", "prod"), tag("env", "dev")])),
        exception_tags={"env": ["prod"]},
    ),
    Case("ami-uses-snapshot", one(snap("a"), snap("b"), images=[image("a")])),
    Case("ami-two-snapshots", one(snap("a"), snap("b"), images=[image("a", "b")])),
    Case(
        "ami-in-other-region-not-used",
        {"us-west-2": Region([snap("a")]), "eu-west-1": Region([], [image("a")])},
    ),
    Case("ami-no-block-device-mappings", one(snap("a"), images=[{"ImageId": "ami-1"}])),
    Case(
        "ami-ebs-missing", one(snap("a"), images=[{"BlockDeviceMappings": [{"DeviceName": "x"}]}])
    ),
    Case("ami-snapshot-id-none", one(snap("a"), images=[image(None)])),
    Case("ami-snapshot-id-empty", one(snap("a"), images=[image("")])),
    Case("ami-ebs-none-raises", one(snap("a"), images=[{"BlockDeviceMappings": [{"Ebs": None}]}])),
    Case("ami-missing-images-key", {"us-west-2": Region([snap("a")], {})}),
    Case(
        "ami-and-protected-logs-protection-only",
        one(snap("a", tags=[tag("keep")]), images=[image("a")]),
        exception_tags={"keep": []},
    ),
    Case("ami-young-snapshot-not-logged", one(snap("a", 1), images=[image("a")])),
    Case("empty-region", {"us-west-2": Region(), "eu-west-1": Region()}),
    Case("no-regions", {}),
    Case(
        "describe-snapshots-connection-error-skips-region",
        {"us-west-2": Region(conn_error()), "eu-west-1": Region([snap("c")])},
    ),
    Case(
        "describe-snapshots-timeout-skips-region",
        {
            "us-west-2": Region(ConnectTimeoutError(endpoint_url="https://ec2.example")),
            "eu-west-1": Region([snap("c")]),
        },
    ),
    Case(
        "describe-images-connection-error-skips-region",
        {"us-west-2": Region([snap("a")], conn_error()), "eu-west-1": Region([snap("c")])},
    ),
    Case(
        "describe-images-after-earlier-region-deleted",
        {"us-west-2": Region([snap("a")]), "eu-west-1": Region([snap("b")], conn_error())},
    ),
    Case(
        "describe-snapshots-client-error-propagates",
        {"us-west-2": Region([snap("a")]), "eu-west-1": Region(client_error("DescribeSnapshots"))},
    ),
    Case(
        "describe-images-client-error-propagates",
        {
            "us-west-2": Region([snap("a")]),
            "eu-west-1": Region([snap("b")], client_error("DescribeImages")),
            "ap-south-1": Region([snap("c")]),
        },
    ),
    Case("describe-unexpected-error", {"us-west-2": Region(ValueError("boom"))}),
    Case(
        "delete-client-error-swallowed",
        one(*_many("s", 4)),
        delete_errors={("us-west-2", "s1"): client_error("DeleteSnapshot")},
    ),
    Case(
        "delete-connection-error-swallowed-not-skipped",
        {"us-west-2": Region(_many("s", 4)), "eu-west-1": Region([snap("c")])},
        delete_errors={("us-west-2", "s2"): conn_error()},
    ),
    Case(
        "delete-error-on-every-snapshot",
        one(*_many("s", 3)),
        delete_errors={("us-west-2", f"s{i}"): ValueError(f"no {i}") for i in range(3)},
    ),
    Case(
        "delete-error-in-dry-run-never-called",
        one(snap("a")),
        dry_run=True,
        delete_errors={("us-west-2", "a"): client_error("DeleteSnapshot")},
    ),
    Case("duplicate-ids-in-region", one(snap("a"), snap("a", 200), snap("b"))),
    Case(
        "same-id-two-regions", {"us-west-2": Region([snap("a")]), "eu-west-1": Region([snap("a")])}
    ),
    Case(
        "many-snapshots",
        {"us-west-2": Region(_many("snap-", 25)), "eu-west-1": Region(_many("x-", 9))},
    ),
    Case("many-snapshots-dry-run", one(*_many("snap-", 25)), dry_run=True),
    Case(
        "mixed-decisions",
        one(
            snap("old"),
            snap("young", 1),
            snap("protected", tags=[tag("keep")]),
            snap("ami"),
            snap("old2", 91),
            images=[image("ami")],
        ),
        exception_tags={"keep": []},
    ),
    Case(
        "describe-regions-client-error-propagates",
        one(snap("a")),
        regions_error=client_error("DescribeRegions"),
    ),
    Case(
        "describe-regions-connection-error-falls-back",
        one(snap("a")),
        regions_error=conn_error(),
    ),
]


DIVERGES: Mapping[str, str] = {
    "tag-missing-value-raises": "BUG-144",
    "tag-missing-key-raises": "BUG-144",
    "tag-malformed-after-deleted-snapshot": "BUG-144",
    "missing-start-time": "BUG-149",
    "missing-snapshot-id": "BUG-149",
    "missing-snapshots-key": "BUG-149",
    "naive-start-time": "BUG-149",
    "start-time-string": "BUG-149",
    "tags-is-dict": "BUG-149",
    "exception-tags-not-a-dict": "BUG-149",
    "ami-ebs-none-raises": "BUG-149",
    "ami-missing-images-key": "BUG-149",
    "describe-unexpected-error": "BUG-149",
    "describe-snapshots-client-error-propagates": "BUG-149",
    "describe-images-client-error-propagates": "BUG-149",
}
"""Cases whose outcome differs from the oracle on purpose; their new outcome is asserted in
`tests/test_aws_snapshot_fix.py`. Every other case still equals the oracle."""

SAME_CASES = [c for c in CASES if c.id not in DIVERGES]


class FakeEc2:
    def __init__(self, aws: "FakeAws", region: str) -> None:
        self.aws = aws
        self.region = region

    def describe_regions(self) -> dict[str, Any]:
        self.aws.calls.append(("describe_regions", self.region))
        if self.aws.case.regions_error is not None:
            raise self.aws.case.regions_error
        return {"Regions": [{"RegionName": r} for r in self.aws.case.regions]}

    def describe_snapshots(self, *, OwnerIds: Sequence[str]) -> Any:
        self.aws.calls.append(("describe_snapshots", self.region, tuple(OwnerIds)))
        outcome = self.aws.case.regions[self.region].snaps
        if isinstance(outcome, Exception):
            raise outcome
        return outcome if isinstance(outcome, dict) else {"Snapshots": outcome}

    def describe_images(self, *, Owners: Sequence[str]) -> Any:
        self.aws.calls.append(("describe_images", self.region, tuple(Owners)))
        outcome = self.aws.case.regions[self.region].images
        if isinstance(outcome, Exception):
            raise outcome
        return outcome if isinstance(outcome, dict) else {"Images": outcome}

    def delete_snapshot(self, *, SnapshotId: str) -> dict[str, Any]:
        self.aws.calls.append(("delete_snapshot", self.region, SnapshotId))
        error = self.aws.case.delete_errors.get((self.region, SnapshotId))
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
    deleted: list[str]
    count: int
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_aws_snapshot")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    module.__dict__["datetime"] = types.SimpleNamespace(
        datetime=types.SimpleNamespace(now=lambda tz=None: NOW),
        timezone=datetime.timezone,
        timedelta=datetime.timedelta,
    )
    return module


def _legacy(case: Case) -> Any:
    return _load_oracle().Snapshot(case.dry_run, case.age_days, case.exception_tags)


def _prepare(
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


def _logs(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [(r.name, r.levelno, r.getMessage()) for r in caplog.records]


def _run(
    build: Callable[[Case], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    aws = _prepare(case, monkeypatch, caplog, capsys)
    cleaner = build(case)
    error: tuple[str, str] | None = None
    try:
        cleaner.delete()
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    deleted = list(cleaner.get_deleted)
    return Outcome(
        aws.calls, deleted, cleaner.delete_count, _logs(caplog), capsys.readouterr().out, error
    )


def _run_cleaner(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    aws = _prepare(case, monkeypatch, caplog, capsys)
    options = {"age_days": case.age_days, "exception_tags": case.exception_tags}
    run = run_through_engine("aws", "snapshot", options, dry_run=case.dry_run, now=NOW)
    return Outcome(
        aws.calls, run.deleted, len(run.deleted), _logs(caplog), capsys.readouterr().out, run.error
    )


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


LOG_BUGS = {"BUG-359"}


@pytest.mark.parametrize("case", SAME_CASES, ids=[c.id for c in SAME_CASES])
def test_matches_main(
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
    assert [c for c in new.calls if c[0] != "delete_snapshot"] == [
        c for c in old.calls if c[0] != "delete_snapshot"
    ]
    if old.error is None:
        assert Counter(new.calls) == Counter(old.calls)
        assert new.deleted == old.deleted
        assert new.count == old.count
        assert Counter(new.logs) == Counter(expected_logs(old.logs, LOG_BUGS, rules=RULES))


def _split_calls(calls: list[tuple[Any, ...]]) -> tuple[list[Any], list[Any]]:
    return (
        [c for c in calls if c[0] != "delete_snapshot"],
        [c for c in calls if c[0] == "delete_snapshot"],
    )


def _split_logs(logs: list[tuple[str, int, str]]) -> tuple[list[Any], list[Any]]:
    return (
        [r for r in logs if not r[2].startswith(DELETE_PREFIXES)],
        [r for r in logs if r[2].startswith(DELETE_PREFIXES)],
    )


@pytest.mark.parametrize("case", SAME_CASES, ids=[c.id for c in SAME_CASES])
def test_cleaner_protocol_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The Cleaner lists everything before it deletes, so only the interleaving of delete
    calls and delete log lines with the listing differs from main; each stream is equal."""
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    old_other, old_deletes = _split_calls(old.calls)
    new_other, new_deletes = _split_calls(new.calls)
    old_plan_logs, old_delete_logs = _split_logs(old.logs)
    new_plan_logs, new_delete_logs = _split_logs(new.logs)
    assert new.error == old.error
    assert new.stdout == old.stdout
    assert new_other == old_other
    assert new_plan_logs == expected_logs(old_plan_logs, LOG_BUGS, rules=RULES)
    if new.error is None:
        assert new.deleted == old.deleted
        assert new.count == old.count
        assert new_deletes == old_deletes
        assert new_delete_logs == expected_logs(old_delete_logs, LOG_BUGS, rules=RULES)
    else:
        assert new_deletes == []


def _single_region_error_free_matches_exactly(case: Case) -> bool:
    return len(case.regions) == 1 and not case.delete_errors


@pytest.mark.parametrize("case", SAME_CASES, ids=[c.id for c in SAME_CASES])
def test_cleaner_protocol_logs_in_order_when_nothing_interleaves(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With nothing to skip between deletions, the whole log stream is equal to main's.
    Otherwise every listing line comes before the first delete line, and after an error
    there is no delete line at all."""
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    skip_logs = [r for r in old.logs if r[2].startswith(("Skipping snapshot", "Snap "))]
    if old.error is None and _single_region_error_free_matches_exactly(case) and not skip_logs:
        assert new.logs == expected_logs(old.logs, LOG_BUGS, rules=RULES)
        return
    plan_logs, delete_logs = _split_logs(new.logs)
    assert new.logs == plan_logs + delete_logs
    if new.error is not None:
        assert delete_logs == []


def _logged(o: Outcome, message: str) -> bool:
    """Whether the message is logged, at any level (the level is DEBUG now)."""
    return any(r[2] == message for r in o.logs)


def _deleted(o: Outcome) -> list[str]:
    return o.deleted


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("age-equal-cutoff", lambda o: o.deleted == ["a"]),
    ("age-just-under", lambda o: o.deleted == []),
    ("age-future-start", lambda o: o.deleted == []),
    ("age-days-zero-deletes-all-past", lambda o: o.deleted == ["a"]),
    ("age-days-negative", lambda o: o.deleted == ["a"]),
    ("age-days-true", lambda o: o.deleted == ["a"]),
    (
        "age-days-none-fails-before-any-call",
        lambda o: o.error is not None and o.error[0] == "TypeError" and o.calls == [],
    ),
    ("age-days-overflow", lambda o: o.error is not None and o.error[0] == "OverflowError"),
    ("exception-key-only", lambda o: o.deleted == ["b"]),
    ("exception-value-match", lambda o: o.deleted == ["b"]),
    ("exception-value-case-sensitive", lambda o: o.deleted == ["a"]),
    ("exception-values-none-means-any", lambda o: o.deleted == []),
    ("exception-value-is-string-substring", lambda o: o.deleted == ["b"]),
    ("exception-first-key-logged", lambda o: _logged(o, "Snap a has tag a")),
    ("exception-second-key-logged", lambda o: _logged(o, "Snap a has tag b")),
    ("exception-tags-empty", lambda o: o.deleted == ["a"]),
    ("untagged-deleted", lambda o: o.deleted == ["a", "b"]),
    ("tag-malformed-ignored-without-exception-tags", lambda o: o.deleted == ["a"]),
    ("tag-malformed-on-young-snapshot-not-read", lambda o: o.error is None and o.deleted == []),
    ("exception-tags-duplicate-tag-last-wins", lambda o: o.deleted == ["a"]),
    ("ami-uses-snapshot", lambda o: o.deleted == ["b"]),
    ("ami-in-other-region-not-used", lambda o: o.deleted == ["a"]),
    ("ami-snapshot-id-empty", lambda o: o.deleted == ["a"]),
    (
        "ami-and-protected-logs-protection-only",
        lambda o: (
            _logged(o, "Snap a has tag keep") and not any("used by AMI" in r[2] for r in o.logs)
        ),
    ),
    (
        "delete-client-error-swallowed",
        lambda o: o.error is None and o.deleted == ["s0", "s2", "s3"],
    ),
    (
        "delete-connection-error-swallowed-not-skipped",
        lambda o: (
            o.deleted == ["s0", "s1", "s3", "c"] and not any("skipped" in r[2] for r in o.logs)
        ),
    ),
    ("delete-error-on-every-snapshot", lambda o: o.error is None and o.deleted == []),
    (
        "describe-snapshots-connection-error-skips-region",
        lambda o: o.deleted == ["c"] and any("skipped snapshot cleanup" in r[2] for r in o.logs),
    ),
    ("describe-images-connection-error-skips-region", lambda o: o.deleted == ["c"]),
    ("describe-regions-client-error-propagates", lambda o: o.error is not None and o.deleted == []),
    ("describe-regions-connection-error-falls-back", lambda o: o.deleted == ["a"]),
    ("duplicate-ids-in-region", lambda o: o.deleted == ["a", "a", "b"]),
    ("same-id-two-regions", lambda o: o.deleted == ["a", "a"]),
    ("mixed-decisions", lambda o: o.deleted == ["old", "old2"]),
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
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    assert check(old), "oracle no longer shows the quirk"
    assert check(new)
    if old.error is None:
        assert Counter(new.logs) == Counter(expected_logs(old.logs, LOG_BUGS, rules=RULES))


def test_cases_cover_both_dry_run_and_real_runs() -> None:
    assert any(c.dry_run for c in CASES)
    assert any(not c.dry_run for c in CASES)


def test_factory_defaults_age_days_to_ninety() -> None:
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    cleaner = registry.get("aws", "snapshot")(ctx)
    assert isinstance(cleaner, SnapshotCleaner)
    assert registry.get("aws", "snapshot") is build_snapshot_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("aws", "snapshot")
    assert cleaner.supports(Operation.DELETE)
    assert not cleaner.supports(Operation.STOP)


def _protected_outcome(
    is_protected: Callable[[Any, str], bool],
    tags: Any,
    snap_id: str,
    caplog: pytest.LogCaptureFixture,
) -> tuple[str, Any, list[tuple[str, int, str]]]:
    caplog.clear()
    try:
        return ("ok", is_protected(tags, snap_id), _logs(caplog))
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        return (type(e).__name__, str(e), _logs(caplog))


@pytest.mark.parametrize(
    ("tags", "exception_tags"),
    [
        (None, {"keep": []}),
        ([], {"keep": []}),
        ([tag("keep")], None),
        ([tag("keep")], {}),
        ([tag("keep")], {"keep": []}),
        ([tag("env", "prod")], {"env": ["prod"]}),
        ([tag("env", "dev")], {"env": ["prod"]}),
        ([tag("env", "pro")], {"env": "production"}),
        ([tag("b"), tag("a")], {"a": [], "b": []}),
        ([tag("env", "prod"), tag("env", "dev")], {"env": ["prod"]}),
        ({"keep": "x"}, {"keep": []}),
        ([tag("keep")], ["keep"]),
    ],
)
def test_is_protected_matches_main(
    tags: Any,
    exception_tags: Any,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    old = _load_oracle().Snapshot(False, 90, exception_tags)
    ctx = RunContext(
        dry_run=False,
        operation=Operation.DELETE,
        options={"exception_tags": exception_tags},
        now=NOW,
    )
    new = registry.get("aws", "snapshot")(ctx)
    assert isinstance(new, SnapshotCleaner)
    assert _protected_outcome(new.is_protected, tags, "snap-1", caplog) == expected_outcome(
        _protected_outcome(old._is_protected, tags, "snap-1", caplog), LOG_BUGS, rules=RULES
    )


def _ami_outcome(used: Callable[[Any], set[str]], ec2: Any) -> tuple[str, Any]:
    try:
        return ("ok", used(ec2))
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        return (type(e).__name__, str(e))


@pytest.mark.parametrize(
    "images",
    [
        [],
        [image("s1"), image("s2", "s3")],
        [{"ImageId": "ami-1"}],
        [{"BlockDeviceMappings": [{"DeviceName": "/dev/xvda"}]}],
        [image(None), image("")],
        [{"BlockDeviceMappings": [{"Ebs": None}]}],
        {},
    ],
)
def test_get_snapshots_used_by_amis_matches_main(
    images: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    case = Case("ami-helper", {"us-west-2": Region([], images)})
    old = _load_oracle().Snapshot(False, 90, None)
    old_aws = FakeAws(case)
    new_aws = FakeAws(case)
    assert _ami_outcome(used_by_amis, FakeEc2(new_aws, "us-west-2")) == _ami_outcome(
        old._get_snapshots_used_by_amis, FakeEc2(old_aws, "us-west-2")
    )
    assert new_aws.calls == old_aws.calls


SNAPSHOT_LOG_CASES = ["ami-uses-snapshot", "exception-first-key-logged", "mixed-decisions"]


@pytest.mark.parametrize("case_id", SNAPSHOT_LOG_CASES)
def test_snapshot_skip_lines_are_debug(
    case_id: str,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """BUG-359: the oracle logs one INFO line per snapshot used by an AMI (or tagged); the new
    code logs the same text at DEBUG and nothing else about the outcome changes."""
    case = next(c for c in CASES if c.id == case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    skip_lines = [r for r in old.logs if r[2].startswith(("Skipping snapshot", "Snap "))]
    assert {r[1] for r in skip_lines} <= {logging.INFO}
    assert _split_calls(new.calls) == _split_calls(old.calls)
    assert new.deleted == old.deleted
    assert Counter(new.logs) == Counter(expected_logs(old.logs, LOG_BUGS, rules=RULES))
    if skip_lines:
        assert new.logs != old.logs
