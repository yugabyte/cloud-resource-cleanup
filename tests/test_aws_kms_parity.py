"""Parity: `crc.aws.kms.Kms` against main (`tests/legacy/aws_kms.txt`).

Both run against the same fake `boto3.client`; SDK calls (in order), results, log records,
stdout and raised errors must be identical for every case. The registered `Cleaner` path
(factory with a `RunContext`, `plan()` then `apply()` unless dry run) is compared on results,
errors, stdout, SDK calls and log records. `kms` works in one region and never lists regions
(no `describe_regions` fallback to cover); a test asserts that.
"""

import datetime
import json
import logging
import re
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError

from crc.aws.kms import Kms, KmsCleaner, KmsRuntime, build_kms_cleaner
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import get
from tests.helpers_log_fix import expected_logs
from tests.log_fix_aws_misc import RULES

ORACLE = Path(__file__).parent / "legacy" / "aws_kms.txt"
UTC = datetime.UTC
CLOCK_NOW = datetime.datetime.now(UTC)
OLD = CLOCK_NOW - datetime.timedelta(days=40)
MID = CLOCK_NOW - datetime.timedelta(days=3, hours=5)
YOUNG = CLOCK_NOW - datetime.timedelta(minutes=30)
USER = "arn:aws:iam::1:user/jenkins"
OTHER = "arn:aws:iam::1:user/other"
DESC = "jenkins key"
ABSENT: Any = "absent"
TWO_KEYS = 2


def tag_list(mapping: Mapping[str, str]) -> list[dict[str, str]]:
    return [{"TagKey": k, "TagValue": v} for k, v in mapping.items()]


def meta(
    state: Any = "Enabled", desc: Any = DESC, created: Any = OLD, **drop: bool
) -> dict[str, Any]:
    data: dict[str, Any] = {"KeyState": state, "Description": desc, "CreationDate": created}
    for name in drop:
        del data[name]
    return {"KeyMetadata": data}


def policy_doc(*principals: Any) -> str:
    return json.dumps({"Statement": [{"Principal": {"AWS": p}} for p in principals]})


@dataclass
class KeySpec:
    id: Any
    tags: Any = field(default_factory=list)
    meta: Any = field(default_factory=meta)
    policy: Any = field(default_factory=lambda: policy_doc(USER))


def key(key_id: Any, **kwargs: Any) -> KeySpec:
    return KeySpec(key_id, **kwargs)


def client_error(operation: str) -> Exception:
    error: Exception = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "denied"}}, operation
    )
    return error


def conn_error() -> Exception:
    error: Exception = EndpointConnectionError(endpoint_url="https://kms.example")
    return error


@dataclass
class Case:
    id: str
    keys: Sequence[KeySpec] = ()
    pages: Sequence[Any] | None = None
    filter_tags: Any = field(default_factory=dict)
    exception_tags: Any = field(default_factory=dict)
    description: str = DESC
    user: str = USER
    window: int = 7
    age: Any = field(default_factory=lambda: {"days": 1})
    age_tag: str = ""
    dry_run: bool = False
    client_error: Exception | None = None
    paginator_error: Exception | None = None
    paginate_error: Exception | None = None
    schedule_errors: Mapping[str, Exception] = field(default_factory=dict)
    reject_repeat: bool = False
    max_age: str | None = None
    fails_in_plan: bool = False


def _many(count: int) -> list[KeySpec]:
    return [key(f"k{i}") for i in range(count)]


def _retention(raw: str) -> list[dict[str, str]]:
    return [{"TagKey": "x", "TagValue": "y", "Key": "crc_age", "Value": raw}]


