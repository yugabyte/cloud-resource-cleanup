# Copyright (c) Yugabyte, Inc.

import logging
from typing import Dict, List

import boto3
import json

from crc.aws._base import get_all_regions
from crc.service import Service

class KMS(Service):
    service_name = "kms"
    """
    The service_name variable specifies the AWS service that this class will interact with.
    """

    default_region_name = "us-west-2"
    """
    The default_region_name variable specifies the default region to be used when interacting with the AWS service.
    """

    jenkins_user = 'arn:aws:iam::454529406029:user/jenkins-slave'
    """
    The jenkins_user variable specifis the user for which associated KMS has to be deleted
    """
    def __init__(
        self,
        dry_run: bool,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
    ) -> None:
        """
        Initializes the object with filter tags to be used when searching for kms.

        :param dry_run: A boolean variable that indicates whether the class should operate in dry_run mode or not.
        In dry_run mode, the class will only list the Resources that match the specified filter and exception tags,
        but will not perform any operations on them.
        :param filter_tags: dictionary containing key-value pairs as filter tags
        :type filter_tags: Dict[str, List[str]]
        """
        super().__init__()
        self.kms_keys_to_delete = []
        self.dry_run = dry_run
        self.filter_tags = filter_tags
        self.exception_tags = exception_tags
    
    @property
    def get_deleted(self):
        """
        This is a property decorator that returns the list of items in the kms_keys_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        return self.kms_keys_to_delete
    
    def _get_filter(self) -> List[Dict[str, List[str]]]:
        """
        Creates a filter to be used when searching for kms, based on the filter tags provided during initialization.

        :return: list of filters.
        :rtype: List[Dict[str, List[str]]]
        """
        filters = []
        if self.filter_tags:
            for key, value in self.filter_tags.items():
                filters.append({"Name": f"tag:{key}", "Values": value})

        logging.info(f"Filters created: {filters}")
        return filters

    def _should_skip_kms(self, tags: List[Dict[str, str]]) -> bool:
        """
        Check if the kms should be skipped based on the exception tags.
        :param tags: List of tags associated with the kms
        :type tags: List[Dict[str,str]]
        :return: True if the kms should be skipped, False otherwise
        :rtype: bool
        """
        if not self.exception_tags:
            logging.warning("Tags and not present")
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

        kms_filter = self._get_filter()

        regions = get_all_regions(self.service_name, self.default_region_name)

        for region in regions:
            keys = {}
            skipped_keys = []
            kms_keys = []
            active_keys = []
            client = boto3.client(self.service_name, region_name=region)
            
            paginator = client.get_paginator('list_keys')
            for page in paginator.paginate(Filters=kms_filter):
                kms_keys.extend(page['Keys'])

            self.kms_keys_to_delete.extend(kms_keys)

            if self.dry_run:
                continue

            logging.info(f"Total keys found = {len(kms_keys)}")
            for keys in kms_keys:
                try: 
                    if self._should_skip_vpc(keys):
                        continue

                    key_metadata = client.describe_key(KeyId=keys['KeyId'])
                    key_state = key_metadata['KeyMetadata']['KeyState']
                    key_des = key_metadata['KeyMetadata']['Description']
                    # Check if the key meets the filter conditions
                    if (
                        key_state == 'Enabled'
                        and "Yugabyte Master Key" in key_des
                        and all(keys.get(tag, None) in self.filter_tags.get(tag, []) for tag in self.filter_tags)
                    ):
                        policy = client.get_key_policy(KeyId=keys['KeyId'], PolicyName='default')['Policy']
                        policy_json = json.loads(policy)
                        for ids in policy_json['Statement']:
                            user_arn = ids['Principal']['AWS']
                            if ids['Principal']['AWS'] == self.jenkins_user:
                                print(f"Key {keys['KeyId']} found with user {ids['Principal']['AWS']}")
                                active_keys.append(keys['KeyId'])
                except:
                    print(f"KEY SKIPPED")
                    skipped_keys.append(keys['KeyId'])

            if not self.dry_run:
                for cmk_id in keys:
                    client.schedule_key_deletion(KeyId=cmk_id, PendingWindowInDays=14) # Decide for appropriate window
                    logging.info(f"CMK - {cmk_id} Deleted from AWS console")

            # Add deleted IPs to deleted_ips list
            self.kms_keys_to_delete.extend(list(keys.keys()))

        if not self.dry_run:
            logging.warning(
                f"number of AWS KMS deleted: {len(self.kms_keys_to_delete)}"
            )
            logging.warning(f"List of AWS KMS deleted: {self.kms_keys_to_delete}")
        else:
            logging.warning(
                f"List of AWS KMS (Total: {len(self.kms_keys_to_delete)}) which will be deleted: {self.kms_keys_to_delete}"
            )