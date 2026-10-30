"""Pytest setup shared by the whole suite.

1. Install the `MAX_AGE` reader that `main()` installs in production. `crc/core` reads no
   environment variable (ARCH-003); `crc.cli.main.main` gives `crc.core.age` a source that reads
   `MAX_AGE`. Tests that call a cleaner or `is_old` without `main` and set the variable with
   `monkeypatch` rely on the same production function, installed here.
2. Deny the network. Every test runs with socket connections to anything but this machine
   (`AF_UNIX`, loopback) and DNS lookups of other hosts raising `NetworkDeniedError`. A test that
   needs a cloud, Slack or InfluxDB answer must fake the SDK client instead.
"""

import socket
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from crc.cli.context import install_max_age_source

install_max_age_source()

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})


class NetworkDeniedError(RuntimeError):
    """A test tried to reach a host over the network."""


def _is_local(sock: socket.socket, address: Any) -> bool:
    if sock.family == socket.AF_UNIX:
        return True
    if isinstance(address, tuple) and address:
        host = address[0]
        return isinstance(host, str) and (host in LOCAL_HOSTS or host.startswith("127."))
    return False


def _guard_address(real: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(self: socket.socket, address: Any, *args: Any) -> Any:
        if not _is_local(self, address):
            raise NetworkDeniedError(f"the test suite denies network access: {address!r}")
        return real(self, address, *args)

    return guarded


def _guard_sendto(real: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(self: socket.socket, data: Any, *args: Any) -> Any:
        address = args[-1] if args else None
        if address is not None and not _is_local(self, address):
            raise NetworkDeniedError(f"the test suite denies network access: {address!r}")
        return real(self, data, *args)

    return guarded


def _guard_getaddrinfo(real: Callable[..., Any]) -> Callable[..., Any]:
    def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is not None and host not in LOCAL_HOSTS and not str(host).startswith("127."):
            raise NetworkDeniedError(f"the test suite denies DNS lookups: {host!r}")
        return real(host, *args, **kwargs)

    return guarded


@pytest.fixture(autouse=True)
def _deny_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(socket.socket, "connect", _guard_address(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", _guard_address(socket.socket.connect_ex))
    monkeypatch.setattr(socket.socket, "sendto", _guard_sendto(socket.socket.sendto))
    monkeypatch.setattr(socket, "getaddrinfo", _guard_getaddrinfo(socket.getaddrinfo))
    yield
