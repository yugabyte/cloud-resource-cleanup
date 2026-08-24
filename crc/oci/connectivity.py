# Copyright (c) Yugabyte, Inc.

"""
Shared handling for transient OCI network/service failures (e.g. region unreachable
from CI). Use CONNECTIVITY_ERRORS in ``except`` clauses so jobs skip a region instead of failing.
"""

import logging

from oci.exceptions import ConnectTimeout, RequestException, ServiceError

# ServiceError covers throttling/5xx responses from OCI; ConnectTimeout/RequestException
# cover network-level failures reaching a region's endpoint.
CONNECTIVITY_ERRORS = (
    ConnectTimeout,
    RequestException,
    ServiceError,
)


def log_skipped_region(region: str, context: str, exc: BaseException) -> None:
    logging.warning(
        "Region %s: skipped %s — network/connectivity (%s): %s",
        region,
        context,
        type(exc).__name__,
        exc,
    )
