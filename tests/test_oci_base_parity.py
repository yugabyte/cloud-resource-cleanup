"""Parity: `crc.oci._base.Base` against main's version (`tests/legacy/oci_base.txt`).

Both run against the same fake OCI SDK (client constructors patched on the `oci` modules), env
and logging capture. Constructor calls and their configs, SDK calls in order, return values,
cache behaviour, log records, stdout and raised errors must be identical for every case.
"""

import logging
import types
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import oci
import pytest
from oci.exceptions import ConnectTimeout, RequestException, ServiceError

import crc.oci._base as new_module

ORACLE = Path(__file__).parent / "legacy" / "oci_base.txt"
HOME = "us-sanjose-1"

Step = tuple[str, ...]


def conn_error() -> Exception:
    error: Exception = ConnectTimeout(Exception("timed out"))
    return error


def request_error() -> Exception:
    error: Exception = RequestException(Exception("no route"))
    return error


def service_error(status: int = 503, code: str = "ServiceUnavailable") -> Exception:
    error: Exception = ServiceError(status, code, {}, "service said no")
    return error


@dataclass
class Case:
    id: str
    steps: Sequence[Step] = ()
    content: str | None = "pem-content"
    key_file: str | None = None
    region_names: Sequence[str] = ("us-sanjose-1", "us-ashburn-1")
    regions_outcomes: Sequence[BaseException | None] = (None,)
    ads: Mapping[str, Sequence[str]] = field(default_factory=dict)
    ads_outcomes: Mapping[str, Sequence[BaseException | None]] = field(default_factory=dict)
    create_outcomes: Mapping[tuple[str, str], Sequence[BaseException | None]] = field(
        default_factory=dict
    )
    falsy_identity: bool = False


REGIONS: Step = ("regions",)
IDENTITY: Step = ("identity",)


def compute(region: str) -> Step:
    return ("compute", region)


def block(region: str) -> Step:
    return ("block", region)


def rid(region: str) -> Step:
    return ("rid", region)


def ads(region: str) -> Step:
    return ("ads", region)


