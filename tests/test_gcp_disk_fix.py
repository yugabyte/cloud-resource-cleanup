"""Behaviour fixes of `crc.gcp.disk`: BUG-005, BUG-006, BUG-126, BUG-127.

Fakes only; no cloud call. The parity test marks the cases of the oracle that diverge.
"""

import datetime
import logging
import types
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any

import pytest

from crc.core import logger
from crc.core.cleaner import Operation, RunContext
from crc.gcp.disk import DiskCleaner, DiskSettings, DiskView, build_disk_cleaner
from crc.gcp.spot_vm.settings import DEFAULT_WAIT_SEC
from tests.helpers_failed_once import assert_failure_printed_once
from tests.helpers_wc_gcp_engine import deleted_ids, run_engine

UTC = datetime.UTC
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
PROJECT = "proj-1"
TWO = 2


def stamp(**delta: float) -> str:
    return (NOW - datetime.timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%S.%f%z")


def disk(name: str, labels: Mapping[str, str] | None = None, **extra: Any) -> Any:
    values: dict[str, Any] = {
        "name": name,
        "users": [],
        "labels": dict(labels or {}),
        "last_detach_timestamp": stamp(days=3),
    }
    values.update(extra)
    return types.SimpleNamespace(**values)


class FakeClient:
    """Compute fake: `operations` maps a disk name to the statuses `get_operation` returns."""

    def __init__(
        self,
        zones: Mapping[str, Any],
        operations: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        delete_result: Callable[[str], object] | None = None,
    ) -> None:
        self.zones = zones
        self.operations = {k: list(v) for k, v in (operations or {}).items()}
        self.delete_result = delete_result
        self.calls: list[tuple[str, ...]] = []

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        self.calls.append(("list_zones",))
        return [{"name": zone} for zone in self.zones]

    def list_disks(self, project_id: str, zone: str) -> Iterable[DiskView]:
        self.calls.append(("list_disks", zone))
        outcome = self.zones[zone]
        if isinstance(outcome, Exception):
            raise outcome
        items: Iterable[DiskView] = outcome
        return items

    def delete_disk(self, project_id: str, zone: str, disk: str) -> object:
        self.calls.append(("delete_disk", zone, disk))
        if self.delete_result is not None:
            return self.delete_result(disk)
        return {"name": f"op-{disk}"}

    def get_operation(
        self, project_id: str, scope: str, location: str, operation: str
    ) -> Mapping[str, Any]:
        self.calls.append(("get_operation", scope, location, operation))
        name = operation.removeprefix("op-")
        statuses = self.operations.get(name)
        if statuses is None:
            return {"status": "DONE"}
        return statuses.pop(0) if len(statuses) > 1 else statuses[0]

    def close(self) -> None:
        self.calls.append(("close",))

    def names(self) -> list[str]:
        return [call[0] for call in self.calls]


def settings(**overrides: Any) -> DiskSettings:
    values: dict[str, Any] = {
        "project_id": PROJECT,
        "filter_labels": None,
        "exception_labels": None,
        "exception_regex": None,
        "custom_age_tag_key": "",
        "detach_age": {"days": 1},
        "poll_sec": 0,
        "wait_sec": 5,
    }
    values.update(overrides)
    return DiskSettings(**values)


def context(*, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def run(client: FakeClient, **overrides: Any) -> tuple[list[str], list[str]]:
    """(planned ids, applied ids) of the Cleaner path."""
    cleaner = DiskCleaner(client, settings(**overrides))
    plan = cleaner.plan(context())
    result = cleaner.apply(plan, context())
    return [c.id for c in plan.candidates], list(result.succeeded)


@pytest.fixture
def run_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    caplog.set_level(logging.DEBUG)
    logger.begin("gcp", "disk", "delete", False, {}, clock=lambda: 0.0)
    yield caplog
    logger.finish()


def failed_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("FAILED ")]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("crc.gcp.spot_vm.operations.time.sleep", lambda seconds: None)


# BUG-005: notags is applied as the GCP VM cleaner does.


@pytest.mark.parametrize(
    ("notags", "labels", "kept"),
    [
        ({"env": ["qa"]}, {"env": "qa"}, False),
        ({"env": ["qa"]}, {"env": "prod"}, True),
        ({"env": ["qa"]}, {}, True),
        ({"env": []}, {"env": "anything"}, False),
        ({"env": ["qa"], "team": ["db"]}, {"env": "qa", "team": "db"}, False),
        ({"env": ["qa"], "team": ["db"]}, {"env": "qa"}, True),
        ({}, {"env": "qa"}, True),
        (None, {"env": "qa"}, True),
    ],
)
def test_bug_005_notags_skips_a_disk_whose_labels_match_every_key(
    notags: Any, labels: Mapping[str, str], kept: bool
) -> None:
    client = FakeClient({"z": [disk("d", labels)]})
    planned, applied = run(client, notags=notags)
    assert (planned == ["d"]) is kept
    assert (applied == ["d"]) is kept
    assert (
        ("delete_disk", "z", "d") in client.calls if kept else "delete_disk" not in client.names()
    )


def test_bug_005_notags_skip_is_counted_as_exception_tag(
    run_log: pytest.LogCaptureFixture,
) -> None:
    client = FakeClient({"z": [disk("d", {"env": "qa"})]})
    DiskCleaner(client, settings(notags={"env": ["qa"]})).plan(context())
    logger.finish()
    assert any("reason=exception_tag count=1" in r.getMessage() for r in run_log.records)


def test_bug_005_engine_and_registry_pass_notags() -> None:
    client = FakeClient({"z": [disk("a", {"env": "qa"}), disk("b")]})
    outcome = run_engine(DiskCleaner(client, settings(notags={"env": ["qa"]})), "disk", now=NOW)
    assert deleted_ids(outcome) == ["b"]
    assert ("delete_disk", "z", "a") not in client.calls
    options = {
        "project_id": PROJECT,
        "notags": {"env": ["qa"]},
        "name_regex": ["b"],
        "detach_age": {"days": 1},
    }
    built = build_disk_cleaner(RunContext(False, Operation.DELETE, options, NOW))
    assert isinstance(built, DiskCleaner)
    assert built._settings.notags == {"env": ["qa"]}
    assert built._settings.name_regex == ["b"]


# BUG-006: name_regex is applied (re.search, any pattern; empty matches all).


@pytest.mark.parametrize(
    ("name_regex", "expected"),
    [
        (["^itest"], ["itest-1", "itest-2"]),
        (["2$"], ["itest-2", "other-2"]),
        (["itest", "other"], ["itest-1", "itest-2", "other-2"]),
        (["est-"], ["itest-1", "itest-2"]),
        (["nothing"], []),
        ([], ["itest-1", "itest-2", "other-2"]),
        (None, ["itest-1", "itest-2", "other-2"]),
    ],
)
def test_bug_006_name_regex_filters_by_search(
    name_regex: list[str] | None, expected: list[str]
) -> None:
    client = FakeClient({"z": [disk("itest-1"), disk("itest-2"), disk("other-2")]})
    planned, applied = run(client, name_regex=name_regex)
    assert planned == expected
    assert applied == expected
    deleted = [c[2] for c in client.calls if c[0] == "delete_disk"]
    assert deleted == expected


def test_bug_006_exception_regex_still_wins() -> None:
    client = FakeClient({"z": [disk("itest-1"), disk("itest-2")]})
    planned, _ = run(client, name_regex=["itest"], exception_regex=["itest-2"])
    assert planned == ["itest-1"]


@pytest.mark.parametrize("bad", [["tmp("], ["ok", "["], "itest", "", {"a": "b"}, [1], 123])
def test_bug_006_invalid_name_regex_fails_closed_before_any_call(bad: Any) -> None:
    client = FakeClient({"z": [disk("itest-1")]})
    with pytest.raises(ValueError, match="name_regex"):
        DiskCleaner(client, settings(name_regex=bad))
    options = {"project_id": PROJECT, "detach_age": {"days": 1}, "name_regex": bad}
    context_ = RunContext(False, Operation.DELETE, options, NOW)
    with pytest.raises(ValueError, match="name_regex"):
        build_disk_cleaner(context_)
    assert client.calls == []


def test_bug_006_dry_run_lists_only_matching_disks() -> None:
    client = FakeClient({"z": [disk("itest-1"), disk("other")]})
    plan = DiskCleaner(client, settings(name_regex=["itest"])).plan(context(dry_run=True))
    assert [c.id for c in plan.candidates] == ["itest-1"]
    assert "delete_disk" not in client.names()


# BUG-126: the disk is recorded as deleted only after the zone operation is DONE without error.


def test_bug_126_waits_for_the_zone_operation_before_recording() -> None:
    client = FakeClient(
        {"z": [disk("a")]},
        operations={"a": [{"status": "PENDING"}, {"status": "RUNNING"}, {"status": "DONE"}]},
    )
    deleted: list[str] = []
    cleaner = DiskCleaner(client, settings())
    candidate = next(cleaner.plan_zone("z", lambda: NOW))
    assert cleaner.apply_disk("z", candidate, deleted, dry_run=False)
    assert deleted == ["a"]
    assert client.calls[-4:] == [
        ("delete_disk", "z", "a"),
        ("get_operation", "zone", "z", "op-a"),
        ("get_operation", "zone", "z", "op-a"),
        ("get_operation", "zone", "z", "op-a"),
    ]


@pytest.mark.parametrize(
    "operation",
    [
        [{"status": "DONE", "error": {"errors": [{"code": "RESOURCE_IN_USE"}]}}],
        [{"status": "RUNNING"}],
    ],
    ids=["operation-error", "timeout"],
)
def test_bug_126_failed_or_incomplete_operation_is_not_recorded(
    operation: list[dict[str, Any]],
    run_log: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter(range(0, 1000, 3))
    monkeypatch.setattr("crc.gcp.spot_vm.operations.time.monotonic", lambda: next(ticks))
    client = FakeClient({"z": [disk("a"), disk("b")]}, operations={"a": operation})
    planned, applied = run(client, wait_sec=5)
    assert planned == ["a", "b"]
    assert applied == ["b"]
    assert [c[2] for c in client.calls if c[0] == "delete_disk"] == ["a", "b"]
    logger.finish()
    assert failed_lines(run_log) == []
    assert_failure_printed_once(run_log, "Disk delete failed or incomplete for a")


@pytest.mark.parametrize(
    "result",
    [None, object(), {}, {"name": ""}],
    ids=["None", "object", "{}", "{'name': ''}"],
)
def test_bug_126_delete_without_an_operation_name_is_not_recorded(result: object) -> None:
    client = FakeClient({"z": [disk("a")]}, delete_result=lambda name: result)
    _, applied = run(client)
    assert applied == []
    assert "get_operation" not in client.names()


def test_bug_126_poll_error_is_not_recorded() -> None:
    class Broken(FakeClient):
        def get_operation(
            self, project_id: str, scope: str, location: str, operation: str
        ) -> Mapping[str, Any]:
            raise ConnectionError("down")

    client = Broken({"z": [disk("a"), disk("b")]})
    _, applied = run(client)
    assert applied == []
    assert [c[2] for c in client.calls if c[0] == "delete_disk"] == ["a", "b"]


def test_bug_126_dry_run_neither_deletes_nor_waits() -> None:
    client = FakeClient({"z": [disk("a")]})
    cleaner = DiskCleaner(client, settings())
    plan = cleaner.plan(context(dry_run=True))
    result = cleaner.apply(plan, context(dry_run=True))
    assert result.succeeded == ("a",)
    assert "delete_disk" not in client.names()
    assert "get_operation" not in client.names()


def test_bug_126_slack_groups_only_confirmed_deletes() -> None:
    client = FakeClient(
        {"z": [disk("a", {"owner": "alice"}), disk("b", {"owner": "bob"})]},
        operations={"b": [{"status": "DONE", "error": {"errors": []}}]},
    )
    cleaner = DiskCleaner(client, settings(slack_notify_users=True, slack_user_label="owner"))
    outcome = run_engine(cleaner, "disk", now=NOW)
    assert outcome.result is not None
    assert outcome.result.by_owner is not None
    assert {k: list(v) for k, v in outcome.result.by_owner.items()} == {"alice": ["a"]}


# BUG-127: an error on one disk or one zone listing is recorded and the run goes on.


def test_bug_127_evaluation_error_on_one_disk_keeps_it_and_continues(
    run_log: pytest.LogCaptureFixture,
) -> None:
    bad = disk("bad", last_detach_timestamp="not a time")
    client = FakeClient({"z1": [disk("a"), bad, disk("c")], "z2": [disk("d")]})
    planned, applied = run(client)
    assert planned == applied == ["a", "c", "d"]
    assert [c[2] for c in client.calls if c[0] == "delete_disk"] == ["a", "c", "d"]
    logger.finish()
    assert failed_lines(run_log) == []
    errors = [r.getMessage() for r in run_log.records if r.levelno == logging.ERROR]
    assert errors[0].startswith("Error occurred while evaluating disk bad: ")
    assert "ERRORS count=1" in errors
    assert_failure_printed_once(run_log, "evaluating disk bad: ")


def test_bug_127_missing_attribute_on_one_disk_is_recorded() -> None:
    broken = types.SimpleNamespace(name="x", labels={}, last_detach_timestamp=stamp(days=3))
    client = FakeClient({"z": [broken, disk("ok")]})
    planned, applied = run(client)
    assert planned == applied == ["ok"]


def test_bug_127_listing_error_of_a_zone_is_recorded_and_the_zone_skipped(
    run_log: pytest.LogCaptureFixture,
) -> None:
    client = FakeClient({"z1": [disk("a")], "z2": PermissionError("denied"), "z3": [disk("c")]})
    planned, applied = run(client)
    assert planned == applied == ["a", "c"]
    assert [c for c in client.calls if c[0] == "list_disks"] == [
        ("list_disks", "z1"),
        ("list_disks", "z2"),
        ("list_disks", "z3"),
    ]
    logger.finish()
    assert failed_lines(run_log) == []
    assert_failure_printed_once(run_log, "listing disks of zone z2: denied")


def test_bug_127_listing_error_while_iterating_keeps_earlier_disks() -> None:
    def pages() -> Iterator[Any]:
        yield disk("a")
        raise ConnectionError("dropped")

    client = FakeClient({"z1": pages(), "z2": [disk("b")]})
    planned, applied = run(client)
    assert planned == applied == ["a", "b"]


def test_bug_127_engine_deletes_the_other_disks_and_logs_the_summary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    client = FakeClient({"z1": PermissionError("denied"), "z2": [disk("b")]})
    outcome = run_engine(DiskCleaner(client, settings()), "disk", now=NOW)
    assert deleted_ids(outcome) == ["b"]
    messages = [r.getMessage() for r in caplog.records]
    assert "List of GCP disk deleted: ['b']" in messages
    assert client.names()[-1] == "close"


def test_bug_127_zones_listing_error_still_raises_and_closes() -> None:
    class Broken(FakeClient):
        def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
            raise PermissionError("denied")

    client = Broken({})
    with pytest.raises(PermissionError, match="denied"):
        DiskCleaner(client, settings()).plan(context())
    assert client.calls == [("close",)]


def test_bug_127_a_delete_error_is_still_recorded_and_skipped() -> None:
    class Failing(FakeClient):
        def delete_disk(self, project_id: str, zone: str, disk: str) -> object:
            if disk == "a":
                raise RuntimeError("denied")
            return super().delete_disk(project_id, zone, disk)

    client = Failing({"z": [disk("a"), disk("b")]})
    _, applied = run(client)
    assert applied == ["b"]
    assert "get_operation" in client.names()


def test_default_wait_bounds_match_the_spot_cleaner() -> None:
    plain = DiskSettings(PROJECT, None, None, None, "", {})
    assert (plain.wait_sec, plain.poll_sec) == (DEFAULT_WAIT_SEC, 5)
    assert plain.notags is None
    assert plain.name_regex is None


def test_production_jenkins_flags_selection_unchanged() -> None:
    filter_tags = {
        "yb_task": [
            "bug_bash_22_9_2023",
            "uitest",
            "itest",
            "itests",
            "itest-system",
            "db-cross-functional",
            "dev-itest",
            "dev-uitest",
            "itest-keep-universe",
            "itest-system-keep-universe",
            "db-cross-functional-keep-universe",
            "uitest-keep-universe",
            "dev-itest-keep-universe",
            "dev-uitest-keep-universe",
        ]
    }
    exception_tags = {
        "autoclean": ["false", "False", "FALSE"],
        "auto_clean": ["false", "False", "FALSE"],
        "crc": ["gke"],
    }
    client = FakeClient(
        {
            "us-west1-a": [
                disk("itest-ok", {"yb_task": "itest"}),
                disk("itest-gke", {"yb_task": "itest", "crc": "gke"}),
                disk("itest-keep", {"yb_task": "itest", "autoclean": "FALSE"}),
                disk("itest-retention", {"yb_task": "itest", "max_retention_age": "{'days': 10}"}),
                disk("unmatched", {"yb_task": "other"}),
            ]
        }
    )
    cleaner = DiskCleaner(
        client,
        DiskSettings(
            project_id="yugabyte",
            filter_labels=filter_tags,
            exception_labels=exception_tags,
            exception_regex=None,
            custom_age_tag_key="max_retention_age",
            detach_age=None,  # type: ignore[arg-type]
            notags=None,
            name_regex=None,
            slack_notify_users=False,
            slack_user_label="",
        ),
    )
    assert deleted_ids(run_engine(cleaner, "disk", now=NOW)) == ["itest-ok"]
    assert client.calls == [
        ("list_zones",),
        ("list_disks", "us-west1-a"),
        ("close",),
        ("delete_disk", "us-west1-a", "itest-ok"),
        ("get_operation", "zone", "us-west1-a", "op-itest-ok"),
        ("close",),
    ]
