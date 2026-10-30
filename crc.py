# Copyright (c) Yugabyte, Inc.

# Entry point of the CRC command line: `python crc.py <flags>`. main() lives in crc/cli. The
# parser and the validators are re-exported so that code that loads this file and looks them up
# here (the tests) keeps working.

from crc.cli.args import get_argparser
from crc.cli.main import main
from crc.cli.validate import (
    _validate_influxdb_input,
    are_values_of_dict_lists,
    is_valid_dict,
    is_valid_list,
    is_valid_type,
    validate_influxdb_inputs,
)

__all__ = [
    "_validate_influxdb_input",
    "are_values_of_dict_lists",
    "get_argparser",
    "is_valid_dict",
    "is_valid_list",
    "is_valid_type",
    "main",
    "validate_influxdb_inputs",
]


if __name__ == "__main__":
    main()
