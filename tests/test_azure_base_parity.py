"""Parity: `crc.azu._base.Base` against main (`tests/legacy/azu_base.txt`).

Both modules get the same fake SDK classes (credential, compute and network client) and the
same environment. The SDK constructions (order, positional and keyword arguments), the
environment reads (which missing variable raises first), the returned objects (singleton reuse,
a falsy client is rebuilt), the attributes left behind and any raised error must be identical.
The module lists no regions and logs nothing, so there is no region fallback or log to compare.
"""

import logging
import types
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from crc.azu import _base

ORACLE = Path(__file__).parent / "legacy" / "azu_base.txt"
ENV_NAMES = (
    "AZURE_CREDENTIALS_SUBSCRIPTION_ID",
    "AZURE_CREDENTIALS_TENANT_ID",
    "AZURE_CREDENTIALS_CLIENT_ID",
    "AZURE_CREDENTIALS_CLIENT_SECRET",
    "AZURE_RESOURCE_GROUP",
)
FULL_ENV = {
    "AZURE_CREDENTIALS_SUBSCRIPTION_ID": "sub-1",
    "AZURE_CREDENTIALS_TENANT_ID": "tenant-1",
    "AZURE_CREDENTIALS_CLIENT_ID": "client-1",
    "AZURE_CREDENTIALS_CLIENT_SECRET": "secret-1",
    "AZURE_RESOURCE_GROUP": "env-rg",
}


def _without(*names: str) -> dict[str, str]:
    return {k: v for k, v in FULL_ENV.items() if k not in names}


@dataclass
class Case:
    id: str
    env: dict[str, str] = field(default_factory=lambda: dict(FULL_ENV))
    resource_group: Any = "arg-rg"
    actions: Sequence[str] = ()
    falsy_clients: bool = False
    credential_error: Exception | None = None
    compute_errors: int = 0
    network_errors: int = 0


CASES = [
    Case("construct-only"),
    Case("construct-resource-group-from-argument"),
    Case("construct-resource-group-empty-uses-env", resource_group=""),
    Case("construct-resource-group-none-uses-env", resource_group=None),
    Case("construct-resource-group-argument-wins-env-unset", _without("AZURE_RESOURCE_GROUP")),
    Case(
        "construct-resource-group-empty-and-env-unset",
        _without("AZURE_RESOURCE_GROUP"),
        resource_group="",
    ),
    Case("construct-env-values-empty", dict.fromkeys(FULL_ENV, ""), resource_group=""),
    Case("missing-subscription", _without("AZURE_CREDENTIALS_SUBSCRIPTION_ID")),
    Case("missing-tenant", _without("AZURE_CREDENTIALS_TENANT_ID")),
    Case("missing-client-id", _without("AZURE_CREDENTIALS_CLIENT_ID")),
    Case("missing-secret", _without("AZURE_CREDENTIALS_CLIENT_SECRET")),
    Case("missing-all", {}),
    Case(
        "missing-subscription-and-secret",
        _without("AZURE_CREDENTIALS_SUBSCRIPTION_ID", "AZURE_CREDENTIALS_CLIENT_SECRET"),
    ),
    Case(
        "missing-tenant-and-client-id",
        _without("AZURE_CREDENTIALS_TENANT_ID", "AZURE_CREDENTIALS_CLIENT_ID"),
    ),
    Case(
        "missing-client-id-and-secret",
        _without("AZURE_CREDENTIALS_CLIENT_ID", "AZURE_CREDENTIALS_CLIENT_SECRET"),
    ),
    Case(
        "missing-secret-and-resource-group",
        _without("AZURE_CREDENTIALS_CLIENT_SECRET", "AZURE_RESOURCE_GROUP"),
        resource_group="",
    ),
    Case("credential-error", credential_error=RuntimeError("bad credential")),
    Case("compute-once", actions=["compute"]),
    Case("compute-twice-is-singleton", actions=["compute", "compute"]),
    Case("network-once", actions=["network"]),
    Case("network-twice-is-singleton", actions=["network", "network"]),
    Case("both-interleaved", actions=["compute", "network", "compute", "network"]),
    Case("network-then-compute", actions=["network", "compute"]),
    Case("falsy-compute-rebuilt-every-call", actions=["compute", "compute"], falsy_clients=True),
    Case("falsy-network-rebuilt-every-call", actions=["network", "network"], falsy_clients=True),
    Case("compute-error-then-retry", actions=["compute", "compute"], compute_errors=1),
    Case("network-error-then-retry", actions=["network", "network"], network_errors=1),
    Case("compute-error-twice", actions=["compute", "compute", "compute"], compute_errors=2),
    Case("network-error-keeps-compute", actions=["compute", "network"], network_errors=1),
]


class FakeClient:
    serial = 0

    def __init__(self, kind: str, truthy: bool) -> None:
        FakeClient.serial += 1
        self.kind = kind
        self.number = FakeClient.serial
        self.truthy = truthy

    def __bool__(self) -> bool:
        return self.truthy


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    results: list[tuple[Any, ...]]
    state: dict[str, Any]
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_azu_base")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    return module


def _install(
    module: Any, case: Case, calls: list[tuple[Any, ...]], pending: dict[str, int]
) -> None:
    def credential(*args: Any, **kwargs: Any) -> str:
        calls.append(("credential", args, kwargs))
        if case.credential_error is not None:
            raise case.credential_error
        return "credential"

    def make(kind: str) -> Callable[..., FakeClient]:
        def build(*args: Any, **kwargs: Any) -> FakeClient:
            calls.append((kind, args, kwargs))
            if pending[kind] > 0:
                pending[kind] -= 1
                raise RuntimeError(f"{kind} failed")
            return FakeClient(kind, not case.falsy_clients)

        return build

    module.ClientSecretCredential = credential
    module.ComputeManagementClient = make("compute")
    module.NetworkManagementClient = make("network")