CASES = [
    Case("env-content-only"),
    Case("env-file-only", content=None, key_file="/tmp/key.pem"),
    Case("env-both-content-wins", key_file="/tmp/key.pem"),
    Case("env-neither", content=None),
    Case("env-both-empty", content="", key_file=""),
    Case("env-empty-content-file-set", content="", key_file="/tmp/key.pem"),
    Case("env-content-set-empty-file", key_file=""),
    Case("env-empty-content-no-file", content=""),
    Case("env-no-content-empty-file", content=None, key_file=""),
    Case("construct-only"),
    Case("regions-success", steps=[REGIONS]),
    Case("regions-twice-lists-twice", steps=[REGIONS, REGIONS]),
    Case("regions-empty", steps=[REGIONS], region_names=()),
    Case("regions-one", steps=[REGIONS], region_names=("us-phoenix-1",)),
    Case("regions-connect-timeout-falls-back", steps=[REGIONS], regions_outcomes=[conn_error()]),
    Case("regions-request-error-falls-back", steps=[REGIONS], regions_outcomes=[request_error()]),
    Case(
        "regions-service-error-503-falls-back",
        steps=[REGIONS],
        regions_outcomes=[service_error()],
    ),
    Case(
        "regions-auth-error-401-falls-back",
        steps=[REGIONS],
        regions_outcomes=[service_error(401, "NotAuthenticated")],
    ),
    Case(
        "regions-auth-error-403-falls-back",
        steps=[REGIONS],
        regions_outcomes=[service_error(403, "NotAuthorized")],
    ),
    Case(
        "regions-unexpected-error-propagates",
        steps=[REGIONS],
        regions_outcomes=[ValueError("boom")],
    ),
    Case(
        "regions-keyboard-interrupt-propagates",
        steps=[REGIONS],
        regions_outcomes=[KeyboardInterrupt()],
    ),
    Case(
        "regions-error-then-success",
        steps=[REGIONS, REGIONS],
        regions_outcomes=[conn_error(), None],
    ),
    Case(
        "regions-success-then-error",
        steps=[REGIONS, REGIONS],
        regions_outcomes=[None, service_error()],
    ),
    Case(
        "identity-creation-error-falls-back-and-retries",
        steps=[REGIONS, REGIONS],
        create_outcomes={("identity", HOME): [service_error(401, "NotAuthenticated"), None]},
    ),
    Case(
        "identity-creation-connect-error-falls-back",
        steps=[REGIONS],
        create_outcomes={("identity", HOME): [conn_error()]},
    ),
    Case(
        "identity-creation-unexpected-error-propagates",
        steps=[REGIONS],
        create_outcomes={("identity", HOME): [RuntimeError("bad config")]},
    ),
    Case("identity-client-cached", steps=[IDENTITY, IDENTITY, REGIONS, IDENTITY]),
    Case("identity-client-falsy-is-recreated", steps=[IDENTITY, IDENTITY], falsy_identity=True),
    Case("regions-falsy-identity-recreated", steps=[REGIONS, REGIONS], falsy_identity=True),
    Case("compute-client", steps=[compute("r1")]),
    Case("compute-client-cached-per-region", steps=[compute("r1"), compute("r1"), compute("r2")]),
    Case("block-client", steps=[block("r1")]),
    Case("block-client-cached-per-region", steps=[block("r1"), block("r2"), block("r1")]),
    Case("regional-identity-client", steps=[rid("r1")]),
    Case("regional-identity-cached", steps=[rid("r1"), rid("r1"), rid("r2")]),
    Case(
        "clients-are-separate-caches",
        steps=[compute("r1"), block("r1"), rid("r1"), IDENTITY, compute("r1"), rid("r1")],
    ),
    Case(
        "compute-creation-error-propagates-and-retries",
        steps=[compute("r1"), compute("r1")],
        create_outcomes={("compute", "r1"): [service_error(), None]},
    ),
    Case(
        "block-creation-error-propagates",
        steps=[block("r1")],
        create_outcomes={("block", "r1"): [conn_error()]},
    ),
    Case(
        "regional-identity-creation-error-propagates",
        steps=[rid("r1"), ads("r1")],
        create_outcomes={("rid", "r1"): [request_error(), None]},
    ),
    Case("ads-success", steps=[ads("r1")], ads={"r1": ["AD-1", "AD-2"]}),
    Case("ads-cached", steps=[ads("r1"), ads("r1"), ads("r2")], ads={"r1": ["AD-1"]}),
    Case("ads-empty-is-cached", steps=[ads("r1"), ads("r1")], ads={"r1": []}),
    Case(
        "ads-service-error-propagates-not-cached",
        steps=[ads("r1"), ads("r1")],
        ads={"r1": ["AD-1"]},
        ads_outcomes={"r1": [service_error(401, "NotAuthenticated"), None]},
    ),
    Case(
        "ads-connect-error-propagates",
        steps=[ads("r1")],
        ads_outcomes={"r1": [conn_error()]},
    ),
    Case(
        "ads-uses-regional-identity-client",
        steps=[rid("r1"), ads("r1"), ads("r2")],
        ads={"r1": ["AD-1"], "r2": ["AD-9"]},
    ),
    Case(
        "mixed-session",
        steps=[REGIONS, compute("us-sanjose-1"), block("us-sanjose-1"), ads("us-sanjose-1")],
        ads={"us-sanjose-1": ["AD-1"]},
    ),
    Case(
        "falls-back-then-clients",
        steps=[REGIONS, compute("us-sanjose-1"), ads("us-sanjose-1")],
        regions_outcomes=[service_error(403, "NotAuthorized")],
        ads={"us-sanjose-1": ["AD-1"]},
    ),
]


class World:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []
        self.created: list[Any] = []
        self.counters: dict[Any, int] = {}
        self.base: Any = None

    def next_outcome(
        self, key: Any, outcomes: Sequence[BaseException | None]
    ) -> BaseException | None:
        index = self.counters.get(key, 0)
        self.counters[key] = index + 1
        if not outcomes:
            return None
        return outcomes[min(index, len(outcomes) - 1)]


class FakeClient:
    kind = ""

    def __init__(self, world: World, config: Any) -> None:
        self.world = world
        self.config = config
        self.region = config["region"]
        world.calls.append(("new", self.kind, dict(config)))
        planned = world.case.create_outcomes.get((self.kind, self.region), ())
        outcome = world.next_outcome(("new", self.kind, self.region), planned)
        if outcome is not None:
            raise outcome
        self.index = len(world.created)
        world.created.append(self)


class FakeCompute(FakeClient):
    kind = "compute"


class FakeBlock(FakeClient):
    kind = "block"


