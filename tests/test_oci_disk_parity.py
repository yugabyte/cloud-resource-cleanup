"""Parity: `crc.oci.disk.Disk` and the registered Cleaner against main's version
(`tests/legacy/oci_disk.txt`).

All run against the same fake OCI SDK; SDK calls (in order), results, log records, stdout and
raised errors must be identical for every case. The Cleaner path runs `plan()` then `apply()`,
so reads happen before deletes: its reads and its deletes are each compared in order.
"""

import datetime
import logging
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import oci
import pytest
from oci.exceptions import ConnectTimeout, RequestException, ServiceError

from crc.core.cleaner import Operation, RunContext
from crc.oci.disk import Disk, DiskRuntime, build_disk_cleaner
from tests.helpers_log_fix import expected_logs
from tests.log_fix_oci import RULES

BUGS = {"BUG-353", "BUG-358"}
ORACLE = Path(__file__).parent / "legacy" / "oci_disk.txt"
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)
NOW_UTC = NOW.astimezone(datetime.UTC)
HOME = "us-sanjose-1"
AD = "AD-1"
TWO = 2
THREE = 3


def vol(
    vol_id: str,
    name: str | None = None,
    hours: float | None = 100,
    state: str = "AVAILABLE",
    tags: Any = "default",
) -> Any:
    created = None if hours is None else NOW_UTC - datetime.timedelta(hours=hours)
    return types.SimpleNamespace(
        id=vol_id,
        display_name=vol_id if name is None else name,
        lifecycle_state=state,
        freeform_tags={"env": "ci"} if tags == "default" else tags,
        time_created=created,
    )


def att(volume_id: str, state: str = "ATTACHED") -> Any:
    return types.SimpleNamespace(
        lifecycle_state=state, volume_id=volume_id, boot_volume_id=volume_id
    )


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
class Region:
    volumes: Any = field(default_factory=list)
    boot_volumes: Any = field(default_factory=list)
    attachments: Any = field(default_factory=list)
    ads: Any = field(default_factory=lambda: [AD])
    boot_attachments: Mapping[str, Any] = field(default_factory=dict)
    delete_errors: Mapping[str, Exception] = field(default_factory=dict)
    client_error: Exception | None = None


@dataclass
class Case:
    id: str
    regions: Mapping[str, Region]
    filter_tags: Any = field(default_factory=lambda: {"env": ["ci"]})
    exception_tags: Any = field(default_factory=dict)
    notags: Any = field(default_factory=dict)
    age: Any = field(default_factory=lambda: {"hours": 5})
    custom_age_tag_key: Any = ""
    dry_run: bool = False
    max_age: str | None = None
    regions_error: Exception | None = None


def one(**kwargs: Any) -> Mapping[str, Region]:
    return {"r1": Region(**kwargs)}


