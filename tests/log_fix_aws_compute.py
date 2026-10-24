"""Log-fix rules of the AWS compute cleaners (disk, spot instance requests, VM).

Pass ``rules=RULES`` to ``expected_logs`` / ``expected_outcome`` of ``tests.helpers_log_fix``
for every test that uses one of these bug IDs. ``RULES`` is the core rules plus the rules below.
A rule may only change the level of a line or insert documented new lines; it never drops an
oracle line or changes its text. Contract: ``docs/logging.md``.
"""

import logging
import re

from tests import helpers_log_fix
from tests.helpers_log_fix import LogRecordKey, Rule, _with_level

_SKIP_LINE_PATTERNS = (
    re.compile(r"^Skipping volume \S+: "),
    re.compile(r"^Skipping leftover volume \S+: "),
    re.compile(r"^Skipping tagged leftover volume "),
    re.compile(r"^Skipping cancelled spot request "),
)
_AWS_TAG_DUMP = re.compile(r"^\[\{.*\}\]$")


def _is_info(record: LogRecordKey) -> bool:
    return record[-2] in (logging.INFO, "INFO")


def _is_skip_line(record: LogRecordKey) -> bool:
    """The INFO "Skipping <resource> ..." line of one resource (WARNING and ERROR
    skip lines flag an anomaly and keep their level)."""
    return _is_info(record) and any(p.search(str(record[-1])) for p in _SKIP_LINE_PATTERNS)


def _bug_356(records: list[LogRecordKey]) -> list[LogRecordKey]:
    """BUG-356: the core rule (age-check lines become DEBUG), then the INFO skip lines
    of the AWS compute cleaners become DEBUG too, text unchanged."""
    return [
        _with_level(record, logging.DEBUG) if _is_skip_line(record) else record
        for record in helpers_log_fix.RULES["BUG-356"](records)
    ]


def _bug_358_aws_vm(records: list[LogRecordKey]) -> list[LogRecordKey]:
    """BUG-358: the INFO dump of an instance's raw tag list is DEBUG."""
    return [
        _with_level(record, logging.DEBUG)
        if _is_info(record) and _AWS_TAG_DUMP.search(str(record[-1]))
        else record
        for record in records
    ]


_BUG_358_PARTS = (_bug_358_aws_vm,)


def _bug_358(records: list[LogRecordKey]) -> list[LogRecordKey]:
    for part in _BUG_358_PARTS:
        records = part(records)
    return records


def _new_lines_only(records: list[LogRecordKey]) -> list[LogRecordKey]:
    """BUG-361 and BUG-362 only add REGION/ACTION/SKIPPED/ERRORS/FAILED/RESULT lines inside a
    run begun with `logger.begin`. Parity runs have none, so the oracle list is unchanged; the
    new lines are asserted exactly in the unit tests of each cleaner."""
    return records


RULES: dict[str, Rule] = {
    **helpers_log_fix.RULES,
    "BUG-356": _bug_356,
    "BUG-358": _bug_358,
    "BUG-361": _new_lines_only,
    "BUG-362": _new_lines_only,
}
