# Copyright (c) Yugabyte, Inc.

"""Read-only dry-run path: what a real run would cancel, terminate and sweep."""

import datetime
import logging

from crc.aws.spot_instance_requests.client import SpotClient
from crc.aws.spot_instance_requests.model import RequestMatch, SpotSettings, SpotState
from crc.aws.spot_instance_requests.volumes import (
    filter_deletable_leftover_volumes,
    inspect_instance,
    run_sweep,
)
from crc.core import logger


def _note_would_delete(
    region: str | None, match: RequestMatch, instance_done: bool, volumes: list[str]
) -> None:
    run = logger.current()
    run.action("would_delete", region, match.request_id)
    if match.instance_id and not instance_done:
        run.action("would_delete", region, match.instance_id)
    for volume_id in volumes:
        run.action("would_delete", region, volume_id)


def preview_request(
    settings: SpotSettings,
    state: SpotState,
    client: SpotClient,
    match: RequestMatch,
    region: str | None = None,
) -> None:
    """Record what a real run would do for one request (reads only)."""
    inspection = inspect_instance(client, match, region)
    if inspection is None:
        return
    deletable = filter_deletable_leftover_volumes(
        settings, client, inspection.leftover_volumes, region
    )
    state.spot_requests_to_delete.append(match.request_id)
    if match.instance_id and not inspection.instance_done:
        state.instance_ids_to_delete.append(match.instance_id)
    state.volumes_to_delete.extend(deletable)
    _note_would_delete(region, match, inspection.instance_done, deletable)
    logging.info(
        "Dry run: would %s spot request %s (type=%s), terminate %s, delete leftover volumes %s",
        "skip-cancel (already cancelled)" if match.already_cancelled else "cancel",
        match.request_id,
        match.request_type,
        match.instance_id or "none",
        deletable,
    )


def preview_sweep(
    settings: SpotSettings,
    state: SpotState,
    client: SpotClient,
    region: str,
    now: datetime.datetime,
) -> None:
    """Record the tagged leftover volumes a real run would sweep (reads only)."""

    def act(volume_id: str) -> None:
        if volume_id not in state.volumes_to_delete:
            state.volumes_to_delete.append(volume_id)
            logger.current().action("would_delete", region, volume_id)
        logging.info("Dry run: would delete tagged leftover volume %s in %s", volume_id, region)

    run_sweep(settings, client, region, now, act)
