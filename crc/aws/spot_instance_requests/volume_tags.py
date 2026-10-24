# Copyright (c) Yugabyte, Inc.

"""Read-only lookups of a volume's tags and the protection decision of one volume."""

import logging
from collections.abc import Sequence

from botocore.exceptions import BotoCoreError, ClientError

from crc.aws.spot_instance_requests.client import SpotClient
from crc.aws.spot_instance_requests.model import LEFTOVER_SCOPE_PREFIX, SpotSettings, Tag
from crc.aws.spot_instance_requests.rules import own_volume_tags, volume_tags_protect
from crc.core import logger

ERRORS = (ClientError, BotoCoreError)


def error_code(error: ClientError) -> str | None:
    """The AWS error code of a ClientError, or None."""
    code: str | None = (error.response.get("Error") or {}).get("Code")
    return code


def _volume_tags(client: SpotClient, volume_id: str) -> list[Tag]:
    desc = client.describe_volumes(VolumeIds=[volume_id])
    volumes = desc.get("Volumes") or []
    if not volumes:
        return []
    return [t for t in (volumes[0].get("Tags") or []) if t.get("Key")]


def describe_volume_tag_list(client: SpotClient, volume_id: str) -> list[Tag] | None:
    """The volume's tags, or None if describe fails."""
    try:
        return _volume_tags(client, volume_id)
    except ERRORS as e:
        logging.error("describe_volumes failed for %s while checking volume tags: %s", volume_id, e)
        return None


def volume_tag_keys(client: SpotClient, volume_id: str) -> set[str] | None:
    """Existing tag keys on a volume, or None if describe fails."""
    try:
        return {t["Key"] for t in _volume_tags(client, volume_id)}
    except ERRORS as e:
        logging.error(
            "describe_volumes failed for %s while preparing leftover tags: %s", volume_id, e
        )
        return None


def prefixed_scope_keys_on_volume(client: SpotClient, volume_id: str) -> list[str]:
    """crc_leftover_tag_* keys currently on the volume."""
    keys = volume_tag_keys(client, volume_id)
    if not keys:
        return []
    return sorted(k for k in keys if k.startswith(LEFTOVER_SCOPE_PREFIX))


def volume_ok_to_delete(
    settings: SpotSettings, client: SpotClient, volume_id: str, region: str | None = None
) -> bool:
    """Fail closed: skip when tags cannot be read or protect the volume."""
    tags = describe_volume_tag_list(client, volume_id)
    if tags is None:
        logging.error("Skipping leftover volume %s: could not read tags (fail closed)", volume_id)
        logger.current().fail(region, volume_id, "could not read volume tags (fail closed)")
        return False
    if volume_tags_protect(settings, own_volume_tags(tags)):
        logging.debug(
            "Skipping leftover volume %s: protected by volume tags "
            "(exception/notags/sensitive yb_task)",
            volume_id,
        )
        logger.current().skip("volume_protected")
        return False
    return True


def filter_deletable_leftover_volumes(
    settings: SpotSettings,
    client: SpotClient,
    volume_ids: Sequence[str],
    region: str | None = None,
) -> list[str]:
    return [vid for vid in volume_ids if volume_ok_to_delete(settings, client, vid, region)]
