# Copyright (c) Yugabyte, Inc.

"""Pure decisions of the spot instance request cleaner: tags, protection, leftover markers."""

from collections.abc import Mapping, Sequence
from typing import Any

from crc.aws.spot_instance_requests.model import (
    LEFTOVER_FROM_TAG,
    LEFTOVER_SCOPE_PREFIX,
    SpotSettings,
    Tag,
    TagList,
)
from crc.core import policy


def tag_map(tags: TagList) -> dict[str, Any]:
    """Key to value for entries that have a key (a missing value becomes an empty string)."""
    return {t["Key"]: t.get("Value", "") for t in tags if t.get("Key")}


def should_skip(settings: SpotSettings, tags: TagList) -> bool:
    """Exception tags (any key) or notags (all keys) protect the resource."""
    if not settings.exception_tags and not settings.notags:
        return False
    all_tags: dict[str, Any] = {}
    for tag in tags:
        key = tag.get("Key")
        if not key:
            continue
        value = tag.get("Value", "")
        if settings.exception_tags and policy.exception_tags_any(
            {key: value}, settings.exception_tags
        ):
            return True
        all_tags[key] = value
    return policy.notags_all(all_tags, settings.notags)


def matches_filter(settings: SpotSettings, tags: TagList) -> bool:
    """Client side `filter_tags`: every key present, values matched as globs."""
    return policy.filter_tags_glob_all_keys(tag_map(tags), settings.filter_tags)


def volume_tags_protect(settings: SpotSettings, tags: TagList) -> bool:
    """Volume-owned tags protect it: exception/notags or a sensitive yb_task."""
    if should_skip(settings, tags):
        return True
    return policy.aws_spot_has_sensitive_yb_task(tag_map(tags))


def is_crc_marker(key: str) -> bool:
    return key == LEFTOVER_FROM_TAG or key.startswith(LEFTOVER_SCOPE_PREFIX)


def own_volume_tags(tags: Sequence[Tag]) -> list[Tag]:
    """Tags of the volume itself, without the crc_leftover markers."""
    return [t for t in tags if not is_crc_marker(t["Key"])]


def own_tags_for_protect(volume: Mapping[str, Any]) -> list[Tag]:
    out: list[Tag] = []
    for t in volume.get("Tags") or []:
        key = t.get("Key") or ""
        if not key or is_crc_marker(key):
            continue
        out.append(t)
    return out


def effective_sweep_tags(volume: Mapping[str, Any]) -> list[Tag]:
    """Scope copies unprefixed plus the volume's own tags (used when the request is gone)."""
    out: list[Tag] = []
    for t in volume.get("Tags") or []:
        key = t.get("Key") or ""
        if not key or key == LEFTOVER_FROM_TAG:
            continue
        if key.startswith(LEFTOVER_SCOPE_PREFIX):
            out.append({"Key": key[len(LEFTOVER_SCOPE_PREFIX) :], "Value": t.get("Value", "")})
        else:
            out.append(t)
    return out


def scope_tags_for_volume(
    settings: SpotSettings, request_id: str, request_tags: TagList | None
) -> list[dict[str, str]]:
    """crc_leftover_of plus filter/exception/notags values under the scope prefix."""
    tags: list[dict[str, str]] = [{"Key": LEFTOVER_FROM_TAG, "Value": request_id}]
    keys_needed = set(settings.filter_tags)
    keys_needed.update(settings.exception_tags)
    keys_needed.update(settings.notags)
    if not keys_needed:
        return tags
    values = tag_map(request_tags or [])
    for key in sorted(keys_needed):
        if key not in values:
            continue
        tags.append({"Key": LEFTOVER_SCOPE_PREFIX + key, "Value": values[key]})
    return tags
