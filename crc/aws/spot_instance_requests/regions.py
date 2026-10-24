# Copyright (c) Yugabyte, Inc.

"""Per-region helpers of the spot cleaner: listing and filtering, and error isolation."""

import datetime
import logging
import time
from collections.abc import Callable
from typing import Any

from botocore.exceptions import ClientError

from crc.aws.spot_instance_requests.client import SpotClient
from crc.aws.spot_instance_requests.model import RequestMatch, SpotSettings
from crc.aws.spot_instance_requests.requests import describe_all_requests, filtered_requests
from crc.core import logger


def list_matches(
    settings: SpotSettings,
    client: SpotClient,
    region: str,
    now: datetime.datetime,
    spot_filter: list[dict[str, Any]],
) -> list[RequestMatch] | None:
    """Matching requests of one region, or None when listing failed (logged, region skipped)."""
    started = time.monotonic()
    try:
        details = describe_all_requests(client, spot_filter)
    except ClientError as e:
        logging.error(
            "Region %s: describe_spot_instance_requests failed (%s): %s",
            region,
            e.response.get("Error", {}).get("Code", "Unknown"),
            e,
        )
        logger.current().fail(region, "-", e)
        return None
    except Exception as e:  # noqa: BLE001 - per-region isolation, failure logged and recorded, region skipped
        logging.error("Region %s: describe_spot_instance_requests failed: %s", region, e)
        logger.current().fail(region, "-", e)
        return None
    matches = filtered_requests(settings, now, details, region)
    scanned = len(details.get("SpotInstanceRequests", []))
    logger.current().region(region, scanned, len(matches), time.monotonic() - started)
    return matches


def guarded_sweep(region: str, sweep: Callable[[], None]) -> None:
    """Run a region sweep; an error is logged and does not stop the run."""
    try:
        sweep()
    except Exception as e:  # noqa: BLE001 - SAF-034 sweep isolation, failure logged and recorded
        logging.error("Region %s: failed sweeping tagged leftover volumes: %s", region, e)
        logger.current().fail(region, "-", e)


def guarded_cleanup(region: str, request_id: str, action: Callable[[], None]) -> None:
    """Run the cleanup of one request; an error is logged and does not stop the run."""
    try:
        action()
    except Exception as e:  # noqa: BLE001 - per-resource isolation, failure logged and recorded, resource kept
        logging.error("Region %s: failed cleaning spot request %s: %s", region, request_id, e)
        logger.current().fail(region, request_id, e)
