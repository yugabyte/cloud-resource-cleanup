# Copyright (c) Yugabyte, Inc.

"""The OCI calls the Spot VM cleaner uses, behind small typed Protocols.

The real compute and block storage clients of the OCI SDK satisfy `ComputeClient` and
`BlockClient` structurally; tests pass fakes with the same methods. Paging goes through an
injected `Pager` (by default `oci.pagination.list_call_get_all_results`, looked up at call time).
"""

import datetime
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

import oci

from crc.oci._base import Base

T_co = TypeVar("T_co", covariant=True)

Pager = Callable[..., Any]
"""`pager(call, **kwargs)` returns the SDK response whose `.data` holds every page."""


class Response(Protocol[T_co]):
    """An OCI SDK response."""

    @property
    def data(self) -> T_co:
        """The payload."""
        ...


class SdkInstance(Protocol):
    """The attributes of an OCI compute instance this cleaner reads."""

    @property
    def id(self) -> str:
        """OCID of the instance."""
        ...

    @property
    def display_name(self) -> str:
        """Display name, the name reported to Slack and InfluxDB."""
        ...

    @property
    def lifecycle_state(self) -> str:
        """Lifecycle state; only `RUNNING` and `STOPPED` are considered."""
        ...

    @property
    def freeform_tags(self) -> Mapping[str, str] | None:
        """Freeform tags, or `None`."""
        ...

    @property
    def defined_tags(self) -> Mapping[str, Any] | None:
        """Defined tags (namespace to key/value dict), or `None`."""
        ...

    @property
    def metadata(self) -> object:
        """Instance metadata (a dict when present)."""
        ...

    @property
    def preemptible_instance_config(self) -> object | None:
        """Set only for preemptible instances."""
        ...

    @property
    def time_created(self) -> datetime.datetime:
        """Creation time."""
        ...


class SdkAttachment(Protocol):
    """The attributes of a volume attachment this cleaner reads."""

    @property
    def lifecycle_state(self) -> str:
        """Attachment lifecycle state."""
        ...

    @property
    def volume_id(self) -> str | None:
        """OCID of the attached volume."""
        ...

    @property
    def instance_id(self) -> str | None:
        """OCID of the instance the volume is attached to."""
        ...


class SdkInstanceState(Protocol):
    """The attribute read while waiting for an instance to terminate."""

    @property
    def lifecycle_state(self) -> str | None:
        """Lifecycle state of the instance."""
        ...


class SdkVolume(Protocol):
    """The attributes read from a leftover volume."""

    @property
    def freeform_tags(self) -> Mapping[str, str] | None:
        """Freeform tags, or `None`."""
        ...

    @property
    def time_created(self) -> datetime.datetime | None:
        """Creation time (the volume is kept when it is missing)."""
        ...


class ComputeClient(Protocol):
    """The compute client calls this cleaner uses (one region)."""

    def list_instances(self, compartment_id: str) -> Response[Sequence[SdkInstance]]:
        """One page of instances (wrapped by the pager)."""
        ...

    def list_volume_attachments(
        self, compartment_id: str, instance_id: str = "", volume_id: str = ""
    ) -> Response[Sequence[SdkAttachment]]:
        """One page of volume attachments of an instance or volume (wrapped by the pager)."""
        ...

    def get_instance(self, instance_id: str) -> Response[SdkInstanceState]:
        """Read one instance."""
        ...

    def terminate_instance(self, instance_id: str, preserve_boot_volume: bool) -> object:
        """Terminate one instance."""
        ...


class BlockClient(Protocol):
    """The block storage client calls this cleaner uses (one region)."""

    def get_volume(self, volume_id: str) -> Response[SdkVolume]:
        """Read one volume."""
        ...

    def delete_volume(self, volume_id: str) -> object:
        """Delete one volume."""
        ...


@dataclass(frozen=True, slots=True)
class RegionClients:
    """The clients of one region and the compartment they are used in."""

    compute: ComputeClient
    block: BlockClient
    compartment_id: str


ClientFactory = Callable[[str], RegionClients]
RegionLister = Callable[[], Sequence[str]]


def list_all(call: Callable[..., Any], **kwargs: Any) -> Any:
    """Every page of an OCI list call."""
    return oci.pagination.list_call_get_all_results(call, **kwargs)


def clients_for(base: Base) -> ClientFactory:
    """Factory of regional clients on top of `Base`, which caches its clients per region."""
    untyped: Any = base

    def factory(region: str) -> RegionClients:
        compute = untyped.get_compute_client(region)
        block = untyped.get_blockstorage_client(region)
        return RegionClients(compute, block, base.compartment_id)

    return factory


def regions_of(base: Base) -> RegionLister:
    """Lister of the tenancy's regions (`Base.get_all_regions`, with its fallback)."""
    untyped: Any = base

    def regions() -> Sequence[str]:
        listed: Sequence[str] = untyped.get_all_regions()
        return listed

    return regions
