# Copyright (c) Yugabyte, Inc.

"""The Azure SDK calls the disk cleaner uses, as typed Protocols."""

import datetime
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Protocol

TagSpec = Mapping[str, Sequence[str]]
AgeSpec = dict[str, int]


class SdkDisk(Protocol):
    """The fields of an Azure `Disk` model this cleaner reads."""

    @property
    def name(self) -> str:
        """Disk name."""
        ...

    @property
    def tags(self) -> dict[str, str] | None:
        """Resource tags, `None` when the disk has none."""
        ...

    @property
    def time_created(self) -> datetime.datetime:
        """Creation time (timezone aware in the SDK)."""
        ...

    @property
    def disk_state(self) -> str:
        """Disk state, for example `Unattached`."""
        ...


class DeletePoller(Protocol):
    """The long-running-operation poller `begin_delete` returns."""

    def result(self) -> object:
        """Block until the deletion finished; raises when Azure reports a failure."""
        ...


class DiskOperations(Protocol):
    """The two `disks` calls this cleaner uses."""

    def list_by_resource_group(self, resource_group_name: str, /) -> Iterable[SdkDisk]:
        """List the disks of one resource group (lazy paging)."""
        ...

    def begin_delete(self, resource_group_name: str, disk_name: str, /) -> DeletePoller:
        """Start the deletion of one disk (positional arguments)."""
        ...


class DiskComputeClient(Protocol):
    """The part of the Azure compute client this cleaner uses."""

    @property
    def disks(self) -> DiskOperations:
        """Operations on managed disks."""
        ...


ClientFactory = Callable[[], DiskComputeClient]
