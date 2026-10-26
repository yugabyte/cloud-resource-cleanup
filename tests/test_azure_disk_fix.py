"""BUG-172 (only the configured resource group is listed) and BUG-174 (each delete
is isolated and its poller is awaited)."""

import logging
from typing import Any

import pytest

from crc.azu.disk import AzureDiskCleaner, DiskSettings
from crc.core import logger
from crc.core.cleaner import Operation, RunContext
from tests.helpers_azure_disk import NOW, FakeAzure, disk
from tests.helpers_failed_once import assert_failure_printed_once
from tests.helpers_wc_azu_engine import candidate_ids, engine_run


@pytest.fixture(autouse=True)
def _reset_run_log() -> Any:
    logger.finish()
    yield
    logger.finish()


def _context(*, dry_run: bool = False) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def _cleaner(azure: FakeAzure, group: str = "rg") -> AzureDiskCleaner:
    return AzureDiskCleaner(lambda: azure.client(None, "sub-1"), group, DiskSettings())


def _calls(azure: FakeAzure, name: str) -> list[tuple[Any, ...]]:
    return [c for c in azure.calls if c[0] == name]


def test_bug_172_lists_only_the_configured_group_never_the_subscription() -> None:
    azure = FakeAzure([disk("a")])
    _cleaner(azure, "configured-rg").plan(_context(dry_run=True))
    assert _calls(azure, "list") == []
    assert _calls(azure, "list_by_resource_group") == [("list_by_resource_group", "configured-rg")]


def test_bug_172_delete_targets_the_group_that_was_listed() -> None:
    azure = FakeAzure([disk("a")])
    cleaner = _cleaner(azure, "configured-rg")
    cleaner.apply(cleaner.plan(_context()), _context())
    assert [c[1] for c in _calls(azure, "list_by_resource_group")] == ["configured-rg"]
    assert _calls(azure, "begin_delete") == [("begin_delete", "configured-rg", "a")]


def test_bug_172_listing_error_still_propagates() -> None:
    azure = FakeAzure([], list_error=ValueError("list failed"))
    with pytest.raises(ValueError, match="list failed"):
        _cleaner(azure).plan(_context(dry_run=True))


def test_bug_174_error_on_one_disk_does_not_stop_the_next(
    caplog: pytest.LogCaptureFixture,
) -> None:
    azure = FakeAzure([disk("a"), disk("b"), disk("c")], begin_script={"b": [RuntimeError("nope")]})
    cleaner = _cleaner(azure)
    result = cleaner.apply(cleaner.plan(_context()), _context())
    assert result.succeeded == ("a", "c")
    assert [(f.id, f.error) for f in result.failed] == [("b", "nope")]
    assert [c[2] for c in _calls(azure, "begin_delete")] == ["a", "b", "c"]
    assert azure.waited == ["a", "c"]
    assert "Error occurred while deleting disk b: nope" in caplog.text


def test_bug_174_poller_is_awaited_before_the_disk_counts_as_deleted() -> None:
    azure = FakeAzure([disk("a"), disk("b")], result_script={"a": [RuntimeError("quota")]})
    cleaner = _cleaner(azure)
    result = cleaner.apply(cleaner.plan(_context()), _context())
    assert azure.waited == ["a", "b"]
    assert result.succeeded == ("b",)
    assert [f.id for f in result.failed] == ["a"]


def test_bug_174_failure_is_recorded_in_the_run_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    azure = FakeAzure([disk("a")], begin_script={"a": [RuntimeError("nope")]})
    logger.begin("azure", "disk", "delete", False, {})
    cleaner = _cleaner(azure)
    cleaner.apply(cleaner.plan(_context()), _context())
    logger.finish()
    lines = [r.getMessage() for r in caplog.records]
    result_line = next(line for line in lines if line.startswith("RESULT"))
    assert "failed=1" in result_line
    assert_failure_printed_once(caplog, "disk a: nope")


def test_bug_174_dry_run_never_deletes_or_waits() -> None:
    azure = FakeAzure([disk("a")], begin_script={"a": [RuntimeError("nope")]})
    cleaner = _cleaner(azure)
    result = cleaner.apply(cleaner.plan(_context(dry_run=True)), _context(dry_run=True))
    assert result.succeeded == ("a",)
    assert _calls(azure, "begin_delete") == []
    assert azure.waited == []


