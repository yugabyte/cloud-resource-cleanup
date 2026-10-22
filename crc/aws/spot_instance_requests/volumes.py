# Copyright (c) Yugabyte, Inc.

"""Read-only lookups of instances and leftover volumes, and the sweep gates."""

import datetime
import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from crc.aws.spot_instance_requests.client import SpotClient
from crc.aws.spot_instance_requests.model import (
    LEFTOVER_FROM_TAG,
    LEFTOVER_INVALID_VALUE,
    LEFTOVER_SCOPE_PREFIX,
    NOT_FOUND_INSTANCE,
    NOT_FOUND_REQUEST,
    Inspection,
    RequestMatch,
    SpotSettings,
    Tag,
    TagList,
)
from crc.aws.spot_instance_requests.requests import passes_age_gate
from crc.aws.spot_instance_requests.rules import (
    effective_sweep_tags,
    matches_filter,
    own_tags_for_protect,
    own_volume_tags,
    should_skip,
    tag_map,
    volume_tags_protect,
)
from crc.core import logger

ERRORS = (ClientError, BotoCoreError)
LiveLookup = tuple[str, TagList | None, Any]


def _error_code(error: ClientError) -> str | None:
    code: str | None = (error.response.get("Error") or {}).get("Code")
    return code


def describe_volume_tag_list(client: SpotClient, volume_id: str) -> list[Tag] | None:
    """The volume's tags, or None if describe fails."""
    try:
        desc = client.describe_volumes(VolumeIds=[volume_id])
        volumes = desc.get("Volumes") or []
        if not volumes:
            return []
        return [t for t in (volumes[0].get("Tags") or []) if t.get("Key")]
    except ERRORS as e:
        logging.error("describe_volumes failed for %s while checking volume tags: %s", volume_id, e)
        return None


def volume_tag_keys(client: SpotClient, volume_id: str) -> set[str] | None:
    """Existing tag keys on a volume, or None if describe fails."""
    try:
        desc = client.describe_volumes(VolumeIds=[volume_id])
        volumes = desc.get("Volumes") or []
        if not volumes:
            return set()
        return {t["Key"] for t in (volumes[0].get("Tags") or []) if t.get("Key")}
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


def instance_state_and_dot_false_volumes(
    client: SpotClient, instance_id: str
) -> tuple[str | None, list[str]]:
    """(instance state or None when not found, DeleteOnTermination=false volume ids)."""
    try:
        resp = client.describe_instances(InstanceIds=[instance_id])
    except ClientError as e:
        if _error_code(e) in NOT_FOUND_INSTANCE:
            return None, []
        logging.error(
            "describe_instances failed for %s while checking DeleteOnTermination: %s",
            instance_id,
            e,
        )
        return "unknown", []
    except BotoCoreError as e:
        logging.error(
            "describe_instances transport error for %s while checking DeleteOnTermination: %s",
            instance_id,
            e,
        )
        return "unknown", []
    volume_ids: list[str] = []
    state = None
    for reservation in resp.get("Reservations") or []:
        for instance in reservation.get("Instances") or []:
            state = (instance.get("State") or {}).get("Name")
            for mapping in instance.get("BlockDeviceMappings") or []:
                ebs = mapping.get("Ebs") or {}
                volume_id = ebs.get("VolumeId")
                if not volume_id:
                    continue
                # Explicit False only: a missing value defaults to True for root.
                if ebs.get("DeleteOnTermination") is False:
                    volume_ids.append(volume_id)
                    logging.info(
                        "Instance %s volume %s has DeleteOnTermination=false",
                        instance_id,
                        volume_id,
                    )
    return state, volume_ids


def describe_tagged_leftover_volumes(
    client: SpotClient,
    request_id: str | None = None,
    statuses: Sequence[str] | None = None,
    region: str | None = None,
) -> list[Mapping[str, Any]]:
    """describe_volumes for crc_leftover_of, optionally one request id and given states."""
    filters: list[dict[str, Any]] = [
        {"Name": "status", "Values": statuses or ["available", "in-use"]}
    ]
    if request_id:
        filters.append({"Name": f"tag:{LEFTOVER_FROM_TAG}", "Values": [request_id]})
    else:
        filters.append({"Name": "tag-key", "Values": [LEFTOVER_FROM_TAG]})
    volumes: list[Mapping[str, Any]] = []
    try:
        paginator = client.get_paginator("describe_volumes")
        for page in paginator.paginate(Filters=filters):
            volumes.extend(page.get("Volumes") or [])
    except ERRORS as e:
        logging.error("describe_volumes for %s leftovers failed: %s", LEFTOVER_FROM_TAG, e)
        logger.current().fail(region, request_id or "-", e)
    return volumes


