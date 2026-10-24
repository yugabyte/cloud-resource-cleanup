# Copyright (c) Yugabyte, Inc.

"""The Azure SDK calls the IP cleaner uses, as typed Protocols."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Protocol

TagSpec = Mapping[str, Sequence[str]]


class PublicIpOperations(Protocol):
    """The two `public_ip_addresses` calls this cleaner uses."""

    def list(self, resource_group_name: str) -> Iterable[Any]:
        """List the public IP addresses of one resource group."""
        ...

    def begin_delete(self, *, resource_group_name: str, public_ip_address_name: str) -> object:
        """Start the deletion of one public IP address."""
        ...


class IpNetworkClient(Protocol):
    """The part of the Azure network client this cleaner uses."""

    @property
    def public_ip_addresses(self) -> PublicIpOperations:
        """Operations on public IP addresses."""
        ...


ClientFactory = Callable[[], IpNetworkClient]
