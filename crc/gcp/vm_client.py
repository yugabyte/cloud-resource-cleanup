# Copyright (c) Yugabyte, Inc.

"""Compute Engine client of the GCP VM cleaner: the `GcpVmClient` protocol, the `InstanceView`
read by the cleaner and `ComputeVmClient`, its SDK implementation. Split from `crc.gcp.vm`;
`crc.gcp.vm` re-exports the three names."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol

from google.cloud import compute_v1
from googleapiclient import discovery

_SERVICE_NAME = "compute"
_SERVICE_VERSION = "v1"


class InstanceView(Protocol):
    """The four fields of a GCP instance this cleaner reads."""

    @property
    def status(self) -> str:
        """Instance status, for example `RUNNING`."""
        ...

    @property
    def name(self) -> str:
        """Name of the instance."""
        ...

    @property
    def labels(self) -> Mapping[str, str]:
        """Labels of the instance; empty when unlabelled."""
        ...

    @property
    def creation_timestamp(self) -> str:
        """RFC 3339 creation time."""
        ...


class GcpVmClient(Protocol):
    """The Compute Engine calls this cleaner uses."""

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        """The `items` of the zones listing (one page, as dictionaries with a `name`)."""
        ...

    def list_instances(self, project_id: str, zone: str) -> Iterable[InstanceView]:
        """Instances of one zone."""
        ...

    def delete_instance(self, project_id: str, zone: str, instance: str) -> object:
        """Delete one instance of a zone."""
        ...

    def stop_instance(self, project_id: str, zone: str, instance: str) -> object:
        """Stop one instance of a zone."""
        ...

    def get_zone_operation(self, project_id: str, zone: str, operation: str) -> Mapping[str, Any]:
        """The current state of a zone operation (`status`, and `error` when it failed)."""
        ...


class ComputeVmClient:
    """`GcpVmClient` on google-api-python-client (zones, deletes, stops) and
    google-cloud-compute (instance listing). Like `main`, the discovery service is closed as
    soon as the zones were listed and the instances client was built; deletes and stops still
    use that service object afterwards."""

    def __init__(self) -> None:
        self._service: Any = None
        self._instances: compute_v1.InstancesClient | None = None

    def _compute(self) -> Any:
        if self._service is None:
            self._service = discovery.build(_SERVICE_NAME, _SERVICE_VERSION)
        return self._service

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        with discovery.build(_SERVICE_NAME, _SERVICE_VERSION) as service:
            self._service = service
            request = service.zones().list(project=project_id)
            zones: Sequence[Mapping[str, str]] = request.execute()["items"]
            self._instances = compute_v1.InstancesClient()
        return zones

    def list_instances(self, project_id: str, zone: str) -> Iterable[InstanceView]:
        if self._instances is None:
            self._instances = compute_v1.InstancesClient()
        instances: Iterable[InstanceView] = self._instances.list(project=project_id, zone=zone)
        return instances

    def delete_instance(self, project_id: str, zone: str, instance: str) -> object:
        service = self._compute()
        return (
            service.instances().delete(project=project_id, zone=zone, instance=instance).execute()
        )

    def stop_instance(self, project_id: str, zone: str, instance: str) -> object:
        service = self._compute()
        return service.instances().stop(project=project_id, zone=zone, instance=instance).execute()

    def get_zone_operation(self, project_id: str, zone: str, operation: str) -> Mapping[str, Any]:
        service = self._compute()
        request = service.zoneOperations().get(project=project_id, zone=zone, operation=operation)
        state: Mapping[str, Any] = request.execute()
        return state
