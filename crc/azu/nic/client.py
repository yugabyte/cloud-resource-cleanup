# Copyright (c) Yugabyte, Inc.

"""The Azure SDK calls the NIC cleaner uses, as typed Protocols."""

from collections.abc import Callable, Iterable
from typing import Any, Protocol


class DeletePoller(Protocol):
    """The long-running-operation handle `begin_delete` returns."""

    def result(self) -> object:
        """Wait for the deletion to finish."""
        ...


class NicOperations(Protocol):
    """The two `network_interfaces` calls this cleaner uses."""

    def list(self, resource_group_name: str, /) -> Iterable[Any]:
        """List the NICs of one resource group."""
        ...

    def begin_delete(
        self, resource_group_name: str, network_interface_name: str, /
    ) -> DeletePoller:
        """Start the deletion of one NIC (positional arguments, as on main)."""
        ...


class NicNetworkClient(Protocol):
    """The part of the Azure network client this cleaner uses."""

    @property
    def network_interfaces(self) -> NicOperations:
        """Operations on network interfaces."""
        ...


ClientFactory = Callable[[], NicNetworkClient]
