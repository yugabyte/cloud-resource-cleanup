"""Custom age tag lookup and fail-closed parsing (split out of ``crc.core.age``).

Names are re-exported by ``crc.core.age``; import them from there.
"""

import ast
import logging
from typing import Any

from crc.core import logger
from crc.core.age_spec import KEEP_AGE, AgeSpec, require_positive_age


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


def _checked_retention_age(
    tags: dict[str, str] | list[dict[str, str]], key: str, level: int
) -> tuple[AgeSpec, Any] | None:
    """Shared body of ``validated_retention_age`` / ``get_retention_age``; ``level`` is the
    log level of the "bad tag" diagnostics. Returns ``(normalized, parsed)`` or ``None``
    when the tag is absent; raises ``ValueError`` when it is present but bad."""
    if not key:
        return None
    present, raw_str = lookup_custom_age_tag(tags, key)
    if not present:
        return None
    if raw_str is None or str(raw_str).strip() == "":
        logging.log(level, "Custom age tag %r is present but empty (skipping resource)", key)
        raise ValueError(f"custom age tag {key!r} is present but empty")
    # Parsed here (not via parse_literal) so TypeError becomes ValueError for the callers.
    try:
        parsed = ast.literal_eval(str(raw_str))
    except (ValueError, SyntaxError, TypeError) as e:
        logging.log(
            level,
            "Custom age tag %r=%r is unparseable (skipping resource): %s",
            key,
            raw_str,
            e,
        )
        raise ValueError(f"custom age tag {key!r} is present but unparseable: {raw_str!r}") from e
    try:
        return require_positive_age(parsed, "custom age tag"), parsed
    except ValueError as e:
        logging.log(
            level,
            "Invalid custom age tag %r for key %r (skipping resource): %s",
            parsed,
            key,
            e,
        )
        raise


def validated_retention_age(
    tags: dict[str, str] | list[dict[str, str]],
    key: str,
) -> AgeSpec | None:
    """Parse the custom age tag ``key`` and require a positive age.

    Returns ``None`` when the tag is absent (caller should use ``--age``).
    Raises ``ValueError`` when the tag is present but empty, unparseable, invalid, or
    zero: callers must skip the resource (fail closed), not fall back to ``--age``.
    """
    checked = _checked_retention_age(tags, key, logging.WARNING)
    return None if checked is None else checked[0]


def parse_literal(value: str, key: str) -> Any:
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError) as e:
        logging.error("Error parsing '%s' tag value '%s': %s", key, value, e)
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
    """Return the custom age tag ``key`` (the parsed literal, validated as positive), ``None`` or
    ``KEEP_AGE``.

    ``None``: no key, no tags or the tag is absent (caller uses ``--age``). ``KEEP_AGE``
    (fail closed, BUG-004/BUG-016): the tag is present but empty, unparseable, not finite,
    zero, negative or otherwise invalid. Callers feed it to ``is_old``, which keeps the
    resource; the run log counts it as skipped (``bad_age_tag``) and the cause is logged at
    DEBUG. Validation is the one of ``validated_retention_age``.
    """
    if not key:
        logging.debug("No custom_age_tag_key provided to search for.")
        return None

    if not tags:
        logging.debug("No tags provided to search for custom_age_tag_key.")
        return None

    logging.debug("Searching for custom_age_tag_key: %s", key)
    try:
        value = _find_tag_value(tags, key)
        if value is not None and not isinstance(value, str):
            logging.debug("Custom age tag %r has a non-string value %r", key, value)
            raise ValueError(f"custom age tag {key!r} has a non-string value")
        checked = _checked_retention_age(tags, key, logging.DEBUG)
        if checked is None:
            return None
        logging.info("Found '%s' tag: %s", key, value)
        return checked[1]
    except ValueError:
        logger.current().skip("bad_age_tag")
        return dict(KEEP_AGE)
    except Exception as e:  # noqa: BLE001 - unreadable age tag yields no retention age (fail closed: the caller keeps the resource)
        logging.error("Error retrieving '%s' tag: %s", key, e)

    return None