CASES = [
    Case("dry-run", [key("k1"), key("k2")], dry_run=True),
    Case("schedule", [key("k1"), key("k2")]),
    Case("schedule-window-30", [key("k1")], window=30),
    Case("no-keys", []),
    Case("no-keys-dry-run", [], dry_run=True),
    Case("many-keys", _many(25)),
    Case("many-keys-dry-run", _many(25), dry_run=True),
    Case("two-pages", [], pages=[[key("a"), key("b")], [key("c")]]),
    Case("empty-page-between", [], pages=[[key("a")], [], [key("c")]]),
    Case("missing-keys-in-page", [], pages=[{}], fails_in_plan=True),
    Case("client-connection-error-skips", [key("k1")], client_error=conn_error()),
    Case(
        "client-timeout-skips",
        [key("k1")],
        client_error=ConnectTimeoutError(endpoint_url="https://kms.example"),
    ),
    Case("paginator-connection-error-skips", [key("k1")], paginator_error=conn_error()),
    Case("paginate-connection-error-skips", [key("k1")], paginate_error=conn_error()),
    Case(
        "paginate-connection-error-skips-dry-run",
        [key("k1")],
        paginate_error=conn_error(),
        dry_run=True,
    ),
    Case(
        "list-client-error-propagates",
        [key("k1")],
        paginate_error=client_error("ListKeys"),
        fails_in_plan=True,
    ),
    Case(
        "client-creation-value-error-propagates",
        [key("k1")],
        client_error=ValueError("boom"),
        fails_in_plan=True,
    ),
    Case(
        "paginator-client-error-propagates",
        [key("k1")],
        paginator_error=client_error("ListKeys"),
        fails_in_plan=True,
    ),
    # filter_tags (BUG-007, BUG-014)
    Case("filter-none", [key("k1", tags=tag_list({"a": "1"}))], filter_tags=None),
    Case(
        "filter-exact-pair-match",
        [key("k1", tags=tag_list({"a": "1"})), key("k2", tags=tag_list({"a": "2"}))],
        filter_tags={"a": ["1"]},
    ),
    Case("filter-no-match", [key("k1", tags=tag_list({"a": "1"}))], filter_tags={"a": ["2"]}),
    Case("filter-case-sensitive", [key("k1", tags=tag_list({"a": "X"}))], filter_tags={"a": ["x"]}),
    Case(
        "filter-multi-value-never-matches",
        [key("k1", tags=tag_list({"a": "1"})), key("k2", tags=tag_list({"a": "2"}))],
        filter_tags={"a": ["1", "2"]},
    ),
    Case(
        "filter-empty-value-list-adds-no-constraint",
        [key("k1", tags=tag_list({"zzz": "1"})), key("k2")],
        filter_tags={"a": []},
    ),
    Case(
        "filter-two-keys-all-pairs",
        [
            key("k1", tags=tag_list({"a": "1", "b": "2"})),
            key("k2", tags=tag_list({"a": "1"})),
        ],
        filter_tags={"a": ["1"], "b": ["2"]},
    ),
    Case("filter-on-untagged", [key("k1")], filter_tags={"a": ["1"]}),
    Case(
        "filter-string-value-iterates-characters",
        [key("k1", tags=tag_list({"a": "1"}))],
        filter_tags={"a": "1"},
    ),
    Case(
        "filter-repeated-tag-key-keeps-every-value",
        [key("k1", tags=[*tag_list({"a": "1"}), *tag_list({"a": "2"})])],
        filter_tags={"a": ["1"]},
    ),
    Case(
        "filter-repeated-tag-key-both-values",
        [key("k1", tags=[*tag_list({"a": "1"}), *tag_list({"a": "2"})])],
        filter_tags={"a": ["1", "2"]},
    ),
    Case(
        "filter-non-iterable-second-key-after-mismatch-skips-with-warning",
        [key("k1", tags=tag_list({"a": "2"}))],
        filter_tags={"a": ["1"], "b": None},
    ),
    # tags in the response
    Case("tags-absent-from-response", [key("k1", tags=ABSENT)]),
    Case("tags-none", [key("k1", tags=None)]),
    Case("tags-none-with-filter", [key("k1", tags=None)], filter_tags={"a": ["1"]}),
    Case("tag-without-value", [key("k1", tags=[{"TagKey": "a"}])]),
    Case("tag-without-key", [key("k1", tags=[{"TagValue": "a"}])]),
    Case("tag-unhashable-value-skips", [key("k1", tags=[{"TagKey": "a", "TagValue": ["1"]}])]),
    Case("list-resource-tags-error-skips", [key("k1", tags=client_error("ListResourceTags"))]),
    Case(
        "list-resource-tags-error-on-one-key",
        [key("k1"), key("k2", tags=client_error("ListResourceTags")), key("k3")],
    ),
    # exception_tags (BUG-015)
    Case(
        "exception-set-untagged-key-proceeds",
        [key("k1")],
        exception_tags={"a": []},
    ),
    Case(
        "exception-set-tagged-key-keyerror-skips",
        [key("k1", tags=tag_list({"a": "1"})), key("k2")],
        exception_tags={"a": []},
    ),
    Case(
        "exception-set-value-list",
        [key("k1", tags=tag_list({"b": "1"}))],
        exception_tags={"a": ["1"]},
    ),
    Case(
        "exception-with-key-and-tagkey-entries",
        [key("k1", tags=[{"TagKey": "t", "TagValue": "v", "Key": "a", "Value": "1"}])],
        exception_tags={"a": ["1"]},
    ),
    Case(
        "exception-with-key-and-tagkey-entries-no-hit",
        [key("k1", tags=[{"TagKey": "t", "TagValue": "v", "Key": "a", "Value": "2"}])],
        exception_tags={"a": ["1"]},
    ),
    Case(
        "exception-with-key-only-key-list",
        [key("k1", tags=[{"TagKey": "t", "TagValue": "v", "Key": "a", "Value": "2"}])],
        exception_tags={"a": []},
    ),
    Case(
        "exception-none-warns-per-key",
        [key("k1"), key("k2")],
        exception_tags=None,
    ),
    Case(
        "exception-with-filter-mismatch-first",
        [key("k1", tags=tag_list({"a": "1"}))],
        filter_tags={"zzz": ["1"]},
        exception_tags={"a": []},
    ),
    # describe_key
    Case("disabled-key", [key("k1", meta=meta("Disabled"))]),
    Case("pending-deletion-key", [key("k1", meta=meta("PendingDeletion"))]),
    Case("state-case-sensitive", [key("k1", meta=meta("enabled"))]),
    Case("description-mismatch", [key("k1", meta=meta(desc="other"))]),
    Case("description-substring", [key("k1", meta=meta(desc=f"prefix {DESC} suffix"))]),
    Case("description-empty-needle-matches-all", [key("k1", meta=meta(desc="x"))], description=""),
    Case("description-none", [key("k1", meta=meta(desc=None))]),
    Case("describe-error-skips", [key("k1", meta=client_error("DescribeKey")), key("k2")]),
    Case("describe-missing-metadata", [key("k1", meta={})]),
    Case("describe-missing-state", [key("k1", meta=meta(KeyState=True))]),
    Case("describe-missing-description", [key("k1", meta=meta(Description=True))]),
    Case("describe-missing-creation-date", [key("k1", meta=meta(CreationDate=True))]),
    Case(
        "creation-date-naive-typeerror-skips",
        [key("k1", meta=meta(created=OLD.replace(tzinfo=None)))],
    ),
    Case(
        "creation-date-other-timezone",
        [
            key(
                "k1",
                meta=meta(created=OLD.astimezone(datetime.timezone(datetime.timedelta(hours=5)))),
            )
        ],
    ),
    # age gate
    Case("age-young-key-kept", [key("k1", meta=meta(created=YOUNG))]),
    Case("age-mid-key-days-1", [key("k1", meta=meta(created=MID))]),
    Case("age-days-10-keeps-mid", [key("k1", meta=meta(created=MID))], age={"days": 10}),
    Case("age-days-3-takes-mid", [key("k1", meta=meta(created=MID))], age={"days": 3}),
    Case("age-hours-only", [key("k1", meta=meta(created=MID))], age={"hours": 80}),
    Case("age-hours-only-young", [key("k1", meta=meta(created=YOUNG))], age={"hours": 1}),
    Case(
        "age-days-and-hours",
        [key("k1", meta=meta(created=MID))],
        age={"days": 3, "hours": 4},
    ),
    Case(
        "age-days-and-hours-too-long",
        [key("k1", meta=meta(created=MID))],
        age={"days": 3, "hours": 6},
    ),
    Case("age-empty-matches-everything", [key("k1", meta=meta(created=YOUNG))], age={}),
    Case("age-none-matches-everything", [key("k1", meta=meta(created=YOUNG))], age=None),
    Case("age-int-days", [key("k1", meta=meta(created=MID))], age=2),
    Case("age-zero-days-key-old", [key("k1", meta=meta(created=YOUNG))], age={"days": 0}),
    Case("age-unknown-key-never-old", [key("k1")], age={"weeks": 1}),
    Case(
        "max-age-shortens-threshold",
        [key("k1", meta=meta(created=MID))],
        age={"days": 10},
        max_age="{'days': 1}",
    ),
    Case(
        "max-age-longer-is-ignored",
        [key("k1", meta=meta(created=MID))],
        age={"days": 1},
        max_age="{'days': 30}",
    ),
    Case(
        "max-age-invalid-skips-key",
        [key("k1", meta=meta(created=MID))],
        max_age="not a dict",
    ),
    # custom age tag (BUG-004, BUG-008)
    Case(
        "age-tag-never-matches-tagkey-entries",
        [key("k1", tags=tag_list({"crc_age": "{'days': 100}"}), meta=meta(created=MID))],
        age_tag="crc_age",
    ),
    Case(
        "age-tag-without-tags",
        [key("k1", tags=[], meta=meta(created=MID))],
        age_tag="crc_age",
    ),
    Case(
        "age-tag-found-via-key-entry-keeps",
        [key("k1", tags=_retention("{'days': 100}"), meta=meta(created=MID))],
        age_tag="crc_age",
    ),
    Case(
        "age-tag-found-via-key-entry-short-takes",
        [key("k1", tags=_retention("{'hours': 1}"), meta=meta(created=MID))],
        age_tag="crc_age",
        age={"days": 100},
    ),
    Case(
        "age-tag-unparseable-falls-back-to-age",
        [key("k1", tags=_retention("nope"), meta=meta(created=MID))],
        age_tag="crc_age",
    ),
    Case(
        "age-tag-empty-falls-back-to-age",
        [key("k1", tags=_retention(""), meta=meta(created=MID))],
        age_tag="crc_age",
    ),
    Case(
        "age-tag-zero-age-is-falsy-falls-back",
        [key("k1", tags=_retention("{}"), meta=meta(created=MID))],
        age_tag="crc_age",
    ),
    # key policy
    Case("policy-other-user", [key("k1", policy=policy_doc(OTHER))]),
    Case("policy-no-statement-for-user-skips-silently", [key("k1", policy=policy_doc())]),
    Case("policy-second-statement-matches", [key("k1", policy=policy_doc(OTHER, USER))]),
    Case("policy-duplicate-statements-select-twice", [key("k1", policy=policy_doc(USER, USER))]),
    Case(
        "policy-duplicate-statements-second-schedule-rejected",
        [key("k1", policy=policy_doc(USER, USER)), key("k2")],
        reject_repeat=True,
    ),
    Case(
        "policy-match-then-statement-without-aws-stays-selected",
        [
            key(
                "k1",
                policy=json.dumps(
                    {"Statement": [{"Principal": {"AWS": USER}}, {"Principal": {"Service": "s"}}]}
                ),
            ),
            key("k2"),
        ],
    ),
    Case(
        "policy-service-principal-first-skips",
        [key("k1", policy=json.dumps({"Statement": [{"Principal": {"Service": "s"}}]}))],
    ),
    Case(
        "policy-string-principal-skips",
        [key("k1", policy=json.dumps({"Statement": [{"Principal": "*"}]}))],
    ),
    Case(
        "policy-list-principal-never-equals",
        [key("k1", policy=json.dumps({"Statement": [{"Principal": {"AWS": [USER]}}]}))],
    ),
    Case("policy-invalid-json-skips", [key("k1", policy="{not json")]),
    Case("policy-no-statement-key-skips", [key("k1", policy=json.dumps({}))]),
    Case("policy-missing-policy-key", [key("k1", policy={})]),
    Case("policy-error-skips", [key("k1", policy=client_error("GetKeyPolicy")), key("k2")]),
    Case(
        "policy-keyboard-interrupt-is-swallowed", [key("k1", policy=KeyboardInterrupt()), key("k2")]
    ),
    Case("user-empty-string", [key("k1", policy=policy_doc(""))], user=""),
    Case("user-mismatch-exact", [key("k1", policy=policy_doc(USER + "x"))]),
    # key ids
    Case("key-without-keyid-propagates", [key(None)], fails_in_plan=True),
    Case("key-with-int-keyid", [key(5)]),
    # schedule_key_deletion
    Case(
        "schedule-error-mid-run-loses-record",
        _many(4),
        schedule_errors={"k2": client_error("ScheduleKeyDeletion")},
    ),
    Case(
        "schedule-error-first",
        _many(2),
        schedule_errors={"k0": client_error("ScheduleKeyDeletion")},
    ),
    Case(
        "schedule-error-ignored-in-dry-run",
        _many(2),
        dry_run=True,
        schedule_errors={"k0": client_error("ScheduleKeyDeletion")},
    ),
    Case(
        "schedule-connection-error-propagates",
        _many(2),
        schedule_errors={"k1": conn_error()},
    ),
    # mixed
    Case(
        "mixed",
        [
            key("keep-young", meta=meta(created=YOUNG)),
            key("keep-user", policy=policy_doc(OTHER)),
            key("skip-error", meta=client_error("DescribeKey")),
            key("take-1"),
            key("keep-disabled", meta=meta("Disabled")),
            key("take-2"),
        ],
    ),
]


