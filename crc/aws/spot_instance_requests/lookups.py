# Copyright (c) Yugabyte, Inc.

"""Read-only lookups of an instance's volumes, the leftover-marked volumes and the inspection
of one request."""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from crc.aws.spot_instance_requests.client import SpotClient
from crc.aws.spot_instance_requests.model import (
    LEFTOVER_FROM_TAG,
    NOT_FOUND_INSTANCE,
    Inspection,
    RequestMatch,
)
from crc.aws.spot_instance_requests.volume_tags import ERRORS, error_code
from crc.core import logger


def instance_state_and_dot_false_volumes(
    client: SpotClient, instance_id: str
) -> tuple[str | None, list[str]]:
    """(instance state or None when not found, DeleteOnTermination=false volume ids)."""
    try:
        resp = client.describe_instances(InstanceIds=[instance_id])
    except ClientError as e:
        if error_code(e) in NOT_FOUND_INSTANCE:
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
