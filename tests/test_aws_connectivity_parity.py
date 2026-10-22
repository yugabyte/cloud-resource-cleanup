"""Parity: `crc.aws.connectivity` against main (`tests/legacy/aws_connectivity.txt`).

`CONNECTIVITY_ERRORS` must classify every exception the same way, and `log_skipped_region`
must emit the same log records and print the same stdout for every input.
"""

import logging
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    NoCredentialsError,
    ProxyConnectionError,
    ReadTimeoutError,
    SSLError,
)
from botocore.exceptions import (
    ConnectionError as BotoConnectionError,
)

from crc.aws import connectivity as new

ORACLE = Path(__file__).parent / "legacy" / "aws_connectivity.txt"

EXCEPTIONS: dict[str, Callable[[], BaseException]] = {
    "endpoint": lambda: EndpointConnectionError(endpoint_url="https://ec2.example"),
    "connect-timeout": lambda: ConnectTimeoutError(endpoint_url="https://ec2.example"),
    "read-timeout": lambda: ReadTimeoutError(endpoint_url="https://ec2.example"),
    "closed": lambda: ConnectionClosedError(endpoint_url="https://ec2.example"),
    "proxy": lambda: ProxyConnectionError(proxy_url="http://proxy", error="refused"),
    "ssl": lambda: SSLError(endpoint_url="https://ec2.example", error="bad cert"),
    "boto-connection": lambda: BotoConnectionError(error="down"),
    "client-error": lambda: ClientError(
        {"Error": {"Code": "AuthFailure", "Message": "denied"}}, "DescribeRegions"
    ),
    "no-credentials": lambda: NoCredentialsError(),
    "builtin-connection": lambda: ConnectionError("reset"),
    "timeout": lambda: TimeoutError("slow"),
    "os-error": lambda: OSError("io"),
    "value-error": lambda: ValueError("bad"),
    "key-error": lambda: KeyError("Regions"),
    "empty-message": lambda: RuntimeError(),
    "unicode-message": lambda: RuntimeError("région — déconnectée %s %d"),
    "keyboard-interrupt": lambda: KeyboardInterrupt(),
}

INPUTS = [
    ("us-west-2", "ec2 vm delete"),
    ("eu-west-1", ""),
    ("", "ec2 ip"),
    ("ap-%s-1", "ctx %d %s"),
    ("région", "contexte — x"),
]


def _oracle() -> types.ModuleType:
    module = types.ModuleType("legacy_aws_connectivity")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    return module


def _log(
    module: Any,
    region: str,
    context: str,
    exc: BaseException,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> tuple[Any, ...]:
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        result = module.log_skipped_region(region, context, exc)
    records = [(r.name, r.levelno, r.msg, r.args, r.getMessage()) for r in caplog.records]
    return result, records, capsys.readouterr()


def test_connectivity_errors_tuple_is_the_same() -> None:
    old = _oracle()
    assert new.CONNECTIVITY_ERRORS == old.CONNECTIVITY_ERRORS
    assert isinstance(new.CONNECTIVITY_ERRORS, tuple)
    assert len(new.CONNECTIVITY_ERRORS) == len(old.CONNECTIVITY_ERRORS)


@pytest.mark.parametrize("name", list(EXCEPTIONS))
def test_connectivity_errors_classify_like_main(name: str) -> None:
    old = _oracle()
    exc = EXCEPTIONS[name]()
    assert isinstance(exc, new.CONNECTIVITY_ERRORS) == isinstance(exc, old.CONNECTIVITY_ERRORS)


@pytest.mark.parametrize("name", list(EXCEPTIONS))
@pytest.mark.parametrize(("region", "context"), INPUTS)
def test_log_skipped_region_matches_main(
    name: str,
    region: str,
    context: str,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exc = EXCEPTIONS[name]()
    old = _log(_oracle(), region, context, exc, caplog, capsys)
    result = _log(new, region, context, exc, caplog, capsys)
    assert result == old
    assert len(result[1]) == 1
    assert result[1][0][1] == logging.WARNING


def test_connectivity_split_is_not_vacuous() -> None:
    connectivity = {n for n, f in EXCEPTIONS.items() if isinstance(f(), new.CONNECTIVITY_ERRORS)}
    assert {"endpoint", "connect-timeout", "read-timeout", "closed", "proxy", "ssl"} <= connectivity
    assert "client-error" not in connectivity
    assert "no-credentials" not in connectivity
    assert "builtin-connection" not in connectivity
