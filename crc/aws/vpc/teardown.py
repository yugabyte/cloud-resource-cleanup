# Copyright (c) Yugabyte, Inc.

"""Teardown of what an AWS VPC owns before the VPC itself is deleted (order of `main`)."""

from crc.aws.vpc.client import Ec2Client, Ec2Resource, Vpc


def _detach_default_dhcp_options(ec2: Ec2Resource, vpc: Vpc) -> None:
    dhcp_options_default = ec2.DhcpOptions("default")
    if dhcp_options_default:
        dhcp_options_default.associate_with_vpc(VpcId=vpc.id)


def _delete_gateways(vpc: Vpc) -> None:
    for gw in vpc.internet_gateways.all():
        vpc.detach_internet_gateway(InternetGatewayId=gw.id)
        gw.delete()


def _delete_route_tables(vpc: Vpc) -> None:
    for rt in vpc.route_tables.all():
        for rta in rt.associations:
            if not rta.main:
                rta.delete()
        if not rt.associations:
            rt.delete()


def _terminate_instances(vpc: Vpc) -> None:
    for subnet in vpc.subnets.all():
        for instance in subnet.instances.all():
            instance.terminate()


def _delete_endpoints(client: Ec2Client, vpc: Vpc) -> None:
    for ep in client.describe_vpc_endpoints(Filters=[{"Name": "vpc-id", "Values": [vpc.id]}])[
        "VpcEndpoints"
    ]:
        client.delete_vpc_endpoints(VpcEndpointIds=[ep["VpcEndpointId"]])


def _delete_security_groups(vpc: Vpc) -> None:
    for sg in vpc.security_groups.all():
        if sg.group_name != "default":
            sg.delete()


def _delete_peering_connections(client: Ec2Client, ec2: Ec2Resource, vpc: Vpc) -> None:
    peering = client.describe_vpc_peering_connections(
        Filters=[{"Name": "requester-vpc-info.vpc-id", "Values": [vpc.id]}]
    )
    for vpcpeer in peering["VpcPeeringConnections"]:
        ec2.VpcPeeringConnection(vpcpeer["VpcPeeringConnectionId"]).delete()


def _delete_network_acls(vpc: Vpc) -> None:
    for netacl in vpc.network_acls.all():
        if not netacl.is_default:
            netacl.delete()


def _delete_subnets(vpc: Vpc) -> None:
    for subnet in vpc.subnets.all():
        for interface in subnet.network_interfaces.all():
            interface.delete()
        subnet.delete()


def tear_down(client: Ec2Client, ec2: Ec2Resource, vpc: Vpc) -> None:
    """Detach and delete everything the VPC owns, in the order of main (not the VPC itself)."""
    _detach_default_dhcp_options(ec2, vpc)
    _delete_gateways(vpc)
    _delete_route_tables(vpc)
    _terminate_instances(vpc)
    _delete_endpoints(client, vpc)
    _delete_security_groups(vpc)
    _delete_peering_connections(client, ec2, vpc)
    _delete_network_acls(vpc)
    _delete_subnets(vpc)
