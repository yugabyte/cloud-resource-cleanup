# Copyright (c) Yugabyte, Inc.

"""The OCI calls the disk cleaner (`crc.oci.disk`) uses, behind small typed Protocols.

`OciVolumeApi` is the real implementation over the OCI SDK clients of one region; tests pass
fakes that satisfy `VolumeApi` instead.
"""

import datetime
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

import oci

from crc.oci._base import Base


class SdkVolume(Protocol):
    """The attributes of an OCI volume or boot volume this cleaner reads."""

    @property
    def id(self) -> str:
        """OCID of the volume."""
        ...

    @property
    def display_name(self) -> str:
        """Display name, the name reported to Slack and InfluxDB."""
        ...

    @property
    def lifecycle_state(self) -> str:
        """Lifecycle state; only `AVAILABLE` volumes are considered."""
        ...

    @property
    def freeform_tags(self) -> Mapping[str, str] | None:
        """Freeform tags, or `None`."""
        ...

    @property
    def time_created(self) -> datetime.datetime:
        """Creation time."""
        ...


class SdkAttachment(Protocol):
    """The attributes of a volume or boot volume attachment this cleaner reads."""

    @property
    def lifecycle_state(self) -> str:
        """Attachment lifecycle state."""
        ...

    @property
    def volume_id(self) -> str:
        """OCID of the attached volume (volume attachments)."""
        ...

    @property
    def boot_volume_id(self) -> str:
        """OCID of the attached boot volume (boot volume attachments)."""
        ...


class VolumeApi(Protocol):
    """The OCI calls this cleaner uses, for one region and the fixed compartment.

    The list calls return every page of results.
    """

    def list_volumes(self) -> Sequence[SdkVolume]:
        """Block volumes of the compartment."""
        ...

    def list_boot_volumes(self) -> Sequence[SdkVolume]:
        """Boot volumes of the compartment."""
        ...

    def list_volume_attachments(self) -> Sequence[SdkAttachment]:
        """Volume attachments of the compartment."""
        ...

    def list_availability_domains(self) -> Sequence[str]:
        """Availability domain names of the region."""
        ...

    def list_boot_volume_attachments(self, availability_domain: str) -> Sequence[SdkAttachment]:
        """Boot volume attachments of one availability domain."""
        ...

    def delete_volume(self, volume_id: str) -> None:
        """Delete one block volume."""
        ...

    def delete_boot_volume(self, volume_id: str) -> None:
        """Delete one boot volume."""
        ...


ApiFactory = Callable[[str], VolumeApi]
RegionLister = Callable[[], Sequence[str]]


class OciVolumeApi:
    """`VolumeApi` over the OCI compute and block storage clients of one region."""

    def __init__(
        self,
        compute_client: Any,
        blockstorage_client: Any,
        compartment_id: str,
        availability_domains: Callable[[], Sequence[str]],
    ) -> None:
        self._compute = compute_client
        self._blockstorage = blockstorage_client
        self._compartment_id = compartment_id
        self._availability_domains = availability_domains

    def list_volumes(self) -> Sequence[SdkVolume]:
        volumes: Sequence[SdkVolume] = oci.pagination.list_call_get_all_results(
            self._blockstorage.list_volumes, compartment_id=self._compartment_id
        ).data
        return volumes

    def list_boot_volumes(self) -> Sequence[SdkVolume]:
        volumes: Sequence[SdkVolume] = oci.pagination.list_call_get_all_results(
            self._blockstorage.list_boot_volumes, compartment_id=self._compartment_id
        ).data
        return volumes

    def list_volume_attachments(self) -> Sequence[SdkAttachment]:
        attachments: Sequence[SdkAttachment] = oci.pagination.list_call_get_all_results(
            self._compute.list_volume_attachments, compartment_id=self._compartment_id
        ).data
        return attachments

    def list_availability_domains(self) -> Sequence[str]:
        return self._availability_domains()

    def list_boot_volume_attachments(self, availability_domain: str) -> Sequence[SdkAttachment]:
        attachments: Sequence[SdkAttachment] = oci.pagination.list_call_get_all_results(
            self._compute.list_boot_volume_attachments,
            availability_domain=availability_domain,
            compartment_id=self._compartment_id,
        ).data
        return attachments

    def delete_volume(self, volume_id: str) -> None:
        self._blockstorage.delete_volume(volume_id)

    def delete_boot_volume(self, volume_id: str) -> None:
        self._blockstorage.delete_boot_volume(volume_id)


def api_for(base: Base) -> ApiFactory:
    """Factory of regional APIs on top of `Base`, which caches its clients per region."""
    untyped: Any = base

    def factory(region: str) -> VolumeApi:
        compute_client = base.get_compute_client(region)
        blockstorage_client = base.get_blockstorage_client(region)
        return OciVolumeApi(
            compute_client,
            blockstorage_client,
            base.compartment_id,
            lambda: untyped.get_availability_domains(region),
        )

    return factory


def regions_of(base: Base) -> RegionLister:
    untyped: Any = base

    def regions() -> Sequence[str]:
        listed: Sequence[str] = untyped.get_all_regions()
        return listed

    return regions
