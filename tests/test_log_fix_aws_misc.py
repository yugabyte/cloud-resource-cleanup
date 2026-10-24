"""Unit tests of the log rules in ``tests/log_fix_aws_misc.py``."""

import logging

from tests.helpers_log_fix import RULES as CORE_RULES
from tests.helpers_log_fix import expected_logs
from tests.log_fix_aws_misc import RULES


def test_expected_logs_rules_only_change_levels() -> None:
    oracle = [
        ("root", logging.WARNING, "KEY SKIPPED k1"),
        ("root", logging.INFO, "Skipping snapshot s1, used by AMI"),
        ("root", logging.INFO, "Snap s2 has tag keep"),
        ("root", logging.WARNING, "List of AWS KMS deleted: []"),
    ]
    rewritten = expected_logs(oracle, {"BUG-357", "BUG-359"}, rules=RULES)
    assert [r[2] for r in rewritten] == [r[2] for r in oracle]
    assert [r[1] for r in rewritten] == [
        logging.DEBUG,
        logging.DEBUG,
        logging.DEBUG,
        logging.WARNING,
    ]
    assert expected_logs(oracle, {"BUG-357"}, rules=RULES)[1] == oracle[1]


def test_rules_extend_the_core_rules() -> None:
    assert set(RULES) == set(CORE_RULES) | {"BUG-357", "BUG-359"}
    assert set(CORE_RULES) == {"BUG-356"}


def test_bug_356_keeps_core_patterns_and_adds_keypair_skip_lines() -> None:
    oracle = [
        ("root", logging.INFO, "Validating resource age with threshold: 3 days"),
        ("root", logging.INFO, "Keypair k1 is not old enough to be deleted."),
        ("root", logging.INFO, "Keypair k2 is in exception_regex ^k"),
        ("root", logging.INFO, "Keypair k3 deleted"),
    ]
    rewritten = expected_logs(oracle, {"BUG-356"}, rules=RULES)
    assert [r[2] for r in rewritten] == [r[2] for r in oracle]
    assert [r[1] for r in rewritten] == [logging.DEBUG, logging.DEBUG, logging.DEBUG, logging.INFO]
    core = expected_logs(oracle, {"BUG-356"})
    assert [r[1] for r in core] == [logging.DEBUG, logging.INFO, logging.INFO, logging.INFO]


def test_level_names_are_kept_as_names() -> None:
    oracle = [("INFO", "Snap s2 has tag keep")]
    assert expected_logs(oracle, {"BUG-359"}, rules=RULES) == [("DEBUG", "Snap s2 has tag keep")]
