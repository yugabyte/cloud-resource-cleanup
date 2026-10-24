"""Parity: `crc.gcp.spot_vm` against main (`tests/legacy/gcp_spot_vm.txt`).

The oracle (main's `SpotVM`) and the registered `Cleaner` (through `crc.core.engine.run` and
through `plan()` / `apply()`) run against the same fake `googleapiclient.discovery.build` and
`compute_v1.InstancesClient`, with a frozen clock and a fake `time`. Results, errors, the calls
after listing (deletes, operation polls, disk reads), log records and stdout must match. The
wait for an operation reads `time.monotonic` in the new code (`ARCH-003` bans `time.time`); the
fake clock serves both.
"""

import datetime
import logging
import re
import types
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from google.api_core.exceptions import PermissionDenied, ServiceUnavailable
from google.auth.exceptions import DefaultCredentialsError

from crc.core.cleaner import Operation, RunContext
from crc.core.registry import get
from crc.gcp.spot_vm import build_spot_cleaner
from tests.helpers_ad_spot_engine import run_through_engine
from tests.helpers_log_fix import expected_logs
from tests.log_fix_gcp import RULES

ORACLE = Path(__file__).parent / "legacy" / "gcp_spot_vm.txt"
UTC = datetime.UTC
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
PROJECT = "proj-1"
BUGS = {"BUG-358"}
# BUG-228 (fixed): an unlabelled Spot VM is kept when filter_labels is empty.
# case id -> (bug, names of the VMs main deletes and the fixed code keeps, deleted names now).
DIVERGES: dict[str, tuple[str, tuple[str, ...], list[str]]] = {
    "labels-none-no-filter": ("BUG-228", ("v",), []),
    "filter-none": ("BUG-228", ("u",), ["a", "b", "c"]),
    "untagged-without-filter-included": ("BUG-228", ("u",), []),
    "exception-skips": ("BUG-228", ("u",), ["b", "c"]),
    "exception-any-key": ("BUG-228", ("u",), []),
    "exception-empty-values": ("BUG-228", ("u",), ["c"]),
    "exception-none": ("BUG-228", ("u",), ["a", "b", "c"]),
    "exception-untagged-not-protected": ("BUG-228", ("u",), []),
    "notags-all-keys-skips": ("BUG-228", ("u",), ["b", "c"]),
    "notags-partial-keeps-deleting": ("BUG-228", ("u",), ["a", "b", "c"]),
    "notags-single-key": ("BUG-228", ("u",), ["a", "c"]),
    "notags-none": ("BUG-228", ("u",), ["a", "b", "c"]),
}
DAY = {"days": 1}
LABELS = {"yb_task": "itest"}
SLEEP = 5
WAIT = 180
POLLS_UNTIL_TIMEOUT = WAIT // SLEEP


def _make(kind: type[Exception], message: str) -> Exception:
    return kind(message)


BOOM = _make(ServiceUnavailable, "unreachable")
DENIED = _make(PermissionDenied, "denied")
NO_CREDS = _make(DefaultCredentialsError, "no credentials")


