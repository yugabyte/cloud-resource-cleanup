"""Fake Azure network SDK shared by the Azure IP tests.

`FakeAzure.install` replaces the two SDK classes `crc.azu._base` builds and sets the
environment `Base` reads, so the real `Base` runs against the fake. `list_all` is lazy and
records every item it yields, which shows how listing and deleting interleave.
"""

import datetime
import types
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import pytest

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)


def ip(
    name: str | None, tags: Mapping[str, Any] | None, config: Any = None
) -> types.SimpleNamespace:
    return types.SimpleNamespace(name=name, tags=tags, ip_configuration=config)


class FakeIpOperations:
    def __init__(self, azure: "FakeAzure") -> None:
        self.azure = azure

    def list_all(self) -> Iterator[Any]:
        self.azure.calls.append(("list_all",))
        if self.azure.list_error is not None:
            raise self.azure.list_error
        return self._pages()

    def _pages(self) -> Iterator[Any]:
        for item in self.azure.ips:
            if isinstance(item, Exception):
                raise item
            self.azure.calls.append(("yield", getattr(item, "name", None)))
            yield item

    def begin_delete(self, *, resource_group_name: str, public_ip_address_name: str) -> object:
        self.azure.calls.append(("begin_delete", resource_group_name, public_ip_address_name))
        error = self.azure.delete_errors.get(public_ip_address_name)
        if error is not None:
            raise error
        return types.SimpleNamespace()


class FakeNetworkClient:
    def __init__(self, azure: "FakeAzure") -> None:
        self.public_ip_addresses = FakeIpOperations(azure)


class FakeAzure:
    def __init__(
        self,
        ips: Sequence[Any],
        list_error: Exception | None = None,
        delete_errors: Mapping[str, Exception] | None = None,
    ) -> None:
        self.ips = ips
        self.list_error = list_error
        self.delete_errors = delete_errors or {}
        self.calls: list[tuple[Any, ...]] = []

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
        monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
