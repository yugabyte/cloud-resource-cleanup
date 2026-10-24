"""Age spec type, the keep marker and ``--age`` validation (split out of ``crc.core.age``).

Names are re-exported by ``crc.core.age``; import them from there.
"""

AgeSpec = dict[str, int]

AGE_KEYS = frozenset({"days", "hours"})

# Returned by ``get_retention_age`` for a custom age tag that is present but empty,
# unparseable, not finite, zero or negative. ``is_old`` answers False for it before the
# ``MAX_AGE`` override, so the resource is kept. About 2,700 years: ``timedelta``-safe.
KEEP_AGE: AgeSpec = {"days": 1_000_000}


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
