"""The BUG-358 rule of `tests/log_fix_gcp.py` (raw dump lines become DEBUG, nothing else)."""

import logging

import pytest

from tests.helpers_log_fix import expected_logs
from tests.log_fix_gcp import RULES

DUMPS = ["{}", "{'owner': 'me'}", "namespace(name='a', status='RESERVED')"]
SKIP = "Skipping leftover Spot disk p/z/d: exception_labels/notags"
OTHER = [
    "Deleting disk a",
    "List of GCP disk deleted: [{'a': 1}]",
    "Updating age for disk: a",
    "RESULT cloud=gcp resource=disk operation=delete dry_run=false selected=0",
]


@pytest.mark.parametrize("message", [*DUMPS, SKIP])
def test_bug_358_dump_becomes_debug_in_every_record_shape(message: str) -> None:
    assert expected_logs([(logging.INFO, message)], {"BUG-358"}, RULES) == [
        (logging.DEBUG, message)
    ]
    assert expected_logs([("INFO", message)], {"BUG-358"}, RULES) == [("DEBUG", message)]
    assert expected_logs([("root", logging.INFO, message)], {"BUG-358"}, RULES) == [
        ("root", logging.DEBUG, message)
    ]


@pytest.mark.parametrize("message", OTHER)
def test_bug_358_leaves_other_lines_alone(message: str) -> None:
    records = [("root", logging.INFO, message), ("root", logging.WARNING, message)]
    assert expected_logs(records, {"BUG-358"}, RULES) == records


def test_bug_358_only_touches_info_dumps() -> None:
    records = [("root", logging.WARNING, "{}"), ("root", logging.ERROR, SKIP)]
    assert expected_logs(records, {"BUG-358"}, RULES) == records


def test_rules_extend_core_rules() -> None:
    from tests import helpers_log_fix

    assert set(RULES) == {*helpers_log_fix.RULES, "BUG-358"}
    assert RULES["BUG-356"] is helpers_log_fix.RULES["BUG-356"]
    assert "BUG-358" not in helpers_log_fix.RULES


def test_bug_356_and_bug_358_apply_together() -> None:
    age = "The resource is not older than the threshold specified in the age argument"
    records = [("root", logging.INFO, age), ("root", logging.INFO, "{}")]
    assert expected_logs(records, {"BUG-356", "BUG-358"}, RULES) == [
        ("root", logging.DEBUG, age),
        ("root", logging.DEBUG, "{}"),
    ]
