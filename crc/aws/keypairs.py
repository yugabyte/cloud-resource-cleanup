import datetime
import logging
import re
from typing import Dict, List

import boto3

from crc.aws._base import get_all_regions
from crc.service import Service


class KeyPairs(Service):
    """
    A class that deletes all keypairs that match the specified name regex and are older than the specified age.
    This skips deleting variables which are in exception_regex
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
        name_regex: List[str],
        exception_regex: List[str],
        age: Dict[str, int],
    ) -> None:
        """
        Initialize the KeyPairs class

        Parameters:
            name_regex (list): List of regular expressions that match the keypair names to be deleted
            exception_regex (list): List of regular expressions that match the keypair names to be ignored
            age (Dict): A dictionary containing the time unit (hours, days, weeks) and the corresponding time value. For example: {'days': 30}
        """
        super().__init__()
        self.deleted_keypairs = []
        self.name_regex = name_regex
        self.exception_regex = exception_regex
        self.age = age

    @property
    def count(self):
        """
        This is a property decorator that returns the count of items in the keypairs_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        count = len(self.deleted_keypairs)
        logging.info(f"count of items in keypairs_to_delete: {count}")
        return count

    def delete(self):
        """
        Delete all keypairs that match the specified name regex and are older than the specified age.
        """
        exception_regex = set(self.exception_regex)
        for region in get_all_regions(
            self.service_name, self.default_region_name
        ):
            keypairs_to_delete = set()
            with boto3.client(self.service_name, region_name=region) as client:
                keypairs = client.describe_key_pairs()
                for keypair in keypairs["KeyPairs"]:
                    if "KeyName" not in keypair or "CreateTime" not in keypair:
                        continue
                    keypair_name = keypair["KeyName"]
                    keypair_create_time = keypair["CreateTime"]
                    dt = datetime.datetime.now().replace(
                        tzinfo=keypair_create_time.tzinfo
                    )

                    if not self.name_regex or (
                        any(
                            re.search(kpn, keypair_name)
                            for kpn in self.name_regex
                        )
                        and not any(
                            re.search(kpn, keypair_name)
                            for kpn in exception_regex
                        )
                    ):
                        if self.is_old(self.age, dt, keypair_create_time):
                            keypairs_to_delete.add(keypair_name)
                        else:
                            logging.info(
                                f"Keypair {keypair_name} is not old enough to be deleted."
                            )
                    else:
                        if any(
                            re.search(kpn, keypair_name)
                            for kpn in self.exception_regex
                        ):
                            logging.info(
                                f"Keypair {keypair_name} is in exception_regex {self.exception_regex}."
                            )
                for keypair_to_delete in keypairs_to_delete:
                    response = client.delete_key_pair(
                        KeyName=keypair_to_delete
                    )
                    logging.info(
                        f"Deleted keypair: {keypair_to_delete} with response: {response}"
                    )
                    self.deleted_keypairs.extend(keypair_to_delete)
