"""Parity: `crc.gcp.disk.Disk` against main (`tests/legacy/gcp_disk.txt`).

Both run against the same fake `googleapiclient.discovery.build` and
`google.cloud.compute_v1.DisksClient`, with a frozen clock; SDK calls (in order), results,
count, log records, stdout and raised errors must be identical for every case. The registered
`Cleaner` path (factory with a `RunContext`, `plan()` then `apply()` unless dry run) is compared
on results, errors, stdout, delete calls and log records.
"""

import datetime
import logging
import re
import types
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from google.api_core.exceptions import PermissionDenied, ServiceUnavailable
from google.auth.exceptions import DefaultCredentialsError

from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import get
from crc.gcp.disk import Disk, DiskCleaner, build_disk_cleaner
from tests.helpers_log_fix import expected_logs
from tests.log_fix_gcp import RULES

ORACLE = Path(__file__).parent / "legacy" / "gcp_disk.txt"
UTC = datetime.UTC
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
PROJECT = "proj-1"
BUGS = {"BUG-358"}
TWO = 2
THREE = 3
DAY = {"days": 1}


def _make(kind: type[Exception], message: str) -> Exception:
    return kind(message)


def stamp(**delta: float) -> str:
    return (NOW - datetime.timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%S.%f%z")


def disk(
    name: Any,
    ago: Mapping[str, float] | None = None,
    labels: Any = None,
    users: Sequence[str] = (),
    stamp_value: Any = "default",
) -> Any:
    ts = stamp(**(ago or {"days": 3})) if stamp_value == "default" else stamp_value
    return types.SimpleNamespace(
        name=name,
        users=list(users),
        labels={} if labels is None else labels,
        last_detach_timestamp=ts,
    )


class IterError:
    """A listing that yields `items` and then raises `error` while being iterated."""

    def __init__(self, items: Sequence[Any], error: Exception) -> None:
        self.items = list(items)
        self.error = error


@dataclass
class Case:
    id: str
    zones: Mapping[str, Any]
    dry_run: bool = False
    filter_labels: Any = field(default_factory=dict)
    exception_labels: Any = field(default_factory=dict)
    exception_regex: Any = field(default_factory=list)
    custom_age_tag_key: str = ""
    detach_age: Any = field(default_factory=lambda: dict(DAY))
    slack: bool = False
    slack_label: str = "owner"
    delete_errors: Mapping[tuple[str, str], Exception] = field(default_factory=dict)
    max_age: str | None = None
    zones_response: Any = None
    build_error: Exception | None = None
    zones_error: Exception | None = None
    disks_ctor_error: Exception | None = None
    fails_in_plan: bool = False


def owned(name: str, owner: str | None = "alice", **kw: Any) -> Any:
    labels = {} if owner is None else {"owner": owner}
    return disk(name, labels=labels, **kw)


MIXED = {
    "z1": [
        disk("old-a"),
        disk("attached", users=["vm-1"]),
        disk("young", ago={"hours": 2}),
        disk("never", stamp_value=""),
        disk("old-b"),
    ],
}
TWO_ZONES = {"z1": [disk("a1"), disk("a2")], "z2": [disk("b1"), disk("b2", ago={"hours": 1})]}
BOOM = _make(ServiceUnavailable, "unreachable")
DENIED = _make(PermissionDenied, "denied")
THREE_OWNERS = {
    "z1": [owned("d1"), owned("d2", "bob"), owned("d3", None), owned("d4"), owned("d5", None)]
}


def _age_cases() -> list[Case]:
    one = {"z": [disk("d", ago={"days": 1})]}
    return [
        Case("age-exactly-at-threshold", one),
        Case("age-one-second-short", {"z": [disk("d", ago={"hours": 23, "seconds": 59})]}),
        Case("age-hours-only", {"z": [disk("d", ago={"hours": 5})]}, detach_age={"hours": 5}),
        Case("age-hours-short", {"z": [disk("d", ago={"hours": 4})]}, detach_age={"hours": 5}),
        Case(
            "age-days-and-hours",
            {"z": [disk("d", ago={"hours": 30})]},
            detach_age={"days": 1, "hours": 6},
        ),
        Case(
            "age-days-and-hours-short",
            {"z": [disk("d", ago={"hours": 29})]},
            detach_age={"days": 1, "hours": 6},
        ),
        Case("age-empty-matches-everything", {"z": [disk("d", ago={"seconds": 1})]}, detach_age={}),
        Case(
            "age-none-matches-everything", {"z": [disk("d", ago={"seconds": 1})]}, detach_age=None
        ),
        Case("age-int-days", one, detach_age=1),
        Case("age-zero-days", {"z": [disk("d", ago={"seconds": 1})]}, detach_age={"days": 0}),
        Case("age-future-detach", {"z": [disk("d", ago={"days": -1})]}),
        Case(
            "age-offset-timestamp", {"z": [disk("d", stamp_value="2026-02-27T01:00:00.000-08:00")]}
        ),
        Case("age-offset-young", {"z": [disk("d", stamp_value="2026-03-01T01:00:00.000-08:00")]}),
        Case(
            "age-max-age-shortens",
            {"z": [disk("d", ago={"hours": 3})]},
            detach_age={"days": 5},
            max_age="{'hours': 2}",
        ),
        Case(
            "age-max-age-longer-ignored",
            {"z": [disk("d", ago={"hours": 3})]},
            max_age="{'days': 9}",
        ),
        Case(
            "age-max-age-invalid-raises",
            {"z": [disk("d")]},
            max_age="not a dict",
            fails_in_plan=True,
        ),
        Case(
            "age-timestamp-unparseable",
            {"z": [disk("d", stamp_value="yesterday")]},
            fails_in_plan=True,
        ),
        Case(
            "age-timestamp-without-fraction",
            {"z": [disk("d", stamp_value="2026-02-20T10:00:00-08:00")]},
            fails_in_plan=True,
        ),
        Case(
            "age-timestamp-without-zone",
            {"z": [disk("d", stamp_value="2026-02-20T10:00:00.000")]},
            fails_in_plan=True,
        ),
        Case("age-timestamp-none-skipped", {"z": [disk("d", stamp_value=None)]}),
        Case(
            "age-error-after-earlier-delete",
            {"z": [disk("ok"), disk("bad", stamp_value="x")]},
            fails_in_plan=True,
        ),
        Case(
            "age-error-after-earlier-zone",
            {"z1": [disk("ok")], "z2": [disk("bad", stamp_value="x")]},
            fails_in_plan=True,
        ),
    ]


def _custom_age_cases() -> list[Case]:
    young = {"hours": 12}
    return [
        Case(
            "custom-age-tag-keeps-young",
            {"z": [disk("d", ago={"days": 5}, labels={"ttl": "{'days': 10}"})]},
            custom_age_tag_key="ttl",
        ),
        Case(
            "custom-age-tag-shortens",
            {"z": [disk("d", ago=young, labels={"ttl": "{'hours': 1}"})]},
            custom_age_tag_key="ttl",
        ),
        Case(
            "custom-age-tag-empty-falls-back",
            {"z": [disk("d", labels={"ttl": ""})]},
            custom_age_tag_key="ttl",
        ),
        Case(
            "custom-age-tag-unparseable-falls-back",
            {"z": [disk("d", labels={"ttl": "soon"})]},
            custom_age_tag_key="ttl",
        ),
        Case(
            "custom-age-tag-zero-falls-back-to-default",
            {"z": [disk("d", labels={"ttl": "{}"})]},
            custom_age_tag_key="ttl",
        ),
        Case(
            "custom-age-tag-absent", {"z": [disk("d", labels={"x": "y"})]}, custom_age_tag_key="ttl"
        ),
        Case(
            "custom-age-tag-key-not-set",
            {"z": [disk("d", ago=young, labels={"ttl": "{'hours': 1}"})]},
        ),
        Case("custom-age-tag-on-unlabelled", {"z": [disk("d")]}, custom_age_tag_key="ttl"),
        Case(
            "custom-age-tag-int-literal",
            {"z": [disk("d", ago={"days": 3}, labels={"ttl": "2"})]},
            custom_age_tag_key="ttl",
        ),
    ]


def _label_cases() -> list[Case]:
    labelled = {
        "z": [
            disk("a", labels={"env": "qa"}),
            disk("b", labels={"env": "prod"}),
            disk("c", labels={"team": "x"}),
            disk("u"),
        ]
    }
    return [
        Case("filter-value-match", labelled, filter_labels={"env": ["qa"]}),
        Case("filter-value-mismatch", labelled, filter_labels={"env": ["dev"]}),
        Case("filter-any-key", labelled, filter_labels={"env": ["qa"], "team": ["x"]}),
        Case("filter-empty-values-means-key-present", labelled, filter_labels={"env": []}),
        Case("filter-multiple-values", labelled, filter_labels={"env": ["qa", "prod"]}),
        Case("filter-none", labelled, filter_labels=None),
        Case("filter-string-value-is-substring", labelled, filter_labels={"env": "qa-prod"}),
        Case("filter-case-sensitive", labelled, filter_labels={"env": ["QA"]}),
        Case("untagged-without-filter-included", {"z": [disk("u")]}),
        Case("untagged-with-filter-excluded", {"z": [disk("u")]}, filter_labels={"env": ["qa"]}),
        Case("exception-label-skips", labelled, exception_labels={"env": ["qa"]}),
        Case("exception-label-any-key", labelled, exception_labels={"env": ["dev"], "team": ["x"]}),
        Case("exception-label-empty-values", labelled, exception_labels={"env": []}),
        Case("exception-label-none", labelled, exception_labels=None),
        Case("exception-label-untagged-kept", {"z": [disk("u")]}, exception_labels={"env": ["qa"]}),
        Case(
            "filter-and-exception-labels",
            labelled,
            filter_labels={"env": ["qa", "prod"]},
            exception_labels={"env": ["prod"]},
        ),
        Case(
            "exception-label-on-attached-ignored",
            {"z": [disk("a", users=["vm"], labels={"env": "qa"})]},
        ),
    ]


def _regex_cases() -> list[Case]:
    names = {"z": [disk("tmp-a"), disk("x-tmp-b"), disk("keep"), disk("tmp(c")]}
    return [
        Case("regex-match-anchored", names, exception_regex=["tmp"]),
        Case("regex-match-not-search", names, exception_regex=["x-"]),
        Case("regex-caret", names, exception_regex=["^keep$"]),
        Case("regex-any-of-many", names, exception_regex=["zzz", "keep"]),
        Case("regex-none", names, exception_regex=None),
        Case("regex-empty-list", names, exception_regex=[]),
        Case("regex-empty-pattern-matches-all", names, exception_regex=[""]),
        Case("regex-dot-star", names, exception_regex=[".*-b"]),
        Case("regex-case-sensitive", names, exception_regex=["TMP"]),
        Case("regex-invalid-raises", names, exception_regex=["tmp("], fails_in_plan=True),
        Case(
            "regex-invalid-after-match-not-compiled",
            {"z": [disk("tmp-a")]},
            exception_regex=["tmp", "tmp("],
        ),
        Case(
            "regex-invalid-only-attached-never-compiled",
            {"z": [disk("a", users=["vm"])]},
            exception_regex=["("],
        ),
        Case("regex-non-string-entry-formatted", names, exception_regex=[1]),
        Case("regex-string-iterates-characters", names, exception_regex="kz"),
        Case(
            "regex-non-string-name-raises",
            {"z": [disk(None)]},
            exception_regex=["a"],
            fails_in_plan=True,
        ),
        Case(
            "regex-skips-before-label-filter",
            names,
            exception_regex=["tmp"],
            filter_labels={"env": ["qa"]},
        ),
        Case(
            "regex-and-exception-labels",
            {"z": [disk("tmp-a", labels={"k": "v"}), disk("b", labels={"k": "v"}), disk("c")]},
            exception_regex=["tmp"],
            exception_labels={"k": ["v"]},
        ),
    ]


def _slack_cases() -> list[Case]:
    return [
        Case("slack-groups-by-owner", THREE_OWNERS, slack=True),
        Case("slack-dry-run", THREE_OWNERS, slack=True, dry_run=True),
        Case(
            "slack-label-value-not-tagged-merges",
            {"z": [owned("a", "not_tagged"), owned("b", None)]},
            slack=True,
        ),
        Case("slack-empty-label-name", THREE_OWNERS, slack=True, slack_label=""),
        Case("slack-other-label", THREE_OWNERS, slack=True, slack_label="team"),
        Case("slack-empty-owner-value", {"z": [owned("a", "")]}, slack=True),
        Case("slack-nothing-to-delete", {"z": [disk("young", ago={"hours": 1})]}, slack=True),
        Case(
            "slack-two-zones",
            {"z1": [owned("a")], "z2": [owned("b"), owned("c", "bob")]},
            slack=True,
        ),
        Case(
            "slack-delete-error-not-recorded",
            THREE_OWNERS,
            slack=True,
            delete_errors={("z1", "d2"): DENIED},
        ),
        Case("slack-off-ignores-owner", THREE_OWNERS, slack=False),
    ]


def _empty_and_error_cases() -> list[Case]:
    return [
        Case("zones-empty", {}),
        Case("zones-empty-dry-run", {}, dry_run=True),
        Case("zone-without-disks", {"z1": [], "z2": [disk("a")]}),
        Case("all-zones-empty", {"z1": [], "z2": []}),
        Case(
            "build-error",
            MIXED,
            build_error=_make(DefaultCredentialsError, "no credentials"),
            fails_in_plan=True,
        ),
        Case("zones-list-permission-denied", MIXED, zones_error=DENIED, fails_in_plan=True),
        Case("zones-list-unreachable", MIXED, zones_error=BOOM, fails_in_plan=True),
        Case(
            "zones-list-connection-error",
            MIXED,
            zones_error=ConnectionError("down"),
            fails_in_plan=True,
        ),
        Case("zones-response-without-items", MIXED, zones_response={}, fails_in_plan=True),
        Case("zones-response-empty-items", MIXED, zones_response={"items": []}),
        Case(
            "zone-without-name-after-first-zone",
            TWO_ZONES,
            zones_response={"items": [{"name": "z1"}, {}]},
            fails_in_plan=True,
        ),
        Case(
            "disks-client-credentials-error",
            MIXED,
            disks_ctor_error=_make(DefaultCredentialsError, "no credentials"),
            fails_in_plan=True,
        ),
        Case("disks-list-permission-denied", {"z1": [disk("a")], "z2": DENIED}, fails_in_plan=True),
        Case("disks-list-first-zone-error", {"z1": BOOM, "z2": [disk("a")]}, fails_in_plan=True),
        Case(
            "disks-list-error-dry-run",
            {"z1": [disk("a")], "z2": BOOM},
            dry_run=True,
            fails_in_plan=True,
        ),
        Case(
            "disks-iteration-error",
            {"z1": IterError([disk("a"), disk("b")], BOOM)},
            fails_in_plan=True,
        ),
        Case(
            "disks-iteration-error-second-zone",
            {"z1": [disk("a")], "z2": IterError([disk("b")], DENIED)},
            fails_in_plan=True,
        ),
        Case("delete-error-swallowed", MIXED, delete_errors={("z1", "old-a"): DENIED}),
        Case("delete-error-second-disk-swallowed", MIXED, delete_errors={("z1", "old-b"): BOOM}),
        Case(
            "delete-error-all-fail",
            MIXED,
            delete_errors={("z1", "old-a"): DENIED, ("z1", "old-b"): DENIED},
        ),
        Case("delete-error-second-zone", TWO_ZONES, delete_errors={("z2", "b1"): DENIED}),
        Case(
            "delete-error-ignored-in-dry-run",
            MIXED,
            dry_run=True,
            delete_errors={("z1", "old-a"): DENIED},
        ),
        Case("delete-error-disk-not-selected", MIXED, delete_errors={("z1", "young"): DENIED}),
    ]


def _basic_cases() -> list[Case]:
    return [
        Case("mixed-deletes-old-unattached", MIXED),
        Case("dry-run", MIXED, dry_run=True),
        Case("dry-run-two-zones", TWO_ZONES, dry_run=True),
        Case("two-zones", TWO_ZONES),
        Case("duplicate-names", {"z": [disk("dup"), disk("dup")]}),
        Case("same-name-two-zones", {"z1": [disk("dup")], "z2": [disk("dup")]}),
        Case("ignored-age-notags-name-regex", MIXED),
        Case("attached-only", {"z": [disk("a", users=["vm-1", "vm-2"])]}),
        Case("no-detach-timestamp-only", {"z": [disk("a", stamp_value="")]}),
        Case("empty-name", {"z": [disk("")]}),
        Case(
            "disk-without-users-attribute",
            {"z": [types.SimpleNamespace(name="a")]},
            fails_in_plan=True,
        ),
        Case(
            "disk-without-labels-attribute",
            {"z": [types.SimpleNamespace(name="a", users=[], last_detach_timestamp=stamp(days=3))]},
            fails_in_plan=True,
        ),
        Case(
            "disk-without-timestamp-attribute",
            {"z": [types.SimpleNamespace(name="a", users=[], labels={})]},
            fails_in_plan=True,
        ),
        Case(
            "young-disk-without-name",
            {
                "z": [
                    types.SimpleNamespace(users=[], labels={}, last_detach_timestamp=stamp(hours=1))
                ]
            },
        ),
    ]


def _cases() -> list[Case]:
    return (
        _basic_cases()
        + _age_cases()
        + _custom_age_cases()
        + _label_cases()
        + _regex_cases()
        + _slack_cases()
        + _empty_and_error_cases()
    )


CASES = _cases()


class FakeRequest:
    def __init__(self, gcp: "FakeGcp", label: str, *args: Any) -> None:
        self.gcp = gcp
        self.label = label
        self.args = args

    def execute(self) -> Any:
        self.gcp.calls.append((self.label + ".execute", *self.args))
        if self.label == "zones.list":
            if self.gcp.case.zones_error is not None:
                raise self.gcp.case.zones_error
            if self.gcp.case.zones_response is not None:
                return self.gcp.case.zones_response
            return {"items": [{"name": zone} for zone in self.gcp.case.zones]}
        error = self.gcp.case.delete_errors.get((self.args[1], self.args[2]))
        if error is not None:
            raise error
        return {"name": "operation"}


class FakeZones:
    def __init__(self, gcp: "FakeGcp") -> None:
        self.gcp = gcp

    def list(self, *, project: str) -> FakeRequest:
        self.gcp.calls.append(("zones.list", project))
        return FakeRequest(self.gcp, "zones.list", project)


class FakeDisksResource:
    def __init__(self, gcp: "FakeGcp") -> None:
        self.gcp = gcp

    def delete(self, *, project: str, zone: str, disk: str) -> FakeRequest:
        self.gcp.calls.append(("disks.delete", project, zone, disk))
        return FakeRequest(self.gcp, "disks.delete", project, zone, disk)


class FakeService:
    def __init__(self, gcp: "FakeGcp", name: str, version: str) -> None:
        self.gcp = gcp
        gcp.calls.append(("discovery.build", name, version))
        if gcp.case.build_error is not None:
            raise gcp.case.build_error

    def __enter__(self) -> "FakeService":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.gcp.calls.append(("close",))

    def zones(self) -> FakeZones:
        self.gcp.calls.append(("zones()",))
        return FakeZones(self.gcp)

    def disks(self) -> FakeDisksResource:
        self.gcp.calls.append(("disks()",))
        return FakeDisksResource(self.gcp)


class FakeDisksClient:
    def __init__(self, gcp: "FakeGcp") -> None:
        self.gcp = gcp
        gcp.calls.append(("DisksClient",))
        if gcp.case.disks_ctor_error is not None:
            raise gcp.case.disks_ctor_error

    def list(self, *, project: str, zone: str) -> Any:
        self.gcp.calls.append(("disks.list", project, zone))
        outcome = self.gcp.case.zones[zone]
        if isinstance(outcome, Exception):
            raise outcome
        return self._pages(zone, outcome)

    def _pages(self, zone: str, outcome: Any) -> Iterator[Any]:
        items = outcome.items if isinstance(outcome, IterError) else outcome
        for item in items:
            self.gcp.calls.append(("disks.next", zone, getattr(item, "name", None)))
            yield item
        if isinstance(outcome, IterError):
            raise outcome.error


class FakeGcp:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []

    def build(self, name: str, version: str) -> FakeService:
        return FakeService(self, name, version)

    def disks_client(self) -> FakeDisksClient:
        return FakeDisksClient(self)


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: Any
    count: int
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


class FrozenDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz: datetime.tzinfo | None = None) -> Any:
        return NOW if tz is None else NOW.astimezone(tz)


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_gcp_disk")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    setattr(module, "datetime", FrozenDatetime)  # noqa: B010
    return module


