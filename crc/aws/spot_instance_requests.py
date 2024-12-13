# Copyright (c) Yugabyte, Inc.

import datetime
import logging
from typing import Dict, List, Tuple

import boto3
from dateutil.tz import tzutc

from crc.aws._base import get_all_regions
from crc.service import Service


class SpotInstanceRequests(Service):
    """
    The VM class provides an interface for managing AWS SpotInstanceRequests.
    It inherits from the Service class and uses boto3 to interact with the AWS EC2 service.
    The class defaults to using the UTC time format for calculating age.
    The class allows for filtering and excluding SpotInstanceRequests based on specified tags, as well as an age threshold for SpotInstanceRequests.
    The class also has properties for the number of SpotInstanceRequests that will be deleted.
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
        age: Dict[str, int],
        notags: Dict[str, List[str]],
    ) -> None:
        """
        Initializes the object with filter and exception tags to be used when searching for SpotInstanceRequests, as well as an age threshold for SpotInstanceRequests.

        :param dry_run: A boolean variable that indicates whether the class should operate in dry_run mode or not.
        In dry_run mode, the class will only list the Resources that match the specified filter and exception tags,
        but will not perform any operations on them.
        :param filter_tags: dictionary containing key-value pairs as filter tags
        :type filter_tags: Dict[str, List[str]]
        :param exception_tags: dictionary containing key-value pairs as exception tags
        :type exception_tags: Dict[str, List[str]]
        :param age: dictionary containing key-value pairs as age threshold, the key is either "days" or "hours" or both and value is the number of days or hours.
        :type age: Dict[str, int]
        :param notags: dictionary containing key-value pairs as filter tags to exclude instances which do not have these tags
        :type notags: Dict[str, List[str]]
        """
        super().__init__()
        self.spot_requests_to_delete = []
        self.instance_ids_to_delete = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.age = age
        self.notags = notags

    @property
    def get_deleted(self):
        """
        This is a property decorator that returns the list of items in the spot_requests_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        return self.spot_requests_to_delete

    @property
    def delete_count(self):
        """
        This is a property decorator that returns the count of items in the spot_requests_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        count = len(self.spot_requests_to_delete)
        logging.info(f"count of items in spot_requests_to_delete: {count}")
        return count

    def _get_filter(self) -> List[Dict[str, List[str]]]:
        """
        Creates a filter to be used when searching for spot requests, based on the provided filter tags during initialization.

        :return: list of filters.
        :rtype: List[Dict[str, List[str]]]
        """
        filters = []
        if self.filter_tags:
            for key, value in self.filter_tags.items():
                filters.append({"Name": f"tag:{key}", "Values": value})

        logging.info(f"Filters created: {filters}")
        return filters

    def _get_filtered_requests(
        self, spot_request_details: dict
    ) -> Tuple[List[str], List[str]]:
        """
        Retrieves a list of SpotInstanceRequests that match the filter and age threshold,
        checks for exception tags, and also checks for SpotInstanceRequests that do not have the specified notags.
        """
        spot_requests = []
        instance_ids = []
        for spot_request in spot_request_details["SpotInstanceRequests"]:
            for request in spot_request["SpotInstanceRequestId"]:
                try:
                    if "Tags" not in request:
                        continue
                    tags = request["Tags"]
                    if self._should_skip_spot_request(tags):
                        continue
                    instance_id = request["InstanceId"]
                    if self.is_old(
                        self.age,
                        # Defaults to use utc timezone
                        datetime.datetime.now(tz=tzutc()),
                    ):
                        instance_ids.append(instance_id)
                        spot_requests.append(request)
                        logging.info(
                            f"Spot Request {request} with Instance ID {instance_id} added to list of requests to be cleaned up."
                        )
                except Exception as e:
                    logging.error(
                        f"Error occurred while processing spot request {request}: {e}"
                    )
        return spot_requests, instance_ids

    def _should_skip_spot_request(self, tags: List[Dict[str, str]]) -> bool:
        """
        Check if the SpotInstanceRequests should be skipped based on the exception tags and SpotInstanceRequests that do not have the specified notags.
        :param tags: List of tags associated with the SpotInstanceRequests
        :type tags: List[Dict[str,str]]
        :return: True if the SpotInstanceRequests should be skipped, False otherwise
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

    def delete(
        self,
    ) -> None:
        """
        Deletes SpotInstanceRequests that match the filter and age threshold, and also checks for exception tags.
        It checks if the filter_tags and notags attribute of the class is empty,
        if so, it returns True because there are no tags set to filter the SpotInstanceRequests, so any SpotInstanceRequest should be considered.
        The method will list the SpotInstanceRequests that match the specified filter and exception tags but will not perform
        any operations on them if dry_run mode is enabled.
        """
        spot_filter = self._get_filter()
        for region in get_all_regions(self.service_name, self.default_region_name):
            client = boto3.client(self.service_name, region_name=region)
            describe_spot_response = client.describe_spot_instance_requests(
                Filters=spot_filter
            )

            (
                requests_to_operate,
                instance_id_to_operate,
            ) = self._get_filtered_requests(describe_spot_response)

            if requests_to_operate:
                try:
                    finalized_requests = []
                    finalised_instances = []
                    if not self.dry_run:
                        for ind, req in enumerate(requests_to_operate):
                            try:
                                client.cancel_spot_instance_requests(
                                    SpotInstanceRequestIds=[req]
                                )
                                finalized_requests.append(req)
                                finalised_instances.append(instance_id_to_operate[ind])
                            except Exception as e:
                                logging.error(
                                    f"Error occured while deleting spot instance request {req}: {e}"
                                )
                        for i in range(len(finalized_requests)):
                            logging.info(
                                f"Spot Instance Request: {finalized_requests[i]} deleted."
                            )
                        self.spot_requests_to_delete.extend(finalized_requests)
                        self.instance_ids_to_delete.extend(finalised_instances)
                    else:
                        self.spot_requests_to_delete.extend(finalized_requests)
                        self.instance_ids_to_delete.extend(finalised_instances)
                except Exception as e:
                    logging.error(
                        f"Error occurred while deleting spot instance requests: {e}"
                    )

        if not self.spot_requests_to_delete:
            logging.warning(f"No SpotInstanceRequest to delete.")

        if self.dry_run:
            logging.warning(
                f"List of AWS SpotInstanceRequest (Total: {len(self.spot_requests_to_delete)}) which will be deleted: {self.spot_requests_to_delete}"
            )
            logging.warning(
                f"List of AWS SpotInstanceRequest (Total: {len(self.instance_ids_to_delete)}) which we should delete manually: {self.instance_ids_to_delete}"
            )
        else:
            logging.warning(
                f"List of AWS SpotInstanceRequest (Total: {len(self.spot_requests_to_delete)}) deleted: {self.spot_requests_to_delete}"
            )
            logging.warning(
                f"List of AWS SpotInstanceRequest (Total: {len(self.instance_ids_to_delete)}) which we should delete manually: {self.instance_ids_to_delete}"
            )
