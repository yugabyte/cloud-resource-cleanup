"""Expected log lists of the LOG rungs: the oracle logs rewritten one bug rule at a time.

Oracles in ``tests/legacy/*.txt`` stay frozen. A parity test compares the new code with
``expected_logs(oracle_logs, {<bug IDs of the lane>})``. A rule may only change the level of a
line or insert documented new lines; it never drops an oracle line or changes its text.
Contract: ``docs/refactor/LOGGING.md``.
"""

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

LogRecordKey = tuple[Any, ...]
Rule = Callable[[list[LogRecordKey]], list[LogRecordKey]]

_AGE_CHECK_PATTERNS = (
    re.compile(r"^Validating resource age with threshold: "),
    re.compile(r"^The resource is .+ old and older than the threshold of .+\.$"),
    re.compile(r"^The resource is not older than the threshold specified in the age argument$"),
    re.compile(r"^No tags provided to search for custom_age_tag_key\.$"),
    re.compile(r"^Searching for custom_age_tag_key: "),
)


def _is_age_check(message: str) -> bool:
    return any(pattern.search(message) for pattern in _AGE_CHECK_PATTERNS)


def _with_level(record: LogRecordKey, level: int) -> LogRecordKey:
    """Return the record with its level replaced, keeping the level's type (int or name)."""
    current = record[-2]
    new_level: Any = logging.getLevelName(level) if isinstance(current, str) else level
    return (*record[:-2], new_level, record[-1])


def _bug_356(records: list[LogRecordKey]) -> list[LogRecordKey]:
    """BUG-356: the per-resource age-check lines become DEBUG, text unchanged."""
    return [
        _with_level(record, logging.DEBUG) if _is_age_check(str(record[-1])) else record
        for record in records
    ]


# Core rules only. Each cloud lane keeps its own rules in tests/log_fix_<lane>.py and passes
# them as ``rules`` (``{**RULES, ...}``), so lanes never edit this file and merge cleanly.
RULES: dict[str, Rule] = {
    "BUG-356": _bug_356,
}


def expected_logs(
    oracle_logs: Sequence[LogRecordKey], bugs: set[str], rules: Mapping[str, Rule] | None = None
) -> list[LogRecordKey]:
    """Rewrite the oracle log records with the rule of each bug ID (from ``rules``, default
    the core ``RULES``).

    A record is a tuple whose last item is the message and whose second to last is the level
    (``levelno`` or ``levelname``); any items before them (for example the logger name) are kept.
    Rules apply in sorted bug-ID order. An unknown bug ID raises ``KeyError``.
    """
    records = [tuple(record) for record in oracle_logs]
    for bug in sorted(bugs):
        records = (RULES if rules is None else rules)[bug](records)
    return records


def expected_outcome(
    oracle: tuple[Any, Any, Sequence[LogRecordKey]],
    bugs: set[str],
    rules: Mapping[str, Rule] | None = None,
) -> Any:
    """Rewrite the log list (third item) of a ``(kind, result, logs)`` parity outcome."""
    kind, result, logs = oracle
    return kind, result, expected_logs(logs, bugs, rules)