def _state(base: Any) -> dict[str, Any]:
    return {
        "subscription_id": base.subscription_id,
        "tenant_id": base.tenant_id,
        "client_id": base.client_id,
        "secret": base.secret,
        "resource_group": base.resource_group,
        "credential": base.credential,
        "compute_client": _describe(base.compute_client),
        "network_client": _describe(base.network_client),
    }


def _describe(client: Any) -> Any:
    return client if isinstance(client, dict) else (client.kind, client.number)


def _records(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [(r.name, r.levelno, r.getMessage()) for r in caplog.records]


def _run(
    module: Any,
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in case.env.items():
        monkeypatch.setenv(name, value)
    FakeClient.serial = 0
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    capsys.readouterr()
    calls: list[tuple[Any, ...]] = []
    _install(module, case, calls, {"compute": case.compute_errors, "network": case.network_errors})
    results: list[tuple[Any, ...]] = []
    try:
        base = module.Base(case.resource_group)
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        return Outcome(
            calls,
            results,
            {},
            _records(caplog),
            capsys.readouterr().out,
            (type(e).__name__, str(e)),
        )
    error: tuple[str, str] | None = None
    for action in case.actions:
        try:
            client = getattr(base, f"get_{action}_client")()
            results.append((action, client.kind, client.number))
        except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
            results.append((action, "error", str(e)))
            error = (type(e).__name__, str(e))
    return Outcome(calls, results, _state(base), _records(caplog), capsys.readouterr().out, error)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_matches_main(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_load_oracle(), case, monkeypatch, caplog, capsys)
    new = _run(_base, case, monkeypatch, caplog, capsys)
    assert new.calls == old.calls
    assert new.results == old.results
    assert new.state == old.state
    assert new.logs == old.logs == []
    assert new.stdout == old.stdout == ""
    assert new.error == old.error


def _find(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    (
        "missing-subscription",
        lambda o: o.error == ("KeyError", "'AZURE_CREDENTIALS_SUBSCRIPTION_ID'"),
    ),
    ("missing-all", lambda o: o.error == ("KeyError", "'AZURE_CREDENTIALS_SUBSCRIPTION_ID'")),
    (
        "missing-subscription-and-secret",
        lambda o: o.error == ("KeyError", "'AZURE_CREDENTIALS_SUBSCRIPTION_ID'"),
    ),
    (
        "missing-tenant-and-client-id",
        lambda o: o.error == ("KeyError", "'AZURE_CREDENTIALS_TENANT_ID'"),
    ),
    (
        "missing-client-id-and-secret",
        lambda o: o.error == ("KeyError", "'AZURE_CREDENTIALS_CLIENT_ID'"),
    ),
    (
        "missing-secret-and-resource-group",
        lambda o: o.error == ("KeyError", "'AZURE_CREDENTIALS_CLIENT_SECRET'"),
    ),
    ("missing-secret", lambda o: o.error == ("KeyError", "'AZURE_CREDENTIALS_CLIENT_SECRET'")),
    (
        "construct-resource-group-empty-and-env-unset",
        lambda o: o.error == ("KeyError", "'AZURE_RESOURCE_GROUP'") and o.calls == [],
    ),
    (
        "construct-resource-group-argument-wins-env-unset",
        lambda o: o.state["resource_group"] == "arg-rg",
    ),
    ("construct-resource-group-none-uses-env", lambda o: o.state["resource_group"] == "env-rg"),
    (
        "construct-only",
        lambda o: (
            o.calls
            == [
                (
                    "credential",
                    (),
                    {"tenant_id": "tenant-1", "client_id": "client-1", "client_secret": "secret-1"},
                )
            ]
            and o.state["compute_client"] == {}
            and o.state["network_client"] == {}
        ),
    ),
    (
        "both-interleaved",
        lambda o: (
            [c[0] for c in o.calls] == ["credential", "compute", "network"]
            and o.calls[1][1:] == ((), {"credential": "credential", "subscription_id": "sub-1"})
            and o.calls[2][1:] == (("credential", "sub-1"), {})
        ),
    ),
    (
        "falsy-compute-rebuilt-every-call",
        lambda o: [c[0] for c in o.calls] == ["credential", "compute", "compute"],
    ),
    (
        "compute-error-then-retry",
        lambda o: (
            o.results == [("compute", "error", "compute failed"), ("compute", "compute", 1)]
            and o.error == ("RuntimeError", "compute failed")
        ),
    ),
    (
        "network-error-keeps-compute",
        lambda o: o.state["compute_client"] == ("compute", 1) and o.state["network_client"] == {},
    ),
    (
        "credential-error",
        lambda o: o.error == ("RuntimeError", "bad credential") and len(o.calls) == 1,
    ),
]


@pytest.mark.parametrize(("case_id", "check"), QUIRKS, ids=[q[0] for q in QUIRKS])
def test_quirk_is_preserved(
    case_id: str,
    check: Callable[[Outcome], bool],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert check(_run(_base, _find(case_id), monkeypatch, caplog, capsys))
    assert check(_run(_load_oracle(), _find(case_id), monkeypatch, caplog, capsys))
