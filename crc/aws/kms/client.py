# Copyright (c) Yugabyte, Inc.

"""Typed KMS client Protocols, default factory and constants."""

import datetime
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol

import boto3

SERVICE_NAME = "kms"
POLICY_NAME = "default"


class KmsPaginator(Protocol):
    """The paginator of `list_keys`."""

    def paginate(self) -> Iterable[Mapping[str, Any]]:
        """Yield the pages of keys."""
        ...


class KmsClient(Protocol):
    """The KMS calls this cleaner uses."""

    def get_paginator(self, operation_name: str) -> KmsPaginator:
        """Paginator for `list_keys`."""
        ...

    def list_resource_tags(self, *, KeyId: str) -> Mapping[str, Any]:
        """Tags of one key (`TagKey` / `TagValue` entries)."""
        ...

    def describe_key(self, *, KeyId: str) -> Mapping[str, Any]:
        """Metadata of one key."""
        ...

    def get_key_policy(self, *, KeyId: str, PolicyName: str) -> Mapping[str, Any]:
        """Key policy document (JSON text under `Policy`)."""
        ...

    def schedule_key_deletion(self, *, KeyId: str, PendingWindowInDays: int) -> Mapping[str, Any]:
        """Schedule one key for deletion."""
        ...


ClientFactory = Callable[[str], KmsClient]
Clock = Callable[[], datetime.datetime]


def boto3_client(region: str) -> KmsClient:
    """Default KMS client factory."""
    client: KmsClient = boto3.client(SERVICE_NAME, region_name=region)
    return client
