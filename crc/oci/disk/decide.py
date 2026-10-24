# Copyright (c) Yugabyte, Inc.

"""Which volumes and boot volumes are fair game: state, attachment, tag and age checks."""

import datetime
import logging
from collections.abc import Iterable
from typing import cast

from crc.core import age as core_age
from crc.core import policy
from crc.core.cleaner import Candidate, Skipped
from crc.oci.disk.api import SdkAttachment, SdkVolume
from crc.oci.disk.settings import (
    AVAILABLE,
    IN_USE_ATTACHMENT_STATES,
    UNKNOWN_REASONS,
    DiskSettings,
)


def skip_reason(
    settings: DiskSettings,
    volume: SdkVolume,
    attached_ids: set[str] | None,
    kind: str,
    now: datetime.datetime,
) -> str | None:
    """Why the volume is kept, or `None` when it is a candidate. `attached_ids` is `None` when
    the attachments are unknown."""
    if volume.lifecycle_state != AVAILABLE:
        return "state is not AVAILABLE"
    if attached_ids is None or volume.id in attached_ids:
        return UNKNOWN_REASONS[kind] if attached_ids is None else "attached"
    tags = policy.normalize_tags(volume.freeform_tags)
    if policy.should_skip_by_tags(tags, settings.exception_tags, settings.notags):
        return "matches exception_tags or notags"
    if not policy.filter_tags_any_key(tags, settings.filter_tags):
        return "does not match filter_tags"
    if not is_old(settings, volume, now):
        return "not older than age"
    return None


def is_old(settings: DiskSettings, volume: SdkVolume, now: datetime.datetime) -> bool:
    """Older than `age`, or than the volume's own custom age tag (capped by MAX_AGE)."""
    created_time = volume.time_created
    current = now.astimezone(created_time.tzinfo)
    tags = volume.freeform_tags or {}
    logging.debug(tags)
    retention_age = core_age.get_retention_age(
        cast(dict[str, str], tags), settings.custom_age_tag_key
    )
    if retention_age:
        logging.info("Updating age for volume: %s", volume.display_name)
    return core_age.is_old(policy.effective_age(retention_age, settings.age), current, created_time)


def sort_decisions(
    decisions: Iterable[Candidate | Skipped], candidates: list[Candidate], skipped: list[Skipped]
) -> None:
    """Append each decision to `candidates` or `skipped`, in order."""
    for decision in decisions:
        if isinstance(decision, Candidate):
            candidates.append(decision)
        else:
            skipped.append(decision)


def in_use(attachments: Iterable[SdkAttachment]) -> list[SdkAttachment]:
    """The attachments that still claim their volume (`ATTACHED` or `ATTACHING`)."""
    return [a for a in attachments if a.lifecycle_state in IN_USE_ATTACHMENT_STATES]
