# Copyright (c) Yugabyte, Inc.

import ast
import datetime
import logging
import os
from typing import Dict

from crc.utils import init_logging


class Service:
    """
    Service class provides a method to check if a resource is older than a specified age threshold.
    It also sets up logging for the class.
    """

    # Directory where logs will be stored
    logs_dir = "logs"
    # File name of the log file
    logs_file = "crc.log"

    def __init__(self) -> None:
        """
        Initializes the Client class and sets up logging.
        """
        log_filename = os.path.join(self.logs_dir, self.logs_file)
        init_logging(log_filename)

    def is_old(
        self,
        age: Dict[str, int],
        current_time: datetime.datetime,
        creation_time: datetime.datetime,
    ) -> bool:
        """
        This function checks if the resource is old based on the age threshold specified in the age argument.
        The age argument should be a dictionary containing either 'days' or 'hours' or both as keys, and the number of days or hours as the value respectively.
        Example: age = {'days': 3, 'hours': 12}
        The function returns a boolean value - True if the resource is older than the specified age threshold and False otherwise.
        It also logs the resource age and the threshold specified for easy debugging.

        Parameters:
        age (Dict[str, int]) : A dictionary containing 'days' or 'hours' or both as keys and number of days or hours as the value respectively.
        current_time (datetime.datetime) : The current time.
        creation_time (datetime.datetime) : The time the resource was created.

        Returns:
        bool : True if the resource is older than the specified age threshold and False otherwise.
        """
        if not age:
            logging.warning("Age is not specified. Ignoring age threshold check")
            return True
        age_delta = current_time - creation_time
        age_in_days = age_delta.days
        age_in_seconds = age_delta.total_seconds()
        age_in_hours = age_in_seconds / 3600

        if "days" in age and "hours" in age:
            threshold_in_days = int(age["days"])
            threshold_in_hours = int(age["hours"])
            threshold_in_seconds = (threshold_in_days * 24 * 3600) + (
                threshold_in_hours * 3600
            )
            threshold_in_hours = threshold_in_seconds / 3600
            if age_in_hours >= threshold_in_hours:
                logging.info(
                    f"The resource is {age_in_hours} hours old and older than the threshold of {threshold_in_hours} hours."
                )
                return True
        elif "days" in age:
            threshold_in_days = int(age["days"])
            if age_in_days >= threshold_in_days:
                logging.info(
                    f"The resource is {age_in_days} days old and older than the threshold of {threshold_in_days} days."
                )
                return True
        elif "hours" in age:
            threshold_in_hours = int(age["hours"])
            if age_in_hours >= threshold_in_hours:
                logging.info(
                    f"The resource is {age_in_hours} hours old and older than the threshold of {threshold_in_hours} hours."
                )
                return True
        logging.info(
            "The resource is not older than the threshold specified in the age argument"
        )
        return False

    def get_retention_age(self, tags: Dict[str, str]):
        """
        Retrieve the 'retention_age' value from the provided tags.

        Args:
            tags (Dict[str, str]): A dictionary of tags.

        Returns:
            str | None: The value of 'retention_age' if present, otherwise None.
        """
        for tag, value in tags.items():
            if tag == "retention_age":
                logging.info(f"Found retention_age tag: {value}.")
                try:
                    value = ast.literal_eval(value)
                except Exception as e:
                    logging.error(f"Error parsing retention_age tag: {e}")
                    return None
                return value
        return None
