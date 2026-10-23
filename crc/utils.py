# Copyright (c) Yugabyte, Inc.

import logging
import os

from crc.core import logger as run_log

LOG_FORMATTER = "%(asctime)s %(filename)s:%(lineno)d %(levelname)s %(threadName)s %(message)s"

_NOISY_LOGGERS = ("azure", "msrest", "urllib3")


def init_logging(filename: str, log_level: str = "debug") -> None:
    """Initialize logging with the given file name and log level.

    Mutates the root logger: sets its level and adds a file handler (the file is
    truncated) and a console handler. Creates the log directory when missing.

    :param filename: name of the log file
    :param log_level: log level of the log messages, default is "debug"
    """
    logger = logging.getLogger()
    print(f"Setting loglevel to logging: {log_level.upper()}")
    logger.setLevel(getattr(logging, log_level.upper()))

    formatter = logging.Formatter(LOG_FORMATTER)

    os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)

    fh = logging.FileHandler(filename, mode="w")
    fh.setLevel(logging.INFO)
    if log_level.upper() == "DEBUG":
        fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    # Reduce noise from Azure SDK and HTTP libs (request/response dumps)
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    run_log.logging_ready()
    logging.info(f"Logging initialized with file: {filename} and level: {log_level.upper()}")