class FakePaginator:
    def __init__(self, kms: "FakeKms") -> None:
        self.kms = kms

    def paginate(self) -> Any:
        case = self.kms.case
        self.kms.calls.append(("paginate",))
        if case.paginate_error is not None:
            raise case.paginate_error
        if case.pages is not None:
            return [
                p if isinstance(p, dict) else {"Keys": [self.kms.listed(k) for k in p]}
                for p in case.pages
            ]
        return [{"Keys": [self.kms.listed(k) for k in case.keys]}]


class FakeKms:
    def __init__(self, case: Case, calls: list[tuple[Any, ...]]) -> None:
        self.case = case
        self.calls = calls
        self.scheduled: list[str] = []

    def all_keys(self) -> list[KeySpec]:
        pages = [k for page in (self.case.pages or []) if not isinstance(page, dict) for k in page]
        return pages or list(self.case.keys)

    def listed(self, spec: KeySpec) -> dict[str, Any]:
        return {} if spec.id is None else {"KeyId": spec.id}

    def spec(self, key_id: Any) -> KeySpec:
        return next(k for k in self.all_keys() if k.id == key_id)

    def get_paginator(self, name: str) -> FakePaginator:
        self.calls.append(("get_paginator", name))
        if self.case.paginator_error is not None:
            raise self.case.paginator_error
        return FakePaginator(self)

    def list_resource_tags(self, *, KeyId: Any) -> Any:
        self.calls.append(("list_resource_tags", KeyId))
        tags = self.spec(KeyId).tags
        if isinstance(tags, Exception):
            raise tags
        return {} if isinstance(tags, str) and tags == "absent" else {"Tags": tags}

    def describe_key(self, *, KeyId: Any) -> Any:
        self.calls.append(("describe_key", KeyId))
        data = self.spec(KeyId).meta
        if isinstance(data, Exception):
            raise data
        return data

    def get_key_policy(self, *, KeyId: Any, PolicyName: str) -> Any:
        self.calls.append(("get_key_policy", KeyId, PolicyName))
        data = self.spec(KeyId).policy
        if isinstance(data, BaseException):
            raise data
        return data if isinstance(data, dict) else {"Policy": data}

    def schedule_key_deletion(self, *, KeyId: str, PendingWindowInDays: int) -> Any:
        self.calls.append(("schedule_key_deletion", KeyId, PendingWindowInDays))
        error = self.case.schedule_errors.get(KeyId)
        if error is not None:
            raise error
        if self.case.reject_repeat and KeyId in self.scheduled:
            raise client_error("ScheduleKeyDeletion")
        self.scheduled.append(KeyId)
        return {"ResponseMetadata": {}}


