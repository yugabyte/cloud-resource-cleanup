"""Unit tests of `crc.aws.connectivity`."""

import logging

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, ReadTimeoutError

from crc.aws.connectivity import CONNECTIVITY_ERRORS, log_skipped_region


def test_network_errors_are_connectivity_errors() -> None:
    assert isinstance(ConnectTimeoutError(endpoint_url="u"), CONNECTIVITY_ERRORS)
    assert isinstance(ReadTimeoutError(endpoint_url="u"), CONNECTIVITY_ERRORS)


def test_auth_error_is_not_a_connectivity_error() -> None:
    error = ClientError({"Error": {"Code": "AuthFailure", "Message": "m"}}, "DescribeRegions")
    assert not isinstance(error, CONNECTIVITY_ERRORS)


def test_log_skipped_region_warns_with_region_context_and_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        log_skipped_region("eu-west-1", "ec2 vm delete", ValueError("down"))
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (
            logging.WARNING,
            "Region eu-west-1: skipped ec2 vm delete — network/connectivity (ValueError): down",
        )
    ]
