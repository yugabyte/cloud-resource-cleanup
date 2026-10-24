"""Tests of tests/helpers_log_fix.py: the BUG-356 rule only changes levels, never text."""

import logging

import pytest

from tests.helpers_log_fix import expected_logs, expected_outcome

AGE_LINES = [
    "Validating resource age with threshold: {'days': 1}",
    "The resource is 2 days old and older than the threshold of 1 days.",
    "The resource is 3.0 hours old and older than the threshold of 2 hours.",
    "The resource is not older than the threshold specified in the age argument",
    "No tags provided to search for custom_age_tag_key.",
    "Searching for custom_age_tag_key: crc_age",
]
OTHER_LINES = [
    "Found 'crc_age' tag: {'days': 1}",
    "Overwriting resource age: Setting age to the maximum threshold of {'days': 1}.",
    "List of AWS keypairs deleted: []",
    "Age is not specified. Ignoring age threshold check",
]


@pytest.mark.parametrize("message", AGE_LINES)
@pytest.mark.parametrize("level", [logging.INFO, logging.WARNING])
def test_bug_356_age_line_becomes_debug_in_every_record_shape(message: str, level: int) -> None:
    assert expected_logs([(level, message)], {"BUG-356"}) == [(logging.DEBUG, message)]
    name = logging.getLevelName(level)
    assert expected_logs([(name, message)], {"BUG-356"}) == [("DEBUG", message)]
    assert expected_logs([("root", level, message)], {"BUG-356"}) == [
        ("root", logging.DEBUG, message)
    ]


@pytest.mark.parametrize("message", OTHER_LINES)
def test_bug_356_leaves_other_lines_alone(message: str) -> None:
    records = [(logging.INFO, message), ("WARNING", message)]
    assert expected_logs(records, {"BUG-356"}) == records


def test_rule_keeps_count_order_and_text() -> None:
    oracle = [(logging.INFO, m) for m in AGE_LINES + OTHER_LINES]
    new = expected_logs(oracle, {"BUG-356"})
    assert [m for _, m in new] == [m for _, m in oracle]
    assert [lvl for lvl, _ in new] == [logging.DEBUG] * len(AGE_LINES) + [logging.INFO] * len(
        OTHER_LINES
    )


def test_no_bugs_is_identity_and_unknown_bug_raises() -> None:
    records = [(logging.INFO, AGE_LINES[0])]
    assert expected_logs(records, set()) == records
    with pytest.raises(KeyError):
        expected_logs(records, {"BUG-" + "999"})


def test_expected_outcome_rewrites_only_the_logs() -> None:
    outcome = ("return", True, [("INFO", AGE_LINES[0])])
    assert expected_outcome(outcome, {"BUG-356"}) == ("return", True, [("DEBUG", AGE_LINES[0])])
