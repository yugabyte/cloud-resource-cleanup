# Copyright (c) Yugabyte, Inc.

import logging
from typing import List

import boto3

"""
This module contains a function to get all available regions on AWS for a specific service.
"""


def get_all_regions(service_name: str, default_region_name: str) -> List[str]:
    """
    Returns a list of all regions available on AWS for a given service.

    :param service_name: The name of the service, such as 'ec2' or 's3'
    :type service_name: str
    :param default_region_name: The default region to use when initializing the boto3 client
    :type default_region_name: str
    :return: list of regions available for the given service
    :rtype: List[str]
    """
    client = boto3.client(service_name, region_name=default_region_name)
    regions = [region["RegionName"] for region in client.describe_regions()["Regions"]]
    logging.info(f"Retrieved list of regions: {regions}")
    return regions
