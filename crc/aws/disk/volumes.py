# Copyright (c) Yugabyte, Inc.

"""Tag, name, state and creation-age decisions for one EBS volume (no SDK calls)."""

import datetime
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from crc.core import age as core_age
from crc.core import logger, policy

from .settings import DiskSettings, normalize_age

Volume = Mapping[str, Any]


def tag_map(tags: Sequence[Mapping[str, Any]] | None) -> dict[str, str]:
    """`{Key: Value}` of an EC2 tag list; entries without a `Key` are dropped."""
    return {t["Key"]: t.get("Value", "") for t in tags or [] if t.get("Key")}


def volume_label(region: str, volume: Volume) -> str:
    """`region/volume-id` plus `yb_task=` or `Name=` when the volume has one."""
    volume_id = volume.get("VolumeId")
    tags = tag_map(volume.get("Tags"))
    name = tags.get("Name") or volume_id
    label = f"{region}/{volume_id}"
    yb_task = _yb_task(tags)
    if yb_task:
        return f"{label} yb_task={yb_task}"
    if name != volume_id:
        return f"{label} Name={name}"
    return label


def server_filters(filter_tags: Mapping[str, Sequence[str]]) -> list[dict[str, Any]]:
    """EC2 `describe_volumes` filters: status available plus `tag:key` / `tag-key` per filter."""
    filters: list[dict[str, Any]] = [{"Name": "status", "Values": ["available"]}]
    for key, values in filter_tags.items():
        if values:
            filters.append({"Name": f"tag:{key}", "Values": values})
        else:
            filters.append({"Name": "tag-key", "Values": [key]})
    return filters


def _skip(reason: str, template: str, *args: Any) -> None:
    """One resource kept: the line goes to DEBUG, the count to the run's SKIPPED summary."""
    logging.debug(template, *args)
    logger.current().skip(reason)


def _yb_task(tags: Mapping[str, str]) -> str:
    return policy.aws_disk_yb_task({key: value or "" for key, value in tags.items()})


def older_than_floor(
    created: datetime.datetime, now: datetime.datetime, floor: Mapping[str, int]
) -> bool:
    """True if CreateTime is at least `floor` old, ignoring MAX_AGE."""
    if not policy.age_to_timedelta(dict(floor)):
        return False
    return policy.is_older_than_floor(created, now.astimezone(created.tzinfo), dict(floor))


class VolumeFilter:
    """Decides whether an available volume is a deletion candidate (detach age excluded)."""

    def __init__(self, settings: DiskSettings) -> None:
        self._settings = settings

    def should_skip(self, tags: dict[str, str], name: Any, volume_id: Any) -> bool:
        """Sensitive yb_task, Kubernetes CSI, exception tags, notags, exception regex."""
        settings = self._settings
        yb_task = _yb_task(tags)
        if policy.is_sensitive_yb_task(yb_task):
            _skip(
                "sensitive_yb_task", "Skipping volume %s: sensitive yb_task=%s", volume_id, yb_task
            )
            return True
        if policy.aws_disk_skips_kubernetes(tags, settings.filter_tags):
            _skip(
                "kubernetes_csi",
                "Skipping volume %s: Kubernetes CSI volume without kubernetes filter_tags",
                volume_id,
            )
            return True
        if self._skipped_by_tags(tags, volume_id):
            return True
        if policy.exception_regex_search_matches(settings.exception_regex, name):
            _skip("exception_regex", "Skipping volume %s: Name matched exception_regex", volume_id)
            return True
        return False

    def _skipped_by_tags(self, tags: dict[str, str], volume_id: Any) -> bool:
        settings = self._settings
        for key, value in tags.items():
            if policy.exception_tags_any({key: value}, settings.exception_tags):
                _skip(
                    "exception_tag",
                    "Skipping volume %s: exception tag %s=%s",
                    volume_id,
                    key,
                    value,
                )
                return True
        if policy.notags_all(tags, settings.notags):
            _skip("notags", "Skipping volume %s: matched notags", volume_id)
            return True
        return False

    def matches_filter_tags(self, tags: dict[str, str]) -> bool:
        """Client side `filter_tags`: every key present, values are fnmatch globs."""
        return policy.filter_tags_glob_all_keys(tags, self._settings.filter_tags)

    def is_candidate(self, volume: Volume, now: datetime.datetime) -> bool:
        """State, attachment, tag, name and creation-age checks; logs every skip."""
        volume_id = volume.get("VolumeId")
        if not self._is_unattached(volume, volume_id):
            return False
        settings = self._settings
        tags = volume.get("Tags")
        if not tags and policy.skip_untagged(
            {}, policy.UntaggedMode.SKIP_UNLESS_NOTAGS, has_notags=bool(settings.notags)
        ):
            _skip("untagged", "Skipping volume %s: untagged", volume_id)
            return False
        tags_by_key = tag_map(tags)
        name = tags_by_key.get("Name") or volume_id
        if not self.matches_filter_tags(tags_by_key):
            logger.current().skip("filter_tags")
            return False
        if not policy.name_regex_search_matches(settings.name_regex, name):
            logger.current().skip("name_regex")
            return False
        if self.should_skip(tags_by_key, name, volume_id):
            return False
        return self._old_enough(volume, volume_id, now)

    @staticmethod
    def _is_unattached(volume: Volume, volume_id: Any) -> bool:
        if volume.get("State") != "available":
            logger.current().skip("state")
            return False
        if volume.get("Attachments"):
            _skip("attached", "Skipping volume %s: still has attachments", volume_id)
            return False
        if volume.get("MultiAttachEnabled"):
            _skip("multi_attach", "Skipping volume %s: Multi-Attach enabled", volume_id)
            return False
        return True

    def _old_enough(self, volume: Volume, volume_id: Any, now: datetime.datetime) -> bool:
        settings = self._settings
        created = volume.get("CreateTime")
        if created is None:
            logging.warning("Skipping volume %s: missing CreateTime", volume_id)
            logger.current().skip("no_create_time")
            return False
        retention_age = core_age.get_retention_age(
            volume.get("Tags") or [], settings.custom_age_tag_key
        )
        if retention_age:
            try:
                retention_age = normalize_age(retention_age, "custom age tag")
            except ValueError as e:
                logging.warning("Skipping volume %s: invalid custom age tag: %s", volume_id, e)
                logger.current().skip("invalid_age_tag")
                return False
            logging.info("Updating age for volume: %s", volume_id)
        # Always apply a CreateTime floor. Prefer the most restrictive window so a zero/short
        # custom tag cannot disable detach_age-only mode. Compare timedelta directly so the
        # MAX_AGE env override of is_old cannot shorten the floor.
        floor = policy.aws_disk_creation_floor(settings.age, settings.detach_age, retention_age)
        if not floor:
            logging.warning("Skipping volume %s: no usable CreateTime age floor", volume_id)
            logger.current().skip("no_age_floor")
            return False
        if not older_than_floor(created, now, floor):
            logger.current().skip("too_young")
            return False
        return True