CASES = [
    Case(
        "dry-run", one(volumes=[vol("a"), vol("b", hours=1)], boot_volumes=[vol("c")]), dry_run=True
    ),
    Case("delete-volumes-and-boot-volumes", one(volumes=[vol("a")], boot_volumes=[vol("b")])),
    Case(
        "two-regions",
        {"r1": Region(volumes=[vol("a")]), "r2": Region(boot_volumes=[vol("b")])},
    ),
    Case("empty-region", one()),
    Case("no-regions", {}),
    Case(
        "state-not-available", one(volumes=[vol("a", state="TERMINATED"), vol("b", state="FAULTY")])
    ),
    Case("state-provisioning", one(volumes=[vol("a", state="PROVISIONING"), vol("b")])),
    Case(
        "attached-volume-kept",
        one(volumes=[vol("a"), vol("b")], attachments=[att("a")]),
    ),
    Case(
        "attaching-volume-kept",
        one(volumes=[vol("a")], attachments=[att("a", "ATTACHING")]),
    ),
    Case(
        "detaching-volume-is-deleted",
        one(
            volumes=[vol("a"), vol("b")], attachments=[att("a", "DETACHING"), att("b", "DETACHED")]
        ),
    ),
    Case(
        "attached-boot-volume-kept",
        one(boot_volumes=[vol("a"), vol("b")], boot_attachments={AD: [att("a")]}),
    ),
    Case(
        "boot-attachments-in-second-domain",
        one(
            boot_volumes=[vol("a"), vol("b"), vol("c")],
            ads=["AD-1", "AD-2"],
            boot_attachments={"AD-1": [att("a")], "AD-2": [att("b", "ATTACHING")]},
        ),
    ),
    Case(
        "attachment-listing-connection-error-deletes-attached",
        one(volumes=[vol("a")], attachments=conn_error()),
    ),
    Case(
        "attachment-listing-auth-error-deletes-attached",
        one(volumes=[vol("a")], attachments=service_error(401, "NotAuthenticated")),
    ),
    Case(
        "boot-attachment-listing-error-deletes-attached",
        one(
            boot_volumes=[vol("a")],
            boot_attachments={AD: request_error()},
        ),
    ),
    Case(
        "boot-attachment-second-domain-error-keeps-first",
        one(
            boot_volumes=[vol("a"), vol("b")],
            ads=["AD-1", "AD-2"],
            boot_attachments={"AD-1": [att("a")], "AD-2": conn_error()},
        ),
    ),
    Case(
        "availability-domains-error-deletes-attached",
        one(boot_volumes=[vol("a")], ads=conn_error(), boot_attachments={AD: [att("a")]}),
    ),
    Case(
        "attachment-listing-unexpected-error",
        one(volumes=[vol("a")], attachments=ValueError("boom")),
    ),
    Case(
        "boot-attachment-listing-unexpected-error",
        one(volumes=[vol("a")], boot_volumes=[vol("b")], boot_attachments={AD: ValueError("boom")}),
    ),
    Case(
        "no-availability-domains",
        one(boot_volumes=[vol("a")], ads=[], boot_attachments={AD: [att("a")]}),
    ),
    Case(
        "no-filter-includes-everything", one(volumes=[vol("a", tags={}), vol("b")]), filter_tags={}
    ),
    Case("filter-none-includes-everything", one(volumes=[vol("a", tags=None)]), filter_tags=None),
    Case("untagged-with-filter-skipped", one(volumes=[vol("a", tags=None), vol("b", tags={})])),
    Case(
        "untagged-without-filter-included",
        one(volumes=[vol("a", tags=None)], boot_volumes=[vol("b", tags={})]),
        filter_tags={},
    ),
    Case(
        "filter-any-key",
        one(
            volumes=[
                vol("a", tags={"x": "1"}),
                vol("b", tags={"y": "2"}),
                vol("c", tags={"x": "0"}),
            ]
        ),
        filter_tags={"x": ["1"], "y": ["2"]},
    ),
    Case(
        "filter-empty-value-list-means-key-present",
        one(volumes=[vol("a", tags={"x": "anything"}), vol("b", tags={"z": "1"})]),
        filter_tags={"x": []},
    ),
    Case(
        "filter-value-case-sensitive",
        one(volumes=[vol("a", tags={"env": "CI"})]),
    ),
    Case(
        "filter-no-match",
        one(volumes=[vol("a", tags={"env": "prod"})]),
    ),
    Case(
        "exception-any-key",
        one(volumes=[vol("a", tags={"env": "ci", "keep": "1"}), vol("b")]),
        exception_tags={"keep": ["1"], "other": []},
    ),
    Case(
        "exception-empty-value-list",
        one(volumes=[vol("a", tags={"env": "ci", "keep": "x"}), vol("b")]),
        exception_tags={"keep": []},
    ),
    Case(
        "exception-wins-over-filter",
        one(volumes=[vol("a", tags={"env": "ci"})]),
        exception_tags={"env": ["ci"]},
    ),
    Case(
        "notags-all-keys-skips",
        one(
            volumes=[
                vol("a", tags={"env": "ci", "k1": "v", "k2": "v"}),
                vol("b", tags={"env": "ci", "k1": "v"}),
            ]
        ),
        notags={"k1": ["v"], "k2": []},
    ),
    Case(
        "notags-value-mismatch-not-skipped",
        one(volumes=[vol("a", tags={"env": "ci", "k1": "other"})]),
        notags={"k1": ["v"]},
    ),
    Case("exception-none", one(volumes=[vol("a")]), exception_tags=None),
    Case("notags-none", one(volumes=[vol("a")]), notags=None),
    Case(
        "boot-volumes-use-same-filters",
        one(
            boot_volumes=[
                vol("a", tags={"env": "ci", "keep": "1"}),
                vol("b", tags={"env": "dev"}),
                vol("c"),
            ]
        ),
        exception_tags={"keep": ["1"]},
    ),
    Case("age-equal-hours", one(volumes=[vol("a", hours=5)])),
    Case("age-just-under-hours", one(volumes=[vol("a", hours=4.99)])),
    Case("age-just-over-hours", one(volumes=[vol("a", hours=5.01)])),
    Case(
        "age-days",
        one(volumes=[vol("a", hours=48), vol("b", hours=47.9)]),
        age={"days": 2},
    ),
    Case(
        "age-days-and-hours",
        one(volumes=[vol("a", hours=30), vol("b", hours=29.9)]),
        age={"days": 1, "hours": 6},
    ),
    Case("age-int-days", one(volumes=[vol("a", hours=25), vol("b", hours=23)]), age=1),
    Case("age-empty-matches-all", one(volumes=[vol("a", hours=0.1)]), age={}),
    Case("age-none-matches-all", one(volumes=[vol("a", hours=0.1)]), age=None),
    Case("age-unknown-keys-keep-all", one(volumes=[vol("a", hours=9999)]), age={"weeks": 1}),
    Case(
        "max-age-shortens",
        one(volumes=[vol("a", hours=3), vol("b", hours=1)]),
        age={"days": 30},
        max_age="{'hours': 2}",
    ),
    Case("max-age-empty-dict", one(volumes=[vol("a", hours=3)]), age={"days": 1}, max_age="{}"),
    Case("max-age-not-a-dict", one(volumes=[vol("a", hours=3)]), age={"days": 1}, max_age="2"),
    Case("created-in-the-future", one(volumes=[vol("a", hours=-10)])),
    Case(
        "naive-created-time",
        one(volumes=[types.SimpleNamespace(**{**vol("a").__dict__, "time_created": NOW})]),
    ),
    Case("missing-created-time", one(volumes=[vol("a", hours=None)])),
    Case(
        "custom-age-tag-shortens",
        one(
            volumes=[
                vol("a", hours=3, tags={"env": "ci", "ttl": "{'hours': 2}"}),
                vol("b", hours=3),
            ]
        ),
        age={"days": 5},
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-lengthens",
        one(volumes=[vol("a", hours=30, tags={"env": "ci", "ttl": "{'days': 3}"})]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-unparseable-falls-back-to-age",
        one(volumes=[vol("a", hours=30, tags={"env": "ci", "ttl": "{'days': "})]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-empty-falls-back-to-age",
        one(volumes=[vol("a", hours=30, tags={"env": "ci", "ttl": ""})]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-zero-days-falls-back-to-age",
        one(volumes=[vol("a", hours=30, tags={"env": "ci", "ttl": "{}"})]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-key-missing-on-volume",
        one(volumes=[vol("a", hours=30)]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-none-key",
        one(volumes=[vol("a", hours=30, tags={"env": "ci", "ttl": "{'days': 9}"})]),
        custom_age_tag_key=None,
    ),
    Case(
        "list-volumes-connection-error-skips-region",
        {"r1": Region(volumes=conn_error()), "r2": Region(volumes=[vol("c")])},
    ),
    Case(
        "list-boot-volumes-error-skips-whole-region",
        {
            "r1": Region(volumes=[vol("a")], boot_volumes=request_error()),
            "r2": Region(boot_volumes=[vol("c")]),
        },
    ),
    Case(
        "list-volumes-auth-error-skips-region",
        {
            "r1": Region(volumes=service_error(401, "NotAuthenticated")),
            "r2": Region(volumes=[vol("c")]),
        },
    ),
    Case(
        "client-creation-connection-error-skips-region",
        {"r1": Region(client_error=conn_error()), "r2": Region(volumes=[vol("c")])},
    ),
    Case(
        "list-volumes-unexpected-error",
        {"r1": Region(volumes=ValueError("boom")), "r2": Region(volumes=[vol("c")])},
    ),
    Case(
        "delete-error-swallowed-and-logged",
        one(
            volumes=[vol("a"), vol("b"), vol("c")],
            delete_errors={"b": RuntimeError("busy")},
        ),
    ),
    Case(
        "delete-service-error-swallowed",
        one(
            volumes=[vol("a")],
            boot_volumes=[vol("b")],
            delete_errors={
                "a": service_error(409, "Conflict"),
                "b": service_error(404, "NotFound"),
            },
        ),
    ),
    Case(
        "delete-error-in-dry-run-never-called",
        one(volumes=[vol("a")], delete_errors={"a": RuntimeError("busy")}),
        dry_run=True,
    ),
    Case(
        "all-deletes-fail",
        one(
            volumes=[vol("a")],
            boot_volumes=[vol("b")],
            delete_errors={"a": ValueError("x"), "b": ValueError("y")},
        ),
    ),
    Case(
        "duplicate-display-names",
        one(volumes=[vol("a", "same"), vol("b", "same")], boot_volumes=[vol("c", "same")]),
    ),
    Case(
        "display-name-none",
        one(volumes=[types.SimpleNamespace(**{**vol("a").__dict__, "display_name": None})]),
    ),
    Case(
        "unexpected-error-in-later-region",
        {"r1": Region(volumes=[vol("a")]), "r2": Region(volumes=[vol("b", hours=None)])},
    ),
    Case(
        "unexpected-error-in-boot-volume-after-volume-delete",
        one(volumes=[vol("a")], boot_volumes=[vol("b", hours=None)]),
    ),
    Case(
        "many-volumes",
        {
            "r1": Region(
                volumes=[vol(f"v{i}", hours=50 + i) for i in range(25)],
                boot_volumes=[vol(f"b{i}", hours=60 + i) for i in range(9)],
            ),
            "r2": Region(volumes=[vol(f"w{i}") for i in range(5)]),
        },
    ),
    Case("many-volumes-dry-run", one(volumes=[vol(f"v{i}") for i in range(25)]), dry_run=True),
    Case(
        "regions-connection-error-falls-back", one(volumes=[vol("a")]), regions_error=conn_error()
    ),
    Case(
        "regions-auth-error-falls-back",
        {HOME: Region(volumes=[vol("a")])},
        regions_error=service_error(401, "NotAuthenticated"),
    ),
    Case(
        "regions-request-error-falls-back",
        {HOME: Region(volumes=[vol("a")])},
        regions_error=request_error(),
    ),
    Case(
        "regions-unexpected-error-propagates",
        one(volumes=[vol("a")]),
        regions_error=ValueError("boom"),
    ),
]


class FakeClient:
    def __init__(self, world: "FakeOci", region: str) -> None:
        self.world = world
        self.region = region

    def _data(self, op: str, outcome: Any) -> Any:
        if isinstance(outcome, Exception):
            raise outcome
        return types.SimpleNamespace(data=outcome)


class FakeBlockstorage(FakeClient):
    def list_volumes(self, compartment_id: str) -> Any:
        self.world.calls.append(("list_volumes", self.region, compartment_id))
        return self._data("list_volumes", self.world.region(self.region).volumes)

    def list_boot_volumes(self, compartment_id: str) -> Any:
        self.world.calls.append(("list_boot_volumes", self.region, compartment_id))
        return self._data("list_boot_volumes", self.world.region(self.region).boot_volumes)

    def delete_volume(self, volume_id: str) -> None:
        self.world.calls.append(("delete_volume", self.region, volume_id))
        error = self.world.region(self.region).delete_errors.get(volume_id)
        if error is not None:
            raise error

    def delete_boot_volume(self, volume_id: str) -> None:
        self.world.calls.append(("delete_boot_volume", self.region, volume_id))
        error = self.world.region(self.region).delete_errors.get(volume_id)
        if error is not None:
            raise error


class FakeCompute(FakeClient):
    def list_volume_attachments(self, compartment_id: str) -> Any:
        self.world.calls.append(("list_volume_attachments", self.region, compartment_id))
        return self._data("list_volume_attachments", self.world.region(self.region).attachments)

    def list_boot_volume_attachments(self, availability_domain: str, compartment_id: str) -> Any:
        self.world.calls.append(
            ("list_boot_volume_attachments", self.region, availability_domain, compartment_id)
        )
        outcome = self.world.region(self.region).boot_attachments.get(availability_domain, [])
        return self._data("list_boot_volume_attachments", outcome)


class FakeIdentity(FakeClient):
    def list_region_subscriptions(self, tenancy_id: str) -> Any:
        self.world.calls.append(("list_region_subscriptions", self.region, tenancy_id))
        if self.world.case.regions_error is not None:
            raise self.world.case.regions_error
        names = [types.SimpleNamespace(region_name=r) for r in self.world.case.regions]
        return types.SimpleNamespace(data=names)

    def list_availability_domains(self, compartment_id: str) -> Any:
        self.world.calls.append(("list_availability_domains", self.region, compartment_id))
        ads = self.world.region(self.region).ads
        if isinstance(ads, Exception):
            raise ads
        return types.SimpleNamespace(data=[types.SimpleNamespace(name=n) for n in ads])


class FakeOci:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []

    def region(self, name: str) -> Region:
        return self.case.regions[name]

    def compute(self, config: Mapping[str, Any]) -> FakeCompute:
        self.calls.append(("ComputeClient", config["region"]))
        client_error = self.region(config["region"]).client_error
        if client_error is not None:
            raise client_error
        return FakeCompute(self, config["region"])

    def blockstorage(self, config: Mapping[str, Any]) -> FakeBlockstorage:
        self.calls.append(("BlockstorageClient", config["region"]))
        return FakeBlockstorage(self, config["region"])

    def identity(self, config: Mapping[str, Any]) -> FakeIdentity:
        self.calls.append(("IdentityClient", config["region"]))
        return FakeIdentity(self, config["region"])

    def paginate(self, fn: Any, **kwargs: Any) -> Any:
        self.calls.append(("list_call_get_all_results", fn.__name__))
        return fn(**kwargs)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(oci.core, "ComputeClient", self.compute)
        monkeypatch.setattr(oci.core, "BlockstorageClient", self.blockstorage)
        monkeypatch.setattr(oci.identity, "IdentityClient", self.identity)
        monkeypatch.setattr(oci.pagination, "list_call_get_all_results", self.paginate)


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[Any] | None
    count: int | None
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_oci_disk")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    module.__dict__["datetime"] = types.SimpleNamespace(
        now=lambda: NOW,
    )
    return module


def _legacy(case: Case) -> Any:
    return _load_oracle().Disk(
        case.dry_run,
        case.filter_tags,
        case.exception_tags,
        case.age,
        case.custom_age_tag_key,
        case.notags,
    )


def _new(case: Case) -> Any:
    runtime = DiskRuntime(clock=lambda: NOW)
    return Disk(
        case.dry_run,
        case.filter_tags,
        case.exception_tags,
        case.age,
        case.custom_age_tag_key,
        case.notags,
        runtime,
    )


def _prepare(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeOci:
    oci_world = FakeOci(case)
    oci_world.install(monkeypatch)
    monkeypatch.setenv("OCI_PRIVATE_KEY_CONTENT", "not-a-key")
    monkeypatch.delenv("OCI_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    if case.max_age is None:
        monkeypatch.delenv("MAX_AGE", raising=False)
    else:
        monkeypatch.setenv("MAX_AGE", case.max_age)
    caplog.clear()
    capsys.readouterr()
    return oci_world


def _run(
    build: Callable[[Case], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    world = _prepare(case, monkeypatch, caplog, capsys)
    cleaner = build(case)
    error: tuple[str, str] | None = None
    try:
        cleaner.delete()
    except Exception as e:
        error = (type(e).__name__, str(e))
    deleted = list(cleaner.get_deleted)
    count = cleaner.count
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(world.calls, deleted, count, logs, capsys.readouterr().out, error)


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert new.calls == old.calls
    assert new.error == old.error
    assert new.deleted == old.deleted
    assert new.count == old.count
    assert new.logs == expected_logs(old.logs, BUGS, RULES)
    assert new.stdout == old.stdout


def _run_cleaner(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    world = _prepare(case, monkeypatch, caplog, capsys)
    context = RunContext(
        dry_run=case.dry_run,
        operation=Operation.DELETE,
        options={
            "filter_tags": case.filter_tags,
            "exception_tags": case.exception_tags,
            "notags": case.notags,
            "age": case.age,
            "custom_age_tag_key": case.custom_age_tag_key,
        },
        now=NOW,
    )
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    try:
        cleaner = build_disk_cleaner(context)
        plan = cleaner.plan(context)
        if case.dry_run:
            deleted = [c.name for c in plan.candidates]
        else:
            deleted = list(cleaner.apply(plan, context).succeeded)
    except Exception as e:
        error = (type(e).__name__, str(e))
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(world.calls, deleted, len(deleted), logs, capsys.readouterr().out, error)


def _is_delete(call: tuple[Any, ...]) -> bool:
    return call[0] in ("delete_volume", "delete_boot_volume")


def _is_apply_log(log: tuple[str, int, str]) -> bool:
    return log[2].startswith(("Deleting ", "Error occurred while deleting "))


def _is_summary_log(log: tuple[str, int, str]) -> bool:
    return log[2].startswith(
        ("No OCI disk to delete.", "number of OCI disks deleted:", "List of OCI disk")
    )


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_cleaner_protocol_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    assert new.error == old.error
    assert new.stdout == old.stdout
    if old.error is not None:
        # main may already have deleted volumes before the error; plan() fails before any delete
        assert [c for c in new.calls if _is_delete(c)] == []
        return
    assert new.deleted == old.deleted
    assert [c for c in new.calls if not _is_delete(c)] == [
        c for c in old.calls if not _is_delete(c)
    ]
    assert [c for c in new.calls if _is_delete(c)] == [c for c in old.calls if _is_delete(c)]
    old_logs = [
        log
        for log in expected_logs(old.logs, BUGS, RULES)
        if not log[2].startswith("count of items")
    ]
    plan_logs = [log for log in old_logs if not _is_apply_log(log) and not _is_summary_log(log)]
    apply_logs = [log for log in old_logs if _is_apply_log(log)]
    summary_logs = [log for log in old_logs if _is_summary_log(log)]
    assert new.logs == plan_logs + apply_logs + summary_logs


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    (
        "attachment-listing-connection-error-deletes-attached",
        lambda o: o.deleted == ["a"],
    ),
    ("attachment-listing-auth-error-deletes-attached", lambda o: o.deleted == ["a"]),
    ("boot-attachment-listing-error-deletes-attached", lambda o: o.deleted == ["a"]),
    ("availability-domains-error-deletes-attached", lambda o: o.deleted == ["a"]),
    ("boot-attachment-second-domain-error-keeps-first", lambda o: o.deleted == ["b"]),
    ("no-availability-domains", lambda o: o.deleted == ["a"]),
    ("detaching-volume-is-deleted", lambda o: o.deleted == ["a", "b"]),
    ("attaching-volume-kept", lambda o: o.deleted == []),
    ("delete-error-swallowed-and-logged", lambda o: o.error is None and o.deleted == ["a", "c"]),
    ("all-deletes-fail", lambda o: o.error is None and o.deleted == []),
    ("age-empty-matches-all", lambda o: o.deleted == ["a"]),
    ("age-none-matches-all", lambda o: o.deleted == ["a"]),
    ("age-unknown-keys-keep-all", lambda o: o.deleted == []),
    ("max-age-shortens", lambda o: o.deleted == ["a"]),
    ("max-age-empty-dict", lambda o: o.deleted == []),
    ("max-age-not-a-dict", lambda o: o.error is not None),
    ("custom-age-tag-unparseable-falls-back-to-age", lambda o: o.deleted == ["a"]),
    ("custom-age-tag-empty-falls-back-to-age", lambda o: o.deleted == ["a"]),
    ("custom-age-tag-zero-days-falls-back-to-age", lambda o: o.deleted == ["a"]),
    ("untagged-with-filter-skipped", lambda o: o.deleted == []),
    ("untagged-without-filter-included", lambda o: o.deleted == ["a", "b"]),
    ("filter-any-key", lambda o: o.deleted == ["a", "b"]),
    ("filter-empty-value-list-means-key-present", lambda o: o.deleted == ["a"]),
    ("exception-wins-over-filter", lambda o: o.deleted == []),
    ("list-boot-volumes-error-skips-whole-region", lambda o: o.deleted == ["c"]),
    ("list-volumes-auth-error-skips-region", lambda o: o.deleted == ["c"]),
    ("list-volumes-unexpected-error", lambda o: o.error is not None and o.deleted == []),
    ("regions-auth-error-falls-back", lambda o: o.deleted == ["a"]),
    ("regions-unexpected-error-propagates", lambda o: o.error is not None and o.deleted == []),
    ("naive-created-time", lambda o: o.error is not None and o.error[0] == "TypeError"),
    ("missing-created-time", lambda o: o.error is not None and o.error[0] == "AttributeError"),
    (
        "unexpected-error-in-boot-volume-after-volume-delete",
        lambda o: o.error is not None and ("delete_volume", "r1", "a") in o.calls,
    ),
    ("duplicate-display-names", lambda o: o.deleted == ["same", "same", "same"]),
    ("display-name-none", lambda o: o.deleted == [None]),
]


@pytest.mark.parametrize(("case_id", "check"), QUIRKS, ids=[q[0] for q in QUIRKS])
def test_quirk_is_preserved(
    case_id: str,
    check: Callable[[Outcome], bool],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = next(c for c in CASES if c.id == case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert check(old), "oracle no longer shows the quirk"
    assert check(new)


def test_disk_diverges_from_oracle(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """BUG-353 and BUG-358: new logs differ from the oracle by a level only."""
    changed = 0
    for case in CASES:
        old = _run(_legacy, case, monkeypatch, caplog, capsys)
        new = _run(_new, case, monkeypatch, caplog, capsys)
        if old.logs == new.logs:
            continue
        changed += 1
        assert [log[2] for log in new.logs] == [log[2] for log in old.logs]
        assert new.calls == old.calls
        assert {log[1] for log in new.logs if log not in old.logs} == {logging.DEBUG}
    assert changed > 0
