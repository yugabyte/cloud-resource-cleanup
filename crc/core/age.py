"""Age-gate helpers ported verbatim from ``crc.service.Service`` (no behaviour change).

``is_old`` still reads the ``MAX_AGE`` environment variable, as the original did.
"""

import ast
import datetime
import logging
import os
from typing import Any, cast

AgeSpec = dict[str, int]

AGE_KEYS = frozenset({"days", "hours"})

_HOURS_IN_A_DAY = 24


def _normalize_age_values(age: AgeSpec, label: str) -> AgeSpec:
    normalized: AgeSpec = {}
    for key, value in age.items():
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError) as e:
            # OverflowError: e.g. int(inf) from literal {'days': 1e999}
            raise ValueError(f"{label} {key} must be an integer") from e
        if number < 0:
            raise ValueError(f"{label} {key} cannot be negative")
        normalized[key] = number
    return normalized


def require_positive_age(age: AgeSpec | int | None, label: str = "age") -> AgeSpec:
    """Require a non-empty, >0 age gate.

    ``is_old({})`` returns True, so destructive modules must reject missing or zero
    ages in ``__init__``.
    """
    if not age:
        raise ValueError(
            f"{label} is required and must be greater than zero (empty age matches every resource)"
        )
    if isinstance(age, int):
        age = {"days": age}
    if not isinstance(age, dict):
        raise ValueError(f"{label} must be an int or dict of days/hours")
    extra = set(age) - AGE_KEYS
    if extra:
        raise ValueError(
            f"{label} has unsupported keys {sorted(extra, key=repr)}; use days and/or hours"
        )
    if not (set(age) & AGE_KEYS):
        raise ValueError(f"{label} must include days and/or hours")
    normalized = _normalize_age_values(age, label)
    total_hours = normalized.get("days", 0) * 24 + normalized.get("hours", 0)
    if total_hours <= 0:
        raise ValueError(f"{label} must be greater than zero")
    return normalized


def lookup_custom_age_tag(
    tags: dict[str, str] | list[dict[str, str]],
    key: str,
) -> tuple[bool, str | None]:
    """Return ``(present, raw_value)`` for the custom age tag ``key``.

    ``present`` is True whenever the key exists on the resource (including empty or
    unparseable values). Distinguishes "tag absent" from "tag present but bad", which
    ``get_retention_age`` alone cannot.
    """
    if not key or not tags:
        return False, None
    if isinstance(tags, list):
        for tag in tags:
            if tag.get("Key") == key:
                return True, tag.get("Value")
        return False, None
    # Dict / GCP ScalarMapContainer
    if key in tags:
        return True, tags.get(key)
    return False, None


def validated_retention_age(
    tags: dict[str, str] | list[dict[str, str]],
    key: str,
) -> AgeSpec | None:
    """Parse the custom age tag ``key`` and require a positive age.

    Returns ``None`` when the tag is absent (caller should use ``--age``).
    Raises ``ValueError`` when the tag is present but empty, unparseable, invalid, or
    zero: callers must skip the resource (fail closed), not fall back to ``--age``.
    """
    if not key:
        return None
    present, raw_str = lookup_custom_age_tag(tags, key)
    if not present:
        return None
    if raw_str is None or str(raw_str).strip() == "":
        logging.warning("Custom age tag %r is present but empty (skipping resource)", key)
        raise ValueError(f"custom age tag {key!r} is present but empty")
    # Parsed here (not via parse_literal) so TypeError becomes ValueError for spot
    # callers. get_retention_age swallows parse errors and returns None (callers then
    # treat the tag as absent: fail-open to --age); fail-closed callers use this.
    try:
        parsed = ast.literal_eval(str(raw_str))
    except (ValueError, SyntaxError, TypeError) as e:
        logging.warning(
            "Custom age tag %r=%r is unparseable (skipping resource): %s",
            key,
            raw_str,
            e,
        )
        raise ValueError(f"custom age tag {key!r} is present but unparseable: {raw_str!r}") from e
    try:
        return require_positive_age(parsed, "custom age tag")
    except ValueError as e:
        logging.warning(
            "Invalid custom age tag %r for key %r (skipping resource): %s",
            parsed,
            key,
            e,
        )
        raise


def get_min_age(dict1: AgeSpec, dict2: AgeSpec) -> AgeSpec:
    """Return the age spec representing the lesser duration; ties return ``dict2``.

    Both dicts may contain ``days`` and/or ``hours``; missing keys count as 0.
    """
    total_hours1 = (dict1.get("days", 0) * _HOURS_IN_A_DAY) + dict1.get("hours", 0)
    total_hours2 = (dict2.get("days", 0) * _HOURS_IN_A_DAY) + dict2.get("hours", 0)
    return dict1 if total_hours1 < total_hours2 else dict2


def _apply_max_age_override(age: AgeSpec) -> AgeSpec:
    max_age_raw = os.getenv("MAX_AGE")
    if max_age_raw:
        max_age: Any = ast.literal_eval(max_age_raw)
        if get_min_age(age, max_age) == max_age:
            logging.info(
                f"Overwriting resource age: Setting age to the maximum threshold of {max_age}."
            )
            return cast(AgeSpec, max_age)
    return age


def _log_older(amount: float, unit: str, threshold: float) -> None:
    logging.debug(
        f"The resource is {amount} {unit} old and older than the threshold of {threshold} {unit}."
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
    returns True (matches every resource). The ``MAX_AGE`` environment variable, a
    literal dict, replaces ``age`` when it is the shorter duration.
    """
    if not age:
        logging.debug("Age is not specified. Ignoring age threshold check")
        return True

    if isinstance(age, int):
        age = {"days": age}

    age = _apply_max_age_override(age)

    logging.debug(f"Validating resource age with threshold: {age}")
    if _meets_threshold(age, current_time - creation_time):
        return True
    logging.debug("The resource is not older than the threshold specified in the age argument")
    return False


def parse_literal(value: str, key: str) -> Any:
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError) as e:
        logging.error(f"Error parsing '{key}' tag value '{value}': {e}")
        return None


def _find_tag_value(tags: dict[str, str] | list[dict[str, str]], key: str) -> str | None:
    if isinstance(tags, list):
        for tag in tags:
            if tag.get("Key") == key:
                value = tag.get("Value")
                if value:
                    return value
        return None
    # Dict tags (Azure, GCP ScalarMapContainer).
    return tags.get(key)


def get_retention_age(tags: dict[str, str] | list[dict[str, str]], key: str) -> Any:
    """Return the parsed custom age tag ``key``, or ``None`` (fail open).

    Absent, empty and unparseable tags all give ``None``; parse errors are logged.
    """
    if not key:
        logging.debug("No custom_age_tag_key provided to search for.")
        return None

    if not tags:
        logging.debug("No tags provided to search for custom_age_tag_key.")
        return None

    logging.debug(f"Searching for custom_age_tag_key: {key}")
    try:
        value = _find_tag_value(tags, key)
        if value:
            logging.info(f"Found '{key}' tag: {value}")
            return parse_literal(value, key)
    except Exception as e:
        logging.error(f"Error retrieving '{key}' tag: {e}")

    return None