def stamp(**delta: float) -> str:
    return (NOW - datetime.timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%S.%f%z")


def disk_src(name: str, zone: str = "z1", project: str = "proj-1", kind: str = "zones") -> str:
    return f"projects/{project}/{kind}/{zone}/disks/{name}"


def attached(source: Any, auto_delete: bool = False) -> Any:
    return types.SimpleNamespace(source=source, auto_delete=auto_delete)


def vm(  # noqa: PLR0913, PLR0917 - keyword-style fixture builder
    name: Any,
    labels: Any = "default",
    ago: Mapping[str, float] | None = None,
    spot: Any = True,
    status: str = "RUNNING",
    disks: Sequence[Any] | None = (),
    metadata: Any = None,
    stamp_value: Any = "default",
    provisioning: Any = None,
) -> Any:
    scheduling: Any = types.SimpleNamespace(
        preemptible=spot is True, provisioning_model=provisioning
    )
    if spot is None:
        scheduling = None
    return types.SimpleNamespace(
        name=name,
        status=status,
        labels=dict(LABELS) if labels == "default" else labels,
        scheduling=scheduling,
        disks=None if disks is None else list(disks),
        metadata=metadata,
        creation_timestamp=stamp(**(ago or {"days": 3}))
        if stamp_value == "default"
        else stamp_value,
    )


def meta(*pairs: tuple[Any, Any]) -> Any:
    return types.SimpleNamespace(items=[types.SimpleNamespace(key=k, value=v) for k, v in pairs])


class IterError:
    """A listing that yields `items` and then raises `error` while being iterated."""

    def __init__(self, items: Sequence[Any], error: Exception) -> None:
        self.items = list(items)
        self.error = error


@dataclass
class Case:
    id: str
    zones: Mapping[str, Any]
    dry_run: bool = False
    filter_labels: Any = field(default_factory=dict)
    exception_labels: Any = field(default_factory=dict)
    notags: Any = field(default_factory=dict)
    age: Any = field(default_factory=lambda: dict(DAY))
    custom_age_tag_key: Any = None
    instance_deletes: Mapping[tuple[str, Any], Any] = field(default_factory=dict)
    polls: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    disk_gets: Mapping[tuple[str, str], Any] = field(default_factory=dict)
    disk_deletes: Mapping[tuple[str, str], Any] = field(default_factory=dict)
    max_age: str | None = None
    zones_response: Any = None
    build_error: Exception | None = None
    zones_error: Exception | None = None
    client_error: Exception | None = None
    fails_in_plan: bool = False


MIXED = {
    "z1": [
        vm("old-a"),
        vm("standard", spot=False),
        vm("young", ago={"hours": 2}),
        vm("stopping", status="STOPPING"),
        vm("old-b"),
    ],
}
TWO_ZONES = {"z1": [vm("a1"), vm("a2")], "z2": [vm("b1"), vm("b2", ago={"hours": 1})]}
ONE = {"z1": [vm("v")]}
WITH_DISK = {"z1": [vm("v", disks=[attached(disk_src("d1"))])]}


def _basic_cases() -> list[Case]:
    return [
        Case("mixed", MIXED),
        Case("mixed-dry-run", MIXED, dry_run=True),
        Case("two-zones", TWO_ZONES),
        Case("two-zones-dry-run", TWO_ZONES, dry_run=True),
        Case("duplicate-names", {"z": [vm("dup"), vm("dup")]}),
        Case("same-name-two-zones", {"z1": [vm("dup")], "z2": [vm("dup")]}),
        Case("no-zones", {}),
        Case("empty-zone", {"z1": []}),
        Case("no-zones-dry-run", {}, dry_run=True),
        Case("empty-name", {"z": [vm("")]}),
        Case("instance-without-status", {"z": [types.SimpleNamespace(name="x")]}),
        Case("instance-without-name", {"z": [types.SimpleNamespace(status="RUNNING")]}),
        Case(
            "instance-without-timestamp",
            {
                "z": [
                    types.SimpleNamespace(
                        name="x",
                        status="RUNNING",
                        scheduling=vm("s").scheduling,
                        labels={},
                        metadata=None,
                    )
                ]
            },
        ),
        Case("labels-none-no-filter", {"z": [vm("v", labels=None)]}),
        Case("labels-not-a-mapping", {"z": [vm("v", labels=42)]}),
    ]


def _status_and_spot_cases() -> list[Case]:
    return [
        Case("status-terminated", {"z": [vm("v", status="TERMINATED")]}),
        Case("status-suspended", {"z": [vm("v", status="SUSPENDED")]}),
        Case("status-staging-skipped", {"z": [vm("v", status="STAGING")]}),
        Case("status-lowercase-skipped", {"z": [vm("v", status="running")]}),
        Case("spot-provisioning-model", {"z": [vm("v", spot=False, provisioning="SPOT")]}),
        Case("spot-provisioning-lowercase", {"z": [vm("v", spot=False, provisioning="spot")]}),
        Case("standard-provisioning", {"z": [vm("v", spot=False, provisioning="STANDARD")]}),
        Case("no-scheduling", {"z": [vm("v", spot=None)]}),
        Case("gke-node-skipped", {"z": [vm("v", labels={**LABELS, "goog-gke-node": ""})]}),
        Case(
            "mig-member-skipped",
            {
                "z": [
                    vm(
                        "v",
                        metadata=meta(("created-by", "projects/p/zones/z/instanceGroupManagers/m")),
                    )
                ]
            },
        ),
        Case(
            "mig-metadata-after-other-key",
            {
                "z": [
                    vm("v", metadata=meta(("a", "b"), ("created-by", "x/instanceGroupManagers/m")))
                ]
            },
        ),
        Case("created-by-not-mig", {"z": [vm("v", metadata=meta(("created-by", "someone")))]}),
        Case(
            "mig-value-wrong-key",
            {"z": [vm("v", metadata=meta(("owner", "instanceGroupManagers")))]},
        ),
        Case("metadata-item-no-value", {"z": [vm("v", metadata=meta(("created-by", None)))]}),
        Case(
            "metadata-item-no-key", {"z": [vm("v", metadata=meta((None, "instanceGroupManagers")))]}
        ),
        Case("metadata-without-items", {"z": [vm("v", metadata=types.SimpleNamespace())]}),
        Case("metadata-not-iterable", {"z": [vm("v", metadata=types.SimpleNamespace(items=5))]}),
    ]


def _label_cases() -> list[Case]:
    labelled = {
        "z": [
            vm("a", labels={"env": "qa", "team": "x"}),
            vm("b", labels={"env": "prod", "team": "x"}),
            vm("c", labels={"team": "x"}),
            vm("u", labels={}),
        ]
    }
    return [
        Case("filter-value-match", labelled, filter_labels={"env": ["qa"]}),
        Case("filter-all-keys", labelled, filter_labels={"env": ["qa"], "team": ["x"]}),
        Case("filter-one-key-missing", labelled, filter_labels={"env": ["qa"], "other": ["x"]}),
        Case("filter-empty-values-means-present", labelled, filter_labels={"env": []}),
        Case("filter-multiple-values", labelled, filter_labels={"env": ["qa", "prod"]}),
        Case("filter-none", labelled, filter_labels=None),
        Case("filter-string-value-is-substring", labelled, filter_labels={"env": "qa-prod"}),
        Case("filter-case-sensitive", labelled, filter_labels={"env": ["QA"]}),
        Case("untagged-without-filter-included", {"z": [vm("u", labels={})]}),
        Case(
            "untagged-with-filter-excluded",
            {"z": [vm("u", labels={})]},
            filter_labels={"env": ["qa"]},
        ),
        Case("exception-skips", labelled, exception_labels={"env": ["qa"]}),
        Case("exception-any-key", labelled, exception_labels={"env": ["dev"], "team": ["x"]}),
        Case("exception-empty-values", labelled, exception_labels={"env": []}),
        Case("exception-none", labelled, exception_labels=None),
        Case(
            "exception-untagged-not-protected",
            {"z": [vm("u", labels={})]},
            exception_labels={"env": ["qa"]},
        ),
        Case("notags-all-keys-skips", labelled, notags={"env": ["qa"], "team": ["x"]}),
        Case("notags-partial-keeps-deleting", labelled, notags={"env": ["qa"], "other": ["x"]}),
        Case("notags-single-key", labelled, notags={"env": ["prod"]}),
        Case("notags-none", labelled, notags=None),
        Case(
            "filter-and-exception",
            labelled,
            filter_labels={"team": ["x"]},
            exception_labels={"env": ["prod"]},
        ),
    ]


def _age_cases() -> list[Case]:
    return [
        Case("age-exactly-at-threshold", {"z": [vm("v", ago={"days": 1})]}),
        Case("age-one-second-short", {"z": [vm("v", ago={"hours": 23, "seconds": 59})]}),
        Case("age-hours", {"z": [vm("v", ago={"hours": 5})]}, age={"hours": 5}),
        Case("age-hours-short", {"z": [vm("v", ago={"hours": 4})]}, age={"hours": 5}),
        Case(
            "age-days-and-hours", {"z": [vm("v", ago={"hours": 30})]}, age={"days": 1, "hours": 6}
        ),
        Case(
            "age-days-and-hours-short",
            {"z": [vm("v", ago={"hours": 29})]},
            age={"days": 1, "hours": 6},
        ),
        Case("age-int-days", {"z": [vm("v", ago={"days": 2})]}, age=2),
        Case("age-empty-rejected", ONE, age={}, fails_in_plan=True),
        Case("age-none-rejected", ONE, age=None, fails_in_plan=True),
        Case("age-zero-rejected", ONE, age={"days": 0}, fails_in_plan=True),
        Case("age-negative-rejected", ONE, age={"days": -1}, fails_in_plan=True),
        Case("age-unknown-key-rejected", ONE, age={"weeks": 1}, fails_in_plan=True),
        Case("age-future-creation", {"z": [vm("v", ago={"days": -1})]}),
        Case(
            "age-offset-timestamp",
            {"z": [vm("v", stamp_value="2026-02-27T01:00:00.000-08:00")]},
        ),
        Case(
            "age-max-age-shortens",
            {"z": [vm("v", ago={"hours": 3})]},
            age={"days": 5},
            max_age="{'hours': 2}",
        ),
        Case(
            "age-max-age-longer-ignored", {"z": [vm("v", ago={"hours": 3})]}, max_age="{'days': 9}"
        ),
        Case("age-max-age-invalid-caught-per-instance", ONE, max_age="not a dict"),
        Case("timestamp-unparseable", {"z": [vm("v", stamp_value="yesterday")]}),
        Case(
            "timestamp-without-fraction", {"z": [vm("v", stamp_value="2026-02-20T10:00:00-08:00")]}
        ),
        Case("timestamp-without-zone", {"z": [vm("v", stamp_value="2026-02-20T10:00:00.000")]}),
        Case("timestamp-none", {"z": [vm("v", stamp_value=None)]}),
        Case("timestamp-error-keeps-next", {"z": [vm("bad", stamp_value="x"), vm("good")]}),
    ]


def _custom_age_cases() -> list[Case]:
    def tagged(value: str, **kw: Any) -> dict[str, Any]:
        return {"z": [vm("v", labels={**LABELS, "ttl": value}, **kw)]}

    return [
        Case(
            "custom-age-keeps-young",
            tagged("{'days': 10}", ago={"days": 5}),
            custom_age_tag_key="ttl",
        ),
        Case(
            "custom-age-shortens",
            tagged("{'hours': 1}", ago={"hours": 12}),
            custom_age_tag_key="ttl",
        ),
        Case("custom-age-empty-keeps", tagged(""), custom_age_tag_key="ttl"),
        Case("custom-age-unparseable-keeps", tagged("soon"), custom_age_tag_key="ttl"),
        Case("custom-age-zero-keeps", tagged("{'days': 0}"), custom_age_tag_key="ttl"),
        Case("custom-age-empty-dict-keeps", tagged("{}"), custom_age_tag_key="ttl"),
        Case("custom-age-unknown-key-keeps", tagged("{'weeks': 1}"), custom_age_tag_key="ttl"),
        Case("custom-age-int-literal", tagged("2", ago={"days": 3}), custom_age_tag_key="ttl"),
        Case("custom-age-absent-uses-age", {"z": [vm("v")]}, custom_age_tag_key="ttl"),
        Case("custom-age-key-not-set", tagged("{'hours': 1}", ago={"hours": 12})),
        Case(
            "custom-age-key-empty-string",
            tagged("{'hours': 1}", ago={"hours": 12}),
            custom_age_tag_key="",
        ),
    ]


def _leftover_disk_cases() -> list[Case]:
    two = {"z1": [vm("v", disks=[attached(disk_src("d1")), attached(disk_src("d2", "z2"))])]}
    return [
        Case("disk-zonal", WITH_DISK),
        Case("disk-zonal-dry-run", WITH_DISK, dry_run=True),
        Case("disk-two", two),
        Case("disk-two-dry-run", two, dry_run=True),
        Case("disk-auto-delete-skipped", {"z1": [vm("v", disks=[attached(disk_src("d1"), True)])]}),
        Case(
            "disk-regional",
            {"z1": [vm("v", disks=[attached(disk_src("rd", "r1", kind="regions"))])]},
        ),
        Case("disk-name-only-source", {"z1": [vm("v", disks=[attached("plain-name")])]}),
        Case(
            "disk-source-trailing-slash", {"z1": [vm("v", disks=[attached(disk_src("d1") + "/")])]}
        ),
        Case("disk-source-none", {"z1": [vm("v", disks=[attached(None)])]}),
        Case("disk-source-empty", {"z1": [vm("v", disks=[attached("")])]}),
        Case("disk-source-slash-only", {"z1": [vm("v", disks=[attached("/")])]}),
        Case("disk-source-projects-only", {"z1": [vm("v", disks=[attached("projects")])]}),
        Case("disk-source-no-location", {"z1": [vm("v", disks=[attached("projects/p/zones")])]}),
        Case(
            "disk-other-project",
            {"z1": [vm("v", disks=[attached(disk_src("d1", project="other"))])]},
        ),
        Case(
            "disk-same-twice",
            {"z1": [vm("v", disks=[attached(disk_src("d1")), attached(disk_src("d1"))])]},
        ),
        Case("disk-without-attributes", {"z1": [vm("v", disks=[types.SimpleNamespace()])]}),
        Case("disks-attribute-none", {"z1": [vm("v", disks=None)]}),
        Case("disk-get-error-skips", WITH_DISK, disk_gets={("z1", "d1"): DENIED}),
        Case("disk-get-error-dry-run", WITH_DISK, dry_run=True, disk_gets={("z1", "d1"): DENIED}),
        Case(
            "disk-labels-exception-keeps",
            WITH_DISK,
            exception_labels={"keep": ["yes"]},
            disk_gets={("z1", "d1"): {"labels": {"keep": "yes"}}},
        ),
        Case(
            "disk-labels-notags-keeps",
            WITH_DISK,
            notags={"keep": ["yes"]},
            disk_gets={("z1", "d1"): {"labels": {"keep": "yes"}}},
        ),
        Case(
            "disk-labels-notags-partial-deletes",
            WITH_DISK,
            notags={"keep": ["yes"], "b": []},
            disk_gets={("z1", "d1"): {"labels": {"keep": "yes"}}},
        ),
        Case(
            "disk-labels-null",
            WITH_DISK,
            exception_labels={"k": []},
            disk_gets={("z1", "d1"): {"labels": None}},
        ),
        Case("disk-get-not-a-dict", WITH_DISK, disk_gets={("z1", "d1"): []}),
        Case("disk-delete-error-swallowed", WITH_DISK, disk_deletes={("z1", "d1"): DENIED}),
        Case("disk-delete-no-op-name-counted", WITH_DISK, disk_deletes={("z1", "d1"): {}}),
        Case(
            "disk-delete-op-error",
            WITH_DISK,
            polls={"op-disk-d1": [{"status": "DONE", "error": {"code": 1}}]},
        ),
        Case("disk-delete-op-poll-error", WITH_DISK, polls={"op-disk-d1": [DENIED]}),
        Case(
            "disk-delete-op-pending-then-done",
            WITH_DISK,
            polls={
                "op-disk-d1": [{"status": "RUNNING"}, {"status": "RUNNING"}, {"status": "DONE"}]
            },
        ),
        Case("disk-delete-op-timeout", WITH_DISK, polls={"op-disk-d1": [{"status": "RUNNING"}]}),
        Case(
            "disk-delete-second-fails",
            {"z1": [vm("v", disks=[attached(disk_src("d1")), attached(disk_src("d2"))])]},
            disk_deletes={("z1", "d1"): BOOM},
        ),
        Case("disk-delete-response-not-a-dict", WITH_DISK, disk_deletes={("z1", "d1"): []}),
    ]


def _instance_delete_cases() -> list[Case]:
    return [
        Case("delete-error-swallowed", MIXED, instance_deletes={("z1", "old-a"): DENIED}),
        Case("delete-error-keeps-disks", WITH_DISK, instance_deletes={("z1", "v"): BOOM}),
        Case(
            "delete-error-ignored-in-dry-run",
            MIXED,
            dry_run=True,
            instance_deletes={("z1", "old-a"): DENIED},
        ),
        Case("delete-error-second-zone", TWO_ZONES, instance_deletes={("z2", "b1"): DENIED}),
        Case("delete-no-op-name-not-counted", WITH_DISK, instance_deletes={("z1", "v"): {}}),
        Case("delete-op-name-empty", WITH_DISK, instance_deletes={("z1", "v"): {"name": ""}}),
        Case("delete-response-not-a-dict", WITH_DISK, instance_deletes={("z1", "v"): []}),
        Case(
            "delete-op-error-skips-disks",
            WITH_DISK,
            polls={"op-v": [{"status": "DONE", "error": {"c": 1}}]},
        ),
        Case("delete-op-poll-error-skips-disks", WITH_DISK, polls={"op-v": [DENIED]}),
        Case(
            "delete-op-pending-then-done",
            WITH_DISK,
            polls={"op-v": [{"status": "PENDING"}, {"status": "DONE"}]},
        ),
        Case(
            "delete-op-status-missing-then-done",
            WITH_DISK,
            polls={"op-v": [{}, {"status": "DONE"}]},
        ),
        Case("delete-op-timeout-skips-disks", WITH_DISK, polls={"op-v": [{"status": "RUNNING"}]}),
        Case("delete-op-not-a-dict", WITH_DISK, polls={"op-v": [[]]}),
        Case(
            "delete-op-timeout-then-next-vm",
            {"z1": [vm("a"), vm("b")]},
            polls={"op-a": [{"status": "RUNNING"}]},
        ),
    ]


def _listing_error_cases() -> list[Case]:
    return [
        Case("build-error", MIXED, build_error=NO_CREDS, fails_in_plan=True),
        Case(
            "zones-connectivity-error",
            MIXED,
            zones_error=ConnectionError("down"),
            fails_in_plan=True,
        ),
        Case("zones-auth-error", MIXED, zones_error=DENIED, fails_in_plan=True),
        Case("zones-credentials-error", MIXED, zones_error=NO_CREDS, fails_in_plan=True),
        Case("zones-response-without-items", MIXED, zones_response={}),
        Case("zones-response-empty-items", MIXED, zones_response={"items": []}),
        Case("zones-response-not-a-dict", MIXED, zones_response=[], fails_in_plan=True),
        Case(
            "zone-without-name-after-first-zone",
            TWO_ZONES,
            zones_response={"items": [{"name": "z1"}, {}]},
            fails_in_plan=True,
        ),
        Case(
            "instances-client-credentials-error", MIXED, client_error=NO_CREDS, fails_in_plan=True
        ),
        Case("instances-list-error-skips-zone", {"z1": BOOM, "z2": [vm("a")]}),
        Case("instances-list-permission-denied", {"z1": [vm("a")], "z2": DENIED}),
        Case("instances-list-error-dry-run", {"z1": BOOM, "z2": [vm("a")]}, dry_run=True),
        Case(
            "instances-iteration-error-skips-zone",
            {"z1": IterError([vm("a"), vm("b")], BOOM), "z2": [vm("c")]},
        ),
    ]


def _cases() -> list[Case]:
    return (
        _basic_cases()
        + _status_and_spot_cases()
        + _label_cases()
        + _age_cases()
        + _custom_age_cases()
        + _leftover_disk_cases()
        + _instance_delete_cases()
        + _listing_error_cases()
    )


CASES = _cases()


class FakeClock:
    """Serves `time.time` (oracle) and `time.monotonic` (new code); sleep advances it."""

    def __init__(self, calls: list[tuple[Any, ...]]) -> None:
        self.now = 1000.0
        self.calls = calls

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.calls.append(("sleep", seconds))
        self.now += seconds


class FakeRequest:
    def __init__(self, gcp: "FakeGcp", resource: str, verb: str, kwargs: dict[str, Any]) -> None:
        self.gcp = gcp
        self.resource = resource
        self.verb = verb
        self.kwargs = kwargs

    def execute(self) -> Any:
        self.gcp.calls.append(("execute", self.resource, self.verb, *self.kwargs.values()))
        return self.gcp.respond(self.resource, self.verb, self.kwargs)


class FakeResource:
    def __init__(self, gcp: "FakeGcp", name: str) -> None:
        self.gcp = gcp
        self.name = name

    def __getattr__(self, verb: str) -> Callable[..., FakeRequest]:
        def call(**kwargs: Any) -> FakeRequest:
            self.gcp.calls.append((f"{self.name}.{verb}", *kwargs.values()))
            return FakeRequest(self.gcp, self.name, verb, kwargs)

        return call


class FakeService:
    def __init__(self, gcp: "FakeGcp", name: str, version: str) -> None:
        self.gcp = gcp
        gcp.calls.append(("discovery.build", name, version))
        if gcp.case.build_error is not None:
            raise gcp.case.build_error

    def __enter__(self) -> "FakeService":
        return self

    def __exit__(self, *exc: object) -> None:
        self.gcp.calls.append(("close",))

    def close(self) -> None:
        self.gcp.calls.append(("close",))

    def __getattr__(self, name: str) -> Callable[[], FakeResource]:
        def resource() -> FakeResource:
            self.gcp.calls.append((f"{name}()",))
            return FakeResource(self.gcp, name)

        return resource


class FakeInstancesClient:
    def __init__(self, gcp: "FakeGcp") -> None:
        self.gcp = gcp
        gcp.calls.append(("InstancesClient",))
        if gcp.case.client_error is not None:
            raise gcp.case.client_error

    def list(self, *, project: str, zone: str) -> Any:
        self.gcp.calls.append(("instances.list", project, zone))
        outcome = self.gcp.case.zones[zone]
        if isinstance(outcome, Exception):
            raise outcome
        return self._pages(zone, outcome)

    def _pages(self, zone: str, outcome: Any) -> Iterator[Any]:
        items = outcome.items if isinstance(outcome, IterError) else outcome
        for item in items:
            self.gcp.calls.append(("instances.next", zone, getattr(item, "name", None)))
            yield item
        if isinstance(outcome, IterError):
            raise outcome.error


class FakeGcp:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []
        self.polled: Counter[str] = Counter()
        self.clock = FakeClock(self.calls)

    def build(self, name: str, version: str) -> FakeService:
        return FakeService(self, name, version)

    def instances_client(self) -> FakeInstancesClient:
        return FakeInstancesClient(self)

    def respond(self, resource: str, verb: str, kwargs: dict[str, Any]) -> Any:
        case = self.case
        if (resource, verb) == ("zones", "list"):
            if case.zones_error is not None:
                raise case.zones_error
            if case.zones_response is not None:
                return case.zones_response
            return {"items": [{"name": zone} for zone in case.zones]}
        if resource in {"zoneOperations", "regionOperations"}:
            return self._poll(kwargs["operation"])
        if resource == "instances":
            key = (kwargs["zone"], kwargs["instance"])
            return self._outcome(case.instance_deletes.get(key), {"name": f"op-{key[1]}"})
        location = kwargs.get("zone") or kwargs.get("region")
        key = (location, kwargs["disk"])
        if verb == "get":
            return self._outcome(case.disk_gets.get(key), {"labels": {}})
        return self._outcome(case.disk_deletes.get(key), {"name": f"op-disk-{key[1]}"})

    def _poll(self, operation: str) -> Any:
        sequence = self.case.polls.get(operation, [{"status": "DONE"}])
        index = min(self.polled[operation], len(sequence) - 1)
        self.polled[operation] += 1
        return self._outcome(sequence[index], None)

    @staticmethod
    def _outcome(value: Any, default: Any) -> Any:
        if isinstance(value, Exception):
            raise value
        return default if value is None else value


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[str]
    disks: list[str]
    count: int
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


class FrozenDatetime(datetime.datetime):
    @classmethod
    def now(cls, tz: datetime.tzinfo | None = None) -> Any:
        return NOW if tz is None else NOW.astimezone(tz)


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_gcp_spot_vm")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    setattr(module, "datetime", FrozenDatetime)  # noqa: B010
    return module


def _args(case: Case) -> dict[str, Any]:
    return {
        "dry_run": case.dry_run,
        "project_id": PROJECT,
        "filter_labels": case.filter_labels,
        "exception_labels": case.exception_labels,
        "age": case.age,
        "custom_age_tag_key": case.custom_age_tag_key,
        "notags": case.notags,
    }


def _setup(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeGcp:
    gcp = FakeGcp(case)
    monkeypatch.setattr("googleapiclient.discovery.build", gcp.build)
    monkeypatch.setattr("google.cloud.compute_v1.InstancesClient", gcp.instances_client)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    monkeypatch.setattr("crc.gcp.spot_vm.operations.time", gcp.clock)
    if case.max_age is None:
        monkeypatch.delenv("MAX_AGE", raising=False)
    else:
        monkeypatch.setenv("MAX_AGE", case.max_age)
    caplog.clear()
    capsys.readouterr()
    return gcp


def _logs(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [
        (r.name, r.levelno, re.sub(r"0x[0-9a-f]+", "0x0", r.getMessage())) for r in caplog.records
    ]


def _legacy(case: Case, gcp: FakeGcp) -> Any:
    module = _load_oracle()
    module.time = gcp.clock
    return module.SpotVM(**_args(case))


def _run(
    build: Callable[[Case, FakeGcp], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    gcp = _setup(case, monkeypatch, caplog, capsys)
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    disks: list[str] = []
    count = 0
    try:
        spot = build(case, gcp)
        try:
            spot.delete()
        finally:
            deleted = list(spot.get_deleted)
            disks = list(spot.get_deleted_disks)
            count = spot.delete_count
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    return Outcome(gcp.calls, deleted, disks, count, _logs(caplog), capsys.readouterr().out, error)


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


NO_VMS = ("root", logging.WARNING, "No GCP Spot/preemptible VMs to delete.")


def _assert_diverges(case: Case, old: Outcome, new: Outcome) -> None:
    bug, removed, deleted = DIVERGES[case.id]
    assert bug == "BUG-228"
    assert old.deleted != new.deleted, f"{case.id} no longer shows the oracle behaviour"
    assert [n for n in old.deleted if n not in removed] == deleted
    assert new.deleted == deleted
    assert new.count == len(deleted)
    assert new.disks == old.disks
    assert new.error == old.error
    assert new.stdout == old.stdout
    assert [c for c in new.calls if c[0] == "instances.delete"] == [
        c for c in old.calls if c[0] == "instances.delete" and c[3] not in removed
    ]
    kept = [
        entry
        for entry in expected_logs(old.logs, BUGS, RULES)
        if not entry[2].startswith("List of GCP")
        and not any(entry[2] == f"Deleting GCP Spot VM {n}" for n in removed)
    ]
    summary = [entry for entry in new.logs if entry[2].startswith("List of GCP")]
    assert Counter(e for e in new.logs if e not in summary and e[2] != NO_VMS[2]) == Counter(kept)
    assert (NO_VMS in new.logs) == (not deleted)
    assert summary[0][2].startswith(f"List of GCP Spot VMs (Total: {len(deleted)})")
    assert [n for n in removed if f"Deleting GCP Spot VM {n}" in [e[2] for e in new.logs]] == []


def _cleaner_context(case: Case) -> RunContext:
    return RunContext(
        dry_run=case.dry_run,
        operation=Operation.DELETE,
        options={
            "project_id": PROJECT,
            "filter_tags": case.filter_labels,
            "exception_tags": case.exception_labels,
            "notags": case.notags,
            "age": case.age,
            "custom_age_tag_key": case.custom_age_tag_key,
        },
        now=NOW,
    )


def _run_engine(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    gcp = _setup(case, monkeypatch, caplog, capsys)
    ctx = _cleaner_context(case)
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    disks: list[str] = []
    try:
        outcome = run_through_engine("gcp", "spot_instances", build_spot_cleaner, ctx)
        if outcome.result is None:
            deleted = [c.id for c in outcome.plan.candidates]
            disks = list(outcome.plan.related.get("Disk", ()))
        else:
            deleted = list(outcome.result.succeeded)
            disks = list(outcome.result.related.get("Disk", ()))
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    return Outcome(
        gcp.calls, deleted, disks, len(deleted), _logs(caplog), capsys.readouterr().out, error
    )


def _run_cleaner(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    gcp = _setup(case, monkeypatch, caplog, capsys)
    ctx = _cleaner_context(case)
    error: tuple[str, str] | None = None
    deleted: list[str] = []
    disks: list[str] = []
    try:
        cleaner = get("gcp", "spot_instances")(ctx)
        plan = cleaner.plan(ctx)
        if case.dry_run:
            deleted = [c.id for c in plan.candidates]
            disks = list(plan.related.get("Disk", ()))
        else:
            result = cleaner.apply(plan, ctx)
            deleted = list(result.succeeded)
            disks = list(result.related.get("Disk", ()))
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    return Outcome(
        gcp.calls, deleted, disks, len(deleted), _logs(caplog), capsys.readouterr().out, error
    )


_LISTING = {"discovery.build", "close", "zones()", "zones.list", "InstancesClient"}
_LISTING_PREFIXES = ("instances.list", "instances.next")


def _after_listing(calls: Sequence[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """Calls other than session handling and listing, in order: deletes, polls, disk reads."""
    kept = []
    for call in calls:
        if call[0] in _LISTING or call[0].startswith(_LISTING_PREFIXES):
            continue
        if call[0] == "execute" and call[1] == "zones":
            continue
        kept.append(call)
    return kept


def _listed_instances(case: Case) -> int:
    return sum(len(v) if isinstance(v, list) else 2 for v in case.zones.values())


def _assert_matches_main(case: Case, old: Outcome, new: Outcome) -> None:
    if new.error is not None:
        assert case.fails_in_plan
        assert new.error == old.error
        assert not [c for c in new.calls if c[0] in {"instances.delete", "disks.delete"}]
        return
    assert old.error is None
    if case.id in DIVERGES:
        _assert_diverges(case, old, new)
        return
    assert new.deleted == old.deleted
    assert new.disks == old.disks
    assert new.count == old.count
    assert new.stdout == old.stdout
    assert _after_listing(new.calls) == _after_listing(old.calls)
    assert [c for c in new.calls if c[0] == "instances.list"] == [
        c for c in old.calls if c[0] == "instances.list"
    ]
    expected = expected_logs(old.logs, BUGS, RULES)
    assert Counter(new.logs) == Counter(expected)
    assert new.logs[-2:] == expected[-2:]
    if _listed_instances(case) <= 1:
        assert new.logs == expected


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_engine_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_engine(case, monkeypatch, caplog, capsys)
    _assert_matches_main(case, old, new)


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
    _assert_matches_main(case, old, new)


def _find(case_id: str) -> Case:
    return next(c for c in CASES if c.id == case_id)


def _calls_named(o: Outcome, name: str) -> list[tuple[Any, ...]]:
    return [c for c in o.calls if c[0] == name]


def _error_logged(o: Outcome, text: str) -> bool:
    return any(level == logging.ERROR and text in message for _, level, message in o.logs)


def _has_error(o: Outcome) -> bool:
    return o.error is not None


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("mixed", lambda o: o.deleted == ["old-a", "old-b"]),
    ("same-name-two-zones", lambda o: o.deleted == ["dup", "dup"]),
    (
        "mixed-dry-run",
        lambda o: not _calls_named(o, "instances.delete") and o.deleted == ["old-a", "old-b"],
    ),
    ("status-staging-skipped", lambda o: o.deleted == []),
    ("status-lowercase-skipped", lambda o: o.deleted == []),
    ("spot-provisioning-lowercase", lambda o: o.deleted == ["v"]),
    ("no-scheduling", lambda o: o.deleted == []),
    ("gke-node-skipped", lambda o: o.deleted == []),
    ("mig-metadata-after-other-key", lambda o: o.deleted == []),
    ("created-by-not-mig", lambda o: o.deleted == ["v"]),
    (
        "metadata-not-iterable",
        lambda o: o.deleted == [] and _error_logged(o, "Error processing GCP Spot VM v"),
    ),
    ("untagged-with-filter-excluded", lambda o: o.deleted == []),
    ("filter-all-keys", lambda o: o.deleted == ["a"]),
    ("filter-empty-values-means-present", lambda o: o.deleted == ["a", "b"]),
    ("filter-string-value-is-substring", lambda o: o.deleted == ["a", "b"]),
    (
        "labels-not-a-mapping",
        lambda o: o.deleted == [] and _error_logged(o, "Error processing GCP Spot VM v"),
    ),
    ("age-exactly-at-threshold", lambda o: o.deleted == ["v"]),
    ("age-one-second-short", lambda o: o.deleted == []),
    ("age-empty-rejected", lambda o: o.error is not None and o.error[0] == "ValueError"),
    ("age-future-creation", lambda o: o.deleted == []),
    ("age-max-age-shortens", lambda o: o.deleted == ["v"]),
    ("age-max-age-invalid-caught-per-instance", lambda o: o.deleted == [] and not _has_error(o)),
    (
        "timestamp-without-fraction",
        lambda o: o.deleted == [] and _error_logged(o, "Error processing"),
    ),
    ("timestamp-error-keeps-next", lambda o: o.deleted == ["good"]),
    ("custom-age-keeps-young", lambda o: o.deleted == []),
    ("custom-age-shortens", lambda o: o.deleted == ["v"]),
    ("custom-age-empty-keeps", lambda o: o.deleted == []),
    ("custom-age-unparseable-keeps", lambda o: o.deleted == []),
    ("custom-age-zero-keeps", lambda o: o.deleted == []),
    ("custom-age-absent-uses-age", lambda o: o.deleted == ["v"]),
    ("disk-zonal", lambda o: o.disks == ["proj-1/z1/d1"]),
    (
        "disk-zonal-dry-run",
        lambda o: o.disks == ["proj-1/z1/d1"] and not _calls_named(o, "disks.delete"),
    ),
    ("disk-zonal-dry-run", lambda o: len(_calls_named(o, "disks.get")) == 1),
    ("disk-auto-delete-skipped", lambda o: o.disks == []),
    (
        "disk-regional",
        lambda o: o.disks == ["proj-1/r1/rd"] and len(_calls_named(o, "regionDisks.delete")) == 1,
    ),
    ("disk-name-only-source", lambda o: o.disks == ["proj-1/z1/plain-name"]),
    ("disk-source-none", lambda o: o.disks == []),
    ("disk-same-twice", lambda o: o.disks == ["proj-1/z1/d1", "proj-1/z1/d1"]),
    ("disk-get-error-skips", lambda o: o.disks == [] and o.deleted == ["v"]),
    ("disk-labels-exception-keeps", lambda o: o.disks == [] and o.deleted == ["v"]),
    ("disk-labels-notags-keeps", lambda o: o.disks == []),
    (
        "disk-delete-error-swallowed",
        lambda o: o.disks == [] and o.deleted == ["v"] and not _has_error(o),
    ),
    ("disk-delete-no-op-name-counted", lambda o: o.disks == ["proj-1/z1/d1"]),
    ("disk-delete-op-error", lambda o: o.disks == []),
    (
        "disk-delete-op-timeout",
        lambda o: o.disks == [] and len(_calls_named(o, "sleep")) == POLLS_UNTIL_TIMEOUT,
    ),
    ("disk-get-not-a-dict", lambda o: o.disks == [] and o.deleted == ["v"]),
    ("delete-error-swallowed", lambda o: o.deleted == ["old-b"] and not _has_error(o)),
    (
        "delete-error-keeps-disks",
        lambda o: o.deleted == [] and o.disks == [] and not _calls_named(o, "disks.get"),
    ),
    ("delete-no-op-name-not-counted", lambda o: o.deleted == [] and o.disks == []),
    ("delete-op-name-empty", lambda o: o.deleted == []),
    ("delete-op-error-skips-disks", lambda o: o.deleted == [] and o.disks == []),
    (
        "delete-op-pending-then-done",
        lambda o: o.deleted == ["v"] and len(_calls_named(o, "sleep")) == 1,
    ),
    ("delete-op-timeout-skips-disks", lambda o: o.deleted == [] and o.disks == []),
    ("delete-op-timeout-then-next-vm", lambda o: o.deleted == ["b"]),
    (
        "zones-connectivity-error",
        lambda o: o.error == ("ConnectionError", "down") and o.deleted == [],
    ),
    ("zones-auth-error", lambda o: o.error == ("PermissionDenied", "403 denied")),
    ("zones-response-without-items", lambda o: o.deleted == [] and not _has_error(o)),
    (
        "zone-without-name-after-first-zone",
        lambda o: o.error is not None and o.error[0] == "KeyError" and o.deleted == ["a1", "a2"],
    ),
    (
        "instances-list-error-skips-zone",
        lambda o: o.deleted == ["a"] and _error_logged(o, "Zone z1: failed listing"),
    ),
    ("instances-iteration-error-skips-zone", lambda o: o.deleted == ["c"]),
    (
        "instances-client-credentials-error",
        lambda o: o.error is not None and o.error[0] == "DefaultCredentialsError",
    ),
    (
        "no-zones",
        lambda o: (
            o.deleted == []
            and ("root", logging.WARNING, "No GCP Spot/preemptible VMs to delete.") in o.logs
        ),
    ),
]


# BUG-409: plan() lists every zone before apply(), so the later-zone KeyError deletes nothing.
ENGINE_QUIRKS: Mapping[str, Callable[[Outcome], bool]] = {
    "zone-without-name-after-first-zone": (
        lambda o: o.error is not None and o.error[0] == "KeyError" and o.deleted == []
    ),
}


@pytest.mark.parametrize(
    ("case_id", "check"), QUIRKS, ids=[f"{cid}-{i}" for i, (cid, _) in enumerate(QUIRKS)]
)
def test_quirk_is_the_same_on_main_and_new(
    case_id: str,
    check: Callable[[Outcome], bool],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find(case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_engine(case, monkeypatch, caplog, capsys)
    assert check(old), f"main does not show the quirk of {case_id}"
    assert ENGINE_QUIRKS.get(case_id, check)(new), f"new code does not show the quirk of {case_id}"


def test_bug_242_leftover_disk_custom_age_main_deletes_new_code_keeps(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    labels = {"retention": "{'days': 10}"}
    young = {"labels": labels, "creationTimestamp": stamp(days=3)}
    old_disk = {"labels": labels, "creationTimestamp": stamp(days=30)}
    young_case = Case(
        "disk-custom-age-young",
        WITH_DISK,
        custom_age_tag_key="retention",
        disk_gets={("z1", "d1"): young},
    )
    old_case = Case(
        "disk-custom-age-old",
        WITH_DISK,
        custom_age_tag_key="retention",
        disk_gets={("z1", "d1"): old_disk},
    )
    old = _run(_legacy, young_case, monkeypatch, caplog, capsys)
    new = _run_engine(young_case, monkeypatch, caplog, capsys)
    assert old.disks == ["proj-1/z1/d1"]
    assert len(_calls_named(old, "disks.delete")) == 1
    assert new.disks == []
    assert not _calls_named(new, "disks.delete")
    assert new.deleted == old.deleted == ["v"]
    old_ok = _run(_legacy, old_case, monkeypatch, caplog, capsys)
    new_ok = _run_engine(old_case, monkeypatch, caplog, capsys)
    assert _after_listing(new_ok.calls) == _after_listing(old_ok.calls)
    assert [c for c in new_ok.calls if c[0] == "instances.list"] == [
        c for c in old_ok.calls if c[0] == "instances.list"
    ]
    assert new_ok.disks == old_ok.disks == ["proj-1/z1/d1"]
    expected = expected_logs(old_ok.logs, BUGS, RULES)
    assert [e for e in new_ok.logs if e[1] > logging.DEBUG] == [
        e for e in expected if e[1] > logging.DEBUG
    ]


FIXED_QUIRKS: Sequence[tuple[str, str, Callable[[Outcome], bool], Callable[[Outcome], bool]]] = [
    (
        "untagged-without-filter-included",
        "BUG-228",
        lambda o: o.deleted == ["u"],
        lambda o: o.deleted == [] and not _calls_named(o, "instances.delete"),
    ),
    (
        "exception-untagged-not-protected",
        "BUG-228",
        lambda o: o.deleted == ["u"],
        lambda o: o.deleted == [] and not _calls_named(o, "instances.delete"),
    ),
    (
        "notags-all-keys-skips",
        "BUG-228",
        lambda o: o.deleted == ["b", "c", "u"],
        lambda o: o.deleted == ["b", "c"],
    ),
    (
        "notags-partial-keeps-deleting",
        "BUG-228",
        lambda o: o.deleted == ["a", "b", "c", "u"],
        lambda o: o.deleted == ["a", "b", "c"],
    ),
]


@pytest.mark.parametrize("fixed", FIXED_QUIRKS, ids=[f"{q[0]}-{q[1]}" for q in FIXED_QUIRKS])
def test_fixed_quirk_main_deletes_new_code_keeps(
    fixed: tuple[str, str, Callable[[Outcome], bool], Callable[[Outcome], bool]],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case_id, bug, old_check, new_check = fixed
    case = _find(case_id)
    assert DIVERGES[case_id][0] == bug
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_engine(case, monkeypatch, caplog, capsys)
    assert old_check(old), f"main does not show the old behaviour of {case_id}"
    assert new_check(new), f"new code does not show the fixed behaviour of {case_id} ({bug})"


def test_bug_358_leftover_disk_skip_is_debug_not_info(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = _find("disk-labels-exception-keeps")
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_engine(case, monkeypatch, caplog, capsys)
    assert old.logs != new.logs
    assert new.logs == expected_logs(old.logs, BUGS, RULES)
    assert [m for _, _, m in new.logs] == [m for _, _, m in old.logs]
    assert _after_listing(new.calls) == _after_listing(old.calls)
    assert [c for c in new.calls if c[0] == "instances.list"] == [
        c for c in old.calls if c[0] == "instances.list"
    ]
    assert new.stdout == old.stdout
    info = [m for _, level, m in new.logs if level == logging.INFO]
    assert not [m for m in info if m.startswith(("{", "namespace(", "Skipping leftover"))]
    dumps = [m for _, level, m in old.logs if level == logging.INFO]
    assert any(m.startswith("Skipping leftover") for m in dumps)
