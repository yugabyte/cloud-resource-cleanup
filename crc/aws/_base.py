# Copyright (c) Yugabyte, Inc.

"""
This module contains a function to get all available regions on AWS for a specific service.
"""

import logging

import boto3

from crc.aws.connectivity import CONNECTIVITY_ERRORS


def get_all_regions(service_name: str, default_region_name: str) -> list[str]:
    """
    Returns a list of all regions available on AWS for a given service.

    :param service_name: The name of the service, such as 'ec2' or 's3'
    :type service_name: str
    :param default_region_name: The default region to use when initializing the boto3 client
    :type default_region_name: str
    :return: list of regions available for the given service
    :rtype: List[str]
    """
    try:
        client = boto3.client(service_name, region_name=default_region_name)
        regions: list[str] = [
            region["RegionName"] for region in client.describe_regions()["Regions"]
        ]
        logging.info("Retrieved list of regions: %s", regions)
        return regions
    except CONNECTIVITY_ERRORS as e:
        logging.warning(
            "describe_regions failed for %s via %s (%s); using boto3 static region list",
            service_name,
            default_region_name,
            type(e).__name__,
        )
        session = boto3.session.Session()
        static_regions: list[str] = session.get_available_regions(service_name)
        logging.info("Retrieved list of regions (static): %s", static_regions)
        return static_regions
