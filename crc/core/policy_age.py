"""Age policy of the cleaners: effective age, age floors and snapshot cutoff."""

import datetime

from crc.core.age import AgeSpec

__all__ = [
    "age_to_timedelta",
    "aws_disk_creation_floor",
    "effective_age",
    "is_older_than_floor",
    "snapshot_old_enough",
]


def effective_age(custom_age: AgeSpec | None, default_age: AgeSpec) -> AgeSpec:
    """``retention_age or age``: a truthy custom age tag replaces the default age."""
    return custom_age or default_age


def age_to_timedelta(age: AgeSpec | int) -> datetime.timedelta:
    """Convert an age (int days or days/hours dict) to a timedelta, as AWS disk does."""
    spec: AgeSpec = {"days": age} if isinstance(age, int) else age
    return datetime.timedelta(days=int(spec.get("days", 0)), hours=int(spec.get("hours", 0)))


def aws_disk_creation_floor(
    age: AgeSpec, detach_age: AgeSpec, retention_age: AgeSpec | None
) -> AgeSpec:
    """AWS disk CreateTime floor. With ``age``: the custom age if any, else ``age``.
    Without ``age``: the longer of the custom age and ``detach_age``. ``{}`` when none."""
    if age:
        return retention_age or age
    floors = [spec for spec in (retention_age, detach_age) if spec]
    return max(floors, key=age_to_timedelta) if floors else {}


def is_older_than_floor(created: datetime.datetime, now: datetime.datetime, floor: AgeSpec) -> bool:
    """AWS disk: ``now - created >= floor``, ignoring MAX_AGE. A zero or empty floor gives False."""
    limit = age_to_timedelta(floor)
    return bool(limit) and (now - created) >= limit


def snapshot_old_enough(start_time: datetime.datetime, now: datetime.datetime, days: int) -> bool:
    """AWS snapshot: old when ``start_time <= now - days`` (exactly at the cutoff is old)."""
    return not start_time > now - datetime.timedelta(days=days)
