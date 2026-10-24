"""A fake OCI SDK for the frozen oracle adapters (`tests/legacy/oci_*.txt`).

The oracle adapters talk to `oci.core.ComputeClient`, `BlockstorageClient` and
`oci.identity.IdentityClient` and page through `oci.pagination.list_call_get_all_results`. The
parity test builds its fake API objects at the port level (`FakeApi` in `tests/test_oci_disk.py`,
`tests/test_oci_vm.py`; `FakeClient` in `tests/test_oci_spot_instances.py` is already SDK-shaped).
`install_sdk` binds the SDK entry points to thin shims over the very same API objects, so the oracle
reads the same data and every call lands in the same `calls` list as the routed path.
"""

import types
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

import oci
import pytest

OCI_KEY_ENV = "OCI_PRIVATE_KEY_CONTENT"


def _data(value: Any) -> Any:
    return types.SimpleNamespace(data=value)


class ComputeShim:
    """The SDK `ComputeClient` of one region over a disk or VM fake API."""

    def __init__(self, api: Any) -> None:
        self.api = api

    def list_instances(self, compartment_id: str) -> Any:
        return _data(self.api.list_instances())

    def terminate_instance(self, instance_id: str) -> None:
        self.api.terminate_instance(instance_id)

    def instance_action(self, instance_id: str, action: str) -> None:
        assert action == "STOP"
        self.api.stop_instance(instance_id)

    def list_volume_attachments(self, compartment_id: str) -> Any:
        return _data(self.api.list_volume_attachments())

    def list_boot_volume_attachments(self, availability_domain: str, compartment_id: str) -> Any:
        return _data(self.api.list_boot_volume_attachments(availability_domain))


class BlockstorageShim:
    """The SDK `BlockstorageClient` of one region over a disk fake API."""

    def __init__(self, api: Any) -> None:
        self.api = api

    def list_volumes(self, compartment_id: str) -> Any:
        return _data(self.api.list_volumes())

    def list_boot_volumes(self, compartment_id: str) -> Any:
        return _data(self.api.list_boot_volumes())

    def delete_volume(self, volume_id: str) -> None:
        self.api.delete_volume(volume_id)

    def delete_boot_volume(self, volume_id: str) -> None:
        self.api.delete_boot_volume(volume_id)


class IdentityShim:
    """The SDK `IdentityClient`: region subscriptions and availability domains."""

    def __init__(self, api: Any, regions: list[str]) -> None:
        self.api = api
        self.regions = regions

    def list_region_subscriptions(self, tenancy_id: str) -> Any:
        return _data([types.SimpleNamespace(region_name=name) for name in self.regions])

    def list_availability_domains(self, compartment_id: str) -> Any:
        return _data(
            [types.SimpleNamespace(name=name) for name in self.api.list_availability_domains()]
        )


def paginate(call: Callable[..., Any], **kwargs: Any) -> Any:
    return call(**kwargs)


class FixedDatetime:
    """Stands in for the `datetime` class the oracle modules import."""

    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self, tz: Any = None) -> datetime:
        return self.current.astimezone(tz)


def install_sdk(
    mp: pytest.MonkeyPatch,
    apis: Mapping[str, Any],
    oracle_classes: tuple[type, ...],
    now: datetime,
) -> None:
    """Route the SDK used by the oracle adapters to `apis` and fix the clock of the oracles."""
    regions = list(apis)

    def compute(config: Mapping[str, Any]) -> Any:
        api = apis[config["region"]]
        return api if _is_sdk_shaped(api) else ComputeShim(api)

    def block(config: Mapping[str, Any]) -> Any:
        api = apis[config["region"]]
        return api if _is_sdk_shaped(api) else BlockstorageShim(api)

    def identity(config: Mapping[str, Any]) -> Any:
        return IdentityShim(apis.get(config["region"]), regions)

    mp.setenv(OCI_KEY_ENV, "not-a-key")
    mp.delenv("OCI_PRIVATE_KEY_FILE", raising=False)
    mp.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    mp.setattr(oci.core, "ComputeClient", compute)
    mp.setattr(oci.core, "BlockstorageClient", block)
    mp.setattr(oci.identity, "IdentityClient", identity)
    mp.setattr(oci.pagination, "list_call_get_all_results", paginate)
    clock = FixedDatetime(now)
    for cls in oracle_classes:
        mp.setitem(cls.__init__.__globals__, "datetime", clock)  # type: ignore[misc]


def _is_sdk_shaped(api: Any) -> bool:
    """The spot fake is one object playing both SDK clients."""
    return hasattr(api, "get_volume")
