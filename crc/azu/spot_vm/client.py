# Copyright (c) Yugabyte, Inc.

"""The part of the Azure compute and network clients the Spot VM cleaner uses."""

from collections.abc import Callable, Iterable
from typing import Any, Protocol


class Poller(Protocol):
    """The long-running-operation handle `begin_delete` returns."""

    def result(self, timeout: int | None = None) -> object:
        """Wait for the operation to finish."""
        ...


class VirtualMachineOperations(Protocol):
    """The `virtual_machines` calls used: listing and the power-state read."""

    def list(self, resource_group_name: str, /) -> Iterable[Any]:
        """List the VMs of one resource group."""
        ...

    def instance_view(self, resource_group_name: str, vm_name: str, /) -> Any:
        """Read the instance view; its second status is the power state."""
        ...


class DiskOperations(Protocol):
    """The `disks` calls used: the tag read and the deletion."""

    def get(self, resource_group_name: str, disk_name: str, /) -> Any:
        """Read one managed disk (its tags)."""
        ...

    def begin_delete(self, resource_group_name: str, disk_name: str, /) -> Poller:
        """Start the deletion of one managed disk."""
        ...


class SpotComputeClient(Protocol):
    """The part of the Azure compute client this cleaner uses."""

    @property
    def virtual_machines(self) -> VirtualMachineOperations:
        """Operations on VMs."""
        ...

    @property
    def disks(self) -> DiskOperations:
        """Operations on managed disks."""
        ...


class SpotNetworkClient(Protocol):
    """The network client, only handed to the NIC delete helper."""


ComputeFactory = Callable[[], SpotComputeClient]
NetworkFactory = Callable[[], SpotNetworkClient]
