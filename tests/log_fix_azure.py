"""LOG-AZU bug rules for the oracle log rewriter in ``tests/helpers_log_fix.py``.

Parity tests of the Azure lane pass ``rules=RULES`` to ``expected_logs`` / ``expected_outcome``.
Contract: ``docs/refactor/LOGGING.md``.
"""

import ast
import logging
import re

from tests import helpers_log_fix
from tests.helpers_log_fix import LogRecordKey, Rule, _with_level

_SPOT_DISK_SKIP_PATTERNS = (re.compile(r"^Skipping leftover Spot disk "),)


def _is_spot_disk_skip(message: str) -> bool:
    return any(pattern.search(message) for pattern in _SPOT_DISK_SKIP_PATTERNS)


def _bug_356(records: list[LogRecordKey]) -> list[LogRecordKey]:
    """BUG-356: the per-resource age-check lines become DEBUG, text unchanged.

    The core rule, plus the per-leftover-disk skip lines of the Azure Spot VM cleaner.
    """
    return [
        _with_level(record, logging.DEBUG) if _is_spot_disk_skip(str(record[-1])) else record
        for record in helpers_log_fix.RULES["BUG-356"](records)
    ]


def _is_tag_dump(message: str) -> bool:
    """A raw tag or label dump: the str() of a dict, or of None for an untagged resource."""
    if message == "None":
        return True
    if not (message.startswith("{") and message.endswith("}")):
        return False
    try:
        return isinstance(ast.literal_eval(message), dict)
    except (ValueError, SyntaxError):
        return False


def _bug_358(records: list[LogRecordKey]) -> list[LogRecordKey]:
    """BUG-358: the raw tag/label dump of every listed resource becomes DEBUG, text unchanged."""
    return [
        _with_level(record, logging.DEBUG) if _is_tag_dump(str(record[-1])) else record
        for record in records
    ]


RULES: dict[str, Rule] = {
    **helpers_log_fix.RULES,
    "BUG-356": _bug_356,
    "BUG-358": _bug_358,
}
