"""Tests of tests/log_fix_aws_compute.py: the LOG-AWS1 rules only change levels, never text."""

import logging

import pytest

from tests import helpers_log_fix
from tests.helpers_log_fix import expected_logs, expected_outcome
from tests.log_fix_aws_compute import RULES

SKIP_LINES = [
    "Skipping volume vol-1: attached to an instance",
    "Skipping leftover volume vol-2: not old enough",
    "Skipping tagged leftover volume vol-3 with tag crc_keep",
    "Skipping cancelled spot request sir-1",
]
AGE_LINE = "The resource is 2 days old and older than the threshold of 1 days."
TAG_DUMP = "[{'Key': 'Name', 'Value': 'vm-1'}]"
OTHER_LINES = ["Deleted volume vol-1", "Skipping region eu-west-1", "[1, 2]"]


def test_rules_extend_the_core_rules() -> None:
    assert set(RULES) == {*helpers_log_fix.RULES, "BUG-358", "BUG-361", "BUG-362"}
    assert helpers_log_fix.RULES["BUG-356"] is not RULES["BUG-356"]
    assert set(helpers_log_fix.RULES) == {"BUG-356"}


@pytest.mark.parametrize("message", SKIP_LINES)
def test_bug_356_info_skip_line_becomes_debug_in_every_record_shape(message: str) -> None:
    assert expected_logs([(logging.INFO, message)], {"BUG-356"}, RULES) == [
        (logging.DEBUG, message)
    ]
    assert expected_logs([("n", "INFO", message)], {"BUG-356"}, RULES) == [("n", "DEBUG", message)]


@pytest.mark.parametrize("message", SKIP_LINES)
@pytest.mark.parametrize("level", [logging.WARNING, logging.ERROR])
def test_bug_356_warning_and_error_skip_lines_keep_their_level(message: str, level: int) -> None:
    assert expected_logs([(level, message)], {"BUG-356"}, RULES) == [(level, message)]


def test_bug_356_still_applies_the_core_age_rule() -> None:
    records = [(logging.INFO, AGE_LINE), (logging.INFO, SKIP_LINES[0])]
    assert expected_logs(records, {"BUG-356"}, RULES) == [
        (logging.DEBUG, AGE_LINE),
        (logging.DEBUG, SKIP_LINES[0]),
    ]


def test_core_default_leaves_the_skip_lines_alone() -> None:
    records = [(logging.INFO, message) for message in SKIP_LINES]
    assert expected_logs(records, {"BUG-356"}) == records


@pytest.mark.parametrize("message", OTHER_LINES)
def test_other_lines_are_unchanged(message: str) -> None:
    records = [(logging.INFO, message)]
    for bug in ("BUG-356", "BUG-358", "BUG-361", "BUG-362"):
        assert expected_logs(records, {bug}, RULES) == records


def test_bug_358_info_tag_dump_becomes_debug_in_every_record_shape() -> None:
    assert expected_logs([(logging.INFO, TAG_DUMP)], {"BUG-358"}, RULES) == [
        (logging.DEBUG, TAG_DUMP)
    ]
    assert expected_logs([("INFO", TAG_DUMP)], {"BUG-358"}, RULES) == [("DEBUG", TAG_DUMP)]


def test_bug_358_keeps_a_warning_tag_dump_and_skip_lines() -> None:
    records = [(logging.WARNING, TAG_DUMP), (logging.INFO, SKIP_LINES[0])]
    assert expected_logs(records, {"BUG-358"}, RULES) == records


@pytest.mark.parametrize("bug", ["BUG-361", "BUG-362"])
def test_new_lines_only_rules_leave_the_oracle_unchanged(bug: str) -> None:
    records = [(logging.INFO, SKIP_LINES[0]), (logging.INFO, TAG_DUMP)]
    assert expected_logs(records, {bug}, RULES) == records


def test_rules_apply_in_sorted_bug_id_order_and_never_drop_lines() -> None:
    records = [(logging.INFO, SKIP_LINES[0]), (logging.INFO, TAG_DUMP), (logging.INFO, "other")]
    result = expected_logs(records, {"BUG-358", "BUG-356", "BUG-361"}, RULES)
    assert result == [
        (logging.DEBUG, SKIP_LINES[0]),
        (logging.DEBUG, TAG_DUMP),
        (logging.INFO, "other"),
    ]


def test_expected_outcome_takes_the_lane_rules() -> None:
    oracle = ("ok", 3, [(logging.INFO, SKIP_LINES[0])])
    assert expected_outcome(oracle, {"BUG-356"}, RULES) == (
        "ok",
        3,
        [(logging.DEBUG, SKIP_LINES[0])],
    )


def test_unknown_bug_id_raises() -> None:
    with pytest.raises(KeyError):
        expected_logs([(logging.INFO, "x")], {"BUG-999"}, RULES)