class FakeAws:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []

    def client(self, service: str, region_name: str | None = None) -> FakeKms:
        self.calls.append(("client", service, region_name))
        if self.case.client_error is not None:
            raise self.case.client_error
        return FakeKms(self.case, self.calls)

    def session(self) -> Any:
        self.calls.append(("session",))
        return types.SimpleNamespace(get_available_regions=lambda service: [])


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[str]
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_aws_kms")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    return module


def _args(case: Case) -> tuple[Any, ...]:
    return (
        case.dry_run,
        case.filter_tags,
        case.exception_tags,
        case.description,
        case.user,
        case.window,
        case.age,
        case.age_tag,
    )


def _legacy(case: Case) -> Any:
    return _load_oracle().Kms(*_args(case))


def _new(case: Case) -> Any:
    return Kms(*_args(case))


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
    if case.max_age is None:
        monkeypatch.delenv("MAX_AGE", raising=False)
    else:
        monkeypatch.setenv("MAX_AGE", case.max_age)
    caplog.clear()
    capsys.readouterr()
    return aws


def _stable(message: str) -> str:
    # The age logs print the elapsed time and a parse error prints an object address.
    message = re.sub(r"is [\d.]+ (hours|days) old", r"is N \1 old", message)
    return re.sub(r"0x[0-9a-f]+", "0xADDR", message)