def volumes_tagged_leftover(
    client: SpotClient, request_id: str | None = None, region: str | None = None
) -> list[str]:
    """Volumes marked crc_leftover_of in available or in-use state."""
    return [
        vol["VolumeId"]
        for vol in describe_tagged_leftover_volumes(
            client, request_id=request_id, statuses=["available", "in-use"], region=region
        )
        if vol.get("VolumeId")
    ]


def inspect_instance(
    client: SpotClient, match: RequestMatch, region: str | None = None
) -> Inspection | None:
    """Instance state and leftover volumes of a request; None when cleanup must not go on."""
    instance_state: str | None = None
    leftover_volumes: list[str] = []
    volumes_from_bdm = False
    if match.instance_id:
        instance_state, leftover_volumes = instance_state_and_dot_false_volumes(
            client, match.instance_id
        )
        # Fail closed: do not cancel/terminate when the volume list is unreliable.
        if instance_state == "unknown":
            logging.error(
                "Aborting cleanup of spot request %s: describe_instances "
                "failed for %s (volume list unreliable)",
                match.request_id,
                match.instance_id,
            )
            logger.current().fail(region, match.request_id, "describe_instances failed")
            return None
        volumes_from_bdm = bool(leftover_volumes)
    if not leftover_volumes:
        leftover_volumes = volumes_tagged_leftover(client, match.request_id, region)
    inspection = Inspection(instance_state, leftover_volumes, volumes_from_bdm)
    if match.already_cancelled and inspection.instance_done and not leftover_volumes:
        logging.debug(
            "Skipping cancelled spot request %s: instance %s already "
            "gone/terminated and no leftover volumes to delete",
            match.request_id,
            match.instance_id or "none",
        )
        logger.current().skip("already_cancelled_done")
        return None
    return inspection


def live_spot_request_tags(
    client: SpotClient, request_id: str, region: str | None = None
) -> LiveLookup:
    """("live", tags, create_time), ("gone", None, None) or ("error", None, None)."""
    try:
        resp = client.describe_spot_instance_requests(SpotInstanceRequestIds=[request_id])
    except ClientError as e:
        if _error_code(e) in NOT_FOUND_REQUEST:
            return "gone", None, None
        logging.error(
            "describe_spot_instance_requests for %s failed during sweep: %s", request_id, e
        )
        logger.current().fail(region, request_id, e)
        return "error", None, None
    except BotoCoreError as e:
        logging.error(
            "describe_spot_instance_requests transport error for %s during sweep: %s",
            request_id,
            e,
        )
        logger.current().fail(region, request_id, e)
        return "error", None, None
    reqs = resp.get("SpotInstanceRequests") or []
    if not reqs:
        return "gone", None, None
    req = reqs[0]
    return "live", req.get("Tags") or [], req.get("CreateTime")


def _tags_pass(settings: SpotSettings, tags: TagList, create_time: Any, now: Any) -> bool:
    if should_skip(settings, tags):
        return False
    if settings.filter_tags and not matches_filter(settings, tags):
        return False
    return passes_age_gate(settings, tags, create_time, now)


def volume_passes_sweep_gates(
    settings: SpotSettings,
    client: SpotClient,
    volume: Mapping[str, Any],
    now: datetime.datetime,
    region: str | None = None,
) -> bool:
    """Same gates as spot requests plus the volume's own exception/notags/yb_task."""
    request_id = tag_map(volume.get("Tags") or []).get(LEFTOVER_FROM_TAG)
    if not request_id or request_id == LEFTOVER_INVALID_VALUE:
        return False
    if volume_tags_protect(settings, own_tags_for_protect(volume)):
        return False
    status, live_tags, create_time = live_spot_request_tags(client, request_id, region)
    if status == "error":
        return False
    if status == "live":
        tags = live_tags or []
        # Untagged live requests: fail closed.
        return bool(tags) and _tags_pass(settings, tags, create_time, now)
    return _tags_pass(settings, effective_sweep_tags(volume), volume.get("CreateTime"), now)


def run_sweep(
    settings: SpotSettings,
    client: SpotClient,
    region: str,
    now: datetime.datetime,
    act: Callable[[str], None],
) -> None:
    """Gate each available crc_leftover_of volume, then call `act(volume_id)`.

    An error on one volume is logged and does not abort the rest.
    """
    for volume in describe_tagged_leftover_volumes(
        client, statuses=["available"], region=region
    ):
        volume_id = volume.get("VolumeId")
        if not volume_id:
            continue
        try:
            if not volume_passes_sweep_gates(settings, client, volume, now, region):
                logging.debug(
                    "Skipping tagged leftover volume %s in %s (filters/exceptions)",
                    volume_id,
                    region,
                )
                logger.current().skip("sweep_gates")
                continue
            act(volume_id)
        except Exception as e:
            logging.error(
                "Region %s: error sweeping tagged leftover volume %s: %s", region, volume_id, e
            )
            logger.current().fail(region, str(volume_id), e)
