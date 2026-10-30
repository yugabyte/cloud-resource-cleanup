# Copyright (c) Yugabyte, Inc.

"""Read-only sweep gates for the tagged leftover volumes of a region."""

import datetime
import logging
from collections.abc import Callable, Mapping
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from crc.aws.spot_instance_requests.client import SpotClient
from crc.aws.spot_instance_requests.lookups import describe_tagged_leftover_volumes
from crc.aws.spot_instance_requests.model import (
    LEFTOVER_FROM_TAG,
    LEFTOVER_INVALID_VALUE,
    NOT_FOUND_REQUEST,
    SpotSettings,
    TagList,
)
from crc.aws.spot_instance_requests.requests import passes_age_gate
from crc.aws.spot_instance_requests.rules import (
    effective_sweep_tags,
    matches_filter,
    own_tags_for_protect,
    should_skip,
    tag_map,
    volume_tags_protect,
)
from crc.aws.spot_instance_requests.volume_tags import error_code
from crc.core import logger

LiveLookup = tuple[str, TagList | None, Any]


def live_spot_request_tags(
    client: SpotClient, request_id: str, region: str | None = None
) -> LiveLookup:
    """("live", tags, create_time), ("gone", None, None) or ("error", None, None)."""
    try:
        resp = client.describe_spot_instance_requests(SpotInstanceRequestIds=[request_id])
    except ClientError as e:
        if error_code(e) in NOT_FOUND_REQUEST:
            return "gone", None, None
        logging.error(
            "describe_spot_instance_requests for %s failed during sweep: %s", request_id, e
        )
        logger.current().fail(region, request_id, e, logged=True)
        return "error", None, None
    except BotoCoreError as e:
        logging.error(
            "describe_spot_instance_requests transport error for %s during sweep: %s",
            request_id,
            e,
        )
        logger.current().fail(region, request_id, e, logged=True)
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
    for volume in describe_tagged_leftover_volumes(client, statuses=["available"], region=region):
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
        except Exception as e:  # noqa: BLE001 - SAF-034 per-volume isolation, failure logged and recorded
            logging.error(
                "Region %s: error sweeping tagged leftover volume %s: %s", region, volume_id, e
            )
            logger.current().fail(region, str(volume_id), e, logged=True)
