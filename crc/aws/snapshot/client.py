# Copyright (c) Yugabyte, Inc.

"""Typed EC2 client Protocol and default client factory of the snapshot cleaner."""

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

import boto3

from crc.aws._common import EC2_SERVICE_NAME


class SnapshotClient(Protocol):
    """The three EC2 calls this cleaner uses."""

    def describe_snapshots(self, *, OwnerIds: Sequence[str]) -> Mapping[str, Any]:
        """List the snapshots of the given owners in the client's region."""
        ...

    def describe_images(self, *, Owners: Sequence[str]) -> Mapping[str, Any]:
        """List the AMIs of the given owners in the client's region."""
        ...

    def delete_snapshot(self, *, SnapshotId: str) -> Mapping[str, Any]:
        """Delete one snapshot by id."""
        ...


ClientFactory = Callable[[str], SnapshotClient]
RegionLister = Callable[[], Sequence[str]]


def boto3_client(region: str) -> SnapshotClient:
    """Default EC2 client factory."""
    client: SnapshotClient = boto3.client(EC2_SERVICE_NAME, region_name=region)
    return client