def test_bug_174_engine_continues_and_lists_only_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    azure = FakeAzure([disk("a"), disk("b"), disk("c")], begin_script={"a": [RuntimeError("nope")]})
    azure.install(monkeypatch)
    options = {
        "resource_group": "rg",
        "filter_tags": {},
        "exception_tags": {},
        "notags": {},
        "age": {"days": 1},
        "custom_age_tag_key": "",
    }
    outcome = engine_run("disk", options, dry_run=False, now=NOW)
    assert candidate_ids(outcome) == ["b", "c"]
    assert outcome.result is not None
    assert [f.id for f in outcome.result.failed] == ["a"]
    assert azure.waited == ["b", "c"]


def test_bug_174_selection_errors_still_propagate() -> None:
    azure = FakeAzure([disk("a"), disk("b", created=None)])
    cleaner = _cleaner(azure)
    with pytest.raises(AttributeError):
        cleaner.plan(_context())
    assert _calls(azure, "begin_delete") == []


PRODUCTION_COMMANDS = [
    (
        "itest-6h",
        [
            "crc.py",
            "--cloud",
            "azure",
            "--resource_group",
            "yugabyte-rg",
            "--resource",
            "disk",
            "--filter_tags",
            '{"yb_task":["bug_bash_22_9_2023","itest", "itests", "itest-system", '
            '"db-cross-functional","uitest", "dev-itest", "dev-uitest"]}',
            "--age",
            '{"hours": 6}',
            "--max_age",
            '{"days": 21}',
            "--exception_tags",
            '{"autoclean": ["false", "False", "FALSE"], '
            '"auto_clean": ["false", "False", "FALSE"],"crc":["gke"]}',
            "--slack_channel",
            "yba-crc-cleanup-notifications",
            "--custom_age_tag_key",
            "max_retention_age",
        ],
        "itest",
    ),
    (
        "keep-universe-1d",
        [
            "crc.py",
            "--cloud",
            "azure",
            "--resource_group",
            "yugabyte-rg",
            "--resource",
            "disk",
            "--filter_tags",
            '{"yb_task":["itest-keep-universe", "itest-system-keep-universe", '
            '"db-cross-functional-keep-universe", "uitest-keep-universe", '
            '"dev-itest-keep-universe", "dev-uitest-keep-universe"]}',
            "--age",
            '{"days": 1}',
            "--max_age",
            '{"days": 21}',
            "--exception_tags",
            '{"autoclean": ["false", "False", "FALSE"], '
            '"auto_clean": ["false", "False", "FALSE"],"crc":["gke"]}',
            "--slack_channel",
            "yba-crc-cleanup-notifications",
            "--custom_age_tag_key",
            "max_retention_age",
        ],
        "itest-keep-universe",
    ),
    (
        "stress-1d",
        [
            "crc.py",
            "--cloud",
            "azure",
            "--resource_group",
            "yugabyte-rg",
            "--resource",
            "disk",
            "--filter_tags",
            '{"yb_task":["stress", "stress-dev", "stress-chaos", "stress-lru-cdc", '
            '"stress-smoke-cdc", "smoke-core", "stress-smoke-core", "stress-sqlancer", '
            '"stress-lru-core", "stress-scale"]}',
            "--age",
            '{"days": 1}',
            "--max_age",
            '{"days": 21}',
            "--exception_tags",
            '{"autoclean": ["false", "False", "FALSE"], '
            '"auto_clean": ["false", "False", "FALSE"],"crc":["gke"]}',
            "--slack_channel",
            "db-stress-tests-cleanup-notifications",
            "--custom_age_tag_key",
            "max_retention_age",
        ],
        "stress",
    ),
]


@pytest.mark.parametrize(
    ("argv", "matching_task"),
    [(cmd, task) for _, cmd, task in PRODUCTION_COMMANDS],
    ids=[name for name, _, _ in PRODUCTION_COMMANDS],
)
def test_production_commands_keep_selection_and_list_configured_group(
    argv: list[str],
    matching_task: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from crc.cli.main import main

    azure = FakeAzure(
        [
            disk("match-old", {"yb_task": matching_task}),
            disk("skip-autoclean", {"yb_task": matching_task, "autoclean": "false"}),
            disk("skip-gke", {"yb_task": matching_task, "crc": "gke"}),
            disk("skip-attached", {"yb_task": matching_task}, state="Attached"),
            disk("skip-other-task", {"yb_task": "unrelated"}),
        ]
    )
    azure.install(monkeypatch)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "fake-token")
    monkeypatch.setattr("crc.cli.main.build_slack_client", lambda *a: None)
    monkeypatch.setattr("sys.argv", argv)
    main()
    assert _calls(azure, "list") == []
    assert _calls(azure, "list_by_resource_group") == [("list_by_resource_group", "yugabyte-rg")]
    assert _calls(azure, "begin_delete") == [("begin_delete", "yugabyte-rg", "match-old")]
    assert azure.waited == ["match-old"]
