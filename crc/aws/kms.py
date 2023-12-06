# Copyright (c) Yugabyte, Inc.

import datetime
import json
import logging
from typing import Dict, List

import boto3

from crc.aws._base import get_all_regions
from crc.service import Service


class Kms(Service):
    service_name = "kms"
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
        kms_key_descriptiom: str,
        jenkins_user: str,
        kms_pending_window: int,
        age: Dict[str, int],
    ) -> None:
        """
        Initializes the object with filter tags to be used when searching for kms.

        :param dry_run: A boolean variable that indicates whether the class should operate in dry_run mode or not.
        In dry_run mode, the class will only list the Resources that match the specified filter and exception tags,
        but will not perform any operations on them.
        :param filter_tags: dictionary containing key-value pairs as filter tags
        :type filter_tags: Dict[str, List[str]]
        :param exception_tags: dictionary containing key-value pairs as exception tags
        :type exception_tags: Dict[str, List[str]]
        :param kms_key_description: name/string matching key description in key policy
        :type kms_key_description: str
        :param jenkins_user: User for which associated keys have to be deleted
        :type jenkins_user: str
        :param kms_pending_window: The duration in days until keys will be in Pending deletion state before getting deleted
        :type kms_pending_window: int
        :param age: dictionary containing key-value pairs as age threshold, the key is either "days" or "hours" or both and value is the number of days or hours.
        :type age: Dict[str, int]
        """
        super().__init__()
        self.kms_keys_to_delete = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
        self.kms_key_description = kms_key_descriptiom
        self.jenkins_user = jenkins_user
        self.kms_pending_window = kms_pending_window
        self.age = age

    @property
    def get_deleted(self):
        """
        This is a property decorator that returns the list of items in the kms_keys_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        return self.kms_keys_to_delete

    def _should_skip_kms(self, tags: List[Dict[str, str]]) -> bool:
        """
        Check if the kms should be skipped based on the exception tags.
        :param tags: List of tags associated with the kms
        :type tags: List[Dict[str,str]]
        :return: True if the kms should be skipped, False otherwise
        :rtype: bool
        """
        if not self.exception_tags:
            logging.warning("Exception tags and not present")
            return False

        for tag in tags:
            k = tag["Key"]
            v = tag["Value"]

            if self.exception_tags:
                if k in self.exception_tags.keys() and (
                    not self.exception_tags[k] or v in self.exception_tags[k]
                ):
                    return True

        return False

    def delete(self):
        """
        Delete KMS that match the specified filter_tags.
        In dry_run mode, this method will only list the KMS (CMK) that match the specified filter,
        but will not perform any operations on them.
        """

        keys = {}
        skipped_keys = []
        kms_keys = []
        active_keys = []
        client = boto3.client(self.service_name, region_name=self.default_region_name)

        paginator = client.get_paginator("list_keys")
        for page in paginator.paginate():
            kms_keys.extend(page["Keys"])

        logging.info(f"Total keys found = {len(kms_keys)}")

        for keys in kms_keys:
            try:
                key_tags = client.list_resource_tags(KeyId=keys["KeyId"])
                response_tags = key_tags.get("Tags", [])

                response_set = {
                    (tag["TagKey"], tag["TagValue"]) for tag in response_tags
                }
                if self.filter_tags:
                    filter_set = {
                        (k, v) for k, values in self.filter_tags.items() for v in values
                    }
                else:
                    filter_set = set()

                if not filter_set.issubset(response_set):
                    continue

                if self._should_skip_kms(keys):
                    continue

                key_metadata = client.describe_key(KeyId=keys["KeyId"])
                key_state = key_metadata["KeyMetadata"]["KeyState"]
                key_des = key_metadata["KeyMetadata"]["Description"]
                key_creation_date = key_metadata["KeyMetadata"]["CreationDate"]

                if (
                    key_state == "Enabled"
                    and self.kms_key_description in key_des
                    and self.is_old(
                        self.age,
                        datetime.datetime.now().astimezone(key_creation_date.tzinfo),
                        key_creation_date,
                    )
                ):
                    policy = client.get_key_policy(
                        KeyId=keys["KeyId"], PolicyName="default"
                    )["Policy"]
                    policy_json = json.loads(policy)
                    for ids in policy_json["Statement"]:
                        user_arn = ids["Principal"]["AWS"]
                        if user_arn == self.jenkins_user:
                            logging.info(
                                f"Key {keys['KeyId']} found with user {user_arn}"
                            )
                            active_keys.append(keys["KeyId"])
            except:
                logging.warning(f"KEY SKIPPED {keys['KeyId']}")
                skipped_keys.append(keys["KeyId"])

        logging.info(f"total number of active Jenkins keys = {len(active_keys)}")

        if not self.dry_run:
            for cmk_id in active_keys:
                client.schedule_key_deletion(
                    KeyId=cmk_id, PendingWindowInDays=self.kms_pending_window
                )
                logging.info(f"CMK - {cmk_id} Deleted from AWS console")

        # Add deleted keys to kms_keys_to_delete list
        self.kms_keys_to_delete.extend(list(active_keys))

        if not self.dry_run:
            logging.warning(
                f"number of AWS KMS deleted: {len(self.kms_keys_to_delete)}"
            )
            logging.warning(f"List of AWS KMS deleted: {self.kms_keys_to_delete}")
        else:
            logging.warning(
                f"List of AWS KMS (Total: {len(self.kms_keys_to_delete)}) which will be deleted: {self.kms_keys_to_delete}"
            )
