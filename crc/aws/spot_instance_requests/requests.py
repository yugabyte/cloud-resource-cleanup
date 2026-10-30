# Copyright (c) Yugabyte, Inc.

"""Read-only discovery of the spot requests to clean (list, filter, age)."""

import datetime
import logging
from collections.abc import Mapping, Sequence
from typing import Any, cast

from crc.aws.spot_instance_requests.client import SpotClient
from crc.aws.spot_instance_requests.model import RequestMatch, SpotSettings, TagList
from crc.aws.spot_instance_requests.rules import matches_filter, should_skip
from crc.core import age as core_age
from crc.core import logger, policy
from crc.core.age import AgeSpec

EC2Filters = list[dict[str, Any]]


def build_filter(settings: SpotSettings) -> EC2Filters:
    """Server side `tag:key` filters (an empty value list is passed through, BUG-021)."""
    filters: EC2Filters = []
    if settings.filter_tags:
        for key, value in settings.filter_tags.items():
            filters.append({"Name": f"tag:{key}", "Values": value})
    logging.info("Filters created: %s", filters)
    return filters


def describe_all_requests(client: SpotClient, spot_filter: EC2Filters) -> dict[str, Any]:
    """Every page of describe_spot_instance_requests."""
    kwargs: dict[str, Any] = {}
    if spot_filter:
        kwargs["Filters"] = spot_filter
    all_requests: list[Any] = []
    next_token = None
    while True:
        if next_token:
            kwargs["NextToken"] = next_token
        resp = client.describe_spot_instance_requests(**kwargs)
        all_requests.extend(resp.get("SpotInstanceRequests", []))
        next_token = resp.get("NextToken")
        if not next_token:
            break
    return {"SpotInstanceRequests": all_requests}


def _retention(settings: SpotSettings, tags: TagList) -> AgeSpec | None:
    """Custom age tag (None when absent); raises ValueError when present but invalid."""
    raw = cast("list[dict[str, str]]", list(tags))
    return core_age.validated_retention_age(raw, settings.custom_age_tag_key or "")


def passes_age_gate(
    settings: SpotSettings, tags: TagList, create_time: Any, now: datetime.datetime
) -> bool:
    """True if old enough. Missing create time or a bad custom age tag keeps the resource."""
    if create_time is None:
        return False
    try:
        retention_age = _retention(settings, tags)
    except ValueError:
        return False
    try:
        local_now = now.astimezone(create_time.tzinfo)
    except Exception:  # noqa: BLE001 - naive create_time fallback to UTC (BUG-279), unchanged behaviour
        if getattr(create_time, "tzinfo", None) is None:
            create_time = create_time.replace(tzinfo=datetime.UTC)
        local_now = now.astimezone(datetime.UTC)
    return core_age.is_old(
        policy.effective_age(retention_age, settings.age), local_now, create_time
    )


def _kept(reason: str) -> RequestMatch | None:
    """A request that is not cleaned: counted under `reason` in the run's SKIPPED summary."""
    logger.current().skip(reason)
    return None


def _decide(
    settings: SpotSettings, now: datetime.datetime, request: Mapping[str, Any]
) -> RequestMatch | None:
    tags: Sequence[Mapping[str, Any]] = request.get("Tags") or []
    already_cancelled = request.get("State") == "cancelled"
    instance_id = request.get("InstanceId") or None
    # Untagged requests are never cleaned (fail closed); so are cancelled ones without instance.
    if not tags or (already_cancelled and not instance_id):
        return _kept("no_tags" if not tags else "cancelled_no_instance")
    if should_skip(settings, tags):
        return _kept("exception_tag")
    if settings.filter_tags and not matches_filter(settings, tags):
        return _kept("filter_tags")
    request_id = request["SpotInstanceRequestId"]
    create_time = request["CreateTime"]
    request_type = (request.get("Type") or "one-time").lower()
    try:
        retention_age = _retention(settings, tags)
    except ValueError:
        return _kept("invalid_age_tag")
    if retention_age:
        logging.info("Updating age for SpotInstanceRequestId: %s", request_id)
    age = policy.effective_age(retention_age, settings.age)
    if not core_age.is_old(age, now.astimezone(create_time.tzinfo), create_time):
        return _kept("too_young")
    logging.info(
        "Spot request %s (type=%s, cancelled=%s) instance=%s queued",
        request_id,
        request_type,
        already_cancelled,
        instance_id or "none",
    )
    return RequestMatch(request_id, instance_id, request_type, already_cancelled, tags)


def filtered_requests(
    settings: SpotSettings,
    now: datetime.datetime,
    details: Mapping[str, Any],
    region: str | None = None,
) -> list[RequestMatch]:
    """Requests that match the filters and are old enough. A bad request is logged and kept."""
    matches: list[RequestMatch] = []
    for request in details.get("SpotInstanceRequests", []):
        try:
            match = _decide(settings, now, request)
            if match is not None:
                matches.append(match)
        except Exception as e:  # noqa: BLE001 - per-request isolation, failure recorded, request kept
            logging.error("Error occurred while processing spot request %s: %s", request, e)
            request_id = request.get("SpotInstanceRequestId") if hasattr(request, "get") else None
            logger.current().fail(region, str(request_id or "-"), e, logged=True)
    return matches
