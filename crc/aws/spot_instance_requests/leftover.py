# Copyright (c) Yugabyte, Inc.

"""Mutating leftover-volume steps: marker tags, marker invalidation, wait and delete."""

import logging
from collections.abc import Mapping, Sequence

from botocore.exceptions import BotoCoreError, ClientError

from crc.aws.spot_instance_requests.client import SpotClient
from crc.aws.spot_instance_requests.model import (
    LEFTOVER_FROM_TAG,
    LEFTOVER_INVALID_VALUE,
    SpotSettings,
    TagList,
    Waiter,
)
from crc.aws.spot_instance_requests.rules import scope_tags_for_volume
from crc.aws.spot_instance_requests.volumes import (
    ERRORS,
    prefixed_scope_keys_on_volume,
    volume_tag_keys,
)
from crc.core import logger

ScopeMap = Mapping[str, Sequence[str]]


def _overwrite_scope_keys(client: SpotClient, volume_id: str, scope_keys: Sequence[str]) -> None:
    """Delete scope tags one by one, then overwrite the sticky values with the sentinel."""
    for key in scope_keys:
        try:
            client.delete_tags(Resources=[volume_id], Tags=[{"Key": key}])
        except ERRORS as per_e:
            logging.error("Failed to clear scope tag %s from volume %s: %s", key, volume_id, per_e)
    # Overwrite sticky values so a later disk run with the same filter_tags cannot match them.
    try:
        client.create_tags(
            Resources=[volume_id],
            Tags=[{"Key": k, "Value": LEFTOVER_INVALID_VALUE} for k in scope_keys],
        )
    except ERRORS as overwrite_e:
        logging.error(
            "Failed to overwrite sticky scope tags on volume %s: %s", volume_id, overwrite_e
        )


def _clear_scope_keys(client: SpotClient, volume_id: str, scope_keys: Sequence[str]) -> None:
    try:
        client.delete_tags(Resources=[volume_id], Tags=[{"Key": k} for k in scope_keys])
    except ERRORS as e:
        logging.error(
            "Failed to clear scope tags from volume %s: %s — trying per-key delete, then overwrite",
            volume_id,
            e,
        )
        _overwrite_scope_keys(client, volume_id, scope_keys)


def _mark_invalid(client: SpotClient, volume_id: str) -> None:
    try:
        client.create_tags(
            Resources=[volume_id],
            Tags=[{"Key": LEFTOVER_FROM_TAG, "Value": LEFTOVER_INVALID_VALUE}],
        )
        logging.info(
            "Marked volume %s as %s=%s (sweep will skip)",
            volume_id,
            LEFTOVER_FROM_TAG,
            LEFTOVER_INVALID_VALUE,
        )
    except ERRORS as e:
        logging.error("Failed to invalidate %s on volume %s: %s", LEFTOVER_FROM_TAG, volume_id, e)


def invalidate_leftover_marker(
    client: SpotClient,
    volume_ids: Sequence[str],
    added_scope_keys: ScopeMap | None = None,
) -> None:
    """Stop a volume from being swept after a failed cancel/terminate/tag.

    Removes crc_leftover_of and every crc_leftover_tag_* key. Prefer delete_tags; if that
    fails, retry scope-only clear, then overwrite the marker with the sentinel.
    """
    scope_map = added_scope_keys or {}
    for volume_id in volume_ids:
        scope_keys = list(scope_map.get(volume_id) or [])
        for key in prefixed_scope_keys_on_volume(client, volume_id):
            if key not in scope_keys:
                scope_keys.append(key)
        tags_to_clear = [{"Key": LEFTOVER_FROM_TAG}] + [{"Key": k} for k in scope_keys]
        try:
            client.delete_tags(Resources=[volume_id], Tags=tags_to_clear)
            continue
        except ERRORS as e:
            logging.error(
                "Failed to clear leftover/scope tags from volume %s: %s — "
                "trying scope-only clear + sentinel",
                volume_id,
                e,
            )
        if scope_keys:
            _clear_scope_keys(client, volume_id, scope_keys)
        _mark_invalid(client, volume_id)


def tag_volumes_as_leftover(
    settings: SpotSettings,
    client: SpotClient,
    request_id: str,
    volume_ids: Sequence[str],
    request_tags: TagList | None = None,
) -> dict[str, list[str]] | None:
    """Mark each DoT=false volume so a later run finds it after the request is gone.

    Returns volume id to the scope keys this run added, or None if tagging failed (partial
    markers are invalidated first).
    """
    if not volume_ids:
        return {}
    tagged_scope: dict[str, list[str]] = {}
    for volume_id in volume_ids:
        # describe_volumes fail-closed: do not cancel if we cannot tag safely.
        if volume_tag_keys(client, volume_id) is None:
            invalidate_leftover_marker(client, list(tagged_scope), tagged_scope)
            return None
        tags = scope_tags_for_volume(settings, request_id, request_tags)
        scope_keys = [t["Key"] for t in tags if t["Key"] != LEFTOVER_FROM_TAG]
        try:
            client.create_tags(Resources=[volume_id], Tags=tags)
            tagged_scope[volume_id] = scope_keys
        except ERRORS as e:
            logging.error(
                "Failed to tag leftover volume %s for spot request %s: %s",
                volume_id,
                request_id,
                e,
            )
            # BotoCoreError may mean AWS applied the tags anyway: invalidate this one too.
            tagged_scope[volume_id] = scope_keys
            invalidate_leftover_marker(client, list(tagged_scope), tagged_scope)
            return None
    logging.info(
        "Tagged leftover volumes %s with %s=%s (plus missing scope tags)",
        list(volume_ids),
        LEFTOVER_FROM_TAG,
        request_id,
    )
    return tagged_scope


def _volume_step(client: SpotClient, volume_id: str) -> str | None:
    """One poll: "deleted", "already_gone" or None while the volume is not available yet."""
    desc = client.describe_volumes(VolumeIds=[volume_id])
    volumes = desc.get("Volumes") or []
    if not volumes:
        logging.info("Volume %s already gone", volume_id)
        return "already_gone"
    state = volumes[0].get("State")
    if state == "available":
        client.delete_volume(VolumeId=volume_id)
        logging.info("Deleted leftover volume %s", volume_id)
        return "deleted"
    if state == "deleted":
        return "already_gone"
    logging.info("Waiting for volume %s to become available (state=%s)", volume_id, state)
    return None


def wait_volume_available_then_delete(
    settings: SpotSettings,
    waiter: Waiter,
    client: SpotClient,
    volume_id: str,
    region: str | None = None,
) -> str | None:
    """Wait for available and delete. "deleted" when this run issued delete_volume,
    "already_gone" when the volume is already missing, or None on failure."""
    deadline = waiter.monotonic() + settings.wait_sec
    while waiter.monotonic() < deadline:
        try:
            result = _volume_step(client, volume_id)
            if result is not None:
                return result
        except ClientError as e:
            code = (e.response.get("Error") or {}).get("Code")
            if code == "InvalidVolume.NotFound":
                return "already_gone"
            logging.error("Error waiting/deleting volume %s: %s", volume_id, e)
            logger.current().fail(region, volume_id, e, logged=True)
            return None
        except BotoCoreError as e:
            logging.error("Transport error waiting/deleting volume %s: %s", volume_id, e)
            logger.current().fail(region, volume_id, e, logged=True)
            return None
        waiter.sleep(settings.poll_interval_sec)
    logging.error("Timed out waiting for volume %s to become available; not deleted", volume_id)
    logger.current().fail(
        region, volume_id, "timed out waiting for the volume to become available", logged=True
    )
    return None
