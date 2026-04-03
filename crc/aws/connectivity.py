# Copyright (c) Yugabyte, Inc.

"""
Shared handling for transient AWS / botocore network failures (e.g. region unreachable
from CI). Use CONNECTIVITY_ERRORS in ``except`` clauses so jobs skip a region instead of failing.
"""

import logging

from botocore.exceptions import (
    ConnectionClosedError,
    ConnectionError as BotoConnectionError,
    ReadTimeoutError,
)

# Covers ConnectTimeoutError, EndpointConnectionError, ProxyConnectionError, etc.
CONNECTIVITY_ERRORS = (
    BotoConnectionError,
    ReadTimeoutError,
    ConnectionClosedError,
)


def log_skipped_region(region: str, context: str, exc: BaseException) -> None:
    logging.warning(
        "Region %s: skipped %s — network/connectivity (%s): %s",
        region,
        context,
        type(exc).__name__,
        exc,
    )
