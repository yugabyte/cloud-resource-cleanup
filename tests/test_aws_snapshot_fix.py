"""BUG-144 (tolerant exception-tag reading) and BUG-149 (a non-connectivity
error while listing a region skips only that region).

The diverging parity cases (`DIVERGES` in `tests/test_aws_snapshot_parity.py`) are run through
the oracle and through both new paths: the outcome differs from `main` and equals the table.
"""

import dataclasses
import datetime
import logging
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from crc.aws.snapshot import SnapshotCleaner, SnapshotClient
from crc.core import engine, logger
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import Registry
from crc.core.selection import Selection
from tests.test_aws_snapshot_parity import (
    CASES,
    DIVERGES,
    NOW,
    _legacy,
    _run,
    _run_cleaner,
)

OLD_ENOUGH = 100

# case id -> (expected old error type, deleted by the Cleaner). The Cleaner decides the whole
# region before it deletes, so a failing region deletes nothing.
EXPECTED: dict[str, tuple[str, list[str]]] = {
    "tag-missing-value-raises": ("KeyError", ["a"]),
    "tag-missing-key-raises": ("KeyError", ["a"]),
    "tag-malformed-after-deleted-snapshot": ("KeyError", ["a", "b", "c"]),
    "missing-start-time": ("KeyError", []),
    "missing-snapshot-id": ("KeyError", []),
    "missing-snapshots-key": ("KeyError", []),
    "naive-start-time": ("TypeError", []),
    "start-time-string": ("TypeError", []),
    "tags-is-dict": ("TypeError", []),
    "exception-tags-not-a-dict": ("AttributeError", []),
    "ami-ebs-none-raises": ("AttributeError", []),
    "ami-missing-images-key": ("KeyError", []),
    "describe-unexpected-error": ("ValueError", []),
    "describe-snapshots-client-error-propagates": ("ClientError", ["a"]),
    "describe-images-client-error-propagates": ("ClientError", ["a", "c"]),
}
BUG_144_CASES = {k for k, v in DIVERGES.items() if v == "BUG-144"}
FAILED_REGION: dict[str, str] = {
    "describe-snapshots-client-error-propagates": "eu-west-1",
    "describe-images-client-error-propagates": "eu-west-1",
}


def test_every_diverging_case_has_an_expected_outcome() -> None:
    assert set(EXPECTED) == set(DIVERGES)
    assert {c.id for c in CASES} >= set(DIVERGES)


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


def _expected_bug_144(
    case_id: str, old_calls: list[tuple[Any, ...]], old_logs: list[tuple[str, int, str]]
) -> tuple[list[tuple[Any, ...]], list[tuple[str, int, str]]]:
    if case_id == "tag-missing-value-raises":
        return (
            [*old_calls, ("delete_snapshot", "us-west-2", "a")],
            [*old_logs, ("root", logging.INFO, "Deleted snapshot a")],
        )
    if case_id == "tag-missing-key-raises":
        return (
            [*old_calls, ("delete_snapshot", "us-west-2", "a")],
            [
                *old_logs,
                ("root", logging.DEBUG, "Ignoring a tag entry without Key: {'Value': 'v'}"),
                ("root", logging.INFO, "Deleted snapshot a"),
            ],
        )
    return (
        [
            *old_calls,
            ("delete_snapshot", "us-west-2", "b"),
            ("delete_snapshot", "us-west-2", "c"),
        ],
        [
            *old_logs,
            ("root", logging.INFO, "Deleted snapshot b"),
            ("root", logging.INFO, "Deleted snapshot c"),
        ],
    )


def _expected_bug_149(
    case_id: str,
    old_calls: list[tuple[Any, ...]],
    old_logs: list[tuple[str, int, str]],
    old_error: tuple[str, str],
) -> tuple[list[tuple[Any, ...]], list[tuple[str, int, str]]]:
    region = FAILED_REGION.get(case_id, "us-west-2")
    skip_record = (
        "root",
        logging.ERROR,
        f"Region {region}: skipped snapshot cleanup — error while listing "
        f"({old_error[0]}): {old_error[1]}",
    )
    tail_calls: list[tuple[Any, ...]] = (
        [
            ("client", "ec2", "ap-south-1"),
            ("describe_snapshots", "ap-south-1", ("self",)),
            ("describe_images", "ap-south-1", ("self",)),
            ("delete_snapshot", "ap-south-1", "c"),
        ]
        if case_id == "describe-images-client-error-propagates"
        else []
    )
    tail_logs: list[tuple[str, int, str]] = (
        [("root", logging.INFO, "Deleted snapshot c")]
        if case_id == "describe-images-client-error-propagates"
        else []
    )
    adapter_calls = [*old_calls, *tail_calls]
    adapter_logs = [*old_logs, skip_record, *tail_logs]
    plan_logs = [r for r in adapter_logs if not r[2].startswith("Deleted snapshot")]
    if case_id in ("missing-start-time", "missing-snapshot-id"):
        return [c for c in adapter_calls if c[0] != "delete_snapshot"], plan_logs
    non_deletes = [c for c in adapter_calls if c[0] != "delete_snapshot"]
    deletes = [c for c in adapter_calls if c[0] == "delete_snapshot"]
    delete_logs = [r for r in adapter_logs if r[2].startswith("Deleted snapshot")]
    return [*non_deletes, *deletes], [*plan_logs, *delete_logs]


