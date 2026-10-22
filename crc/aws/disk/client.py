# Copyright (c) Yugabyte, Inc.

"""Typed Protocols of the boto3 calls the EBS disk cleaner uses, and the default factories."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from crc.aws._base import get_all_regions
from crc.aws.connectivity import CONNECTIVITY_ERRORS

SERVICE_NAME = "ec2"
DEFAULT_REGION = "us-west-2"

# Unreachable opt-in regions (e.g. me-south-1) otherwise sit on 60s connect x retries.
BOTO_CFG = Config(
    connect_timeout=8,
    read_timeout=30,
    retries={"max_attempts": 2, "mode": "standard"},
)

# ClientError + connectivity failures for CloudWatch lookups.
CLOUDWATCH_ERRORS = (ClientError, *CONNECTIVITY_ERRORS)


class VolumePaginator(Protocol):
    """The `describe_volumes` paginator."""

    def paginate(self, *, Filters: Sequence[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
        """Iterate the response pages of one filtered listing."""
        ...


class Ec2Client(Protocol):
    """The two EC2 calls used: the volume paginator and `delete_volume`."""

    def get_paginator(self, operation_name: str) -> VolumePaginator:
        """Paginator of `describe_volumes`."""
        ...

    def delete_volume(self, *, VolumeId: str) -> Mapping[str, Any]:
        """Delete one volume by id."""
        ...


class CloudWatchClient(Protocol):
    """The one CloudWatch call used."""

    def get_metric_data(self, **kwargs: Any) -> Mapping[str, Any]:
        """GetMetricData with MetricDataQueries, StartTime, EndTime, ScanBy, NextToken."""
        ...


Ec2Factory = Callable[[str], Ec2Client]
CloudWatchFactory = Callable[[str], CloudWatchClient]
RegionLister = Callable[[], Sequence[str]]


def boto3_ec2(region: str) -> Ec2Client:
    """Default EC2 client of a region (bounded timeouts and retries)."""
    client: Ec2Client = boto3.client(SERVICE_NAME, region_name=region, config=BOTO_CFG)
    return client


def boto3_cloudwatch(region: str) -> CloudWatchClient:
    """Default CloudWatch client of a region (bounded timeouts and retries)."""
    client: CloudWatchClient = boto3.client("cloudwatch", region_name=region, config=BOTO_CFG)
    return client


def all_regions() -> Sequence[str]:
    """Regions of the account, with the connectivity fallback of `get_all_regions`."""
    return get_all_regions(SERVICE_NAME, DEFAULT_REGION)
