"""The suite-wide network deny (tests/conftest.py) fires, and still lets local sockets through."""

import socket
import tempfile
from pathlib import Path

import pytest

from tests.conftest import NetworkDeniedError

TEST_NET_ADDRESS = ("192.0.2.1", 443)


def test_connect_to_a_remote_address_raises() -> None:
    with socket.socket() as sock, pytest.raises(NetworkDeniedError, match="denies network access"):
        sock.connect(TEST_NET_ADDRESS)


def test_connect_ex_to_a_remote_address_raises() -> None:
    with socket.socket() as sock, pytest.raises(NetworkDeniedError):
        sock.connect_ex(TEST_NET_ADDRESS)


def test_create_connection_raises() -> None:
    with pytest.raises(NetworkDeniedError):
        socket.create_connection(TEST_NET_ADDRESS, timeout=1)


def test_udp_send_to_a_remote_address_raises() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock,
        pytest.raises(NetworkDeniedError),
    ):
        sock.sendto(b"x", TEST_NET_ADDRESS)


def test_dns_lookup_of_a_remote_host_raises() -> None:
    with pytest.raises(NetworkDeniedError, match="DNS"):
        socket.getaddrinfo("example.com", 443)


def test_loopback_connection_is_allowed() -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=2) as client:
            peer, _ = server.accept()
            client.sendall(b"ok")
            assert peer.recv(2) == b"ok"
            peer.close()


def test_unix_socket_connection_is_allowed() -> None:
    # A short directory: macOS caps AF_UNIX paths at 104 bytes, and pytest's tmp_path can be longer.
    with tempfile.TemporaryDirectory(dir="/tmp") as short_dir:
        _connect_unix(str(Path(short_dir) / "s.sock"))


def _connect_unix(path: str) -> None:
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(path)
        server.listen(1)
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(path)
            peer, _ = server.accept()
            peer.close()