def _outcome(
    aws: FakeAws,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    deleted: list[str],
    error: tuple[str, str] | None,
) -> Outcome:
    logs = [(r.name, r.levelno, _stable(r.getMessage())) for r in caplog.records]
    return Outcome(aws.calls, deleted, logs, capsys.readouterr().out, error)


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
    except Exception as e:
        error = (type(e).__name__, str(e))
    return _outcome(aws, caplog, capsys, list(cleaner.get_deleted), error)


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


LOG_BUGS = {"BUG-357"}


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
    assert new.logs == expected_logs(old.logs, LOG_BUGS, rules=RULES)
    assert new.stdout == old.stdout


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
            "kms_key_description": case.description,
            "kms_user": case.user,
            "kms_pending_window": case.window,
            "age": case.age,
            "custom_age_tag_key": case.age_tag,
        },
        now=datetime.datetime.now(),
    )
    cleaner = get("aws", "kms")(ctx)
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
    return _outcome(aws, caplog, capsys, deleted, error)


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
    assert new.error == old.error
    assert new.stdout == old.stdout
    assert _without_client(new.calls) == _without_client(old.calls)
    assert all(c == ("client", "kms", "us-west-2") for c in new.calls if c[0] == "client")
    assert new.logs == expected_logs(old.logs, LOG_BUGS, rules=RULES)
    assert new.deleted == old.deleted
    if case.fails_in_plan:
        assert not [c for c in new.calls if c[0] == "schedule_key_deletion"]


