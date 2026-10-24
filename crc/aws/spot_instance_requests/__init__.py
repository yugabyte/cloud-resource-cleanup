# Copyright (c) Yugabyte, Inc.

"""AWS spot instance request cleaner (`SpotRequestCleaner`, registered as
`aws` / `spot_instance_requests`)."""

from crc.aws.spot_instance_requests.cleaner import (
    SpotRequestCleaner,
    SpotRuntime,
    build_spot_request_cleaner,
)
from crc.aws.spot_instance_requests.client import SpotClient
from crc.aws.spot_instance_requests.model import SpotSettings

__all__ = [
    "SpotClient",
    "SpotRequestCleaner",
    "SpotRuntime",
    "SpotSettings",
    "build_spot_request_cleaner",
]
