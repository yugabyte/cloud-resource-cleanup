# Copyright (c) Yugabyte, Inc.

"""AWS VPC cleaner package.

`VpcCleaner` (`cleaner.py`) implements the `crc.core.cleaner.Cleaner` contract and is
registered as `aws` / `vpc`. `VPC` is the legacy class `crc.py` uses; it keeps its
constructor and attributes and drives the cleaner region by region, VPC by VPC, in the exact
call order of the original implementation. The old import path `crc.aws.vpc` keeps working.
"""

from crc.aws.vpc.cleaner import VPC, VpcCleaner, VpcRuntime, build_vpc_cleaner
from crc.aws.vpc.client import (
    Collection,
    Deletable,
    DhcpOptionsHandle,
    Ec2Client,
    Ec2Resource,
    Instance,
    InternetGateway,
    NetworkAcl,
    RouteTable,
    RouteTableAssociation,
    SecurityGroup,
    Subnet,
    Vpc,
    VpcCollection,
)

__all__ = [
    "VPC",
    "Collection",
    "Deletable",
    "DhcpOptionsHandle",
    "Ec2Client",
    "Ec2Resource",
    "Instance",
    "InternetGateway",
    "NetworkAcl",
    "RouteTable",
    "RouteTableAssociation",
    "SecurityGroup",
    "Subnet",
    "Vpc",
    "VpcCleaner",
    "VpcCollection",
    "VpcRuntime",
    "build_vpc_cleaner",
]
