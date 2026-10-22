"""Lane rules of LOG-AWS2 (AWS KMS, snapshot, keypairs, Elastic IPs, VPC) for the log rewriter.

Pass ``rules=RULES`` to ``expected_logs`` / ``expected_outcome`` of ``tests/helpers_log_fix.py``
for the bug IDs of this lane. ``RULES`` is the core rules plus BUG-357 and BUG-359, and a
BUG-356 entry that applies the core rule and then the keypair skip lines of this lane.
"""

import logging
import re

from tests import helpers_log_fix
from tests.helpers_log_fix import LogRecordKey, Rule, _with_level

_KEYPAIR_AGE_CHECK_PATTERNS = (
    re.compile(r"^Keypair .+ is not old enough to be deleted\.$"),
    re.compile(r"^Keypair .+ is in exception_regex "),
)
_KMS_SKIP_PATTERNS = (re.compile(r"^KEY SKIPPED "),)
_SNAPSHOT_SKIP_PATTERNS = (
    re.compile(r"^Skipping snapshot .+, used by AMI$"),
    re.compile(r"^Snap .+ has tag "),
)


def _matches(patterns: tuple[re.Pattern[str], ...], message: str) -> bool:
    return any(pattern.search(message) for pattern in patterns)


def _to_debug(
    records: list[LogRecordKey], patterns: tuple[re.Pattern[str], ...]
) -> list[LogRecordKey]:
    return [
        _with_level(record, logging.DEBUG) if _matches(patterns, str(record[-1])) else record
        for record in records
    ]


def _bug_356(records: list[LogRecordKey]) -> list[LogRecordKey]:
    """BUG-356: the per-resource age-check lines become DEBUG, text unchanged.

    The core rule, then the keypair ``not old enough`` and ``in exception_regex`` lines.
    """
    return _to_debug(helpers_log_fix.RULES["BUG-356"](records), _KEYPAIR_AGE_CHECK_PATTERNS)


def _bug_357(records: list[LogRecordKey]) -> list[LogRecordKey]:
    """BUG-357: the AWS KMS ``KEY SKIPPED <id>`` warnings become DEBUG, text unchanged."""
    return _to_debug(records, _KMS_SKIP_PATTERNS)


def _bug_359(records: list[LogRecordKey]) -> list[LogRecordKey]:
    """BUG-359: the per-snapshot skip lines (used by AMI, exception tag) become DEBUG."""
    return _to_debug(records, _SNAPSHOT_SKIP_PATTERNS)


RULES: dict[str, Rule] = {
    **helpers_log_fix.RULES,
    "BUG-356": _bug_356,
    "BUG-357": _bug_357,
    "BUG-359": _bug_359,
}
