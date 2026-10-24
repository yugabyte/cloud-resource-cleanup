"""Unit tests for `crc.gcp._base.get_gcp_regions` (offline, SDK mocked)."""

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call

import pytest
from google.api_core.exceptions import PermissionDenied

from crc.gcp import _base


def _denied() -> Exception:
    return PermissionDenied("denied")  # type: ignore[no-untyped-call]


def _sdk(names: list[Any]) -> MagicMock:
    sdk = MagicMock()
    sdk.RegionsClient.return_value.list.return_value = [SimpleNamespace(name=n) for n in names]
    return sdk


def test_returns_region_names_in_listing_order(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = _sdk(["us-east1", "asia-south1", "europe-west4"])
    monkeypatch.setattr(_base, "compute_v1", sdk)
    assert _base.get_gcp_regions("proj-1") == ["us-east1", "asia-south1", "europe-west4"]


def test_client_is_built_once_and_listed_with_project_keyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sdk = _sdk(["r1"])
    monkeypatch.setattr(_base, "compute_v1", sdk)
    _base.get_gcp_regions("proj-9")
    assert sdk.mock_calls == [call.RegionsClient(), call.RegionsClient().list(project="proj-9")]


def test_empty_listing_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_base, "compute_v1", _sdk([]))
    assert _base.get_gcp_regions("proj-1") == []


def test_listing_error_propagates_without_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = _sdk([])
    sdk.RegionsClient.return_value.list.side_effect = _denied()
    monkeypatch.setattr(_base, "compute_v1", sdk)
    with pytest.raises(PermissionDenied, match="denied"):
        _base.get_gcp_regions("proj-1")


def test_does_not_log_or_print(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(_base, "compute_v1", _sdk(["r1"]))
    with caplog.at_level(logging.DEBUG):
        _base.get_gcp_regions("proj-1")
    assert caplog.records == []
    assert capsys.readouterr().out == ""


def test_package_init_declares_no_exports() -> None:
    import crc.gcp

    assert not hasattr(crc.gcp, "__all__")
