# Copyright (c) Yugabyte, Inc.

"""The Azure SDK calls the VM cleaner uses, behind small typed Protocols.

The real clients come from the legacy `crc.azu._base.Base`; tests pass fakes that satisfy these
Protocols instead of the SDK.
"""

import datetime
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Protocol


class SdkVm(Protocol):
    """The attributes of an Azure virtual machine this cleaner reads."""

    @property
    def name(self) -> str:
        """Name of the VM."""
        ...

    @property
    def tags(self) -> Mapping[str, str] | None:
        """Tags, or `None` for an untagged VM."""
        ...

    @property
    def time_created(self) -> datetime.datetime:
        """Creation time."""
        ...


class InstanceStatus(Protocol):
    """One entry of the instance view statuses."""

    @property
    def display_status(self) -> str:
        """Human readable status, for example `VM running`."""
        ...


class InstanceView(Protocol):
    """The instance view of a VM."""

    @property
    def statuses(self) -> Sequence[InstanceStatus]:
        """Provisioning status first, power state second."""
        ...


class LongRunningOperation(Protocol):
    """The poller `begin_delete` returns."""

    def result(self, timeout: float | None = None) -> object:
        """Wait for the operation to finish."""
        ...


class VmOperations(Protocol):
    """The four `virtual_machines` calls this cleaner uses (positional arguments, as on main)."""

    def list_all(self) -> Iterable[SdkVm]:
        """List the VMs of the whole subscription."""
        ...

    def instance_view(self, resource_group_name: str, vm_name: str, /) -> InstanceView:
        """Instance view of one VM of the configured resource group."""
        ...

    def begin_delete(self, resource_group_name: str, vm_name: str, /) -> LongRunningOperation:
        """Start the deletion of one VM."""
        ...

    def begin_power_off(self, resource_group_name: str, vm_name: str, /) -> object:
        """Power off one VM (it stays allocated)."""
        ...


class VmComputeClient(Protocol):
    """The part of the Azure compute client this cleaner uses."""

    @property
    def virtual_machines(self) -> VmOperations:
        """Operations on virtual machines."""
        ...


class NicOperations(Protocol):
    """The `network_interfaces` call the leftover NIC delete uses."""

    def begin_delete(
        self, resource_group_name: str, network_interface_name: str, /
    ) -> LongRunningOperation:
        """Start the deletion of one NIC."""
        ...


class VmNetworkClient(Protocol):
    """The part of the Azure network client this cleaner uses."""

    @property
    def network_interfaces(self) -> NicOperations:
        """Operations on network interfaces."""
        ...


ComputeFactory = Callable[[], VmComputeClient]
NetworkFactory = Callable[[], VmNetworkClient]
