# Copyright (c) Yugabyte, Inc.

"""Azure side of the per-run log collector (`crc.core.logger`, rung LOG-AZU).

Azure has no regions, so one REGION line is logged per resource group with
`region=<resource group>`. These helpers only report; they never decide what is acted on.
"""

import time

from crc.core import logger

# Short snake_case skip reasons for the SKIPPED lines; a reason not listed is slugged.
_SKIP_REASONS: dict[str, str] = {
    "no filter_tags match": "filter_tags",
    "does not match filter_tags": "filter_tags",
    "tags do not match filter_tags (or untagged)": "filter_tags",
    "matches exception_tags": "exception_tag",
    "matches notags": "notags",
    "matches exception_tags or notags": "exception_or_notags",
    "exception_tags or notags match": "exception_or_notags",
    "not older than the age threshold": "too_young",
    "not older than age": "too_young",
    "younger than the age threshold or invalid custom age tag": "too_young",
    "disk state not allowed": "state",
    "status does not contain an allowed state": "state",
    "not stopped or deallocated": "state",
    "attached to an IP configuration": "attached",
    "attached to a VM": "attached",
    "no name_regex match": "name_regex",
    "matches exception_regex": "exception_regex",
    "not a Spot VM": "not_spot",
    "no creation time": "no_creation_time",
}


def reason_slug(reason: str) -> str:
    """Short snake_case form of a skip reason."""
    known = _SKIP_REASONS.get(reason)
    if known is not None:
        return known
    words = "".join(c if c.isalnum() else " " for c in reason.lower()).split()
    return "_".join(words) or "other"


def started() -> float:
    """Start time of a region scan (monotonic seconds)."""
    return time.monotonic()


def region_done(resource_group: str, scanned: int, selected: int, since: float) -> None:
    """Log the REGION line of the resource group just walked."""
    logger.current().region(resource_group, scanned, selected, time.monotonic() - since)


def skip(reason: str) -> None:
    """Count one skipped resource under the slug of `reason`; logs nothing."""
    logger.current().skip(reason_slug(reason))


def acted(kind: str, resource_group: str, resource_id: str, name: str | None = None) -> None:
    """Log one ACTION line for a resource acted on (or that would be in a dry run)."""
    logger.current().action(kind, resource_group, resource_id, name or resource_id)


def failed(resource_group: str, resource_id: str, error: BaseException | str) -> None:
    """Record one per-resource failure for the end-of-run list."""
    logger.current().fail(resource_group, resource_id, error)
