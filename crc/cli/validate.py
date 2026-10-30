"""Input validators of crc.py: type checks on parsed flag values and the InfluxDB fields."""

from typing import Any


def is_valid_type(name: str, value: Any, expected_type: Any) -> None:
    """
    Check if the given value is of the expected type, raises a ValueError if it is not.
    :param name: name of the variable being checked
    :param value: the value of the variable being checked
    :param expected_type: the expected type of the variable
    """
    if not isinstance(value, expected_type):
        raise ValueError(f"{name} should be of type {expected_type}, but got {type(value)}")


def is_valid_list(name: str, value: Any) -> None:
    """
    Check if the given value is a list and raises a ValueError if it is not.
    :param name: name of the variable being checked
    :param value: the value of the variable being checked
    """
    if value is not None:
        is_valid_type(name, value, list)


def is_valid_dict(name: str, value: Any) -> None:
    """
    Check if the given value is a dict and raises a ValueError if it is not.
    :param name: name of the variable being checked
    :param value: the value of the variable being checked
    """
    if value is not None:
        is_valid_type(name, value, dict)


def are_values_of_dict_lists(name: str, value: Any) -> None:
    """
    Check if the values of the given dict are lists and raises a ValueError if any of them is not.
    :param name: name of the variable being checked
    :param value: the value of the variable being checked
    """
    if value is not None:
        is_valid_dict(name, value)
        for key, val in value.items():
            is_valid_list(f"Value of {name} with key {key}", val)


def _validate_influxdb_input(influxdb: Any, field: str) -> None:
    """
    Validates a required field in the InfluxDB input.

    Args:
        influxdb (dict): The InfluxDB input to validate.
        field (str): The field that is required in the InfluxDB input.

    Raises:
        ValueError: If the required field is not present in the InfluxDB input.
    """
    if field not in influxdb:
        raise ValueError(
            f"The field '{field}' is required in the InfluxDB input, but was not found."
        )


def validate_influxdb_inputs(influxdb: Any) -> None:
    """
    Validates the required fields in the InfluxDB input.

    Args:
        influxdb (dict): The InfluxDB input to validate.

    Raises:
        ValueError: If any of the required fields are not present in the InfluxDB input.
    """
    _validate_influxdb_input(influxdb, "url")
    _validate_influxdb_input(influxdb, "org")
    _validate_influxdb_input(influxdb, "bucket")
