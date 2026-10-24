"""Fake Azure network SDK shared by the Azure NIC tests.

`FakeAzure.install` replaces the two SDK classes `crc.azu._base` builds and sets the
environment `Base` reads, so the real `Base` runs against the fake. `list_all` (the oracle's
subscription-wide listing) and `list(resource_group)` (the fixed listing) are lazy and
record every item it yields, which shows how listing and deleting interleave. A delete
script lists, per NIC name, one entry per attempt: an exception to raise, or None to succeed.
"""

import datetime
import types
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import pytest

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical
DEFAULT_CREATED = NOW.replace(tzinfo=datetime.UTC) - datetime.timedelta(days=10)
ROOT_ID = "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.Network/networkInterfaces"


def nic(name: Any, vm: Any = None) -> types.SimpleNamespace:
    return types.SimpleNamespace(id=f"{ROOT_ID}/{name}", name=name, virtual_machine=vm)


class FakePoller:
    def __init__(self, azure: "FakeAzure", name: str) -> None:
        self.azure = azure
        self.name = name

    def result(self) -> object:
        self.azure.calls.append(("result", self.name))
        error = self.azure.next_error(self.azure.result_script, self.name)
        if error is not None:
            raise error
        return None


class FakeNicOperations:
    def __init__(self, azure: "FakeAzure") -> None:
        self.azure = azure

    def list_all(self) -> Iterator[Any]:
        self.azure.calls.append(("list_all",))
        if self.azure.list_error is not None:
            raise self.azure.list_error
        return self._pages()

    def list(self, resource_group_name: str, /) -> Iterator[Any]:
        self.azure.calls.append(("list", resource_group_name))
        if self.azure.list_error is not None:
            raise self.azure.list_error
        return self._pages()

    def _pages(self) -> Iterator[Any]:
        for item in self.azure.nics:
            if isinstance(item, Exception):
                raise item
            self.azure.calls.append(("yield", getattr(item, "name", None)))
            yield item

    def begin_delete(self, resource_group_name: str, network_interface_name: str, /) -> FakePoller:
        self.azure.calls.append(("begin_delete", resource_group_name, network_interface_name))
        error = self.azure.next_error(self.azure.begin_script, network_interface_name)
        if error is not None:
            raise error
        return FakePoller(self.azure, network_interface_name)


class FakeNetworkClient:
    def __init__(self, azure: "FakeAzure") -> None:
        self.network_interfaces = FakeNicOperations(azure)


class FakeResourceOperations:
    def __init__(self, azure: "FakeAzure") -> None:
        self.azure = azure

    def list_by_resource_group(
        self, resource_group_name: str, *, filter: str, expand: str
    ) -> list[Any]:
        return [
            types.SimpleNamespace(
                id=getattr(item, "id", None),
                created_time=getattr(item, "created_time", DEFAULT_CREATED),
            )
            for item in self.azure.nics
            if not isinstance(item, Exception)
        ]


class FakeResourceClient:
    def __init__(self, azure: "FakeAzure") -> None:
        self.resources = FakeResourceOperations(azure)


class FakeAzure:
    def __init__(
        self,
        nics: Sequence[Any],
        list_error: Exception | None = None,
        begin_script: Mapping[str, Sequence[Exception | None]] | None = None,
        result_script: Mapping[str, Sequence[Exception | None]] | None = None,
    ) -> None:
        for index, item in enumerate(nics):
            if not isinstance(item, Exception) and getattr(item, "id", None) is None:
                item.id = f"{ROOT_ID}/{getattr(item, 'name', None) or index}"
        self.nics = nics
        self.list_error = list_error
        self.begin_script = {k: list(v) for k, v in (begin_script or {}).items()}
        self.result_script = {k: list(v) for k, v in (result_script or {}).items()}
        self.calls: list[tuple[Any, ...]] = []

    @staticmethod
    def next_error(script: dict[str, list[Exception | None]], name: str) -> Exception | None:
        steps = script.get(name)
        return steps.pop(0) if steps else None

    def client(self, credential: Any, subscription_id: str) -> FakeNetworkClient:
        self.calls.append(("client", subscription_id))
        return FakeNetworkClient(self)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_CREDENTIALS_SUBSCRIPTION_ID", "sub-1")
        monkeypatch.setenv("AZURE_CREDENTIALS_TENANT_ID", "tenant-1")
        monkeypatch.setenv("AZURE_CREDENTIALS_CLIENT_ID", "client-1")
        monkeypatch.setenv("AZURE_CREDENTIALS_CLIENT_SECRET", "secret-1")
        monkeypatch.setenv("AZURE_RESOURCE_GROUP", "env-rg")
        monkeypatch.setattr("crc.azu._base.ClientSecretCredential", lambda **kwargs: "credential")
        monkeypatch.setattr("crc.azu._base.NetworkManagementClient", self.client)
        monkeypatch.setattr(
            "crc.azu._base.ResourceManagementClient",
            lambda credential, subscription_id: FakeResourceClient(self),
        )
        monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