@pytest.mark.parametrize("case_id", sorted(DIVERGES))
def test_diverging_case_has_the_fixed_outcome(
    case_id: str,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = next(c for c in CASES if c.id == case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    err_type, cleaner_deleted = EXPECTED[case_id]
    assert old.error is not None and old.error[0] == err_type, "oracle no longer shows the bug"
    assert new.error is None
    assert new.deleted == cleaner_deleted
    assert new.count == len(new.deleted)
    assert new.stdout == ""
    expected_calls, expected_logs = (
        _expected_bug_144(case_id, old.calls, old.logs)
        if case_id in BUG_144_CASES
        else _expected_bug_149(case_id, old.calls, old.logs, old.error)
    )
    assert new.calls == expected_calls
    assert new.logs == expected_logs


def _ctx(dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def snap(snap_id: str, days: float = OLD_ENOUGH, **extra: Any) -> dict[str, Any]:
    return {"SnapshotId": snap_id, "StartTime": NOW - datetime.timedelta(days=days), **extra}


class Ec2:
    def __init__(self, snaps: Any, images: Any = ()) -> None:
        self.snaps = snaps
        self.images = images
        self.calls: list[tuple[str, Any]] = []

    def describe_snapshots(self, *, OwnerIds: Sequence[str]) -> Any:
        self.calls.append(("describe_snapshots", tuple(OwnerIds)))
        if isinstance(self.snaps, Exception):
            raise self.snaps
        return {"Snapshots": self.snaps}

    def describe_images(self, *, Owners: Sequence[str]) -> Any:
        self.calls.append(("describe_images", tuple(Owners)))
        return {"Images": self.images}

    def delete_snapshot(self, *, SnapshotId: str) -> Any:
        self.calls.append(("delete_snapshot", SnapshotId))
        return {}


def cleaner_for(clients: dict[str, Ec2], exception_tags: Any = None) -> SnapshotCleaner:
    def factory(region: str) -> SnapshotClient:
        client: SnapshotClient = clients[region]
        return client

    return SnapshotCleaner(factory, lambda: list(clients), 90, exception_tags)


@pytest.fixture
def run_log() -> Iterator[logger.RunLog]:
    run = logger.begin("aws", "snapshot", "delete", False, {})
    yield run
    logger.finish()


def _failed_regions(run: logger.RunLog) -> list[tuple[str | None, str]]:
    return [(f.region, f.resource_id) for f in run._failures]


def auth_error() -> Exception:
    error: Exception = ClientError({"Error": {"Code": "AuthFailure", "Message": "x"}}, "Op")
    return error


def test_listing_error_is_recorded_and_other_regions_continue(run_log: logger.RunLog) -> None:
    clients = {"r1": Ec2(auth_error()), "r2": Ec2([snap("b")])}
    cleaner = cleaner_for(clients)
    plan = cleaner.plan(_ctx())
    assert [c.id for c in plan.candidates] == ["b"]
    assert _failed_regions(run_log) == [("r1", "-")]
    assert run_log._regions_skipped == 1
    assert run_log._regions == 1
    assert [c for c in clients["r1"].calls if c[0] == "delete_snapshot"] == []


def test_listing_error_through_the_engine_skips_only_that_region(run_log: logger.RunLog) -> None:
    clients = {"r1": Ec2([snap("a")]), "r2": Ec2(auth_error()), "r3": Ec2([snap("c")])}
    registry = Registry()
    registry.register("aws", "snapshot")(lambda ctx: _as_cleaner(cleaner_for(clients)))
    (outcome,) = engine.run(Selection((("aws", "snapshot"),)), _ctx(), registry)
    assert outcome.result is not None
    assert list(outcome.result.succeeded) == ["a", "c"]
    assert _failed_regions(run_log) == [("r2", "-")]
    assert run_log._regions_skipped == 1


def _as_cleaner(cleaner: SnapshotCleaner) -> Cleaner:
    return cleaner


def test_connectivity_error_is_still_skipped_without_a_failure(run_log: logger.RunLog) -> None:
    error: Exception = EndpointConnectionError(endpoint_url="https://ec2.example")
    cleaner = cleaner_for({"r1": Ec2(error), "r2": Ec2([snap("b")])})
    plan = cleaner.plan(_ctx())
    assert [c.id for c in plan.candidates] == ["b"]
    assert _failed_regions(run_log) == []
    assert run_log._regions_skipped == 1


def test_failed_region_snapshot_is_kept_in_a_dry_run() -> None:
    clients = {"r1": Ec2([snap("a"), {"SnapshotId": "b"}]), "r2": Ec2([snap("c")])}
    plan = cleaner_for(clients).plan(_ctx(dry_run=True))
    assert [c.id for c in plan.candidates] == ["c"]


@pytest.mark.parametrize(
    "tags",
    [
        [{"Key": "k"}],
        [{"Value": "v"}],
        [{}],
        [{"Key": "k"}, {"Value": "v"}, {"Key": "keep2", "Value": "x"}],
    ],
)
def test_malformed_tag_entries_are_tolerated(tags: list[dict[str, str]]) -> None:
    clients = {"r1": Ec2([snap("a", Tags=tags), snap("b")])}
    plan = cleaner_for(clients, {"keep": []}).plan(_ctx())
    assert [c.id for c in plan.candidates] == ["a", "b"]


def test_missing_value_reads_as_empty_string() -> None:
    tags = [{"Key": "env"}]
    clients = {"r1": Ec2([snap("a", Tags=tags), snap("b", Tags=[{"Key": "env", "Value": "x"}])])}
    plan = cleaner_for(clients, {"env": [""]}).plan(_ctx())
    assert [c.id for c in plan.candidates] == ["b"]


def test_key_only_tag_still_protects_when_any_value_is_listed() -> None:
    clients = {"r1": Ec2([snap("a", Tags=[{"Key": "keep"}, {"Value": "junk"}])])}
    assert cleaner_for(clients, {"keep": []}).plan(_ctx()).candidates == ()


def test_malformed_tags_on_a_young_snapshot_are_read_and_kept(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    clients = {"r1": Ec2([snap("y", 1, Tags=[{"Value": "v"}, {"Key": "k"}]), snap("a")])}
    plan = cleaner_for(clients, {"keep": []}).plan(_ctx())
    assert [c.id for c in plan.candidates] == ["a"]
    assert "Ignoring a tag entry without Key: {'Value': 'v'}" in [
        r.getMessage() for r in caplog.records
    ]


@pytest.mark.parametrize("tags", [{"a": "b"}, ["x"]], ids=["dict", "non-mapping-entry"])
def test_non_list_tags_on_a_young_snapshot_are_not_read_as_on_main(tags: Any) -> None:
    clients = {"r1": Ec2([snap("y", 1, Tags=tags), snap("a")])}
    plan = cleaner_for(clients, {"keep": []}).plan(_ctx())
    assert [c.id for c in plan.candidates] == ["a"]


def test_protected_snapshot_with_a_malformed_sibling_tag_is_kept() -> None:
    tags = [{"Key": "autoclean"}, {"Value": "x"}, {"Key": "auto_clean", "Value": "false"}]
    clients = {"r1": Ec2([snap("a", Tags=tags)])}
    spec = {
        "autoclean": ["false", "False", "FALSE"],
        "auto_clean": ["false", "False", "FALSE"],
    }
    assert cleaner_for(clients, spec).plan(_ctx()).candidates == ()


@pytest.mark.parametrize("case_id", sorted(DIVERGES))
def test_oracle_still_shows_the_bug(
    case_id: str,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = next(c for c in CASES if c.id == case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    assert old.error is not None


@pytest.mark.parametrize(
    ("tags", "exception_tags", "expected"),
    [
        ([{"Key": "k"}], {"keep": []}, False),
        ([{"Value": "v"}], {"keep": []}, False),
        ([{"Value": "v"}, {"Key": "keep"}], {"keep": []}, True),
        ([{"Key": "env"}], {"env": [""]}, True),
        ([{"Key": "env"}], {"env": ["prod"]}, False),
    ],
)
def test_is_protected_tolerates_malformed_entries(
    tags: Any,
    exception_tags: Any,
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    with pytest.raises(KeyError):
        _load_oracle_snapshot(exception_tags)._is_protected(tags, "snap-1")
    assert cleaner_for({}, exception_tags).is_protected(tags, "snap-1") is expected


def _load_oracle_snapshot(exception_tags: Any) -> Any:
    from tests.test_aws_snapshot_parity import _load_oracle

    return _load_oracle().Snapshot(False, 90, exception_tags)


@pytest.mark.parametrize("case_id", sorted(DIVERGES))
def test_diverging_case_in_a_dry_run_through_the_engine_never_deletes(
    case_id: str,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = dataclasses.replace(next(c for c in CASES if c.id == case_id), dry_run=True)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    assert new.error is None
    assert not [c for c in new.calls if c[0] == "delete_snapshot"]
    assert new.stdout == ""
