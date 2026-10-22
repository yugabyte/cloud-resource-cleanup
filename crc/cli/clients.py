"""Slack and InfluxDB client construction and the MAX_AGE environment variable."""

import os
from typing import Any, cast

from influxdb_client.client.influxdb_client import InfluxDBClient
from slack_sdk import WebClient

from crc.cli.inputs import Options, Tokens
from crc.cli.validate import validate_influxdb_inputs


def build_slack_client(options: Options, tokens: Tokens) -> Any:
    """Return the Slack client when a channel is set, else None."""
    if options.slack_channel:
        return WebClient(token=tokens.slack_bot)
    return None


def build_influxdb_client(options: Options, tokens: Tokens) -> Any:
    """Validate the InfluxDB dict and return the client when it is set, else None."""
    influxdb = options.influxdb
    if influxdb:
        validate_influxdb_inputs(influxdb)
        return InfluxDBClient(
            url=influxdb["url"],
            token=cast("str", tokens.influxdb),  # `check_tokens` already rejected an empty one
            org=influxdb["org"],
        )
    return None


def set_max_age(max_age: Any) -> None:
    """Export `--max_age` as MAX_AGE and say so."""
    if max_age:
        os.environ["MAX_AGE"] = str(max_age)
        print(f"Set MAX_AGE: {os.environ['MAX_AGE']}")


def reset_max_age(max_age: Any) -> None:
    """Remove MAX_AGE again after a normal run."""
    if max_age:
        del os.environ["MAX_AGE"]
        print("MAX_AGE reset.")
