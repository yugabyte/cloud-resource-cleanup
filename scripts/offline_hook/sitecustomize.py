"""Offline smoke hook: every SDK client becomes an in-memory fake and sockets are denied.

Python imports `sitecustomize` at interpreter start when its directory is on `PYTHONPATH`.
Only `scripts/smoke_offline.py` puts this directory there and sets `CRC_OFFLINE_SMOKE=1`;
nothing in `crc/` imports or mentions it, and no Jenkins flag or variable can reach it. Without
both, this module does nothing.
"""

import os
import socket
from typing import Any

ENABLED = os.environ.get("CRC_OFFLINE_SMOKE") == "1"


class Fake:
    """Accepts any call or attribute and returns an empty, falsy, iterable result."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __getattr__(self, name: str) -> "Fake":
        if name.startswith("__"):
            raise AttributeError(name)
        return Fake()

    def __call__(self, *args: Any, **kwargs: Any) -> "Fake":
        return Fake()

    def __iter__(self) -> Any:
        return iter(())

    def __getitem__(self, key: Any) -> "Fake":
        return Fake()

    def __contains__(self, key: object) -> bool:
        return False

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False

    def __enter__(self) -> "Fake":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get(self, key: Any, default: Any = None) -> Any:
        return default


def _deny(*args: Any, **kwargs: Any) -> Any:
    raise OSError("network access is denied by the offline smoke hook")


def _deny_sockets() -> None:
    socket.socket.connect = _deny  # type: ignore[method-assign]
    socket.socket.connect_ex = _deny  # type: ignore[method-assign]
    socket.create_connection = _deny
    socket.getaddrinfo = _deny


def _fake_clients(module: Any) -> None:
    for name in dir(module):
        if name.endswith("Client") and isinstance(getattr(module, name), type):
            setattr(module, name, Fake)


def _install() -> None:
    import boto3
    import influxdb_client.client.influxdb_client as influx
    import oci
    import slack_sdk
    from azure import identity
    from azure.mgmt import compute, network
    from google.cloud import compute_v1
    from googleapiclient import discovery

    boto3.client = Fake
    boto3.resource = Fake
    influx.InfluxDBClient = Fake  # type: ignore[misc,assignment]
    slack_sdk.WebClient = Fake  # type: ignore[misc,assignment]
    identity.ClientSecretCredential = Fake  # type: ignore[misc,assignment]
    compute.ComputeManagementClient = Fake  # type: ignore[misc,assignment]
    network.NetworkManagementClient = Fake  # type: ignore[misc,assignment]
    discovery.build = Fake
    _fake_clients(compute_v1)
    _fake_clients(oci.core)
    _fake_clients(oci.identity)
    oci.config.validate_config = Fake
    _deny_sockets()


if ENABLED:
    _install()
