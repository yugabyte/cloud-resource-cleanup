# Copyright (c) Yugabyte, Inc.

import logging
import os

LOG_FORMATTER = (
    "%(asctime)s %(filename)s:%(lineno)d %(levelname)s %(threadName)s %(message)s"
)

def create_directory(filename: str):
    os.mkdir(os.path.dirname(os.path.abspath(filename)))


def init_logging(filename: str, log_level: str = "debug"):
    """
    Initialize logging with specified filename and log level

    :param filename: name of the log file
    :type filename: str
    :param log_level: log level of the log messages, default is "debug"
    :type log_level: str
    """
    logger = logging.getLogger()
    print(f"Setting loglevel to logging: {log_level.upper()}")
    logger.setLevel(getattr(logging, log_level.upper()))

    # Create a log message formatter.
    formatter = logging.Formatter(LOG_FORMATTER)

    # Create log directory in script path if not found
    if not create_directory(filename):
        create_directory(filename)

    # Create file handler which logs debug messages to a log file.
    fh = logging.FileHandler(filename, mode="w")
    fh.setLevel(logging.INFO)
    if log_level.upper() == "DEBUG":
        fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    # Create console handler to log INFO messages.
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)

    # Add handlers to the logger
    logger.addHandler(fh)
    logger.addHandler(ch)
    logging.info(
        f"Logging initialized with file: {filename} and level: {log_level.upper()}"
    )
