"""Unit tests of `crc.aws._base.get_all_regions` with a mocked boto3."""

import logging
from unittest.mock import MagicMock, call

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from crc.aws._base import get_all_regions


def test_returns_region_names_from_describe_regions(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = MagicMock()
    client.describe_regions.return_value = {
        "Regions": [{"RegionName": "us-west-2"}, {"RegionName": "eu-west-1"}]
    }
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(boto3, "client", factory)
    with caplog.at_level(logging.INFO):
        regions = get_all_regions("ec2", "us-west-2")
    assert regions == ["us-west-2", "eu-west-1"]
    assert factory.mock_calls == [call("ec2", region_name="us-west-2")]
    assert client.mock_calls == [call.describe_regions()]
    assert [r.getMessage() for r in caplog.records] == [
        "Retrieved list of regions: ['us-west-2', 'eu-west-1']"
    ]


def test_connectivity_error_falls_back_to_static_list(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = MagicMock()
    client.describe_regions.side_effect = EndpointConnectionError(endpoint_url="https://x")
    session = MagicMock()
    session.get_available_regions.return_value = ["us-east-1"]
    monkeypatch.setattr(boto3, "client", MagicMock(return_value=client))
    monkeypatch.setattr(boto3.session, "Session", MagicMock(return_value=session))
    with caplog.at_level(logging.INFO):
        regions = get_all_regions("ec2", "us-west-2")
    assert regions == ["us-east-1"]
    assert session.mock_calls == [call.get_available_regions("ec2")]
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (
            logging.WARNING,
            "describe_regions failed for ec2 via us-west-2 (EndpointConnectionError); "
            "using boto3 static region list",
        ),
        (logging.INFO, "Retrieved list of regions (static): ['us-east-1']"),
    ]


def test_static_fallback_ignores_partition_of_default_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = MagicMock()
    client.describe_regions.side_effect = EndpointConnectionError(endpoint_url="https://x")
    session = MagicMock()
    session.get_available_regions.return_value = ["us-east-1"]
    monkeypatch.setattr(boto3, "client", MagicMock(return_value=client))
    monkeypatch.setattr(boto3.session, "Session", MagicMock(return_value=session))
    assert get_all_regions("ec2", "us-gov-west-1") == ["us-east-1"]
    assert session.mock_calls == [call.get_available_regions("ec2")]


def test_auth_error_propagates_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    client.describe_regions.side_effect = ClientError(
        {"Error": {"Code": "AuthFailure", "Message": "denied"}}, "DescribeRegions"
    )
    session_factory = MagicMock()
    monkeypatch.setattr(boto3, "client", MagicMock(return_value=client))
    monkeypatch.setattr(boto3.session, "Session", session_factory)
    with pytest.raises(ClientError, match="AuthFailure"):
        get_all_regions("ec2", "us-west-2")
    assert session_factory.mock_calls == []
