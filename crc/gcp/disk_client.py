# Copyright (c) Yugabyte, Inc.

"""Compute Engine access of the GCP disk cleaner: the `DiskView` and `GcpDiskClient` protocols
and the SDK-backed `ComputeDiskClient`. Re-exported by `crc.gcp.disk`."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol

from google.cloud import compute_v1
from googleapiclient import discovery

_SERVICE_NAME = "compute"
_SERVICE_VERSION = "v1"


class DiskView(Protocol):
    """The four fields of a GCP disk this cleaner reads."""

    @property
    def users(self) -> Sequence[str]:
        """Instances the disk is attached to; empty when unattached."""
        ...

    @property
    def name(self) -> str:
        """Name of the disk."""
        ...

    @property
    def labels(self) -> Mapping[str, str]:
        """Labels of the disk; empty when unlabelled."""
        ...

    @property
    def last_detach_timestamp(self) -> str:
        """RFC 3339 time the disk was last detached; empty when never attached."""
        ...


class GcpDiskClient(Protocol):
    """The Compute Engine calls this cleaner uses. `close()` ends the session the first
    `list_zones` or `delete_disk` call opened."""

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        """The `items` of the zones listing (one page, as dictionaries with a `name`)."""
        ...

    def list_disks(self, project_id: str, zone: str) -> Iterable[DiskView]:
        """Disks of one zone."""
        ...

    def delete_disk(self, project_id: str, zone: str, disk: str) -> object:
        """Delete one disk of a zone; returns the zone operation (a mapping with a `name`)."""
        ...

    def get_operation(
        self, project_id: str, scope: str, location: str, operation: str
    ) -> Mapping[str, Any]:
        """Read a zone operation (`scope="zone"`); a mapping with a `status`."""
        ...

    def close(self) -> None:
        """Close the session; a no-op when none is open."""
        ...


class ComputeDiskClient:
    """`GcpDiskClient` on google-api-python-client (zones, deletes) and google-cloud-compute
    (disk listing); as on `main` one discovery service serves a run."""

    def __init__(self) -> None:
        self._service: Any = None
        self._disks: compute_v1.DisksClient | None = None

    def _compute(self) -> Any:
        if self._service is None:
            self._service = discovery.build(_SERVICE_NAME, _SERVICE_VERSION)
        return self._service

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        request = self._compute().zones().list(project=project_id)
        zones: Sequence[Mapping[str, str]] = request.execute()["items"]
        self._disks = compute_v1.DisksClient()
        return zones

    def list_disks(self, project_id: str, zone: str) -> Iterable[DiskView]:
        if self._disks is None:
            self._disks = compute_v1.DisksClient()
        disks: Iterable[DiskView] = self._disks.list(project=project_id, zone=zone)
        return disks

    def delete_disk(self, project_id: str, zone: str, disk: str) -> object:
        return self._compute().disks().delete(project=project_id, zone=zone, disk=disk).execute()

    def get_operation(
        self, project_id: str, scope: str, location: str, operation: str
    ) -> Mapping[str, Any]:
        request = (
            self._compute()
            .zoneOperations()
            .get(project=project_id, zone=location, operation=operation)
        )
        result: Mapping[str, Any] = request.execute()
        return result

    def close(self) -> None:
        service, self._service = self._service, None
        if service is not None:
            service.close()