class FakeIdentity(FakeClient):
    kind = "identity"

    def __bool__(self) -> bool:
        return not self.world.case.falsy_identity

    def list_region_subscriptions(self, tenancy_id: str) -> Any:
        self.world.calls.append(("list_region_subscriptions", self.region, tenancy_id))
        outcome = self.world.next_outcome(("regions",), self.world.case.regions_outcomes)
        if outcome is not None:
            raise outcome
        names = [types.SimpleNamespace(region_name=r) for r in self.world.case.region_names]
        return types.SimpleNamespace(data=names)

    def list_availability_domains(self, compartment_id: str) -> Any:
        self.world.calls.append(("list_availability_domains", self.region, compartment_id))
        outcome = self.world.next_outcome(
            ("ads", self.region), self.world.case.ads_outcomes.get(self.region, ())
        )
        if outcome is not None:
            raise outcome
        data = [types.SimpleNamespace(name=n) for n in self.world.case.ads.get(self.region, ())]
        return types.SimpleNamespace(data=data)


class RegionalIdentity(FakeIdentity):
    kind = "rid"


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    results: list[Any]
    attributes: Any
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def legacy_module() -> Any:
    module = types.ModuleType("legacy_oci_base")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    return module


def _prepare(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> World:
    world = World(case)
    for name, value in (
        ("OCI_PRIVATE_KEY_CONTENT", case.content),
        ("OCI_PRIVATE_KEY_FILE", case.key_file),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setattr(oci.core, "ComputeClient", lambda config: FakeCompute(world, config))
    monkeypatch.setattr(oci.core, "BlockstorageClient", lambda config: FakeBlock(world, config))
    monkeypatch.setattr(
        oci.identity,
        "IdentityClient",
        lambda config: _identity_for(world, config),
    )
    caplog.set_level(logging.DEBUG)
    caplog.clear()
    capsys.readouterr()
    return world


def _identity_for(world: World, config: Any) -> Any:
    # Regional identity clients are built from a config copy, the main one from `base.config`.
    main = world.base is not None and config is world.base.config
    return (FakeIdentity if main else RegionalIdentity)(world, config)


def _step(base: Any, module: Any, step: Step) -> Any:
    name = step[0]
    if name == "regions":
        regions = base.get_all_regions()
        return ("regions", list(regions), regions is module.DEFAULT_REGION_FALLBACK)
    if name == "identity":
        return ("client", base.get_identity_client().index)
    if name == "compute":
        return ("client", base.get_compute_client(step[1]).index)
    if name == "block":
        return ("client", base.get_blockstorage_client(step[1]).index)
    if name == "rid":
        return ("client", base.get_regional_identity_client(step[1]).index)
    names = base.get_availability_domains(step[1])
    return ("ads", list(names))


def _attributes(base: Any) -> Any:
    return {
        "tenancy_id": base.tenancy_id,
        "user_id": base.user_id,
        "fingerprint": base.fingerprint,
        "region": base.region,
        "compartment_id": base.compartment_id,
        "config": dict(base.config),
    }


def _run(
    module: Any,
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    world = _prepare(case, monkeypatch, caplog, capsys)
    results: list[Any] = []
    attributes: Any = None
    error: tuple[str, str] | None = None
    try:
        base = module.Base()
        world.base = base
        attributes = _attributes(base)
        results.extend(_step(base, module, step) for step in case.steps)
    except (Exception, KeyboardInterrupt) as exc:
        error = (type(exc).__name__, str(exc))
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(world.calls, results, attributes, logs, capsys.readouterr().out, error)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_base_matches_main(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(legacy_module(), case, monkeypatch, caplog, capsys)
    new = _run(new_module, case, monkeypatch, caplog, capsys)
    assert new.calls == old.calls
    assert new.results == old.results
    assert new.attributes == old.attributes
    assert new.logs == old.logs
    assert new.stdout == old.stdout
    assert new.error == old.error


def test_constants_match_main() -> None:
    old = legacy_module()
    for name in (
        "HOME_REGION",
        "TENANCY_OCID",
        "USER_OCID",
        "FINGERPRINT",
        "COMPARTMENT_ID",
        "DEFAULT_REGION_FALLBACK",
    ):
        assert getattr(new_module, name) == getattr(old, name)


def test_oracle_is_nonvacuous_on_the_error_path(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = next(c for c in CASES if c.id == "regions-auth-error-401-falls-back")
    outcome = _run(new_module, case, monkeypatch, caplog, capsys)
    assert outcome.results == [("regions", [HOME], True)]
    assert [level for _, level, _ in outcome.logs] == [logging.WARNING, logging.WARNING]
    assert outcome.logs[1][2] == f"Falling back to static region list: ['{HOME}']"


def test_missing_key_message_is_exact(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = next(c for c in CASES if c.id == "env-neither")
    outcome = _run(new_module, case, monkeypatch, caplog, capsys)
    assert outcome.error == (
        "KeyError",
        "'Either OCI_PRIVATE_KEY_CONTENT or OCI_PRIVATE_KEY_FILE must be set'",
    )
    assert outcome.calls == []