def _find(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)


def _scheduled(o: Outcome) -> list[str]:
    return [str(c[1]) for c in o.calls if c[0] == "schedule_key_deletion"]


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("filter-multi-value-never-matches", lambda o: o.deleted == []),
    ("filter-empty-value-list-adds-no-constraint", lambda o: o.deleted == ["k1", "k2"]),
    ("filter-repeated-tag-key-keeps-every-value", lambda o: o.deleted == ["k1"]),
    ("filter-repeated-tag-key-both-values", lambda o: o.deleted == ["k1"]),
    (
        "filter-non-iterable-second-key-after-mismatch-skips-with-warning",
        lambda o: o.deleted == [] and ("KEY SKIPPED k1" in [m for _, _, m in o.logs]),
    ),
    (
        "tag-unhashable-value-skips",
        lambda o: o.deleted == [] and ("KEY SKIPPED k1" in [m for _, _, m in o.logs]),
    ),
    ("filter-string-value-iterates-characters", lambda o: o.deleted == ["k1"]),
    ("exception-set-tagged-key-keyerror-skips", lambda o: o.deleted == ["k2"]),
    ("exception-set-untagged-key-proceeds", lambda o: o.deleted == ["k1"]),
    (
        "exception-with-key-and-tagkey-entries",
        lambda o: o.deleted == [] and ("describe_key", "k1") not in o.calls,
    ),
    (
        "exception-none-warns-per-key",
        lambda o: [m for _, _, m in o.logs].count("Exception tags and not present") == TWO_KEYS,
    ),
    (
        "age-tag-never-matches-tagkey-entries",
        lambda o: o.deleted == ["k1"],
    ),
    ("age-tag-found-via-key-entry-keeps", lambda o: o.deleted == []),
    ("age-tag-found-via-key-entry-short-takes", lambda o: o.deleted == ["k1"]),
    ("age-tag-unparseable-falls-back-to-age", lambda o: o.deleted == ["k1"]),
    ("age-empty-matches-everything", lambda o: o.deleted == ["k1"]),
    ("age-none-matches-everything", lambda o: o.deleted == ["k1"]),
    ("max-age-shortens-threshold", lambda o: o.deleted == ["k1"]),
    ("creation-date-naive-typeerror-skips", lambda o: o.deleted == []),
    ("policy-duplicate-statements-select-twice", lambda o: o.deleted == ["k1", "k1"]),
    (
        "policy-duplicate-statements-second-schedule-rejected",
        lambda o: o.error is not None and _scheduled(o) == ["k1", "k1"] and o.deleted == [],
    ),
    (
        "policy-match-then-statement-without-aws-stays-selected",
        lambda o: o.deleted == ["k1", "k2"] and ("KEY SKIPPED k1" in [m for _, _, m in o.logs]),
    ),
    ("policy-string-principal-skips", lambda o: o.deleted == []),
    ("policy-keyboard-interrupt-is-swallowed", lambda o: o.deleted == ["k2"]),
    ("key-without-keyid-propagates", lambda o: o.error == ("KeyError", "'KeyId'")),
    (
        "schedule-error-mid-run-loses-record",
        lambda o: o.error is not None and o.deleted == [] and _scheduled(o) == ["k0", "k1", "k2"],
    ),
    ("schedule-error-ignored-in-dry-run", lambda o: o.error is None and _scheduled(o) == []),
    (
        "paginate-connection-error-skips",
        lambda o: o.error is None and o.deleted == [] and len(o.logs) >= 1,
    ),
    ("list-client-error-propagates", lambda o: o.error is not None),
    ("mixed", lambda o: o.deleted == ["take-1", "take-2"]),
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
    cleaner = _run_cleaner(case, monkeypatch, caplog, capsys)
    assert check(old), "oracle no longer shows the quirk"
    assert check(new)
    assert check(cleaner)


