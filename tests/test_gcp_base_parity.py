"""Parity: `crc.gcp._base.get_gcp_regions` against main (`tests/legacy/gcp_base.txt`).

Both run against the same fake `compute_v1.RegionsClient`; SDK construction and calls (in order,
with arguments), return values, log records, stdout and raised errors must be identical.
"""

import logging
import types
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from google.api_core.exceptions import PermissionDenied, ServiceUnavailable, Unauthenticated
from google.auth.exceptions import DefaultCredentialsError

from crc.gcp import _base

ORACLE = Path(__file__).parent / "legacy" / "gcp_base.txt"
ENV_NAMES = ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "CLOUDSDK_CORE_PROJECT")


class IterError:
    """A listing that yields `items` and then raises `error` while being iterated."""

    def __init__(self, items: Sequence[Any], error: Exception) -> None:
        self.items = list(items)
        self.error = error

    def __iter__(self) -> Iterator[Any]:
        yield from self.items
        raise self.error


@dataclass
class Case:
    id: str
    project: Any = "proj-1"
    regions: Sequence[Any] = ("us-east1", "us-west1")
    env: Mapping[str, str] = field(default_factory=dict)
    ctor_error: Exception | None = None
    list_error: Exception | None = None
    listing: Any = None
    listing_factory: Callable[[], Any] | None = None


def _make(kind: type[Exception], message: str) -> Exception:
    return kind(message)


def _named(*names: Any) -> list[Any]:
    return [types.SimpleNamespace(name=name) for name in names]


CASES = [
    Case("two-regions"),
    Case("single-region", regions=["europe-west4"]),
    Case("unsorted-regions", regions=["us-west1", "asia-south1", "europe-west4"]),
    Case("empty-listing", regions=[]),
    Case("env-unset"),
    Case("env-credentials-set", env={"GOOGLE_APPLICATION_CREDENTIALS": "/tmp/none.json"}),
    Case("env-project-set", env={"GOOGLE_CLOUD_PROJECT": "other", "CLOUDSDK_CORE_PROJECT": "x"}),
    Case("empty-project-id", project=""),
    Case("none-project-id", project=None),
    Case("duplicate-regions", regions=["a", "a", "b"]),
    Case("non-string-names", regions=[None, 3, ""]),
    Case("listing-is-generator", listing_factory=lambda: (r for r in _named("g1", "g2"))),
    Case("listing-is-tuple", listing=tuple(_named("t1"))),
    Case("object-without-name", listing=[types.SimpleNamespace(), types.SimpleNamespace(name="a")]),
    Case("listing-not-iterable", listing=7),
    Case("listing-none-object", listing=object()),
    Case("ctor-credentials-error", ctor_error=_make(DefaultCredentialsError, "no credentials")),
    Case("ctor-generic-error", ctor_error=RuntimeError("boom")),
    Case("list-permission-denied", list_error=_make(PermissionDenied, "denied")),
    Case("list-unauthenticated", list_error=_make(Unauthenticated, "no auth")),
    Case("list-unreachable-no-fallback", list_error=_make(ServiceUnavailable, "unreachable")),
    Case("list-connection-error", list_error=ConnectionError("down")),
    Case(
        "iteration-error-after-first-region",
        listing=IterError(_named("us-east1"), _make(PermissionDenied, "denied mid-iteration")),
    ),
]


class FakeRegionsClient:
    def __init__(self, calls: list[tuple[Any, ...]], case: Case) -> None:
        self.calls = calls
        self.case = case

    def list(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("list", args, tuple(sorted(kwargs.items()))))
        if self.case.list_error is not None:
            raise self.case.list_error
        if self.case.listing_factory is not None:
            return self.case.listing_factory()
        if self.case.listing is not None:
            return self.case.listing
        return _named(*self.case.regions)


class FakeComputeV1:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []

    def RegionsClient(self, *args: Any, **kwargs: Any) -> FakeRegionsClient:
        self.calls.append(("RegionsClient", args, tuple(sorted(kwargs.items()))))
        if self.case.ctor_error is not None:
            raise self.case.ctor_error
        return FakeRegionsClient(self.calls, self.case)


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    result: Any
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_gcp_base")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    return module


def _run(
    case: Case,
    which: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    for name, value in case.env.items():
        monkeypatch.setenv(name, value)
    sdk = FakeComputeV1(case)
    module: Any = _load_oracle() if which == "legacy" else _base
    monkeypatch.setattr(module, "compute_v1", sdk)
    caplog.clear()
    capsys.readouterr()
    result: Any = None
    error: tuple[str, str] | None = None
    with caplog.at_level(logging.DEBUG):
        try:
            result = module.get_gcp_regions(case.project)
        except Exception as exc:
            error = (type(exc).__name__, str(exc))
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(sdk.calls, result, logs, capsys.readouterr().out, error)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_matches_main(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(case, "legacy", monkeypatch, caplog, capsys)
    new = _run(case, "new", monkeypatch, caplog, capsys)
    assert new == old


def _find(case_id: str) -> Case:
    return next(case for case in CASES if case.id == case_id)


@pytest.mark.parametrize(
    ("case_id", "check"),
    [
        (
            "list-unreachable-no-fallback",
            lambda o: o.error == ("ServiceUnavailable", "503 unreachable"),
        ),
        ("list-permission-denied", lambda o: o.error is not None and o.result is None),
        ("empty-listing", lambda o: o.error is None and o.result == []),
        ("duplicate-regions", lambda o: o.result == ["a", "a", "b"]),
        ("ctor-credentials-error", lambda o: o.error is not None and len(o.calls) == 1),
        ("iteration-error-after-first-region", lambda o: o.error is not None and o.result is None),
    ],
    ids=lambda v: v if isinstance(v, str) else "check",
)
def test_quirk_is_preserved(
    case_id: str,
    check: Any,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find(case_id)
    old = _run(case, "legacy", monkeypatch, caplog, capsys)
    new = _run(case, "new", monkeypatch, caplog, capsys)
    assert check(old)
    assert check(new)


def test_oracle_is_exact_copy_of_main_source_shape() -> None:
    text = ORACLE.read_text()
    assert "def get_gcp_regions(project_id):" in text
    assert "project_id = project_id" in text
