# Copyright (c) Yugabyte, Inc.

"""Compute Engine access of the GCP IP cleaner: the `AddressView`, `OperationView` and
`GcpIpClient` protocols and the SDK-backed `ComputeIpClient`."""

from collections.abc import Iterable, Sequence
from typing import Protocol

from google.cloud import compute_v1


class AddressView(Protocol):
    """The three fields of a GCP address this cleaner reads."""

    @property
    def name(self) -> str:
        """Name of the address."""
        ...

    @property
    def status(self) -> str:
        """Address status, for example RESERVED or IN_USE."""
        ...

    @property
    def creation_timestamp(self) -> str:
        """Creation time, RFC 3339 (`creationTimestamp`)."""
        ...


class OperationView(Protocol):
    """The long-running delete operation GCP returns; `result()` blocks until it is done and
    raises when it failed."""

    def result(self) -> object:
        """Wait for the operation to finish."""
        ...


class GcpIpClient(Protocol):
    """The three Compute Engine calls this cleaner uses."""

    def list_regions(self, project_id: str) -> Sequence[str]:
        """Names of the regions of the project."""
        ...

    def list_addresses(self, project_id: str, region: str) -> Iterable[AddressView]:
        """Addresses of one region."""
        ...

    def delete_address(self, project_id: str, region: str, address: str) -> OperationView:
        """Start the deletion of one address of a region."""
        ...


class ComputeIpClient:
    """`GcpIpClient` on google-cloud-compute. Like `main`, every call builds a new client."""

    def list_regions(self, project_id: str) -> Sequence[str]:
        regions = compute_v1.RegionsClient().list(project=project_id)
        return [region.name for region in regions]

    def list_addresses(self, project_id: str, region: str) -> Iterable[AddressView]:
        addresses: Iterable[AddressView] = compute_v1.AddressesClient().list(
            project=project_id, region=region
        )
        return addresses

    def delete_address(self, project_id: str, region: str, address: str) -> OperationView:
        operation: OperationView = compute_v1.AddressesClient().delete(
            project=project_id,
            region=region,
            address=address,
        )
        return operation
