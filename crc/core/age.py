"""Age-gate helpers ported verbatim from ``crc.service.Service`` (no behaviour change).

``is_old`` applies the ``MAX_AGE`` override, but this module reads no environment variable
(ARCH-003): ``crc/cli`` installs a source with ``set_max_age_source`` (it reads ``MAX_AGE``
there), and ``max_age_override`` calls it at each decision, as the original read the
variable. The age spec and its validation live in ``crc.core.age_spec`` and the custom age
tag parsing in ``crc.core.age_tags``; both are re-exported here so
``crc.core.age.<name>`` keeps working.
"""

import ast
import datetime
import logging
from collections.abc import Callable
from typing import Any, cast

from crc.core.age_spec import AGE_KEYS, KEEP_AGE, AgeSpec, require_positive_age
from crc.core.age_tags import (
    get_retention_age,
    lookup_custom_age_tag,
    parse_literal,
    validated_retention_age,
)

__all__ = [
    "AGE_KEYS",
    "KEEP_AGE",
    "AgeSpec",
    "get_min_age",
    "get_retention_age",
    "is_old",
    "lookup_custom_age_tag",
    "max_age_override",
    "parse_literal",
    "require_positive_age",
    "set_max_age_source",
    "validated_retention_age",
]

_HOURS_IN_A_DAY = 24

MaxAgeSource = Callable[[], str | None]

_max_age_source: MaxAgeSource | None = None


def set_max_age_source(source: MaxAgeSource | None) -> None:
    """Install (or, with ``None``, remove) the function that returns the raw ``MAX_AGE`` text.

    Only ``crc/cli`` calls this. Without a source ``is_old`` applies no override.
    """
    global _max_age_source
    _max_age_source = source


def max_age_override() -> str | None:
    """The raw ``MAX_AGE`` text the installed source returns, else ``None``."""
    return None if _max_age_source is None else _max_age_source()


def get_min_age(dict1: AgeSpec, dict2: AgeSpec) -> AgeSpec:
    """Return the age spec representing the lesser duration; ties return ``dict2``.

    Both dicts may contain ``days`` and/or ``hours``; missing keys count as 0.
    """
    total_hours1 = (dict1.get("days", 0) * _HOURS_IN_A_DAY) + dict1.get("hours", 0)
    total_hours2 = (dict2.get("days", 0) * _HOURS_IN_A_DAY) + dict2.get("hours", 0)
    return dict1 if total_hours1 < total_hours2 else dict2


def _apply_max_age_override(age: AgeSpec) -> AgeSpec:
    max_age_raw = max_age_override()
    if max_age_raw:
        max_age: Any = ast.literal_eval(max_age_raw)
        if get_min_age(age, max_age) == max_age:
            logging.info(
                "Overwriting resource age: Setting age to the maximum threshold of %s.", max_age
            )
            return cast(AgeSpec, max_age)
    return age


def _log_older(amount: float, unit: str, threshold: float) -> None:
    logging.debug(
        "The resource is %s %s old and older than the threshold of %s %s.",
        amount,
        unit,
        threshold,
        unit,
    )


def _meets_threshold(age: AgeSpec, age_delta: datetime.timedelta) -> bool:
    age_in_days = age_delta.days
    age_in_hours = age_delta.total_seconds() / 3600

    if "days" in age and "hours" in age:
        threshold_in_days = int(age["days"])
        extra_hours = int(age["hours"])
        threshold_in_seconds = (threshold_in_days * 24 * 3600) + (extra_hours * 3600)
        total_threshold_hours = threshold_in_seconds / 3600
        if age_in_hours >= total_threshold_hours:
            _log_older(age_in_hours, "hours", total_threshold_hours)
            return True
    elif "days" in age:
        threshold_in_days = int(age["days"])
        if age_in_days >= threshold_in_days:
            _log_older(age_in_days, "days", threshold_in_days)
            return True
    elif "hours" in age:
        threshold_in_hours = int(age["hours"])
        if age_in_hours >= threshold_in_hours:
            _log_older(age_in_hours, "hours", threshold_in_hours)
            return True
    return False


def is_old(
    age: AgeSpec | int,
    current_time: datetime.datetime,
    creation_time: datetime.datetime,
) -> bool:
    """Return True if the resource is at least ``age`` old.

    ``age`` is an int (days) or a dict with ``days`` and/or ``hours``. An empty age
    returns True (matches every resource). The ``MAX_AGE`` text from the installed source (a
    literal dict) replaces ``age`` when it is the shorter duration. ``KEEP_AGE`` (the
    bad custom age tag marker) is never old and ignores ``MAX_AGE``.
    """
    if not age:
        logging.debug("Age is not specified. Ignoring age threshold check")
        return True

    if isinstance(age, int):
        age = {"days": age}

    if age == KEEP_AGE:
        logging.debug("Custom age tag is bad (BUG-004/BUG-016): keeping the resource")
        return False

    age = _apply_max_age_override(age)

    logging.debug("Validating resource age with threshold: %s", age)
    if _meets_threshold(age, current_time - creation_time):
        return True
    logging.debug("The resource is not older than the threshold specified in the age argument")
    return False
