"""Expected-log rules of the LOG-OCI lane (BUG-353, BUG-358, BUG-361, BUG-362).

Pass ``rules=RULES`` to ``expected_logs`` / ``expected_outcome`` of ``tests/helpers_log_fix.py``
in the OCI tests. Contract: ``docs/refactor/LOGGING.md``.
"""

import logging
import re

from tests import helpers_log_fix
from tests.helpers_log_fix import LogRecordKey, _with_level


def _is_tag_dump(message: str) -> bool:
    """OCI dumps `str(freeform_tags)`: a bare dict repr with no other text."""
    return message.startswith("{") and message.endswith("}")


_LEFTOVER_SKIP = re.compile(r"^Skipping leftover volume .+: exception_tags/notags$")


def _bug_353(records: list[LogRecordKey]) -> list[LogRecordKey]:
    """BUG-353: the OCI age-check freeform tag dict becomes DEBUG, text unchanged."""
    return [
        _with_level(record, logging.DEBUG) if _is_tag_dump(str(record[-1])) else record
        for record in records
    ]


def _bug_358(records: list[LogRecordKey]) -> list[LogRecordKey]:
    """BUG-358: raw tag dumps and the OCI per-resource "Skipping leftover volume" line become
    DEBUG, text unchanged."""
    return [
        _with_level(record, logging.DEBUG)
        if _is_tag_dump(str(record[-1])) or _LEFTOVER_SKIP.search(str(record[-1]))
        else record
        for record in _bug_353(records)
    ]


def _inserted_lines_only(records: list[LogRecordKey]) -> list[LogRecordKey]:
    """BUG-361 and BUG-362 only add RUN START/REGION/ACTION/SKIPPED/ERRORS/FAILED/RESULT lines
    in an attached run (`logger.begin`). A parity run is unattached, so the oracle list is
    unchanged; the new lines are asserted exactly in the logger unit tests, never dropped here."""
    return list(records)


RULES = {
    **helpers_log_fix.RULES,
    "BUG-353": _bug_353,
    "BUG-358": _bug_358,
    "BUG-361": _inserted_lines_only,
    "BUG-362": _inserted_lines_only,
}
