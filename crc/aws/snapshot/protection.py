# Copyright (c) Yugabyte, Inc.

"""Tag reading, exception-tag protection and AMI usage of the snapshot cleaner."""

import logging
from collections.abc import Mapping
from typing import Any

from crc.core import policy

from .client import SnapshotClient


def is_tag_list(tags: Any) -> bool:
    return isinstance(tags, list) and all(isinstance(t, Mapping) for t in tags)


def read_tags(tags: Any) -> dict[str, Any]:
    """BUG-144: tag map of a list of tag entries; an entry without `Key` is ignored and a
    missing `Value` reads as ''. Non-list or non-mapping `tags` still raise as on main."""
    if not tags:
        return {}
    if not is_tag_list(tags):
        return {t["Key"]: t["Value"] for t in tags}
    tag_map: dict[str, Any] = {}
    for entry in tags:
        if "Key" not in entry:
            logging.debug("Ignoring a tag entry without Key: %s", dict(entry))
            continue
        tag_map[entry["Key"]] = entry.get("Value", "")
    return tag_map


def is_protected(
    tags: Any,
    snap_id: str,
    exception_tags: Any,
    tag_map: dict[str, Any] | None = None,
) -> bool:
    """Any exception tag key present (with a listed value, when values are given) protects.
    `tag_map` is the already read `_read_tags` result; tags that are not a list of mappings
    still raise as on main."""
    if not tags or not exception_tags:
        return False
    if tag_map is None:
        tag_map = read_tags(tags)
    for key, values in exception_tags.items():
        if policy.exception_tags_any(tag_map, {key: values}):
            logging.debug("Snap %s has tag %s", snap_id, key)
            return True
    return False


def used_by_amis(client: SnapshotClient) -> set[str]:
    used: set[str] = set()
    for image in client.describe_images(Owners=["self"])["Images"]:
        for mapping in image.get("BlockDeviceMappings", []):
            snap_id = mapping.get("Ebs", {}).get("SnapshotId")
            if snap_id:
                used.add(snap_id)
    return used
