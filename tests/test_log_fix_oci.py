"""Unit tests of the log rules in ``tests/log_fix_oci.py``."""

import logging

from tests.helpers_log_fix import expected_logs
from tests.log_fix_oci import RULES


def test_helper_rules_change_only_the_level_of_tag_dumps() -> None:
    oracle = [
        ("root", logging.INFO, "{'env': 'ci'}"),
        ("root", logging.INFO, "Skipping leftover volume v1: exception_tags/notags"),
        ("root", logging.INFO, "Deleting instance x"),
    ]
    assert expected_logs(oracle, {"BUG-353"}, RULES) == [
        ("root", logging.DEBUG, "{'env': 'ci'}"),
        oracle[1],
        oracle[2],
    ]
    assert expected_logs(oracle, {"BUG-358"}, RULES) == [
        ("root", logging.DEBUG, "{'env': 'ci'}"),
        ("root", logging.DEBUG, "Skipping leftover volume v1: exception_tags/notags"),
        oracle[2],
    ]
    assert expected_logs(oracle, {"BUG-361", "BUG-362"}, RULES) == oracle