def _args(case: Case) -> dict[str, Any]:
    return {
        "dry_run": case.dry_run,
        "project_id": PROJECT,
        "filter_labels": case.filter_labels,
        "exception_labels": case.exception_labels,
        "age": {"days": 999},
        "custom_age_tag_key": case.custom_age_tag_key,
        "detach_age": case.detach_age,
        "notags": {"env": ["qa"]},
        "name_regex": ["never-matches"],
        "exception_regex": case.exception_regex,
        "slack_notify_users": case.slack,
        "slack_user_label": case.slack_label,
    }


def _legacy(case: Case) -> Any:
    return _load_oracle().Disk(**_args(case))


def _new(case: Case) -> Any:
    return Disk(**_args(case), clock=lambda: NOW)


def _setup(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeGcp:
    gcp = FakeGcp(case)
    monkeypatch.setattr("googleapiclient.discovery.build", gcp.build)
    monkeypatch.setattr("google.cloud.compute_v1.DisksClient", gcp.disks_client)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    if case.max_age is None:
        monkeypatch.delenv("MAX_AGE", raising=False)
    else:
        monkeypatch.setenv("MAX_AGE", case.max_age)
    caplog.clear()
    capsys.readouterr()
    return gcp


def _logs(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [
        (r.name, r.levelno, re.sub(r"0x[0-9a-f]+", "0x0", r.getMessage())) for r in caplog.records
    ]


def _plain(deleted: Any) -> Any:
    if isinstance(deleted, Mapping):
        return {owner: list(names) for owner, names in deleted.items()}
    return list(deleted)


def _run(
    build: Callable[[Case], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    gcp = _setup(case, monkeypatch, caplog, capsys)
    cleaner = build(case)
    error: tuple[str, str] | None = None
    try:
        cleaner.delete()
    except Exception as e:
        error = (type(e).__name__, str(e))
    deleted = _plain(cleaner.get_deleted)
    count = cleaner.count
    return Outcome(gcp.calls, deleted, count, _logs(caplog), capsys.readouterr().out, error)


@pytest.fixture
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
    assert new.logs == expected_logs(old.logs, BUGS, RULES)
    assert new.stdout == old.stdout


def _cleaner_context(case: Case) -> RunContext:
    return RunContext(
        dry_run=case.dry_run,
        operation=Operation.DELETE,
        options={
            "project_id": PROJECT,
            "filter_tags": case.filter_labels,
            "exception_tags": case.exception_labels,
            "exception_regex": case.exception_regex,
            "custom_age_tag_key": case.custom_age_tag_key,
            "detach_age": case.detach_age,
            "slack_notify_users": case.slack,
            "slack_user_label": case.slack_label,
        },
        now=NOW,
    )


def _run_cleaner(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    gcp = _setup(case, monkeypatch, caplog, capsys)
    ctx = _cleaner_context(case)
    cleaner = get("gcp", "disk")(ctx)
    error: tuple[str, str] | None = None
    deleted: Any = {} if case.slack else []
    try:
        plan = cleaner.plan(ctx)
        if case.dry_run:
            deleted = _plain(plan.by_owner) if case.slack else [c.id for c in plan.candidates]
        else:
            result = cleaner.apply(plan, ctx)
            deleted = _plain(result.by_owner) if case.slack else list(result.succeeded)
    except Exception as e:
        error = (type(e).__name__, str(e))
    count = len(deleted)
    return Outcome(gcp.calls, deleted, count, _logs(caplog), capsys.readouterr().out, error)


def _only(calls: Sequence[tuple[Any, ...]], name: str) -> list[tuple[Any, ...]]:
    return [c for c in calls if c[0] == name]


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
    old_deletes = _only(old.calls, "disks.delete.execute")
    new_deletes = _only(new.calls, "disks.delete.execute")
    if new.error is not None:
        # main deletes while listing; plan() lists everything first, so an error raised while
        # listing or filtering stops the Cleaner path before any delete.
        assert case.fails_in_plan
        assert new.error == old.error
        assert new.stdout == old.stdout
        assert new_deletes == []
        return
    assert old.error is None
    assert new.deleted == old.deleted
    assert new.stdout == old.stdout
    assert new_deletes == old_deletes
    assert _only(new.calls, "disks.list") == _only(old.calls, "disks.list")
    expected = expected_logs(old.logs, BUGS, RULES)
    assert Counter(new.logs) == Counter(expected[:-1])
    assert new.logs[-1] == expected[-2]
    if _listed_disks(case) <= 1:
        assert new.logs == expected[:-1]


def _listed_disks(case: Case) -> int:
    return sum(len(v) if isinstance(v, list) else TWO for v in case.zones.values())


def _find(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)


def _deleted_names(o: Outcome) -> list[str]:
    return [c[3] for c in o.calls if c[0] == "disks.delete"]


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("mixed-deletes-old-unattached", lambda o: o.deleted == ["old-a", "old-b"]),
    ("dry-run", lambda o: not _only(o.calls, "disks.delete") and o.deleted == ["old-a", "old-b"]),
    ("ignored-age-notags-name-regex", lambda o: o.deleted == ["old-a", "old-b"]),
    ("age-exactly-at-threshold", lambda o: o.deleted == ["d"]),
    ("age-one-second-short", lambda o: o.deleted == []),
    ("age-empty-matches-everything", lambda o: o.deleted == ["d"]),
    ("age-none-matches-everything", lambda o: o.deleted == ["d"]),
    ("age-zero-days", lambda o: o.deleted == ["d"]),
    ("age-future-detach", lambda o: o.deleted == []),
    ("age-max-age-shortens", lambda o: o.deleted == ["d"]),
    ("age-max-age-invalid-raises", lambda o: o.error is not None and o.error[0] == "SyntaxError"),
    (
        "age-timestamp-without-fraction",
        lambda o: o.error is not None and o.error[0] == "ValueError",
    ),
    ("age-timestamp-none-skipped", lambda o: o.error is None and o.deleted == []),
    ("age-error-after-earlier-delete", lambda o: o.error is not None and o.deleted == ["ok"]),
    ("age-error-after-earlier-zone", lambda o: o.error is not None and o.deleted == ["ok"]),
    ("custom-age-tag-keeps-young", lambda o: o.deleted == []),
    ("custom-age-tag-shortens", lambda o: o.deleted == ["d"]),
    ("custom-age-tag-empty-falls-back", lambda o: o.deleted == ["d"]),
    ("custom-age-tag-unparseable-falls-back", lambda o: o.deleted == ["d"]),
    ("custom-age-tag-zero-falls-back-to-default", lambda o: o.deleted == ["d"]),
    ("filter-any-key", lambda o: o.deleted == ["a", "c"]),
    ("filter-empty-values-means-key-present", lambda o: o.deleted == ["a", "b"]),
    ("filter-string-value-is-substring", lambda o: o.deleted == ["a", "b"]),
    ("untagged-with-filter-excluded", lambda o: o.deleted == []),
    ("untagged-without-filter-included", lambda o: o.deleted == ["u"]),
    ("exception-label-any-key", lambda o: o.deleted == ["a", "b", "u"]),
    ("regex-match-anchored", lambda o: o.deleted == ["x-tmp-b", "keep"]),
    ("regex-match-not-search", lambda o: o.deleted == ["tmp-a", "keep", "tmp(c"]),
    ("regex-invalid-raises", lambda o: o.error is not None and o.error[0] == "error"),
    ("regex-invalid-after-match-not-compiled", lambda o: o.error is None and o.deleted == []),
    ("regex-invalid-only-attached-never-compiled", lambda o: o.error is None and o.deleted == []),
    ("regex-non-string-entry-formatted", lambda o: o.error is None),
    ("regex-string-iterates-characters", lambda o: o.deleted == ["tmp-a", "x-tmp-b", "tmp(c"]),
    ("regex-non-string-name-raises", lambda o: o.error is not None and o.error[0] == "TypeError"),
    (
        "slack-groups-by-owner",
        lambda o: o.deleted == {"alice": ["d1", "d4"], "bob": ["d2"], "not_tagged": ["d3", "d5"]},
    ),
    ("slack-label-value-not-tagged-merges", lambda o: o.deleted == {"not_tagged": ["a", "b"]}),
    ("slack-dry-run", lambda o: len(o.deleted) == THREE and not _only(o.calls, "disks.delete")),
    ("slack-off-ignores-owner", lambda o: o.deleted == ["d1", "d2", "d3", "d4", "d5"]),
    (
        "slack-delete-error-not-recorded",
        lambda o: o.deleted == {"alice": ["d1", "d4"], "not_tagged": ["d3", "d5"]},
    ),
    ("zones-empty", lambda o: o.deleted == [] and _only(o.calls, "disks.list") == []),
    ("build-error", lambda o: o.error is not None and o.calls[-1] == o.calls[0]),
    ("zones-list-permission-denied", lambda o: o.error == ("PermissionDenied", "403 denied")),
    ("zones-list-connection-error", lambda o: o.error == ("ConnectionError", "down")),
    ("zones-response-without-items", lambda o: o.error == ("KeyError", "'items'")),
    (
        "zone-without-name-after-first-zone",
        lambda o: o.error is not None and o.deleted == ["a1", "a2"],
    ),
    ("disks-client-credentials-error", lambda o: o.error is not None and o.deleted == []),
    ("disks-list-permission-denied", lambda o: o.error is not None and o.deleted == ["a"]),
    ("disks-iteration-error", lambda o: o.error is not None and o.deleted == ["a", "b"]),
    ("delete-error-swallowed", lambda o: o.error is None and o.deleted == ["old-b"]),
    (
        "delete-error-all-fail",
        lambda o: (
            o.error is None
            and o.deleted == []
            and "No GCP disk to delete." in [m for _, _, m in o.logs]
        ),
    ),
    (
        "delete-error-ignored-in-dry-run",
        lambda o: o.error is None and o.deleted == ["old-a", "old-b"],
    ),
    ("duplicate-names", lambda o: o.deleted == ["dup", "dup"]),
    ("same-name-two-zones", lambda o: o.deleted == ["dup", "dup"]),
    ("disk-without-users-attribute", lambda o: o.error is not None and "users" in o.error[1]),
    ("disk-without-labels-attribute", lambda o: o.error is not None and "labels" in o.error[1]),
    ("young-disk-without-name", lambda o: o.error is None and o.deleted == []),
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
    case = _find(case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert check(old), "oracle no longer shows the quirk"
    assert check(new)


def test_listing_and_deleting_are_interleaved_like_main(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("two-zones")
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    names = [c[0] for c in new.calls]
    assert names.index("disks.delete.execute") < names.index(
        "disks.list", names.index("disks.list") + 1
    )
    assert new.calls == old.calls


def test_second_delete_call_accumulates_like_main(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("two-zones")
    results = []
    for build in (_legacy, _new):
        gcp = _setup(case, monkeypatch, caplog, capsys)
        disks = build(case)
        disks.delete()
        disks.delete()
        results.append((gcp.calls, list(disks.get_deleted), _logs(caplog)))
    assert (*results[0][:-1], expected_logs(results[0][-1], BUGS, RULES)) == results[1]


def test_disk_state_argument_is_ignored_like_main(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("mixed-deletes-old-unattached")
    results = []
    for build in (_legacy, _new):
        gcp = _setup(case, monkeypatch, caplog, capsys)
        disks = build(case)
        disks.delete(["Attached"])
        results.append((gcp.calls, list(disks.get_deleted)))
    assert results[0] == results[1]
    assert results[0][1] == ["old-a", "old-b"]


def test_class_attributes_match_main() -> None:
    old = _load_oracle().Disk
    for name in ("service_name", "service_version", "time_format", "default_disk_state"):
        assert getattr(Disk, name) == getattr(old, name)


def test_registered_factory_builds_cleaner() -> None:
    ctx = RunContext(
        dry_run=True, operation=Operation.DELETE, options={"project_id": PROJECT}, now=NOW
    )
    cleaner: Cleaner = get("gcp", "disk")(ctx)
    assert isinstance(cleaner, DiskCleaner)
    assert get("gcp", "disk") is build_disk_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("gcp", "disk")


def test_registered_factory_requires_project_id() -> None:
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    with pytest.raises(ValueError, match="project_id is required"):
        get("gcp", "disk")(ctx)


def test_bug_358_label_dump_is_debug_not_info(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("mixed-deletes-old-unattached")
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert old.logs != new.logs
    assert new.logs == expected_logs(old.logs, BUGS, RULES)
    assert [m for _, _, m in new.logs] == [m for _, _, m in old.logs]
    assert new.calls == old.calls
    assert new.stdout == old.stdout
    info = [m for _, level, m in new.logs if level == logging.INFO]
    assert not [m for m in info if m.startswith(("{", "namespace("))]
    dumps = [m for _, level, m in old.logs if level == logging.INFO]
    assert any(m.startswith(("{", "namespace(")) for m in dumps)
