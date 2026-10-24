# Copyright (c) Yugabyte, Inc.

"""Typed view of the boto3 EC2 client calls the spot instance request cleaner uses."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Protocol

import boto3

from crc.aws._base import get_all_regions
from crc.aws.spot_instance_requests.model import DEFAULT_REGION, SERVICE_NAME


class VolumePaginator(Protocol):
    """Paginator of describe_volumes."""

    def paginate(self, *, Filters: Sequence[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
        """Yield describe_volumes pages."""
        ...


class SpotClient(Protocol):
    """The EC2 calls this cleaner uses (reads first, then the mutating calls)."""

    def describe_spot_instance_requests(self, **kwargs: Any) -> Mapping[str, Any]:
        """List spot requests (Filters, NextToken) or look up SpotInstanceRequestIds."""
        ...

    def describe_instances(self, *, InstanceIds: Sequence[str]) -> Mapping[str, Any]:
        """Describe instances by id."""
        ...

    def describe_volumes(self, *, VolumeIds: Sequence[str]) -> Mapping[str, Any]:
        """Describe volumes by id."""
        ...

    def get_paginator(self, operation_name: str) -> VolumePaginator:
        """Paginator for describe_volumes."""
        ...

    def cancel_spot_instance_requests(
        self, *, SpotInstanceRequestIds: Sequence[str]
    ) -> Mapping[str, Any]:
        """Cancel spot requests."""
        ...

    def terminate_instances(self, *, InstanceIds: Sequence[str]) -> Mapping[str, Any]:
        """Terminate instances."""
        ...

    def create_tags(
        self, *, Resources: Sequence[str], Tags: Sequence[Mapping[str, str]]
    ) -> Mapping[str, Any]:
        """Create or overwrite tags."""
        ...

    def delete_tags(
        self, *, Resources: Sequence[str], Tags: Sequence[Mapping[str, str]]
    ) -> Mapping[str, Any]:
        """Delete tags."""
        ...

    def delete_volume(self, *, VolumeId: str) -> Mapping[str, Any]:
        """Delete one volume."""
        ...


ClientFactory = Callable[[str], SpotClient]
RegionLister = Callable[[], Sequence[str]]


def boto3_client(region: str) -> SpotClient:
    """Real client for `region`."""
    client: SpotClient = boto3.client(SERVICE_NAME, region_name=region)
    return client


def all_regions() -> Sequence[str]:
    """Regions of the EC2 service (static fallback on connectivity errors)."""
    return get_all_regions(SERVICE_NAME, DEFAULT_REGION)
