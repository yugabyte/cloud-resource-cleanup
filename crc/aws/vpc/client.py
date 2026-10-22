# Copyright (c) Yugabyte, Inc.

"""Typed Protocols of the boto3 EC2 client, resource and resource objects the VPC cleaner
uses, and nothing more. Tests pass fakes; production code gets the real `boto3` objects."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol, TypeVar

_T_co = TypeVar("_T_co", covariant=True)


class Deletable(Protocol):
    """A resource with a `delete()` call (route table association, ACL, interface, ...)."""

    def delete(self) -> object:
        """Delete the resource."""
        ...


class Collection(Protocol[_T_co]):
    """A boto3 resource collection: `all()` iterates its members."""

    def all(self) -> Iterable[_T_co]:
        """Every member of the collection."""
        ...


class InternetGateway(Deletable, Protocol):
    """An internet gateway attached to a VPC."""

    @property
    def id(self) -> str:
        """Gateway id."""
        ...


class RouteTableAssociation(Deletable, Protocol):
    """An association of a route table."""

    @property
    def main(self) -> bool:
        """True for the main association."""
        ...


class RouteTable(Deletable, Protocol):
    """A route table of a VPC."""

    @property
    def associations(self) -> Sequence[RouteTableAssociation]:
        """The route table associations."""
        ...


class Instance(Protocol):
    """An EC2 instance in a subnet."""

    def terminate(self) -> object:
        """Terminate the instance."""
        ...


class Subnet(Deletable, Protocol):
    """A subnet of a VPC."""

    @property
    def instances(self) -> Collection[Instance]:
        """Instances in the subnet."""
        ...

    @property
    def network_interfaces(self) -> Collection[Deletable]:
        """Network interfaces in the subnet."""
        ...


class SecurityGroup(Deletable, Protocol):
    """A security group of a VPC."""

    @property
    def group_name(self) -> str:
        """Group name (`default` is never deleted)."""
        ...


class NetworkAcl(Deletable, Protocol):
    """A network ACL of a VPC."""

    @property
    def is_default(self) -> bool:
        """True for the default ACL."""
        ...


class DhcpOptionsHandle(Protocol):
    """A DHCP options set."""

    def associate_with_vpc(self, *, VpcId: str) -> object:
        """Associate the options with a VPC."""
        ...


class Vpc(Protocol):
    """A boto3 VPC resource and the child collections the teardown walks."""

    @property
    def id(self) -> str:
        """VPC id."""
        ...

    @property
    def internet_gateways(self) -> Collection[InternetGateway]:
        """Attached internet gateways."""
        ...

    @property
    def route_tables(self) -> Collection[RouteTable]:
        """Route tables."""
        ...

    @property
    def subnets(self) -> Collection[Subnet]:
        """Subnets."""
        ...

    @property
    def security_groups(self) -> Collection[SecurityGroup]:
        """Security groups."""
        ...

    @property
    def network_acls(self) -> Collection[NetworkAcl]:
        """Network ACLs."""
        ...

    def detach_internet_gateway(self, *, InternetGatewayId: str) -> object:
        """Detach an internet gateway."""
        ...


class VpcCollection(Protocol):
    """`ec2.vpcs`: the server side filtered listing."""

    def filter(self, *, Filters: Sequence[Mapping[str, Any]]) -> Iterable[Vpc]:
        """List VPCs matching the EC2 filters."""
        ...


class Ec2Resource(Protocol):
    """The boto3 EC2 resource calls this cleaner uses."""

    @property
    def vpcs(self) -> VpcCollection:
        """The VPC collection."""
        ...

    def DhcpOptions(self, id: str) -> DhcpOptionsHandle:
        """A DHCP options set by id."""
        ...

    def VpcPeeringConnection(self, id: str) -> Deletable:
        """A VPC peering connection by id."""
        ...


class Ec2Client(Protocol):
    """The four EC2 client calls this cleaner uses."""

    def describe_vpc_endpoints(self, *, Filters: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        """List the VPC endpoints matching the filters."""
        ...

    def delete_vpc_endpoints(self, *, VpcEndpointIds: Sequence[str]) -> Mapping[str, Any]:
        """Delete VPC endpoints."""
        ...

    def describe_vpc_peering_connections(
        self, *, Filters: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Any]:
        """List the VPC peering connections matching the filters."""
        ...

    def delete_vpc(self, *, VpcId: str) -> object:
        """Delete one VPC."""
        ...
