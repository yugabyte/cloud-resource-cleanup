# Copyright (c) Yugabyte, Inc.

import logging
from typing import Dict, List

import boto3

from crc.aws._base import get_all_regions
from crc.service import Service


class VPC(Service):
    """
    The VPC class provides an interface for managing AWS VPC vpcs.
    It inherits from the Service class and uses boto3 to interact with the AWS VPC service.
    By default, boto3 will clean up attached resources (Route Table, subnets etc.) when a VPC is deleted.
    The class allows for filtering and excluding VPCs based on specified tags.
    The class also has properties for the number of VPCs that will be deleted.
    """

    service_name = "ec2"
    """
    The service_name variable specifies the AWS service that this class will interact with.
    """

    default_region_name = "us-west-2"
    """
    The default_region_name variable specifies the default region to be used when interacting with the AWS service.
    """

    def __init__(
        self,
        dry_run: bool,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        notags: Dict[str, List[str]],
    ) -> None:
        """
        Initializes the object with filter and exception tags to be used when searching for vpcs, as well as an age threshold for vpcs.

        :param dry_run: A boolean variable that indicates whether the class should operate in dry_run mode or not.
        In dry_run mode, the class will only list the Resources that match the specified filter and exception tags,
        but will not perform any operations on them.
        :param filter_tags: dictionary containing key-value pairs as filter tags
        :type filter_tags: Dict[str, List[str]]
        :param exception_tags: dictionary containing key-value pairs as exception tags
        :type exception_tags: Dict[str, List[str]]
        :param age: dictionary containing key-value pairs as age threshold, the key is either "days" or "hours" or both and value is the number of days or hours.
        :type age: Dict[str, int]
        :param notags: dictionary containing key-value pairs as filter tags to exclude vpcs which do not have these tags
        :type notags: Dict[str, List[str]]
        """
        super().__init__()
        self.vpc_ids_to_delete = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.notags = notags

    @property
    def get_deleted(self):
        """
        This is a property decorator that returns the list of items in the vpc_ids_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        return self.vpc_ids_to_delete

    @property
    def delete_count(self):
        """
        This is a property decorator that returns the count of items in the vpc_ids_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        count = len(self.vpc_ids_to_delete)
        logging.info(f"count of items in vpc_ids_to_delete: {count}")
        return count

    def _get_filter(self) -> List[Dict[str, List[str]]]:
        """
        Creates a filter to be used when searching for vpcs, based on the filter tags provided during initialization.

        :return: list of filters.
        :rtype: List[Dict[str, List[str]]]
        """
        filters = []
        if self.filter_tags:
            for key, value in self.filter_tags.items():
                filters.append({"Name": f"tag:{key}", "Values": value})

        logging.info(f"Filters created: {filters}")
        return filters

    def _should_skip_vpc(self, tags: List[Dict[str, str]]) -> bool:
        """
        Check if the vpc should be skipped based on the exception tags and vpcs that do not have the specified notags.
        :param tags: List of tags associated with the vpc
        :type tags: List[Dict[str,str]]
        :return: True if the vpc should be skipped, False otherwise
        :rtype: bool
        """
        if not self.exception_tags and not self.notags:
            logging.warning("Tags and notags not present")
            return False

        in_no_tags = False
        all_tags = {}

        for tag in tags:
            k = tag["Key"]
            v = tag["Value"]

            if self.exception_tags:
                if k in self.exception_tags.keys() and (
                    not self.exception_tags[k] or v in self.exception_tags[k]
                ):
                    return True

            all_tags[k] = v

        if self.notags:
            in_no_tags = all(
                key in all_tags and (not value or all_tags[key] in value)
                for key, value in self.notags.items()
            )

        return in_no_tags

    def get_vpc_ids(self, vpcs):
        """
        Given a list of VPCs, return a list of their corresponding VPC IDs.

        Args:
        - vpcs: a list of Boto3 VPC objects

        Returns:
        - A list of VPC IDs (strings)
        """
        vpc_ids = []
        for vpc in vpcs:
            vpc_ids.append(vpc.id)
        return vpc_ids

    def delete(self) -> None:
        """
        Deletes VPCs that match the filter threshold, and also checks for exception tags.
        It checks if the filter_tags and notags attribute of the class is empty,
        if so, it returns True because there are no tags set to filter the VPCs, so any VPC should be considered.

        The method will list the VPCs that match the specified filter and exception tags but will not perform any operations
        on them if dry_run mode is enabled.
        """
        vpc_filter = self._get_filter()

        for region in get_all_regions(self.service_name, self.default_region_name):
            client = boto3.client(self.service_name, region_name=region)
            ec2 = boto3.resource(self.service_name, region_name=region)
            vpcs = list(ec2.vpcs.filter(Filters=vpc_filter))

            self.vpc_ids_to_delete.extend(self.get_vpc_ids(vpcs))

            if self.dry_run:
                continue

            for vpc in vpcs:
                if self._should_skip_vpc(vpc):
                    continue

                # Detach default dhcp_options if associated with the VPC
                dhcp_options_default = ec2.DhcpOptions("default")
                if dhcp_options_default:
                    dhcp_options_default.associate_with_vpc(VpcId=vpc.id)

                # Detach and delete all gateways associated with the VPC
                for gw in vpc.internet_gateways.all():
                    vpc.detach_internet_gateway(InternetGatewayId=gw.id)
                    gw.delete()

                # Delete all route table associations
                for rt in vpc.route_tables.all():
                    for rta in rt.associations:
                        if not rta.main:
                            rta.delete()
                    if not rt.associations:
                        rt.delete()

                # Delete any instances
                for subnet in vpc.subnets.all():
                    for instance in subnet.instances.all():
                        instance.terminate()

                # Delete endpoints
                for ep in client.describe_vpc_endpoints(
                    Filters=[{"Name": "vpc-id", "Values": [vpc.id]}]
                )["VpcEndpoints"]:
                    client.delete_vpc_endpoints(VpcEndpointIds=[ep["VpcEndpointId"]])

                # Delete security groups
                for sg in vpc.security_groups.all():
                    if sg.group_name != "default":
                        sg.delete()

                # Delete VPC peering connections
                for vpcpeer in client.describe_vpc_peering_connections(
                    Filters=[{"Name": "requester-vpc-info.vpc-id", "Values": [vpc.id]}]
                )["VpcPeeringConnections"]:
                    ec2.VpcPeeringConnection(vpcpeer["VpcPeeringConnectionId"]).delete()

                # Delete non-default network ACLs
                for netacl in vpc.network_acls.all():
                    if not netacl.is_default:
                        netacl.delete()

                # Delete network interfaces
                for subnet in vpc.subnets.all():
                    for interface in subnet.network_interfaces.all():
                        interface.delete()
                    subnet.delete()

                # Finally, delete the VPC
                retry = 5
                for _ in range(retry):
                    try:
                        client.delete_vpc(VpcId=vpc.id)
                        break
                    except Exception as e:
                        logging.error(e)
                        logging.error(f"Failed deleting VPC {vpc.id}. Retrying...")
                else:
                    logging.error(
                        f"Failed to Delete VPC {vpc.id} after {retry} retries"
                    )
        if self.dry_run:
            logging.warning(
                f"List of AWS VPCs (Total: {len(self.vpc_ids_to_delete)}) which will be deleted: {self.vpc_ids_to_delete}"
            )
        else:
            logging.warning(
                f"Number of AWS VPCs deleted: {len(self.vpc_ids_to_delete)}"
            )
            logging.warning(f"List of AWS VPCs deleted: {self.vpc_ids_to_delete}")
