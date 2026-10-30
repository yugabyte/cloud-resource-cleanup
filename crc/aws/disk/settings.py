# Copyright (c) Yugabyte, Inc.

"""Validated settings of one EBS disk cleanup run (age gates, filters, regexes)."""

import datetime
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from crc.core import policy

AGE_KEYS = frozenset({"days", "hours"})

# CloudWatch retains EBS metrics for 455 days. A longer detach_age cannot be answered, so it
# is rejected rather than silently shortened.
CW_MAX_LOOKBACK = datetime.timedelta(days=455)

AgeSpec = dict[str, int]
TagSpec = Mapping[str, Sequence[str]]


@dataclass(frozen=True, slots=True)
class DiskSettings:
    """Filters and age gates of one run, as validated by `build_settings()`."""

    filter_tags: TagSpec = field(default_factory=dict)
    exception_tags: TagSpec = field(default_factory=dict)
    notags: TagSpec = field(default_factory=dict)
    name_regex: Sequence[str] = ()
    exception_regex: Sequence[str] = ()
    custom_age_tag_key: Any = None
    age: AgeSpec = field(default_factory=dict)
    detach_age: AgeSpec = field(default_factory=dict)


def normalize_age(age: Any, label: str) -> AgeSpec:
    """Validate an age (int days or days/hours dict); `{}` when empty. Raises `ValueError`."""
    if not age:
        return {}
    if isinstance(age, int):
        age = {"days": age}
    extra = set(age) - AGE_KEYS
    if extra:
        raise ValueError(f"{label} has unsupported keys {sorted(extra)}; use days and/or hours")
    if not (set(age) & AGE_KEYS):
        raise ValueError(f"{label} must include days and/or hours")
    normalized: AgeSpec = {}
    for key, value in age.items():
        try:
            number = int(value)
        except (TypeError, ValueError) as e:
            raise ValueError(f"{label} {key} must be an integer") from e
        if number < 0:
            raise ValueError(f"{label} {key} cannot be negative")
        normalized[key] = number
    if not policy.age_to_timedelta(normalized):
        raise ValueError(f"{label} must be greater than zero")
    return normalized


def build_settings(options: Mapping[str, Any]) -> DiskSettings:
    """Validate raw options in the order of `main`: ages, regexes, the age gate, the window.

    Keys: filter_tags, exception_tags, notags, name_regex, exception_regex,
    custom_age_tag_key, age, detach_age (all optional, `None` or empty means unset).
    """
    settings = DiskSettings(
        filter_tags=options.get("filter_tags") or {},
        exception_tags=options.get("exception_tags") or {},
        notags=options.get("notags") or {},
        name_regex=options.get("name_regex") or [],
        exception_regex=options.get("exception_regex") or [],
        custom_age_tag_key=options.get("custom_age_tag_key"),
        age=normalize_age(options.get("age"), "age"),
        detach_age=normalize_age(options.get("detach_age"), "detach_age"),
    )
    policy.compile_patterns(settings.name_regex, "name_regex")
    policy.compile_patterns(settings.exception_regex, "exception_regex")
    _require_age_gate(settings)
    return settings


def _require_age_gate(settings: DiskSettings) -> None:
    # Service.is_old() returns True for an empty age, so without this an age-less run would
    # delete every available volume in every region.
    if not settings.age and not settings.detach_age:
        raise ValueError(
            "AWS disk cleanup requires an age gate: pass --age (measured from "
            "volume creation) and/or --detach_age (CloudWatch activity while "
            "attached to a running instance — not a last-detach timestamp, and "
            "blind to attachments on stopped instances)."
        )
    if settings.detach_age:
        window = policy.age_to_timedelta(settings.detach_age)
        if window > CW_MAX_LOOKBACK:
            raise ValueError(
                f"AWS --detach_age {window} exceeds CloudWatch EBS metric retention "
                f"({CW_MAX_LOOKBACK}). Shorten detach_age; a longer window cannot be evaluated."
            )
