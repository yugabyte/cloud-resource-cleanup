"""Fake Azure compute SDK shared by the Azure disk tests.

`FakeAzure.install` replaces the two SDK classes `crc.azu._base` builds and sets the environment
`Base` reads, so the real `Base` runs against the fake. `disks.list` is lazy and records every
item it yields, which shows how listing and deleting interleave. `begin_script` lists, per disk
name, one entry per `begin_delete` call: an exception to raise, or None to succeed.
"""

import datetime
import types
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

import pytest

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)
NOW_UTC = NOW.astimezone(datetime.UTC)


def ago(*, days: int = 0, hours: int = 0, minutes: int = 0) -> datetime.datetime:
    """A timezone aware creation time, relative to NOW."""
    return NOW_UTC - datetime.timedelta(days=days, hours=hours, minutes=minutes)


DEFAULT_CREATED = object()


def disk(
    name: Any,
    tags: Any = None,
    created: Any = DEFAULT_CREATED,
    state: Any = "Unattached",
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        name=name,
        tags=tags,
        time_created=ago(days=10) if created is DEFAULT_CREATED else created,
        disk_state=state,
    )


class FakeDiskOperations:
    def __init__(self, azure: "FakeAzure") -> None:
        self.azure = azure

    def list(self) -> Iterator[Any]:
        self.azure.calls.append(("list",))
        if self.azure.list_error is not None:
            raise self.azure.list_error
        return self._pages()

    def _pages(self) -> Iterator[Any]:
        for item in self.azure.disks:
            if isinstance(item, Exception):
                raise item
            self.azure.calls.append(("yield", getattr(item, "name", None)))
            yield item

    def begin_delete(self, resource_group_name: str, disk_name: str, /) -> object:
        self.azure.calls.append(("begin_delete", resource_group_name, disk_name))
        steps = self.azure.begin_script.get(disk_name)
        error = steps.pop(0) if steps else None
        if error is not None:
            raise error
        return object()


class FakeComputeClient:
    def __init__(self, azure: "FakeAzure") -> None:
        self.disks = FakeDiskOperations(azure)


class FakeAzure:
    def __init__(
        self,
        disks: Sequence[Any],
        list_error: Exception | None = None,
        begin_script: Mapping[str, Sequence[Exception | None]] | None = None,
    ) -> None:
        self.disks = disks
        self.list_error = list_error
        self.begin_script = {k: list(v) for k, v in (begin_script or {}).items()}
        self.calls: list[tuple[Any, ...]] = []

    def client(self, credential: Any, subscription_id: str) -> FakeComputeClient:
        self.calls.append(("client", subscription_id))
        return FakeComputeClient(self)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AZURE_CREDENTIALS_SUBSCRIPTION_ID", "sub-1")
        monkeypatch.setenv("AZURE_CREDENTIALS_TENANT_ID", "tenant-1")
        monkeypatch.setenv("AZURE_CREDENTIALS_CLIENT_ID", "client-1")
        monkeypatch.setenv("AZURE_CREDENTIALS_CLIENT_SECRET", "secret-1")
        monkeypatch.setenv("AZURE_RESOURCE_GROUP", "env-rg")
        monkeypatch.delenv("MAX_AGE", raising=False)
        monkeypatch.setattr("crc.azu._base.ClientSecretCredential", lambda **kwargs: "credential")
        monkeypatch.setattr("crc.azu._base.ComputeManagementClient", self.client)
        monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
