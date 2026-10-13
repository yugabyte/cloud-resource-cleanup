# Copyright (c) Yugabyte, Inc.

import ast
import datetime
import logging
import os
from typing import Dict, List, Optional, Tuple, Union

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
    AGE_KEYS = frozenset({"days", "hours"})

    def __init__(self) -> None:
        """
        Initializes the Client class and sets up logging.
        """
        log_filename = os.path.join(self.logs_dir, self.logs_file)
        init_logging(log_filename)

    def require_positive_age(
        self, age: Optional[Union[Dict[str, int], int]], label: str = "age"
    ) -> Dict[str, int]:
        """
        Require a non-empty, >0 age gate. ``is_old({})`` returns True, so
        destructive modules must reject missing/zero ages in ``__init__``.
        """
        if not age:
            raise ValueError(
                "%s is required and must be greater than zero "
                "(empty age matches every resource)" % label
            )
        if isinstance(age, int):
            age = {"days": age}
        if not isinstance(age, dict):
            raise ValueError("%s must be an int or dict of days/hours" % label)
        extra = set(age) - self.AGE_KEYS
        if extra:
            raise ValueError(
                "%s has unsupported keys %s; use days and/or hours"
                % (label, sorted(extra))
            )
        if not (set(age) & self.AGE_KEYS):
            raise ValueError("%s must include days and/or hours" % label)
        normalized: Dict[str, int] = {}
        for key, value in age.items():
            try:
                number = int(value)
            except (TypeError, ValueError, OverflowError) as e:
                # OverflowError: e.g. int(inf) from literal {'days': 1e999}
                raise ValueError("%s %s must be an integer" % (label, key)) from e
            if number < 0:
                raise ValueError("%s %s cannot be negative" % (label, key))
            normalized[key] = number
        total_hours = normalized.get("days", 0) * 24 + normalized.get("hours", 0)
        if total_hours <= 0:
            raise ValueError("%s must be greater than zero" % label)
        return normalized

    def _lookup_custom_age_tag(
        self,
        tags: Union[Dict[str, str], List[Dict[str, str]]],
        key: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Return ``(present, raw_value)`` for ``custom_age_tag_key``.

        ``present`` is True whenever the key exists on the resource (including
        empty / unparseable values). Distinguishes "tag absent" from
        "tag present but bad" — ``get_retention_age`` alone cannot.
        """
        if not key or not tags:
            return False, None
        if isinstance(tags, list):
            for tag in tags:
                if tag.get("Key") == key:
                    return True, tag.get("Value")
            return False, None
        # Dict / GCP ScalarMapContainer
        if key in tags:
            return True, tags.get(key)
        return False, None

    def validated_retention_age(
        self,
        tags: Union[Dict[str, str], List[Dict[str, str]]],
        key: str,
    ) -> Optional[Dict[str, int]]:
        """
        Parse ``custom_age_tag_key`` and require a positive age.

        Returns ``None`` when the tag is absent (caller should use ``--age``).
        Raises ``ValueError`` when the tag is present but empty, unparseable,
        invalid, or zero — callers must skip the resource (fail closed), not
        fall back to ``--age``.
        """
        if not key:
            return None
        present, raw_str = self._lookup_custom_age_tag(tags, key)
        if not present:
            return None
        if raw_str is None or str(raw_str).strip() == "":
            logging.warning(
                "Custom age tag %r is present but empty (skipping resource)", key
            )
            raise ValueError("custom age tag %r is present but empty" % key)
        # Parse here (not via _parse_literal) so TypeError/etc. become
        # ValueError for spot callers. Do not change _parse_literal —
        # get_retention_age callers (disk/vm) rely on TypeError escaping
        # into their except Exception fail-closed paths.
        try:
            parsed = ast.literal_eval(str(raw_str))
        except (ValueError, SyntaxError, TypeError) as e:
            logging.warning(
                "Custom age tag %r=%r is unparseable (skipping resource): %s",
                key,
                raw_str,
                e,
            )
            raise ValueError(
                "custom age tag %r is present but unparseable: %r" % (key, raw_str)
            ) from e
        try:
            return self.require_positive_age(parsed, "custom age tag")
        except ValueError as e:
            logging.warning(
                "Invalid custom age tag %r for key %r (skipping resource): %s",
                parsed,
                key,
                e,
            )
            raise

    def get_min_age(self, dict1, dict2):
        """
        Compares two dictionaries containing 'days' and 'hours' keys and returns the one representing
        the lesser amount of time. The dictionaries may contain both 'days' and 'hours', or just one of them.

        The comparison is done by converting both dictionaries to a total number of hours and comparing those values.

        Args:
            dict1 (dict): A dictionary containing 'days' and/or 'hours'. Example: {"days": 3, "hours": 5}
            dict2 (dict): A dictionary containing 'days' and/or 'hours'. Example: {"days": 2, "hours": 10}

        Returns:
            dict: The dictionary that represents the smaller time duration. If both are equal, returns `dict2`.

        Example:
            dict1 = {"days": 3, "hours": 5}
            dict2 = {"days": 2, "hours": 3}
            get_min_age(dict1, dict2)  # Returns dict2 {"days": 2, "hours": 3}
        """
        # Define the number of hours in a day for conversion
        HOURS_IN_A_DAY = 24

        # Calculate total hours for dict1, assuming 0 for missing keys
        days1 = dict1.get("days", 0)
        hours1 = dict1.get("hours", 0)
        total_hours1 = (days1 * HOURS_IN_A_DAY) + hours1

        # Calculate total hours for dict2, assuming 0 for missing keys
        days2 = dict2.get("days", 0)
        hours2 = dict2.get("hours", 0)
        total_hours2 = (days2 * HOURS_IN_A_DAY) + hours2

        # Compare total hours and return the dictionary with the smaller time duration
        return dict1 if total_hours1 < total_hours2 else dict2

    def is_old(
        self,
        age: Union[Dict[str, int], int],
        current_time: datetime.datetime,
        creation_time: datetime.datetime,
    ) -> bool:
        """
        Determines if a resource is older than a specified age threshold.

        The age threshold can either be an integer or a dictionary:
            - If `age` is an integer, it is treated as 'days' by default.
            - If `age` is a dictionary, it can include 'days', 'hours', or both as keys with their respective values.

        Example:
            age = {'days': 3, 'hours': 12}  # 3 days and 12 hours
            age = 7  # Equivalent to {'days': 7}

        The function calculates the resource's age based on its `creation_time`
        and compares it with the provided threshold.

        Parameters:
            age (Dict[str, int] | int): A dictionary with 'days' and/or 'hours' as keys
                                        and their corresponding integer values,
                                        or a single integer representing days.
            current_time (datetime.datetime): The current time for comparison.
            creation_time (datetime.datetime): The time the resource was created.

        Returns:
            bool: True if the resource is older than the specified threshold, otherwise False.

        Logs:
            - Warning if no age threshold is specified.
            - Info logs for debugging resource age and the threshold applied.
        """
        if not age:
            logging.debug("Age is not specified. Ignoring age threshold check")
            return True

        if isinstance(age, int):
            # Default to 'days' if a single integer is provided
            age = {"days": age}

        max_age = os.getenv("MAX_AGE")
        if max_age:
            max_age = ast.literal_eval(max_age)
            if self.get_min_age(age, max_age) == max_age:
                logging.info(
                    f"Overwriting resource age: Setting age to the maximum threshold of {max_age}."
                )
                age = max_age

        logging.info(f"Validating resource age with threshold: {age}")
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

    def _parse_literal(self, value: str, key: str) -> Optional[str]:
        """Helper function to safely parse a literal value."""
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError) as e:
            logging.error(f"Error parsing '{key}' tag value '{value}': {e}")
            return None

    def get_retention_age(
        self, tags: Union[Dict[str, str], List[Dict[str, str]]], key: str
    ) -> Optional[str]:
        """
        Retrieve the 'retention_age' value from the provided tags.

        Args:
            tags (Union[Dict[str, str], List[Dict[str, str]]]): Tags as a dictionary or list of key-value pairs.
            key (str): The key to search for in the tags.

        Returns:
            Optional[str]: The value of 'retention_age' if present and valid, otherwise None.
        """
        if not key:
            logging.debug("No custom_age_tag_key provided to search for.")
            return None

        if not tags:
            logging.warning("No tags provided to search for custom_age_tag_key.")
            return None

        logging.info(f"Searching for custom_age_tag_key: {key}")
        try:
            # Process tags provided as a list of dictionaries, specific to AWS.
            if isinstance(tags, list):
                for tag in tags:
                    if tag.get("Key") == key:
                        value = tag.get("Value")
                        if value:
                            logging.info(f"Found '{key}' tag: {value}")
                            return self._parse_literal(value, key)
            else:
                # Process tags when provided as a dictionary, used by Azure and GCP.
                # For GCP, the type of tags is <class 'google._upb._message.ScalarMapContainer'>.
                # We will treat it as a dictionary and proceed accordingly.
                value = tags.get(key)
                if value:
                    logging.info(f"Found '{key}' tag: {value}")
                    return self._parse_literal(value, key)
        except Exception as e:
            logging.error(f"Error retrieving '{key}' tag: {e}")

        return None
