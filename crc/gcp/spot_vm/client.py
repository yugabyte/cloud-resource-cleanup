# Copyright (c) Yugabyte, Inc.

"""The Compute Engine calls the Spot VM cleaner uses, and their SDK implementation."""

from collections.abc import Iterable, Mapping, Sequence
from contextlib import ExitStack
from typing import Any, Protocol

from google.cloud import compute_v1
from googleapiclient import discovery

_SERVICE_NAME = "compute"
_SERVICE_VERSION = "v1"
_REGION = "region"


class InstanceView(Protocol):
    """The instance fields this cleaner reads. `scheduling`, `metadata` and `disks` are read
    defensively (a missing attribute counts as absent), so tests may omit them."""

    @property
    def name(self) -> str:
        """Name of the instance."""
        ...

    @property
    def status(self) -> str:
        """Instance status, for example `RUNNING`."""
        ...

    @property
    def labels(self) -> Mapping[str, str]:
        """Labels of the instance; empty when unlabelled."""
        ...

    @property
    def creation_timestamp(self) -> str:
        """RFC 3339 creation time."""
        ...


class GcpSpotClient(Protocol):
    """The Compute Engine calls this cleaner uses. `close()` ends the session the first
    `list_zones`, delete or get call opened."""

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        """The `items` of the zones listing (one page; `[]` when the key is missing)."""
        ...

    def list_instances(self, project_id: str, zone: str) -> Iterable[InstanceView]:
        """Instances of one zone."""
        ...

    def delete_instance(self, project_id: str, zone: str, instance: str) -> Mapping[str, Any]:
        """Delete one instance; returns the operation."""
        ...

    def get_operation(
        self, project_id: str, scope: str, location: str, operation: str
    ) -> Mapping[str, Any]:
        """Read a zone (`scope="zone"`) or region operation."""
        ...

    def get_disk(self, project_id: str, scope: str, location: str, disk: str) -> Mapping[str, Any]:
        """Read a zonal or regional disk."""
        ...

    def delete_disk(
        self, project_id: str, scope: str, location: str, disk: str
    ) -> Mapping[str, Any]:
        """Delete a zonal or regional disk; returns the operation."""
        ...

    def close(self) -> None:
        """Close the session; a no-op when none is open."""
        ...


class ComputeSpotClient:
    """`GcpSpotClient` on google-api-python-client (zones, deletes, operations, disks) and
    google-cloud-compute (instance listing). Like `main`, one discovery service serves a whole
    run and the instances client is built right after the zones were listed. Pass `service`
    to use an existing discovery service that this client does not own and never closes."""

    def __init__(self, service: Any = None) -> None:
        self._stack = ExitStack()
        self._service: Any = service
        self._owned = service is None
        self._instances: compute_v1.InstancesClient | None = None

    def _compute(self) -> Any:
        if self._service is None:
            self._service = self._stack.enter_context(
                discovery.build(_SERVICE_NAME, _SERVICE_VERSION)
            )
        return self._service

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        request = self._compute().zones().list(project=project_id)
        zones: Sequence[Mapping[str, str]] = request.execute().get("items", [])
        self._instances = compute_v1.InstancesClient()
        return zones

    def list_instances(self, project_id: str, zone: str) -> Iterable[InstanceView]:
        if self._instances is None:
            self._instances = compute_v1.InstancesClient()
        instances: Iterable[InstanceView] = self._instances.list(project=project_id, zone=zone)
        return instances

    def delete_instance(self, project_id: str, zone: str, instance: str) -> Mapping[str, Any]:
        request = (
            self._compute().instances().delete(project=project_id, zone=zone, instance=instance)
        )
        operation: Mapping[str, Any] = request.execute()
        return operation

    def get_operation(
        self, project_id: str, scope: str, location: str, operation: str
    ) -> Mapping[str, Any]:
        if scope == _REGION:
            request = (
                self._compute()
                .regionOperations()
                .get(project=project_id, region=location, operation=operation)
            )
        else:
            request = (
                self._compute()
                .zoneOperations()
                .get(project=project_id, zone=location, operation=operation)
            )
        result: Mapping[str, Any] = request.execute()
        return result

    def get_disk(self, project_id: str, scope: str, location: str, disk: str) -> Mapping[str, Any]:
        if scope == _REGION:
            request = (
                self._compute().regionDisks().get(project=project_id, region=location, disk=disk)
            )
        else:
            request = self._compute().disks().get(project=project_id, zone=location, disk=disk)
        result: Mapping[str, Any] = request.execute()
        return result

    def delete_disk(
        self, project_id: str, scope: str, location: str, disk: str
    ) -> Mapping[str, Any]:
        if scope == _REGION:
            request = (
                self._compute().regionDisks().delete(project=project_id, region=location, disk=disk)
            )
        else:
            request = self._compute().disks().delete(project=project_id, zone=location, disk=disk)
        result: Mapping[str, Any] = request.execute()
        return result

    def close(self) -> None:
        self._stack.close()
        if self._owned:
            self._service = None
