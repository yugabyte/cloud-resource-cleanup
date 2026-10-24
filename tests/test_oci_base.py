"""Focused tests of `crc.oci._base.Base` against a fake OCI SDK (no network, no credentials)."""

import logging
import types
from typing import Any

import oci
import pytest
from oci.exceptions import ServiceError

from crc.oci import _base
from crc.oci._base import Base


class Recorder:
    def __init__(self) -> None:
        self.configs: list[tuple[str, dict[str, str]]] = []


@pytest.fixture
def sdk(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()

    def make(kind: str) -> Any:
        def build(config: dict[str, str]) -> Any:
            recorder.configs.append((kind, dict(config)))
            client = types.SimpleNamespace(kind=kind, config=config)
            client.list_region_subscriptions = lambda tenancy: types.SimpleNamespace(
                data=[types.SimpleNamespace(region_name="r1")]
            )
            client.list_availability_domains = lambda compartment_id: types.SimpleNamespace(
                data=[types.SimpleNamespace(name="AD-1")]
            )
            return client

        return build

    monkeypatch.setattr(oci.core, "ComputeClient", make("compute"))
    monkeypatch.setattr(oci.core, "BlockstorageClient", make("block"))
    monkeypatch.setattr(oci.identity, "IdentityClient", make("identity"))
    monkeypatch.setenv("OCI_PRIVATE_KEY_CONTENT", "pem")
    monkeypatch.delenv("OCI_PRIVATE_KEY_FILE", raising=False)
    return recorder


def test_config_uses_key_content(sdk: Recorder) -> None:
    assert Base().config == {
        "user": _base.USER_OCID,
        "tenancy": _base.TENANCY_OCID,
        "fingerprint": _base.FINGERPRINT,
        "region": _base.HOME_REGION,
        "key_content": "pem",
    }


def test_config_uses_key_file_when_no_content(
    sdk: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OCI_PRIVATE_KEY_CONTENT")
    monkeypatch.setenv("OCI_PRIVATE_KEY_FILE", "/tmp/key.pem")
    config = Base().config
    assert config["key_file"] == "/tmp/key.pem"
    assert "key_content" not in config


def test_missing_key_raises_before_any_sdk_call(
    sdk: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OCI_PRIVATE_KEY_CONTENT")
    with pytest.raises(KeyError, match="OCI_PRIVATE_KEY_CONTENT or OCI_PRIVATE_KEY_FILE"):
        Base()
    assert sdk.configs == []


def test_regional_clients_are_built_once_with_the_region(sdk: Recorder) -> None:
    base = Base()
    assert base.get_compute_client("r2") is base.get_compute_client("r2")
    assert base.get_blockstorage_client("r2") is base.get_blockstorage_client("r2")
    assert base.get_regional_identity_client("r2") is base.get_regional_identity_client("r2")
    assert [kind for kind, _ in sdk.configs] == ["compute", "block", "identity"]
    assert all(config["region"] == "r2" for _, config in sdk.configs)
    assert base.config["region"] == _base.HOME_REGION


def test_availability_domains_are_cached(sdk: Recorder) -> None:
    base = Base()
    assert base.get_availability_domains("r1") == ["AD-1"]
    assert base.get_availability_domains("r1") == ["AD-1"]
    assert [kind for kind, _ in sdk.configs] == ["identity"]


def test_regions_listed_with_log(sdk: Recorder, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    assert Base().get_all_regions() == ["r1"]
    assert [r.getMessage() for r in caplog.records] == ["Retrieved list of OCI regions: ['r1']"]


def test_auth_error_falls_back_to_home_region(
    sdk: Recorder, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def deny(config: dict[str, str]) -> Any:
        def list_region_subscriptions(tenancy: str) -> Any:
            raise ServiceError(401, "NotAuthenticated", {}, "bad key")

        return types.SimpleNamespace(list_region_subscriptions=list_region_subscriptions)

    monkeypatch.setattr(oci.identity, "IdentityClient", deny)
    assert Base().get_all_regions() == [_base.HOME_REGION]
    messages = [r.getMessage() for r in caplog.records]
    assert messages[1] == "Falling back to static region list: ['us-sanjose-1']"
    assert "skipped list_region_subscriptions" in messages[0]


def test_unexpected_error_propagates(sdk: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(config: dict[str, str]) -> Any:
        raise RuntimeError("bad config")

    monkeypatch.setattr(oci.identity, "IdentityClient", broken)
    with pytest.raises(RuntimeError, match="bad config"):
        Base().get_all_regions()
