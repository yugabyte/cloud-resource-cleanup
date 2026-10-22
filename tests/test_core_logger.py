"""Unit tests for crc.core.logger: exact line formats, counters, order and the clock."""

import logging
from collections.abc import Iterator

import pytest

from crc.core import logger
from crc.core.logger import RunLog


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def _reset_current() -> Iterator[None]:
    logger.finish()
    yield
    logger.finish()


def _records(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [(r.levelno, r.getMessage()) for r in caplog.records]


def test_begin_logs_run_start_with_non_empty_options_in_fixed_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    options = {
        "project_id": "p1",
        "age": {"hours": 1, "days": 2},
        "filter_tags": {"owner": ["a", "b"]},
        "exception_tags": {},
        "notags": None,
        "name_regex": ["^t-"],
        "exception_regex": [],
        "custom_age_tag_key": "crc_age",
        "resource_group": "rg",
        "detach_age": {"days": 3},
        "slack_channel": "chan",
    }
    logger.begin("aws", "vm", "delete", False, options)
    assert _records(caplog) == [
        (
            logging.INFO,
            "RUN START cloud=aws resource=vm operation=delete dry_run=false "
            'age={"days":2,"hours":1} filter_tags={"owner":["a","b"]} name_regex=["^t-"] '
            'custom_age_tag_key="crc_age" resource_group="rg" project_id="p1"',
        )
    ]


def test_begin_without_options_and_dry_run_stop(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    logger.begin("gcp", "vm", "stop", True, {})
    assert caplog.messages == ["RUN START cloud=gcp resource=vm operation=stop dry_run=true"]


def test_begin_serialises_non_json_values_as_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    logger.begin("oci", "disk", "delete", False, {"age": {"days": {1}}})
    assert caplog.messages == [
        'RUN START cloud=oci resource=disk operation=delete dry_run=false age={"days":"{1}"}'
    ]


def test_begin_sets_current_and_finish_clears_it() -> None:
    run = logger.begin("aws", "disk", "delete", False, {})
    assert logger.current() is run
    logger.finish()
    assert logger.current() is not run


def test_current_without_run_is_fresh_and_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    first, second = logger.current(), logger.current()
    assert first is not second
    assert first.attached is False
    first.region("us-east-1", 3, 1, 2.5)
    first.action("deleted", "us-east-1", "i-1", "n")
    first.skip("too_young")
    first.fail("us-east-1", "i-2", ValueError("boom"))
    first.region_skipped("eu-west-1", "timeout")
    first.finish()
    assert caplog.records == []


def test_finish_without_begin_is_a_noop(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    logger.finish()
    assert caplog.records == []


def test_dry_run_flow_lines_and_result(caplog: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    run = logger.begin("aws", "vm", "delete", True, {"age": {"days": 1}}, clock=clock)
    caplog.set_level(logging.INFO)
    caplog.clear()
    run.region("us-east-1", 10, 2, 3.9)
    run.action("would_delete", "us-east-1", "i-1", "web")
    run.action("would_delete", "us-east-1", "i-2")
    run.skip("too_young")
    run.skip("exception_tag")
    run.skip("too_young")
    run.region("us-west-2", 0, 0, 0.2)
    clock.now = 161.9
    logger.finish()
    assert _records(caplog) == [
        (
            logging.INFO,
            "REGION cloud=aws resource=vm region=us-east-1 scanned=10 selected=2 seconds=3",
        ),
        (
            logging.INFO,
            "ACTION would_delete cloud=aws resource=vm region=us-east-1 id=i-1 name=web",
        ),
        (logging.INFO, "ACTION would_delete cloud=aws resource=vm region=us-east-1 id=i-2 name=-"),
        (
            logging.INFO,
            "REGION cloud=aws resource=vm region=us-west-2 scanned=0 selected=0 seconds=0",
        ),
        (logging.INFO, "SKIPPED cloud=aws resource=vm reason=exception_tag count=1"),
        (logging.INFO, "SKIPPED cloud=aws resource=vm reason=too_young count=2"),
        (
            logging.INFO,
            "RESULT cloud=aws resource=vm operation=delete dry_run=true selected=2 done=0 "
            "failed=0 skipped=3 regions=2 regions_skipped=0 duration_s=61",
        ),
    ]


def test_real_run_with_failure_and_skipped_region(caplog: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    run = logger.begin("azure", "vm", "stop", False, {}, clock=clock)
    caplog.set_level(logging.INFO)
    caplog.clear()
    run.action("stopped", None, "vm-1", "one")
    run.action("would_stop", "westus", "vm-2")
    run.region_skipped("eastus", ConnectionError("down"))
    run.fail("westus", "vm-3", RuntimeError("cannot stop"))
    run.fail(None, "vm-4", "plain text")
    clock.now = 130.0
    logger.finish()
    assert _records(caplog) == [
        (logging.INFO, "ACTION stopped cloud=azure resource=vm region=- id=vm-1 name=one"),
        (logging.INFO, "ACTION would_stop cloud=azure resource=vm region=westus id=vm-2 name=-"),
        (logging.ERROR, "ERRORS count=2"),
        (
            logging.ERROR,
            "FAILED cloud=azure resource=vm region=westus id=vm-3 error=RuntimeError: cannot stop",
        ),
        (logging.ERROR, "FAILED cloud=azure resource=vm region=- id=vm-4 error=plain text"),
        (
            logging.INFO,
            "RESULT cloud=azure resource=vm operation=stop dry_run=false selected=2 done=1 "
            "failed=2 skipped=0 regions=0 regions_skipped=1 duration_s=30",
        ),
    ]


def test_result_line_is_always_logged_for_an_empty_run(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    logger.begin("oci", "disk", "delete", False, {}, clock=Clock())
    caplog.clear()
    logger.finish()
    assert caplog.messages == [
        "RESULT cloud=oci resource=disk operation=delete dry_run=false selected=0 done=0 "
        "failed=0 skipped=0 regions=0 regions_skipped=0 duration_s=0"
    ]


@pytest.mark.parametrize(
    ("kind", "done"),
    [("would_delete", 0), ("deleted", 1), ("would_stop", 0), ("stopped", 1)],
)
def test_done_counts_only_deleted_and_stopped(kind: str, done: int) -> None:
    run = RunLog("aws", "vm", "delete", False)
    run.action(kind, "r", "id")
    assert (run._selected, run._done) == (1, done)


def test_unknown_action_kind_warns_and_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    run = RunLog("aws", "vm", "delete", False, attached=True)
    with caplog.at_level(logging.DEBUG):
        run.action("deleting", "r", "id")
    assert [r.levelno for r in caplog.records] == [logging.WARNING, logging.INFO]
    assert "unknown action kind 'deleting'" in caplog.records[0].getMessage()
    assert (run._selected, run._done) == (1, 0)


def test_skip_and_region_skipped_log_nothing(caplog: pytest.LogCaptureFixture) -> None:
    run = logger.begin("gcp", "disk", "delete", False, {})
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    run.skip("state")
    run.region_skipped("us-central1", "no route")
    run.fail("us-central1", "d-1", ValueError("x"))
    assert caplog.records == []


def test_begin_replaces_a_run_that_was_never_finished(caplog: pytest.LogCaptureFixture) -> None:
    first = logger.begin("aws", "vm", "delete", False, {})
    second = logger.begin("aws", "disk", "delete", False, {})
    assert logger.current() is second
    assert logger.current() is not first
    caplog.set_level(logging.INFO)
    caplog.clear()
    logger.finish()
    assert caplog.messages[-1].startswith("RESULT cloud=aws resource=disk ")


def test_default_clock_is_monotonic_and_duration_is_an_int(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    logger.begin("aws", "vm", "delete", False, {})
    logger.finish()
    assert caplog.messages[-1].rsplit("duration_s=", 1)[1].isdigit()
