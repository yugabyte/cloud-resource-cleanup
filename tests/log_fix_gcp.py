"""GCP log rules: the oracle logs rewritten with the GCP bug rules.

The core rules and the rewriter live in ``tests/helpers_log_fix.py``. This module adds the
rules of the GCP cleaners (BUG-358) and exposes ``RULES``; GCP parity tests pass ``rules=RULES``
to ``expected_logs`` / ``expected_outcome``. Contract: ``docs/logging.md``.
"""

import logging
import re

from tests import helpers_log_fix
from tests.helpers_log_fix import LogRecordKey, Rule

_RAW_DUMP = re.compile(r"^(\{.*\}|namespace\(.*\))$", re.DOTALL)
_LABEL_SKIP = re.compile(r"^Skipping leftover Spot disk .+: exception_labels/notags$")


def _bug_358(records: list[LogRecordKey]) -> list[LogRecordKey]:
    """BUG-358: the raw tag/label dict (or address object) dump of each listed resource and the
    per-resource "Skipping leftover Spot disk" line (GCP) become DEBUG, text unchanged."""
    info = (logging.INFO, "INFO")
    return [
        helpers_log_fix._with_level(record, logging.DEBUG)
        if record[-2] in info
        and (_RAW_DUMP.match(str(record[-1])) or _LABEL_SKIP.match(str(record[-1])))
        else record
        for record in records
    ]


RULES: dict[str, Rule] = {**helpers_log_fix.RULES, "BUG-358": _bug_358}
