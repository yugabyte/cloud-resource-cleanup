"""The legacy side of `tests/test_cli_bridge_gcp.py`: `main`'s CLI (`tests/legacy/crc_main.txt`)
driving `main`'s GCP adapters (`tests/legacy/gcp_*.txt`) on the fake GCP SDK.

The oracle adapters call the SDK directly (`googleapiclient.discovery.build` and the
`google.cloud.compute_v1` clients); `patch_sdk` replaces those with fakes that delegate to one
`FakeGcp`, which records the same calls the routed path records through the `Compute*Client`
wrappers.
"""

import types
from typing import Any

import pytest
from google.cloud import compute_v1
from googleapiclient import discovery

from tests.helpers_oracle import load_legacy_crc


class _Call:
    def __init__(self, run: Any) -> None:
        self.run = run

    def execute(self) -> Any:
        return self.run()


class _Operations:
    def __init__(self, client: Any, scope: str) -> None:
        self.client = client
        self.scope = scope

    def get(self, project: str, operation: str, zone: str = "", region: str = "") -> _Call:
        where = zone or region
        return _Call(lambda: self.client.get_operation(project, self.scope, where, operation))


class _Disks:
    def __init__(self, client: Any, region: bool) -> None:
        self.client = client
        self.region = region

    def _where(self, zone: str, region: str) -> str:
        return region if self.region else zone

    def delete(self, project: str, disk: str, zone: str = "", region: str = "") -> _Call:
        where = self._where(zone, region)
        return _Call(lambda: self.client.delete_disk(project, where, disk))

    def get(self, project: str, disk: str, zone: str = "", region: str = "") -> _Call:
        where = self._where(zone, region)
        scope = "region" if self.region else "zone"
        return _Call(lambda: self.client.get_disk(project, scope, where, disk))


class _Instances:
    def __init__(self, client: Any) -> None:
        self.client = client

    def delete(self, project: str, zone: str, instance: str) -> _Call:
        return _Call(lambda: self.client.delete_instance(project, zone, instance))

    def stop(self, project: str, zone: str, instance: str) -> _Call:
        return _Call(lambda: self.client.stop_instance(project, zone, instance))


class _Zones:
    def __init__(self, client: Any) -> None:
        self.client = client

    def list(self, project: str) -> _Call:
        return _Call(lambda: {"items": self.client.list_zones(project)})


class FakeService:
    """What `discovery.build("compute", "v1")` returns: a context manager closing the client."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def __enter__(self) -> "FakeService":
        return self

    def __exit__(self, *exc: object) -> None:
        self.client.close()

    def zones(self) -> _Zones:
        return _Zones(self.client)

    def disks(self) -> _Disks:
        return _Disks(self.client, region=False)

    def regionDisks(self) -> _Disks:
        return _Disks(self.client, region=True)

    def instances(self) -> _Instances:
        return _Instances(self.client)

    def zoneOperations(self) -> _Operations:
        return _Operations(self.client, "zone")

    def regionOperations(self) -> _Operations:
        return _Operations(self.client, "region")


class _ListClient:
    def __init__(self, client: Any, method: str) -> None:
        self.client = client
        self.method = method

    def list(self, project: str, zone: str = "", region: str = "") -> Any:
        return getattr(self.client, self.method)(project, zone or region)


class _AddressesClient(_ListClient):
    def delete(self, project: str, region: str, address: str) -> Any:
        return self.client.delete_address(project, region, address)


class _RegionsClient:
    def __init__(self, client: Any) -> None:
        self.client = client

    def list(self, project: str) -> list[Any]:
        return [types.SimpleNamespace(name=name) for name in self.client.list_regions(project)]


def patch_sdk(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    """The SDK entry points of the oracle adapters, backed by `client`."""
    monkeypatch.setattr(discovery, "build", lambda *a, **k: FakeService(client))
    monkeypatch.setattr(compute_v1, "DisksClient", lambda: _ListClient(client, "list_disks"))
    monkeypatch.setattr(
        compute_v1, "InstancesClient", lambda: _ListClient(client, "list_instances")
    )
    monkeypatch.setattr(
        compute_v1, "AddressesClient", lambda: _AddressesClient(client, "list_addresses")
    )
    monkeypatch.setattr(compute_v1, "RegionsClient", lambda: _RegionsClient(client))


def run_oracle(kind: str, options: Any, crc_args: tuple[Any, ...]) -> None:
    """One cloud and resource pass on `main`'s `CRC`, as `main` dispatched it for GCP."""
    crc = load_legacy_crc("legacy_crc_main_gcp_bridge").CRC(*crc_args)
    if kind == "disk":
        crc.delete_disks(
            options.filter_tags,
            options.exception_tags,
            options.age,
            options.custom_age_tag_key,
            options.detach_age,
            options.name_regex,
            options.exception_regex,
            options.slack_notify_users,
            options.slack_user_label,
        )
    elif kind == "ip":
        crc.delete_ip(
            options.filter_tags, options.exception_tags, options.name_regex, options.exception_regex
        )
    elif kind == "vm":
        if options.operation_type == "delete":
            crc.delete_vm(
                options.filter_tags,
                options.exception_tags,
                options.age,
                options.custom_age_tag_key,
                options.resource_states,
            )
        elif options.operation_type == "stop":
            crc.stop_vm(
                options.filter_tags, options.exception_tags, options.age, options.custom_age_tag_key
            )
    elif kind == "spot_instances":
        crc.delete_spot_instances(
            options.filter_tags, options.exception_tags, options.age, options.custom_age_tag_key
        )
