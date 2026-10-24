"""Fake Azure compute and network SDK shared by the Azure Spot VM tests.

`FakeAzure.install` replaces the SDK classes `crc.azu._base` builds and sets the environment
`Base` reads, so the real `Base` runs against the fake. Every call is recorded in `calls`.
Errors are scripted per `(operation, key)`: one entry per call, an exception to raise or None.
"""

import datetime
import types
from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

UTC = datetime.UTC
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
RG = "rg-test"
STOPPED = ["Provisioning succeeded", "VM stopped"]
DEALLOCATED = ["Provisioning succeeded", "VM deallocated"]
RUNNING = ["Provisioning succeeded", "VM running"]


def disk_id(name: str, rg: str = RG) -> str:
    return f"/subscriptions/s/resourceGroups/{rg}/providers/Microsoft.Compute/disks/{name}"


def slot(name: str | None, option: str | None = "Detach", rg: str = RG) -> types.SimpleNamespace:
    managed = types.SimpleNamespace(id=disk_id(name, rg)) if name else None
    return types.SimpleNamespace(delete_option=option, managed_disk=managed)


def http_error(status: int | None, cls: type[HttpResponseError] = HttpResponseError) -> Exception:
    error = cls("http error")
    error.status_code = status
    return error


def not_found() -> Exception:
    return http_error(404, ResourceNotFoundError)


def vm(  # noqa: PLR0913
    name: str,
    tags: Any = None,
    *,
    priority: Any = "Spot",
    age: datetime.timedelta | None = datetime.timedelta(days=3),
    os_disk: Any = None,
    data_disks: Sequence[Any] = (),
    profile: bool = True,
) -> types.SimpleNamespace:
    tags = {"yb_task": "itest"} if tags is None else tags
    created = None if age is None else NOW - age
    storage = types.SimpleNamespace(os_disk=os_disk, data_disks=list(data_disks))
    return types.SimpleNamespace(
        name=name,
        tags=tags,
        priority=priority,
        time_created=created,
        storage_profile=storage if profile else None,
    )


class FakePoller:
    def __init__(self, azure: "FakeAzure", op: str, key: str) -> None:
        self.azure, self.op, self.key = azure, op, key

    def result(self, timeout: int | None = None) -> object:
        self.azure.calls.append((f"{self.op}_result", self.key, timeout))
        error = self.azure.next_error(f"{self.op}_result", self.key)
        if error is not None:
            raise error
        return None


class FakeVirtualMachines:
    def __init__(self, azure: "FakeAzure") -> None:
        self.azure = azure

    def list(self, resource_group_name: str, /) -> list[Any]:
        self.azure.calls.append(("list", resource_group_name))
        if self.azure.list_error is not None:
            raise self.azure.list_error
        return list(self.azure.vms)

    def instance_view(self, resource_group_name: str, vm_name: str, /) -> Any:
        self.azure.calls.append(("instance_view", resource_group_name, vm_name))
        error = self.azure.next_error("view", vm_name)
        if error is not None:
            raise error
        statuses = self.azure.power.get(vm_name, STOPPED)
        return types.SimpleNamespace(
            statuses=[types.SimpleNamespace(display_status=s) for s in statuses]
        )

    def begin_delete(self, resource_group_name: str, vm_name: str, /) -> FakePoller:
        self.azure.calls.append(("vm_begin_delete", resource_group_name, vm_name))
        error = self.azure.next_error("vm_begin", vm_name)
        if error is not None:
            raise error
        return FakePoller(self.azure, "vm", vm_name)


class FakeDisks:
    def __init__(self, azure: "FakeAzure") -> None:
        self.azure = azure

    def get(self, resource_group_name: str, disk_name: str, /) -> Any:
        key = f"{resource_group_name}/{disk_name}"
        self.azure.calls.append(("disk_get", resource_group_name, disk_name))
        error = self.azure.next_error("disk_get", key)
        if error is not None:
            raise error
        return types.SimpleNamespace(tags=self.azure.disk_tags.get(key))

    def begin_delete(self, resource_group_name: str, disk_name: str, /) -> FakePoller:
        key = f"{resource_group_name}/{disk_name}"
        self.azure.calls.append(("disk_begin_delete", resource_group_name, disk_name))
        error = self.azure.next_error("disk_begin", key)
        if error is not None:
            raise error
        return FakePoller(self.azure, "disk", key)


class FakeNics:
    def __init__(self, azure: "FakeAzure") -> None:
        self.azure = azure

    def begin_delete(self, resource_group_name: str, nic_name: str, /) -> FakePoller:
        self.azure.calls.append(("nic_begin_delete", resource_group_name, nic_name))
        error = self.azure.next_error("nic_begin", nic_name)
        if error is not None:
            raise error
        return FakePoller(self.azure, "nic", nic_name)


class FakeCompute:
    def __init__(self, azure: "FakeAzure") -> None:
        self.virtual_machines = FakeVirtualMachines(azure)
        self.disks = FakeDisks(azure)


class FakeNetwork:
    def __init__(self, azure: "FakeAzure") -> None:
        self.network_interfaces = FakeNics(azure)


class FakeAzure:
    def __init__(
        self,
        vms: Sequence[Any] = (),
        *,
        list_error: Exception | None = None,
        power: Mapping[str, Sequence[str]] | None = None,
        disk_tags: Mapping[str, Any] | None = None,
        script: Mapping[tuple[str, str], Sequence[Exception | None]] | None = None,
    ) -> None:
        self.vms = vms
        self.list_error = list_error
        self.power = dict(power or {})
        self.disk_tags = dict(disk_tags or {})
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.calls: list[tuple[Any, ...]] = []

    def next_error(self, op: str, key: str) -> Exception | None:
        steps = self.script.get((op, key))
        return steps.pop(0) if steps else None

    def compute(self, credential: Any, subscription_id: str) -> FakeCompute:
        self.calls.append(("client", "compute", subscription_id))
        return FakeCompute(self)

    def network(self, credential: Any, subscription_id: str) -> FakeNetwork:
        self.calls.append(("client", "network", subscription_id))
        return FakeNetwork(self)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_CREDENTIALS_SUBSCRIPTION_ID", "sub-1")
        monkeypatch.setenv("AZURE_CREDENTIALS_TENANT_ID", "tenant-1")
        monkeypatch.setenv("AZURE_CREDENTIALS_CLIENT_ID", "client-1")
        monkeypatch.setenv("AZURE_CREDENTIALS_CLIENT_SECRET", "secret-1")
        monkeypatch.setenv("AZURE_RESOURCE_GROUP", "env-rg")
        monkeypatch.delenv("MAX_AGE", raising=False)
        monkeypatch.setattr("crc.azu._base.ClientSecretCredential", lambda **kwargs: "credential")
        monkeypatch.setattr("crc.azu._base.ComputeManagementClient", self.compute)
        monkeypatch.setattr("crc.azu._base.NetworkManagementClient", self.network)
        monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
        monkeypatch.setattr("crc.azu.vm_delete_helpers.time.sleep", lambda seconds: None)