def test_no_region_listing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("schedule")
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert new.calls[0] == ("client", "kms", "us-west-2")
    assert [c for c in new.calls if c[0] in ("describe_regions", "session")] == []


def test_accumulates_over_delete_calls_like_main(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("schedule")
    results = []
    for build in (_legacy, _new):
        aws = _setup(case, monkeypatch, caplog, capsys)
        cleaner = build(case)
        cleaner.delete()
        cleaner.delete()
        results.append(
            (aws.calls, list(cleaner.get_deleted), [r.getMessage() for r in caplog.records])
        )
    assert results[0] == results[1]
    assert results[0][1] == ["k1", "k2", "k1", "k2"]


SKIP_TAGS: Sequence[Any] = [
    [],
    tag_list({"a": "1"}),
    [{"Key": "a", "Value": "1"}],
    [{"Key": "a", "Value": "2"}],
    [{"Key": "b", "Value": "1"}, {"Key": "a", "Value": "1"}],
    [{"Key": "a"}],
    [{"Value": "v"}],
]
SKIP_SPECS: Sequence[Any] = [{}, None, {"a": []}, {"a": ["1"]}, {"a": ["x"]}, {"b": []}]


def _skip_outcome(obj: Any, tag_items: Any) -> tuple[str, Any]:
    try:
        return ("ok", obj._should_skip_kms(tag_items))
    except Exception as e:
        return (type(e).__name__, str(e))


@pytest.mark.parametrize("tag_items", SKIP_TAGS, ids=range(len(SKIP_TAGS)))
@pytest.mark.parametrize("spec", SKIP_SPECS, ids=range(len(SKIP_SPECS)))
def test_should_skip_kms_matches_main(
    spec: Any, tag_items: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    args: tuple[Any, ...] = (False, {}, spec, DESC, USER, 7, {"days": 1}, "")
    old = _load_oracle().Kms(*args)
    new = Kms(*args)
    assert _skip_outcome(new, tag_items) == _skip_outcome(old, tag_items)


def test_registered_factory_builds_cleaner() -> None:
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=CLOCK_NOW)
    cleaner: Cleaner = get("aws", "kms")(ctx)
    assert isinstance(cleaner, KmsCleaner)
    assert get("aws", "kms") is build_kms_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("aws", "kms")


def test_runtime_injection_replaces_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    case = _find("schedule")
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr("boto3.client", lambda *a, **k: pytest.fail("boto3 used"))
    runtime = KmsRuntime(
        client_factory=lambda region: FakeKms(case, calls), clock=lambda: CLOCK_NOW
    )
    kms = Kms(False, {}, {}, DESC, USER, 7, {"days": 1}, "", runtime)
    kms.delete()
    assert kms.get_deleted == ["k1", "k2"]


@pytest.mark.parametrize(
    "case_id",
    ["tag-unhashable-value-skips", "policy-match-then-statement-without-aws-stays-selected"],
)
def test_key_skipped_is_debug(
    case_id: str,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """BUG-357: the oracle warns `KEY SKIPPED`, the new code logs the same text at DEBUG."""
    case = _find(case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert old.logs != new.logs
    assert ("root", logging.WARNING, "KEY SKIPPED k1") in old.logs
    assert ("root", logging.DEBUG, "KEY SKIPPED k1") in new.logs
    assert not [r for r in new.logs if r[2].startswith("KEY SKIPPED") and r[1] != logging.DEBUG]
    assert new.deleted == old.deleted
    assert new.calls == old.calls
