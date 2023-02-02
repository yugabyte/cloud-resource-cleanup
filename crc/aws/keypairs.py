# Copyright (c) Yugabyte, Inc.

import datetime
import logging
import re
from typing import Dict, List

import boto3

from crc.aws._base import get_all_regions
from crc.service import Service


class KeyPairs(Service):
    """
    The KeyPairs class is a subclass of the Service class and is used to interact with the AWS EC2 service.
    It allows for deletion of keypairs that match the specified name regular expressions and are older than the specified age.
    It also skips deleting keypairs which match the specified exception regular expressions.

    Attributes:
        service_name (str): The name of the AWS service that the class interacts with.
        default_region_name (str): The default region to be used when interacting with the AWS service.
        deleted_keypairs (List[str]): A list of keypairs that have been deleted.
        name_regex (List[str]): A list of regular expressions that match the keypair names to be deleted.
        exception_regex (List[str]): A list of regular expressions that match the keypair names to be ignored.
        age (Dict[str, int]): A dictionary containing the time unit (hours, days, weeks) and the corresponding time value. For example: {'days': 30}
        dry_run (bool): A boolean variable that indicates whether the class should operate in dry_run mode or not.
        In dry_run mode, the class will only list the Resources that match the specified filter and exception tags, but will not perform any operations on them.

    Methods:
        count: Returns the number of keypairs that have been deleted.
        delete: Deletes all keypairs that match the specified name regular expressions and are older than the specified age, and also skips deleting keypairs which match the specified exception regular expressions.
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
        name_regex: List[str],
        exception_regex: List[str],
        age: Dict[str, int],
    ) -> None:
        """
        Initialize the KeyPairs class

        Parameters:
            :param dry_run: A boolean variable that indicates whether the class should operate in dry_run mode or not.
            In dry_run mode, the class will only list the Resources that match the specified filter and exception tags,
            but will not perform any operations on them.
            name_regex (list): List of regular expressions that match the keypair names to be deleted
            exception_regex (list): List of regular expressions that match the keypair names to be ignored
            age (Dict): A dictionary containing the time unit (hours, days, weeks) and the corresponding time value. For example: {'days': 30}
        """
        super().__init__()
        self.deleted_keypairs = []
        self.dry_run = dry_run
        self.name_regex = name_regex
        self.exception_regex = exception_regex
        self.age = age

    @property
    def get_deleted(self):
        """
        This is a property decorator that returns the list of items in the keypairs_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        return self.deleted_keypairs

    @property
    def count(self):
        """
        This is a property decorator that returns the count of items in the keypairs_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        count = len(self.deleted_keypairs)
        logging.info(f"count of items in deleted_keypairs: {count}")
        return count

    def delete(self):
        """
        Delete all keypairs that match the specified name regex and are older than the specified age.
        In dry_run mode, this method will only list the keypairs that match the specified filter and exception tags,
        but will not perform any operations on them.
        """
        if self.exception_regex:
            exception_regex = set(self.exception_regex)
        else:
            exception_regex = set()
        for region in get_all_regions(self.service_name, self.default_region_name):
            keypairs_to_delete = set()
            client = boto3.client(self.service_name, region_name=region)
            keypairs = client.describe_key_pairs()
            for keypair in keypairs["KeyPairs"]:
                if "KeyName" not in keypair or "CreateTime" not in keypair:
                    continue
                keypair_name = keypair["KeyName"]
                keypair_create_time = keypair["CreateTime"]
                dt = datetime.datetime.now().replace(tzinfo=keypair_create_time.tzinfo)

                # Check if keypair name matches specified regex
                match_name_regex = not self.name_regex or any(
                    re.search(kpn, keypair_name) for kpn in self.name_regex
                )
                match_exception_regex = self.exception_regex and any(
                    re.search(kpn, keypair_name) for kpn in exception_regex
                )

                if match_name_regex and not match_exception_regex:
                    if self.is_old(
                        self.age,
                        dt,
                        keypair_create_time,
                    ):
                        keypairs_to_delete.add(keypair_name)
                    else:
                        logging.info(
                            f"Keypair {keypair_name} is not old enough to be deleted."
                        )
                else:
                    if match_exception_regex:
                        logging.info(
                            f"Keypair {keypair_name} is in exception_regex {self.exception_regex}."
                        )

            for keypair_to_delete in keypairs_to_delete:
                if not self.dry_run:
                    response = client.delete_key_pair(KeyName=keypair_to_delete)
                    logging.info(
                        f"Deleted keypair: {keypair_to_delete} with response: {response}"
                    )
                self.deleted_keypairs.append(keypair_to_delete)

        if not self.dry_run:
            logging.warning(
                f"number of AWS keypairs deleted: {len(self.deleted_keypairs)}"
            )
            logging.warning(f"List of AWS keypairs deleted: {self.deleted_keypairs}")
        else:
            logging.warning(
                f"List of AWS keypairs (Total: {len(self.deleted_keypairs)}) which will be deleted: {self.deleted_keypairs}"
            )
