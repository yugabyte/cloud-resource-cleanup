# Copyright (c) Yugabyte, Inc.

"""Typed Protocols of the boto3 EC2 calls the VM cleaner uses, so tests pass fakes."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol


class VmClient(Protocol):
    """The four EC2 client calls of the VM cleaner."""

    def describe_instances(self, *, Filters: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        """List instances of the client's region (one unpaginated call)."""
        ...

    def describe_network_interfaces(
        self, *, NetworkInterfaceIds: Sequence[str]
    ) -> Mapping[str, Any]:
        """Describe network interfaces; the attach time of the first one is the instance age."""
        ...

    def terminate_instances(self, *, InstanceIds: Sequence[str]) -> Mapping[str, Any]:
        """Terminate instances by id."""
        ...

    def stop_instances(self, *, InstanceIds: Sequence[str]) -> Mapping[str, Any]:
        """Stop instances by id."""
        ...


class VmPaginator(Protocol):
    """The `describe_instances` paginator used by the tag scan."""

    def paginate(self, *, Filters: Sequence[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
        """Iterate the pages of a `describe_instances` call."""
        ...


class VmScanClient(Protocol):
    """The EC2 client surface of the read-only tag scan."""

    exceptions: Any

    def get_paginator(self, operation_name: str) -> VmPaginator:
        """Return the paginator of an operation."""
        ...


class VmSession(Protocol):
    """The boto3 session call of the tag scan."""

    def get_available_regions(self, service_name: str) -> Sequence[str]:
        """Static list of the regions of a service."""
        ...
