"""Tests of tests/log_fix_azure.py: the LOG-AZU rules only change levels, never text."""

import logging

import pytest

from tests.helpers_log_fix import RULES as CORE_RULES
from tests.helpers_log_fix import expected_logs, expected_outcome
from tests.log_fix_azure import RULES

AGE_LINES = [
    "Validating resource age with threshold: {'days': 1}",
    "The resource is 2 days old and older than the threshold of 1 days.",
    "The resource is not older than the threshold specified in the age argument",
    "No tags provided to search for custom_age_tag_key.",
    "Searching for custom_age_tag_key: crc_age",
]
SPOT_DISK_LINES = [
    "Skipping leftover Spot disk disk-1: it is not older than the threshold",
    "Skipping leftover Spot disk disk-2: attached",
]
TAG_DUMP_LINES = ["{'env': 'ci'}", "{}", "None", "{'a': {'b': 1}}"]
NOT_TAG_DUMP_LINES = [
    "{not a dict}",
    "{1, 2}",
    "['env']",
    "None found",
    "Found {'env': 'ci'} tags",
    "{'unterminated': ",
]


def test_rules_extend_core_rules() -> None:
    assert set(RULES) == {*CORE_RULES, "BUG-358"}
    assert RULES["BUG-356"] is not CORE_RULES["BUG-356"]


@pytest.mark.parametrize("message", AGE_LINES + SPOT_DISK_LINES)
@pytest.mark.parametrize("level", [logging.INFO, logging.WARNING])
def test_bug_356_lane_rule_covers_core_and_spot_disk_lines(message: str, level: int) -> None:
    assert expected_logs([(level, message)], {"BUG-356"}, RULES) == [(logging.DEBUG, message)]
    name = logging.getLevelName(level)
    assert expected_logs([("root", name, message)], {"BUG-356"}, RULES) == [
        ("root", "DEBUG", message)
    ]


@pytest.mark.parametrize("message", SPOT_DISK_LINES)
def test_core_rule_alone_does_not_touch_spot_disk_lines(message: str) -> None:
    records = [(logging.INFO, message)]
    assert expected_logs(records, {"BUG-356"}) == records


@pytest.mark.parametrize("message", ["Skipping leftover disk x", "Skipping Spot disk x"])
def test_bug_356_lane_rule_leaves_other_lines_alone(message: str) -> None:
    records = [(logging.INFO, message), ("WARNING", message)]
    assert expected_logs(records, {"BUG-356"}, RULES) == records


@pytest.mark.parametrize("message", TAG_DUMP_LINES)
def test_bug_358_tag_dump_becomes_debug_in_every_record_shape(message: str) -> None:
    assert expected_logs([(logging.INFO, message)], {"BUG-358"}, RULES) == [
        (logging.DEBUG, message)
    ]
    assert expected_logs([("root", "INFO", message)], {"BUG-358"}, RULES) == [
        ("root", "DEBUG", message)
    ]


@pytest.mark.parametrize("message", NOT_TAG_DUMP_LINES)
def test_bug_358_leaves_other_lines_alone(message: str) -> None:
    records = [(logging.INFO, message), ("WARNING", message)]
    assert expected_logs(records, {"BUG-358"}, RULES) == records


def test_rules_keep_count_order_and_text() -> None:
    messages = AGE_LINES + SPOT_DISK_LINES + TAG_DUMP_LINES + NOT_TAG_DUMP_LINES
    oracle = [(logging.INFO, m) for m in messages]
    new = expected_logs(oracle, {"BUG-356", "BUG-358"}, RULES)
    assert [m for _, m in new] == messages
    debug = len(AGE_LINES) + len(SPOT_DISK_LINES) + len(TAG_DUMP_LINES)
    assert [lvl for lvl, _ in new] == [logging.DEBUG] * debug + [logging.INFO] * len(
        NOT_TAG_DUMP_LINES
    )


def test_unknown_bug_raises_and_expected_outcome_takes_rules() -> None:
    with pytest.raises(KeyError):
        expected_logs([(logging.INFO, "x")], {"BUG-999"}, RULES)
    outcome = ("return", True, [("INFO", TAG_DUMP_LINES[0])])
    assert expected_outcome(outcome, {"BUG-358"}, RULES) == (
        "return",
        True,
        [("DEBUG", TAG_DUMP_LINES[0])],
    )
