# Copyright (c) Yugabyte, Inc.

"""AWS spot instance request cleaner. `crc.aws.spot_instance_requests.SpotInstanceRequests`
is the class `crc.py` imports; `SpotRequestCleaner` is the registered `Cleaner`."""

from crc.aws.spot_instance_requests.adapter import SpotInstanceRequests
from crc.aws.spot_instance_requests.cleaner import (
    SpotRequestCleaner,
    SpotRuntime,
    build_spot_request_cleaner,
)
from crc.aws.spot_instance_requests.client import SpotClient
from crc.aws.spot_instance_requests.model import SpotSettings

__all__ = [
    "SpotClient",
    "SpotInstanceRequests",
    "SpotRequestCleaner",
    "SpotRuntime",
    "SpotSettings",
    "build_spot_request_cleaner",
]
