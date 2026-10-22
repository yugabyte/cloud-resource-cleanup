"""Parity: `crc.aws._base.get_all_regions` against main (`tests/legacy/aws_base.txt`).

Both run against the same fake `boto3.client` and `boto3.session.Session`; SDK calls (in
order, with arguments), return values, log records, stdout and raised errors must be
identical for every case. The oracle imports `crc.aws.connectivity`, which is replaced by the
frozen copy of main (`tests/legacy/aws_connectivity.txt`) while the oracle runs.
"""

import logging
import sys
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boto3
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

from crc.aws import _base as new_base

LEGACY = Path(__file__).parent / "legacy"
ORACLE = LEGACY / "aws_base.txt"
ORACLE_CONNECTIVITY = LEGACY / "aws_connectivity.txt"

Outcome = dict[str, Any]


def _load(path: Path, name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


def conn_errors() -> dict[str, Callable[[], Exception]]:
    return {
        "endpoint": lambda: EndpointConnectionError(endpoint_url="https://ec2.example"),
        "connect-timeout": lambda: ConnectTimeoutError(endpoint_url="https://ec2.example"),
        "read-timeout": lambda: ReadTimeoutError(endpoint_url="https://ec2.example"),
        "closed": lambda: ConnectionClosedError(endpoint_url="https://ec2.example"),
        "proxy": lambda: ProxyConnectionError(proxy_url="http://proxy", error="refused"),
        "ssl": lambda: SSLError(endpoint_url="https://ec2.example", error="bad cert"),
    }


def auth_error() -> Exception:
    error: Exception = ClientError(
        {"Error": {"Code": "AuthFailure", "Message": "denied"}}, "DescribeRegions"
    )
    return error


@dataclass
class Case:
    id: str
    service: str = "ec2"
    default_region: str = "us-west-2"
    regions: Any = field(default_factory=lambda: [{"RegionName": "us-west-2"}])
    response: Any = None
    client_error: Callable[[], BaseException] | None = None
    describe_error: Callable[[], BaseException] | None = None
    session_error: Callable[[], BaseException] | None = None
    static_error: Callable[[], BaseException] | None = None
    static: Any = field(default_factory=lambda: ["us-east-1", "eu-west-1"])
    env: dict[str, str] = field(default_factory=dict)
    unset_env: tuple[str, ...] = ()


CASES: list[Case] = [
    Case("ec2-one-region"),
    Case(
        "ec2-many-regions",
        regions=[{"RegionName": "us-west-2"}, {"RegionName": "eu-west-1"}, {"RegionName": "ap-1"}],
    ),
    Case("s3-service", service="s3", default_region="us-east-1"),
    Case("empty-list", regions=[]),
    Case("extra-keys-ignored", regions=[{"RegionName": "a", "Endpoint": "x", "OptInStatus": "o"}]),
    Case("duplicate-regions", regions=[{"RegionName": "a"}, {"RegionName": "a"}]),
    Case("response-without-regions", response={"Other": []}),
    Case("region-without-name", regions=[{"Endpoint": "x"}]),
    Case("regions-none", regions=None),
    Case("regions-not-dicts", regions=["us-west-2"]),
    Case("response-none", response=None, regions=None),
    Case("auth-error-propagates", describe_error=auth_error),
    Case("no-credentials-propagates", describe_error=lambda: NoCredentialsError()),
    Case("value-error-propagates", describe_error=lambda: ValueError("boom")),
    Case(
        "no-describe-regions-method",
        service="s3",
        describe_error=lambda: AttributeError("'S3' object has no attribute 'describe_regions'"),
    ),
    Case("keyboard-interrupt-propagates", describe_error=lambda: KeyboardInterrupt()),
    Case("auth-error-in-client-creation", client_error=auth_error),
    Case("static-empty", static=[], describe_error=conn_errors()["endpoint"]),
    Case(
        "static-session-error-propagates",
        describe_error=conn_errors()["endpoint"],
        session_error=lambda: RuntimeError("no session"),
    ),
    Case(
        "static-lookup-error-propagates",
        describe_error=conn_errors()["endpoint"],
        static_error=lambda: KeyError("ec2"),
    ),
    Case("static-default-region", default_region="", describe_error=conn_errors()["endpoint"]),
    Case(
        "env-set",
        env={
            "AWS_DEFAULT_REGION": "ap-south-1",
            "AWS_ACCESS_KEY_ID": "k",
            "AWS_SECRET_ACCESS_KEY": "s",
        },
    ),
    Case("env-unset", unset_env=("AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID", "AWS_PROFILE")),
    Case(
        "env-set-with-fallback",
        env={"AWS_DEFAULT_REGION": "ap-south-1"},
        describe_error=conn_errors()["closed"],
    ),
    Case("static-is-tuple-like", static=("a", "b"), describe_error=conn_errors()["endpoint"]),
    Case(
        "fallback-govcloud-default-region",
        default_region="us-gov-west-1",
        describe_error=conn_errors()["endpoint"],
    ),
    Case(
        "fallback-china-default-region",
        default_region="cn-north-1",
        describe_error=conn_errors()["read-timeout"],
    ),
]
for _name, _factory in conn_errors().items():
    CASES.append(Case(f"fallback-describe-{_name}", describe_error=_factory))
    CASES.append(Case(f"fallback-client-creation-{_name}", client_error=_factory))
    CASES.append(
        Case(
            f"fallback-s3-{_name}",
            service="s3",
            default_region="us-east-1",
            describe_error=_factory,
        )
    )


class FakeClient:
    def __init__(self, case: Case, calls: list[tuple[Any, ...]]) -> None:
        self.case = case
        self.calls = calls

    def describe_regions(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("describe_regions", args, tuple(sorted(kwargs.items()))))
        if self.case.describe_error is not None:
            raise self.case.describe_error()
        if self.case.response is not None:
            return self.case.response
        if self.case.regions is None:
            return {}
        return {"Regions": self.case.regions}


class FakeSession:
    def __init__(self, case: Case, calls: list[tuple[Any, ...]]) -> None:
        self.case = case
        self.calls = calls

    def get_available_regions(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("get_available_regions", args, tuple(sorted(kwargs.items()))))
        if self.case.static_error is not None:
            raise self.case.static_error()
        return self.case.static


def _run(
    module: types.ModuleType,
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    calls: list[tuple[Any, ...]] = []

    def client(*args: Any, **kwargs: Any) -> FakeClient:
        calls.append(("client", args, tuple(sorted(kwargs.items()))))
        if case.client_error is not None:
            raise case.client_error()
        return FakeClient(case, calls)

    def session() -> FakeSession:
        calls.append(("Session",))
        if case.session_error is not None:
            raise case.session_error()
        return FakeSession(case, calls)

    monkeypatch.setattr(boto3, "client", client)
    monkeypatch.setattr(boto3.session, "Session", session)
    for key, value in case.env.items():
        monkeypatch.setenv(key, value)
    for key in case.unset_env:
        monkeypatch.delenv(key, raising=False)
    caplog.clear()
    result: Any = None
    error: tuple[str, str] | None = None
    with caplog.at_level(logging.DEBUG):
        try:
            result = module.get_all_regions(case.service, case.default_region)
        except BaseException as exc:
            error = (type(exc).__name__, str(exc))
    logs = [(r.levelno, r.msg, r.args, r.getMessage()) for r in caplog.records]
    captured = capsys.readouterr()
    return {
        "calls": calls,
        "result": result,
        "result_type": type(result).__name__,
        "logs": logs,
        "stdout": captured.out,
        "stderr": captured.err,
        "error": error,
    }


def _legacy_module(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    connectivity = _load(ORACLE_CONNECTIVITY, "legacy_aws_connectivity")
    monkeypatch.setitem(sys.modules, "crc.aws.connectivity", connectivity)
    return _load(ORACLE, "legacy_aws_base")


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_get_all_regions_matches_main(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy_module(monkeypatch), case, monkeypatch, caplog, capsys)
    new = _run(new_base, case, monkeypatch, caplog, capsys)
    assert new == old


def test_cases_cover_both_paths_and_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    legacy = _legacy_module(monkeypatch)
    outcomes = {c.id: _run(legacy, c, monkeypatch, caplog, capsys) for c in CASES}
    errors = {name for name, out in outcomes.items() if out["error"] is not None}
    fallbacks = {name for name, out in outcomes.items() if ("Session",) in out["calls"]}
    assert "auth-error-propagates" in errors
    assert "no-credentials-propagates" in errors
    assert "no-describe-regions-method" in errors
    assert "fallback-describe-endpoint" in fallbacks
    assert "fallback-describe-ssl" in fallbacks
    assert "fallback-client-creation-read-timeout" in fallbacks
    assert "auth-error-propagates" not in fallbacks
    assert len(fallbacks) >= len(conn_errors()) * 3
    assert outcomes["ec2-many-regions"]["result"] == ["us-west-2", "eu-west-1", "ap-1"]
