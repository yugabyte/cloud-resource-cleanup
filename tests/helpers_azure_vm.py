"""Fake Azure compute and network SDK shared by the Azure VM tests.

`FakeAzure.install` replaces the SDK classes `crc.azu._base` builds, the sleep of the NIC retry
loop and the environment `Base` reads, so the real `Base` and the real delete helpers run against
the fake. `list_all` is lazy and records every item it yields, which shows how listing and
acting interleave. Scripts list, per name, one entry per call: an exception to raise, or None.
"""

import datetime
import types
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import pytest
from azure.core.exceptions import HttpResponseError

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
RUNNING = ("Provisioning succeeded", "VM running")
STOPPED = ("Provisioning succeeded", "VM stopped")
Script = Mapping[str, Sequence[Exception | None]]


_UNSET: Any = object()


def vm(
    name: Any,
    tags: Any = _UNSET,
    hours: float = 100,
    statuses: Any = RUNNING,
    created: Any = _UNSET,
) -> types.SimpleNamespace:
    """A tagged VM (`env=ci` unless `tags` is given) created `hours` before NOW. `statuses` is
    a tuple of display statuses, or an exception that `instance_view` raises."""
    return types.SimpleNamespace(
        name=name,
        tags={"env": "ci"} if tags is _UNSET else tags,
        time_created=NOW - datetime.timedelta(hours=hours) if created is _UNSET else created,
        statuses=statuses,
    )


def not_found(status_code: int | None = 404) -> HttpResponseError:
    error = HttpResponseError("nic gone")
    error.status_code = status_code
    return error


class FakePoller:
    def __init__(self, azure: "FakeAzure", kind: str, name: str, script: Script) -> None:
        self.azure = azure
        self.kind = kind
        self.name = name
        self.script = script

    def result(self, timeout: float | None = None) -> object:
        self.azure.calls.append((self.kind, self.name, timeout))
        error = self.azure.next_error(self.script, self.name)
        if error is not None:
            raise error
        return None


class FakeVirtualMachines:
    def __init__(self, azure: "FakeAzure") -> None:
        self.azure = azure

    def list_all(self) -> Iterator[Any]:
        self.azure.calls.append(("list_all",))
        if self.azure.list_error is not None:
            raise self.azure.list_error
        return self._pages()

    def _pages(self) -> Iterator[Any]:
        for item in self.azure.vms:
            if isinstance(item, Exception):
                raise item
            self.azure.calls.append(("yield", getattr(item, "name", None)))
            yield item

    def instance_view(self, resource_group: str, name: str, /) -> Any:
        self.azure.calls.append(("instance_view", resource_group, name))
        item = next((v for v in self.azure.vms if getattr(v, "name", None) == name), None)
        statuses = getattr(item, "statuses", RUNNING)
        if isinstance(statuses, Exception):
            raise statuses
        return types.SimpleNamespace(
            statuses=[types.SimpleNamespace(display_status=s) for s in statuses]
        )

    def begin_delete(self, resource_group: str, name: str, /) -> FakePoller:
        self.azure.calls.append(("vm_begin_delete", resource_group, name))
        error = self.azure.next_error(self.azure.vm_begin, name)
        if error is not None:
            raise error
        return FakePoller(self.azure, "vm_result", name, self.azure.vm_result)

    def begin_power_off(self, resource_group: str, name: str, /) -> object:
        self.azure.calls.append(("begin_power_off", resource_group, name))
        error = self.azure.next_error(self.azure.off_script, name)
        if error is not None:
            raise error
        return object()


class FakeNetworkInterfaces:
    def __init__(self, azure: "FakeAzure") -> None:
        self.azure = azure

    def begin_delete(self, resource_group: str, name: str, /) -> FakePoller:
        self.azure.calls.append(("nic_begin_delete", resource_group, name))
        error = self.azure.next_error(self.azure.nic_begin, name)
        if error is not None:
            raise error
        return FakePoller(self.azure, "nic_result", name, self.azure.nic_result)


class FakeAzure:
    def __init__(
        self,
        vms: Sequence[Any],
        list_error: Exception | None = None,
        scripts: Mapping[str, Script] | None = None,
    ) -> None:
        """`scripts` maps vm_begin, vm_result, nic_begin, nic_result and off to name scripts."""
        self.vms = vms
        self.list_error = list_error
        given = scripts or {}
        self.vm_begin = _copy(given.get("vm_begin"))
        self.vm_result = _copy(given.get("vm_result"))
        self.nic_begin = _copy(given.get("nic_begin"))
        self.nic_result = _copy(given.get("nic_result"))
        self.off_script = _copy(given.get("off"))
        self.calls: list[tuple[Any, ...]] = []

    @staticmethod
    def next_error(script: Any, name: str) -> Exception | None:
        steps = script.get(name)
        return steps.pop(0) if steps else None

    def compute_client(self, credential: Any, subscription_id: str) -> Any:
        self.calls.append(("compute_client", subscription_id))
        return types.SimpleNamespace(virtual_machines=FakeVirtualMachines(self))

    def network_client(self, credential: Any, subscription_id: str) -> Any:
        self.calls.append(("network_client", subscription_id))
        return types.SimpleNamespace(network_interfaces=FakeNetworkInterfaces(self))

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_CREDENTIALS_SUBSCRIPTION_ID", "sub-1")
        monkeypatch.setenv("AZURE_CREDENTIALS_TENANT_ID", "tenant-1")
        monkeypatch.setenv("AZURE_CREDENTIALS_CLIENT_ID", "client-1")
        monkeypatch.setenv("AZURE_CREDENTIALS_CLIENT_SECRET", "secret-1")
        monkeypatch.setenv("AZURE_RESOURCE_GROUP", "env-rg")
        monkeypatch.setattr("crc.azu._base.ClientSecretCredential", lambda **kwargs: "credential")
        monkeypatch.setattr("crc.azu._base.ComputeManagementClient", self.compute_client)
        monkeypatch.setattr("crc.azu._base.NetworkManagementClient", self.network_client)
        monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            "crc.azu.vm_delete_helpers.time.sleep",
            lambda seconds: self.calls.append(("sleep", seconds)),
        )


def _copy(script: Script | None) -> dict[str, list[Exception | None]]:
    return {k: list(v) for k, v in (script or {}).items()}
