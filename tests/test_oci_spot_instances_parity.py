"""Parity: the registered OCI spot VM Cleaner, run through `crc.core.engine`, against main's
version (`tests/legacy/oci_spot_vm.txt`).

Both run against the same fake OCI SDK; results, log records, stdout and raised errors must match
for every case. The engine runs `plan()` then `apply()` (only `plan()` in a dry run), so all
attachment reads precede the terminate calls: the per-call-name sequences, the mutating sequence
and the summary lines are compared, and the log records as a multiset plus the order of the
plan, apply and summary groups.
"""

import datetime
import logging
import types
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import oci
import pytest
from oci.exceptions import ConnectTimeout, RequestException, ServiceError

import crc.oci.spot_vm.leftovers as leftovers_module
from crc.core.cleaner import Operation, RunContext
from crc.oci.spot_vm import build_spot_vm_cleaner
from tests.helpers_ad_spot_engine import run_through_engine
from tests.helpers_log_fix import expected_logs
from tests.log_fix_oci import RULES

BUGS = {"BUG-358"}
OWNERSHIP_READ = "list_volume_attachments_of_volume"
LATE = datetime.datetime(2026, 12, 31, tzinfo=datetime.UTC)
ORACLE = Path(__file__).parent / "legacy" / "oci_spot_vm.txt"
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical
NOW_UTC = NOW.astimezone(datetime.UTC)
HOME = "us-sanjose-1"
MUTATING = ("terminate_instance", "delete_volume")
READS = ("list_instances", "list_volume_attachments")
SUMMARY_PREFIXES = ("No OCI preemptible instances", "List of OCI ")
APPLY_PREFIXES = (
    "Terminating OCI preemptible",
    "Waiting for OCI instance",
    "Error polling OCI",
    "Timed out waiting",
    "Skipping leftover volume",
    "Failed to get volume",
    "Deleted leftover block volume",
    "Failed to delete leftover volume",
)


def inst(
    inst_id: str,
    name: str | None = None,
    hours: float | None = 100,
    **fields: Any,
) -> Any:
    """An instance; `fields` override state, tags, preemptible, defined or metadata."""
    created = None if hours is None else NOW_UTC - datetime.timedelta(hours=hours)
    tags = fields.get("tags", "default")
    return types.SimpleNamespace(
        id=inst_id,
        display_name=inst_id if name is None else name,
        lifecycle_state=fields.get("state", "RUNNING"),
        freeform_tags={"env": "ci"} if tags == "default" else tags,
        defined_tags=fields.get("defined") or {},
        metadata=fields.get("metadata") or {},
        preemptible_instance_config=object() if fields.get("preemptible", True) else None,
        time_created=created,
    )


def att(volume_id: Any, state: Any = "ATTACHED") -> Any:
    return types.SimpleNamespace(volume_id=volume_id, lifecycle_state=state)


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
    instances: Any = field(default_factory=list)
    attachments: Mapping[str, Any] = field(default_factory=dict)
    states: Mapping[str, Sequence[Any]] = field(default_factory=dict)
    volumes: Mapping[str, Any] = field(default_factory=dict)
    volume_created: Mapping[str, Any] = field(default_factory=dict)
    volume_attachment_errors: Mapping[str, Exception] = field(default_factory=dict)
    terminate_errors: Mapping[str, Exception] = field(default_factory=dict)
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


def custom_tags(ttl: str) -> dict[str, str]:
    return {"env": "ci", "ttl": ttl}


def with_volumes(**kwargs: Any) -> Mapping[str, Region]:
    defaults: dict[str, Any] = {
        "instances": [inst("a"), inst("b")],
        "attachments": {"a": [att("va1"), att("va2")], "b": [att("vb1")]},
        "states": {"a": ["TERMINATING", "TERMINATED"], "b": ["TERMINATED"]},
        "volumes": {"va1": {}, "va2": {}, "vb1": {}},
    }
    return one(**{**defaults, **kwargs})


def spot(**kwargs: Any) -> Mapping[str, Region]:
    defaults: dict[str, Any] = {
        "instances": [inst("a")],
        "attachments": {"a": [att("v1")]},
        "states": {"a": ["TERMINATED"]},
        "volumes": {"v1": {}},
    }
    return one(**{**defaults, **kwargs})


CASES = [
    Case("dry-run", one(instances=[inst("a"), inst("b", hours=1), inst("c")]), dry_run=True),
    Case("terminate-matching", one(instances=[inst("a"), inst("b", hours=1), inst("c")])),
    Case("two-regions", {"r1": Region(instances=[inst("a")]), "r2": Region(instances=[inst("b")])}),
    Case("empty-region", one()),
    Case("empty-region-dry-run", one(), dry_run=True),
    Case("no-regions", {}),
    Case("no-regions-dry-run", {}, dry_run=True),
    Case("nothing-matches", one(instances=[inst("a", hours=1), inst("b", tags={})])),
    Case(
        "state-filter",
        one(
            instances=[
                inst("a", state="STOPPED"),
                inst("b", state="TERMINATED"),
                inst("c", state="PROVISIONING"),
                inst("d", state="RUNNING "),
                inst("e", state="running"),
                inst("f"),
            ]
        ),
    ),
    Case("not-preemptible-skipped", one(instances=[inst("a", preemptible=False), inst("b")])),
    Case(
        "oke-freeform-tag-skipped",
        one(
            instances=[
                inst("a", tags={"env": "ci", "oke-cluster-id": "c1"}),
                inst("b", tags={"env": "ci", "x": "InstancePool/p"}),
                inst("c", tags={"env": "ci", "k": "instance_pool"}),
                inst("d", tags={"env": "ci", "k": "instance-pool"}),
                inst("e"),
            ]
        ),
    ),
    Case(
        "oke-defined-and-metadata-skipped",
        one(
            instances=[
                inst("a", defined={"Oracle-Tags": {"CreatedBy": "instancepool/pool-1"}}),
                inst("b", defined={"ns": "oke"}),
                inst("c", metadata={"oke-native-pod-networking": "true"}),
                inst("d", metadata="oke"),
                inst("e", defined={"ns": {"k": "v"}}, metadata={"k": "v"}),
            ]
        ),
    ),
    Case(
        "no-filter-includes-everything",
        one(instances=[inst("a", tags={}), inst("b")]),
        filter_tags={},
    ),
    Case("filter-none", one(instances=[inst("a", tags=None)]), filter_tags=None),
    Case("untagged-with-filter-skipped", one(instances=[inst("a", tags=None), inst("b", tags={})])),
    Case(
        "untagged-without-filter-included",
        one(instances=[inst("a", tags=None), inst("b", tags={})]),
        filter_tags={},
    ),
    Case(
        "filter-all-keys",
        one(
            instances=[
                inst("a", tags={"x": "1", "y": "2"}),
                inst("b", tags={"x": "1"}),
                inst("c", tags={"y": "2"}),
                inst("d", tags={"x": "1", "y": "0"}),
            ]
        ),
        filter_tags={"x": ["1"], "y": ["2"]},
    ),
    Case(
        "filter-several-values",
        one(instances=[inst(i, tags={"x": v}) for i, v in (("a", "1"), ("b", "2"), ("c", "3"))]),
        filter_tags={"x": ["1", "2"]},
    ),
    Case(
        "filter-empty-value-list-means-key-present",
        one(instances=[inst("a", tags={"x": "anything"}), inst("b", tags={"z": "1"})]),
        filter_tags={"x": []},
    ),
    Case("filter-value-case-sensitive", one(instances=[inst("a", tags={"env": "CI"})])),
    Case("filter-key-case-sensitive", one(instances=[inst("a", tags={"ENV": "ci"})])),
    Case(
        "filter-string-spec-is-substring-test",
        one(instances=[inst("a", tags={"env": "c"}), inst("b", tags={"env": ""}), inst("c")]),
        filter_tags={"env": "ci"},
    ),
    Case(
        "exception-any-key",
        one(instances=[inst("a", tags={"env": "ci", "keep": "1"}), inst("b")]),
        exception_tags={"keep": ["1"], "other": []},
    ),
    Case(
        "exception-empty-value-list",
        one(instances=[inst("a", tags={"env": "ci", "keep": "x"}), inst("b")]),
        exception_tags={"keep": []},
    ),
    Case(
        "exception-wins-over-filter",
        one(instances=[inst("a")]),
        exception_tags={"env": ["ci"]},
    ),
    Case(
        "notags-all-keys-skips",
        one(
            instances=[
                inst("a", tags={"env": "ci", "k1": "v", "k2": "v"}),
                inst("b", tags={"env": "ci", "k1": "v"}),
            ]
        ),
        notags={"k1": ["v"], "k2": []},
    ),
    Case(
        "notags-value-mismatch-not-skipped",
        one(instances=[inst("a", tags={"env": "ci", "k1": "other"})]),
        notags={"k1": ["v"]},
    ),
    Case("exception-none", one(instances=[inst("a")]), exception_tags=None),
    Case("notags-none", one(instances=[inst("a")]), notags=None),
    Case(
        "untagged-with-exception-and-no-filter",
        one(instances=[inst("a", tags=None)]),
        filter_tags={},
        exception_tags={"keep": ["1"]},
        notags={"k": ["v"]},
    ),
    Case("age-equal-hours", one(instances=[inst("a", hours=5)])),
    Case("age-just-under-hours", one(instances=[inst("a", hours=4.99)])),
    Case("age-just-over-hours", one(instances=[inst("a", hours=5.01)])),
    Case("age-days", one(instances=[inst("a", hours=48), inst("b", hours=47.9)]), age={"days": 2}),
    Case(
        "age-days-and-hours",
        one(instances=[inst("a", hours=30), inst("b", hours=29.9)]),
        age={"days": 1, "hours": 6},
    ),
    Case("age-int-days", one(instances=[inst("a", hours=25), inst("b", hours=23)]), age=1),
    Case("age-empty-is-rejected", one(instances=[inst("a")]), age={}),
    Case("age-none-is-rejected", one(instances=[inst("a")]), age=None),
    Case("age-zero-is-rejected", one(instances=[inst("a")]), age={"days": 0}),
    Case("age-unknown-keys-rejected", one(instances=[inst("a")]), age={"weeks": 1}),
    Case(
        "max-age-shortens",
        one(instances=[inst("a", hours=3), inst("b", hours=1)]),
        age={"days": 30},
        max_age="{'hours': 2}",
    ),
    Case("max-age-not-a-dict", one(instances=[inst("a", hours=3)]), age={"days": 1}, max_age="2"),
    Case("created-in-the-future", one(instances=[inst("a", hours=-10)])),
    Case(
        "naive-created-time",
        one(instances=[types.SimpleNamespace(**{**inst("a").__dict__, "time_created": NOW})]),
    ),
    Case("missing-created-time", one(instances=[inst("a", hours=None), inst("b")])),
    Case(
        "custom-age-tag-shortens",
        one(instances=[inst("a", hours=3, tags=custom_tags("{'hours': 2}")), inst("b", hours=3)]),
        age={"days": 5},
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-lengthens",
        one(instances=[inst("a", hours=30, tags=custom_tags("{'days': 3}"))]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-unparseable-keeps",
        one(instances=[inst("a", hours=300, tags=custom_tags("{'days': "))]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-empty-keeps",
        one(instances=[inst("a", hours=300, tags=custom_tags(""))]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-zero-keeps",
        one(instances=[inst("a", hours=300, tags=custom_tags("{'days': 0}"))]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-negative-keeps",
        one(instances=[inst("a", hours=300, tags=custom_tags("{'days': -1}"))]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-key-missing-on-instance",
        one(instances=[inst("a", hours=30)]),
        custom_age_tag_key="ttl",
    ),
    Case(
        "custom-age-tag-none-key",
        one(instances=[inst("a", hours=30, tags=custom_tags("{'days': 9}"))]),
        custom_age_tag_key=None,
    ),
    Case(
        "list-connection-error-skips-region",
        {"r1": Region(instances=conn_error()), "r2": Region(instances=[inst("c")])},
    ),
    Case(
        "list-request-error-skips-region",
        {"r1": Region(instances=request_error()), "r2": Region(instances=[inst("c")])},
    ),
    Case(
        "list-auth-error-skips-region",
        {
            "r1": Region(instances=service_error(401, "NotAuthenticated")),
            "r2": Region(instances=[inst("c")]),
        },
    ),
    Case(
        "client-creation-connection-error-skips-region",
        {"r1": Region(client_error=conn_error()), "r2": Region(instances=[inst("c")])},
    ),
    Case(
        "client-creation-unexpected-error-skips-region",
        {"r1": Region(client_error=ValueError("bad config")), "r2": Region(instances=[inst("c")])},
    ),
    Case(
        "list-unexpected-error-skips-region",
        {"r1": Region(instances=ValueError("boom")), "r2": Region(instances=[inst("c")])},
    ),
    Case("attachment-list-failure-skips-terminate", spot(attachments={"a": RuntimeError("down")})),
    Case(
        "attachment-list-failure-dry-run",
        spot(attachments={"a": service_error(500)}),
        dry_run=True,
    ),
    Case("no-attachments-no-wait", one(instances=[inst("a")], attachments={"a": []})),
    Case(
        "detached-attachments-ignored",
        one(
            instances=[inst("a")],
            attachments={
                "a": [att("v1", "detached"), att("v2", "DETACHING"), att("v3", None), att("v4")]
            },
            states={"a": ["TERMINATED"]},
            volumes={"v3": {}, "v4": {}},
        ),
    ),
    Case(
        "attachment-without-volume-id-ignored",
        one(
            instances=[inst("a")],
            attachments={"a": [att(None), att(""), att("v1")]},
            states={"a": ["TERMINATED"]},
            volumes={"v1": {}},
        ),
    ),
    Case("leftover-volumes-deleted", with_volumes()),
    Case("leftover-volumes-dry-run", with_volumes(), dry_run=True),
    Case(
        "leftover-volume-exception-tag-kept",
        with_volumes(volumes={"va1": {"keep": "1"}, "va2": {}, "vb1": {}}),
        exception_tags={"keep": ["1"]},
    ),
    Case(
        "leftover-volume-exception-tag-kept-dry-run",
        with_volumes(volumes={"va1": {"keep": "1"}, "va2": {}, "vb1": {}}),
        exception_tags={"keep": ["1"]},
        dry_run=True,
    ),
    Case(
        "leftover-volume-notags-kept",
        spot(volumes={"v1": {"k1": "v", "k2": "v"}}),
        notags={"k1": ["v"], "k2": ["v"]},
    ),
    Case("leftover-volume-untagged-deleted", spot(volumes={"v1": None})),
    Case(
        "leftover-volume-created-with-instance-deleted",
        spot(volume_created={"v1": NOW_UTC - datetime.timedelta(hours=100)}),
    ),
    Case(
        "leftover-volume-other-attachment-detached-deleted",
        spot(
            instances=[inst("a"), inst("x", preemptible=False)],
            attachments={"a": [att("v1")], "x": [att("v1", "DETACHED")]},
        ),
    ),
    Case("leftover-volume-get-error-kept", spot(volumes={"v1": RuntimeError("gone")})),
    Case(
        "leftover-volume-get-error-dry-run",
        spot(volumes={"v1": RuntimeError("gone")}),
        dry_run=True,
    ),
    Case(
        "leftover-volume-delete-error-swallowed", spot(delete_errors={"v1": RuntimeError("busy")})
    ),
    Case(
        "leftover-volume-delete-error-one-of-two",
        with_volumes(delete_errors={"va1": service_error(409, "Conflict")}),
    ),
    Case("terminate-error-swallowed", with_volumes(terminate_errors={"a": RuntimeError("busy")})),
    Case(
        "terminate-service-error-swallowed",
        with_volumes(terminate_errors={"a": service_error(409, "Conflict")}),
    ),
    Case(
        "terminate-error-in-dry-run-never-called",
        with_volumes(terminate_errors={"a": RuntimeError("busy")}),
        dry_run=True,
    ),
    Case(
        "wait-polls-until-terminated",
        spot(states={"a": ["RUNNING", "TERMINATING", "TERMINATING", "TERMINATED"]}),
    ),
    Case(
        "wait-instance-gone-404",
        spot(states={"a": [service_error(404, "NotAuthorizedOrNotFound")]}),
    ),
    Case(
        "wait-service-error-stops-wait", spot(states={"a": [service_error(500, "InternalError")]})
    ),
    Case("wait-unexpected-error-stops-wait", spot(states={"a": [RuntimeError("boom")]})),
    Case("wait-state-none-keeps-polling", spot(states={"a": [None, None, "TERMINATED"]})),
    Case("wait-lowercase-state", spot(states={"a": ["terminated"]})),
    Case("wait-times-out-then-deletes-volumes", spot(states={"a": ["TERMINATING"]})),
    Case(
        "terminate-error-then-other-region",
        {
            "r1": Region(
                instances=[inst("a")],
                attachments={"a": [att("v1")]},
                terminate_errors={"a": RuntimeError("busy")},
            ),
            "r2": Region(
                instances=[inst("b")],
                attachments={"b": [att("v2")]},
                states={"b": ["TERMINATED"]},
                volumes={"v2": {}},
            ),
        },
    ),
    Case(
        "unexpected-error-in-instance-is-isolated",
        one(instances=[inst("a"), inst("b", hours=None), inst("c")]),
    ),
    Case(
        "duplicate-display-names",
        one(instances=[inst("a", "same"), inst("b", "same"), inst("c", "same")]),
    ),
    Case(
        "display-name-none",
        one(instances=[types.SimpleNamespace(**{**inst("a").__dict__, "display_name": None})]),
    ),
    Case(
        "many-instances",
        {
            "r1": Region(instances=[inst(f"v{i}", hours=50 + i) for i in range(25)]),
            "r2": Region(instances=[inst(f"w{i}") for i in range(5)]),
        },
    ),
    Case("many-instances-dry-run", one(instances=[inst(f"v{i}") for i in range(25)]), dry_run=True),
    Case(
        "regions-connection-error-falls-back",
        one(instances=[inst("a")]),
        regions_error=conn_error(),
    ),
    Case(
        "regions-auth-error-falls-back",
        {HOME: Region(instances=[inst("a")])},
        regions_error=service_error(401, "NotAuthenticated"),
    ),
    Case(
        "regions-request-error-falls-back",
        {HOME: Region(instances=[inst("a")])},
        regions_error=request_error(),
    ),
    Case(
        "regions-unexpected-error-propagates",
        one(instances=[inst("a")]),
        regions_error=ValueError("boom"),
    ),
]


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0

    def read(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class FakeCompute:
    def __init__(self, world: "FakeOci", region: str) -> None:
        self.world = world
        self.region = region
        self.polls: dict[str, int] = {}

    def list_instances(self, compartment_id: str) -> Any:
        self.world.calls.append(("list_instances", self.region, compartment_id))
        outcome = self.world.region(self.region).instances
        if isinstance(outcome, Exception):
            raise outcome
        return types.SimpleNamespace(data=outcome)

    def list_volume_attachments(
        self, compartment_id: str, instance_id: str = "", volume_id: str = ""
    ) -> Any:
        if volume_id:
            self.world.calls.append((OWNERSHIP_READ, self.region, compartment_id, volume_id))
            region = self.world.region(self.region)
            if volume_id in region.volume_attachment_errors:
                raise region.volume_attachment_errors[volume_id]
            found = [
                types.SimpleNamespace(
                    volume_id=a.volume_id, lifecycle_state=a.lifecycle_state, instance_id=owner
                )
                for owner, listed in region.attachments.items()
                if not isinstance(listed, Exception)
                for a in listed
                if a.volume_id == volume_id
            ]
            return types.SimpleNamespace(data=found)
        self.world.calls.append(
            ("list_volume_attachments", self.region, compartment_id, instance_id)
        )
        outcome = self.world.region(self.region).attachments.get(instance_id, [])
        if isinstance(outcome, Exception):
            raise outcome
        return types.SimpleNamespace(data=outcome)

    def get_instance(self, instance_id: str) -> Any:
        self.world.calls.append(("get_instance", self.region, instance_id))
        script = self.world.region(self.region).states.get(instance_id, ["TERMINATED"])
        index = self.polls.get(instance_id, 0)
        self.polls[instance_id] = index + 1
        outcome = script[min(index, len(script) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return types.SimpleNamespace(data=types.SimpleNamespace(lifecycle_state=outcome))

    def terminate_instance(self, instance_id: str, preserve_boot_volume: bool = True) -> None:
        self.world.calls.append(
            ("terminate_instance", self.region, instance_id, preserve_boot_volume)
        )
        error = self.world.region(self.region).terminate_errors.get(instance_id)
        if error is not None:
            raise error


class FakeBlock:
    def __init__(self, world: "FakeOci", region: str) -> None:
        self.world = world
        self.region = region

    def get_volume(self, volume_id: str) -> Any:
        self.world.calls.append(("get_volume", self.region, volume_id))
        outcome = self.world.region(self.region).volumes.get(volume_id, {})
        if isinstance(outcome, Exception):
            raise outcome
        created = self.world.region(self.region).volume_created.get(volume_id, LATE)
        return types.SimpleNamespace(
            data=types.SimpleNamespace(freeform_tags=outcome, time_created=created)
        )

    def delete_volume(self, volume_id: str) -> None:
        self.world.calls.append(("delete_volume", self.region, volume_id))
        error = self.world.region(self.region).delete_errors.get(volume_id)
        if error is not None:
            raise error


class FakeIdentity:
    def __init__(self, world: "FakeOci", region: str) -> None:
        self.world = world
        self.region = region

    def list_region_subscriptions(self, tenancy_id: str) -> Any:
        self.world.calls.append(("list_region_subscriptions", self.region, tenancy_id))
        if self.world.case.regions_error is not None:
            raise self.world.case.regions_error
        names = [types.SimpleNamespace(region_name=r) for r in self.world.case.regions]
        return types.SimpleNamespace(data=names)


class FakeOci:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []
        self.time = FakeTime()

    def region(self, name: str) -> Region:
        return self.case.regions[name]

    def compute(self, config: Mapping[str, Any]) -> FakeCompute:
        self.calls.append(("ComputeClient", config["region"]))
        client_error = self.region(config["region"]).client_error
        if client_error is not None:
            raise client_error
        return FakeCompute(self, config["region"])

    def block(self, config: Mapping[str, Any]) -> FakeBlock:
        self.calls.append(("BlockstorageClient", config["region"]))
        return FakeBlock(self, config["region"])

    def identity(self, config: Mapping[str, Any]) -> FakeIdentity:
        self.calls.append(("IdentityClient", config["region"]))
        return FakeIdentity(self, config["region"])

    def paginate(self, fn: Any, **kwargs: Any) -> Any:
        self.calls.append(("list_call_get_all_results", fn.__name__))
        return fn(**kwargs)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(oci.core, "ComputeClient", self.compute)
        monkeypatch.setattr(oci.core, "BlockstorageClient", self.block)
        monkeypatch.setattr(oci.identity, "IdentityClient", self.identity)
        monkeypatch.setattr(oci.pagination, "list_call_get_all_results", self.paginate)
        monkeypatch.setattr(
            leftovers_module,
            "time",
            types.SimpleNamespace(monotonic=self.time.read, sleep=self.time.sleep),
        )


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    acted: list[Any] | None
    volumes: list[Any] | None
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


class _FakeDatetime:
    @staticmethod
    def now(tz: Any = None) -> datetime.datetime:
        return NOW.astimezone(tz)


def _load_oracle(world: FakeOci) -> Any:
    module = types.ModuleType("legacy_oci_spot_vm")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    module.__dict__["datetime"] = _FakeDatetime
    module.__dict__["time"] = types.SimpleNamespace(time=world.time.read, sleep=world.time.sleep)
    return module


def _legacy(case: Case, world: FakeOci) -> Any:
    return _load_oracle(world).SpotVM(
        case.dry_run,
        case.filter_tags,
        case.exception_tags,
        case.age,
        case.custom_age_tag_key,
        case.notags,
    )


def _prepare(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeOci:
    world = FakeOci(case)
    world.install(monkeypatch)
    monkeypatch.setenv("OCI_PRIVATE_KEY_CONTENT", "not-a-key")
    monkeypatch.delenv("OCI_PRIVATE_KEY_FILE", raising=False)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    if case.max_age is None:
        monkeypatch.delenv("MAX_AGE", raising=False)
    else:
        monkeypatch.setenv("MAX_AGE", case.max_age)
    caplog.clear()
    capsys.readouterr()
    return world


def _run(
    build: Callable[[Case, FakeOci], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    world = _prepare(case, monkeypatch, caplog, capsys)
    error: tuple[str, str] | None = None
    spot_vm = None
    try:
        spot_vm = build(case, world)
        spot_vm.delete()
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    acted = None if spot_vm is None else list(spot_vm.get_deleted)
    volumes = None if spot_vm is None else list(spot_vm.get_deleted_volumes)
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(world.calls, acted, volumes, logs, capsys.readouterr().out, error)


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


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
    acted: list[str] | None = None
    volumes: list[str] | None = None
    try:
        outcome = run_through_engine("oci", "spot_instances", build_spot_vm_cleaner, context)
        done = outcome.result if outcome.result is not None else outcome.plan
        if outcome.result is None:
            acted = [c.name for c in outcome.plan.candidates]
        else:
            acted = list(outcome.result.succeeded)
        volumes = list(done.related["Disk"])
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        error = (type(e).__name__, str(e))
    logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    return Outcome(world.calls, acted, volumes, logs, capsys.readouterr().out, error)


def _without_ownership_reads(calls: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """BUG-255 adds one read per leftover volume (and its pager call); drop them to compare the
    rest of the calls with the oracle."""
    kept: list[tuple[Any, ...]] = []
    for index, call in enumerate(calls):
        following = calls[index + 1][0] if index + 1 < len(calls) else None
        if call[0] == OWNERSHIP_READ or (
            call[0] == "list_call_get_all_results" and following == OWNERSHIP_READ
        ):
            continue
        kept.append(call)
    return kept


def _of(calls: list[tuple[Any, ...]], names: Sequence[str]) -> list[tuple[Any, ...]]:
    return [c for c in calls if c[0] in names]


def _is_summary(log: tuple[str, int, str]) -> bool:
    return log[2].startswith(SUMMARY_PREFIXES)


def _is_apply(log: tuple[str, int, str]) -> bool:
    return log[2].startswith(APPLY_PREFIXES) or "not TERMINATED yet" in log[2]


def _is_error_processing(log: tuple[str, int, str]) -> bool:
    return log[2].startswith("Error processing OCI preemptible instance")


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
        assert _of(new.calls, MUTATING) == []
        return
    assert new.acted == old.acted
    assert new.volumes == old.volumes
    new_calls = _without_ownership_reads(new.calls)
    names = {c[0] for c in old.calls} | {c[0] for c in new_calls}
    for name in sorted(names):
        assert _of(new_calls, [name]) == _of(old.calls, [name]), name
    assert _of(new.calls, MUTATING) == _of(old.calls, MUTATING)
    old_logs = expected_logs(old.logs, BUGS, RULES)
    assert Counter(new.logs) == Counter(old_logs)
    assert [log for log in new.logs if _is_summary(log)] == [
        log for log in old_logs if _is_summary(log)
    ]
    assert new.logs[len(new.logs) - len([log for log in old_logs if _is_summary(log)]) :] == [
        log for log in old_logs if _is_summary(log)
    ]
    quiet = [log for log in old_logs if not _is_summary(log) and not _is_error_processing(log)]
    plan_group = [log for log in quiet if case.dry_run or not _is_apply(log)]
    apply_group = [] if case.dry_run else [log for log in quiet if _is_apply(log)]
    new_quiet = [log for log in new.logs if not _is_summary(log) and not _is_error_processing(log)]
    assert new_quiet == plan_group + apply_group


def _sequence_legacy(case: Case, world: FakeOci) -> Any:
    spot_vm = _legacy(case, world)
    spot_vm.delete()
    spot_vm.delete()
    return list(spot_vm.get_deleted), list(spot_vm.get_deleted_volumes)


def _sequence_engine(
    case: Case, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> Any:
    """Two engine runs; the lists accumulate across them as the adapter's `get_deleted` did."""
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
    acted: list[Any] = []
    volumes: list[Any] = []
    for _ in range(2):
        outcome = run_through_engine("oci", "spot_instances", build_spot_vm_cleaner, context)
        if outcome.result is None:
            acted += [c.name for c in outcome.plan.candidates]
            volumes += outcome.plan.related["Disk"]
        else:
            acted += outcome.result.succeeded
            volumes += outcome.result.related["Disk"]
    return acted, volumes


@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "dry-run"])
def test_repeated_delete_accumulates_as_main(
    dry_run: bool,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = Case("sequence", with_volumes(), dry_run=dry_run)
    world = _prepare(case, monkeypatch, caplog, capsys)
    old = _sequence_legacy(case, world)
    old_calls, old_logs = list(world.calls), [(r.levelno, r.getMessage()) for r in caplog.records]
    world = _prepare(case, monkeypatch, caplog, capsys)
    new = _sequence_engine(case, caplog, capsys)
    assert new == old
    names = {c[0] for c in old_calls} | {c[0] for c in _without_ownership_reads(world.calls)}
    for name in sorted(n for n in names if n.startswith("list_") or n in MUTATING):
        new_of = _of(_without_ownership_reads(world.calls), [name])
        assert Counter(new_of) == Counter(_of(old_calls, [name])), name
    assert _of(world.calls, MUTATING) == _of(old_calls, MUTATING)
    new_logs = [(r.name, r.levelno, r.getMessage()) for r in caplog.records]
    old_expected = expected_logs([("root", *entry) for entry in old_logs], BUGS, RULES)
    assert {m for m in new_logs if not _is_summary(m)} == {
        m for m in old_expected if not _is_summary(m)
    }
    assert len([m for m in new_logs if _is_summary(m)]) == len(
        [m for m in old_expected if _is_summary(m)]
    )


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_engine_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The engine run ends the same way as main: same error, stdout, resources and mutations."""
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    assert new.error == old.error
    assert new.stdout == old.stdout
    if old.error is None:
        assert new.acted == old.acted
        assert new.volumes == old.volumes
        assert _of(new.calls, MUTATING) == _of(old.calls, MUTATING)
    else:
        assert _of(new.calls, MUTATING) == []


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("regions-auth-error-falls-back", lambda o: o.acted == ["a"]),
    ("regions-unexpected-error-propagates", lambda o: o.error is not None),
    ("list-auth-error-skips-region", lambda o: o.acted == ["c"] and o.error is None),
    ("list-unexpected-error-skips-region", lambda o: o.acted == ["c"] and o.error is None),
    ("age-empty-is-rejected", lambda o: o.error is not None and o.error[0] == "ValueError"),
    ("max-age-shortens", lambda o: o.acted == ["a"]),
    ("max-age-not-a-dict", lambda o: o.acted == []),
    ("custom-age-tag-unparseable-keeps", lambda o: o.acted == []),
    ("custom-age-tag-empty-keeps", lambda o: o.acted == []),
    ("custom-age-tag-zero-keeps", lambda o: o.acted == []),
    ("untagged-with-filter-skipped", lambda o: o.acted == []),
    ("untagged-without-filter-included", lambda o: o.acted == ["a", "b"]),
    ("filter-all-keys", lambda o: o.acted == ["a"]),
    ("filter-string-spec-is-substring-test", lambda o: o.acted == ["a", "b", "c"]),
    ("exception-wins-over-filter", lambda o: o.acted == []),
    ("state-filter", lambda o: o.acted == ["a", "f"]),
    ("not-preemptible-skipped", lambda o: o.acted == ["b"]),
    ("oke-freeform-tag-skipped", lambda o: o.acted == ["e"]),
    ("oke-defined-and-metadata-skipped", lambda o: o.acted == ["b", "d", "e"]),
    ("attachment-list-failure-skips-terminate", lambda o: o.acted == []),
    ("terminate-error-swallowed", lambda o: o.error is None and o.acted == ["b"]),
    ("leftover-volume-delete-error-swallowed", lambda o: o.acted == ["a"] and o.volumes == []),
    ("leftover-volume-get-error-kept", lambda o: o.acted == ["a"] and o.volumes == []),
    ("leftover-volumes-deleted", lambda o: o.volumes == ["va1", "va2", "vb1"]),
    ("wait-times-out-then-deletes-volumes", lambda o: o.volumes == ["v1"]),
    ("unexpected-error-in-instance-is-isolated", lambda o: o.acted == ["a", "c"]),
    ("duplicate-display-names", lambda o: o.acted == ["same", "same", "same"]),
    ("display-name-none", lambda o: o.acted == [None]),
]


@pytest.mark.parametrize("quirk", QUIRKS, ids=[q[0] for q in QUIRKS])
def test_quirk_is_preserved(
    quirk: tuple[str, Callable[[Outcome], bool]],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case_id, check = quirk
    case = next(c for c in CASES if c.id == case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    assert check(old), "oracle no longer shows the quirk"
    assert check(new)


def test_spot_vm_diverges_from_oracle(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """BUG-358: new logs differ from the oracle by a level only."""
    changed = 0
    for case in CASES:
        old = _run(_legacy, case, monkeypatch, caplog, capsys)
        new = _run_cleaner(case, monkeypatch, caplog, capsys)
        if Counter(new.logs) == Counter(old.logs):
            continue
        changed += 1
        assert Counter(log[2] for log in new.logs) == Counter(log[2] for log in old.logs)
        assert _of(new.calls, MUTATING) == _of(old.calls, MUTATING)
        assert {log[1] for log in new.logs if log not in old.logs} == {logging.DEBUG}
    assert changed > 0


BUG_255 = {
    "leftover-volume-older-than-instance": (
        spot(volume_created={"v1": NOW_UTC - datetime.timedelta(hours=200)}),
        "created before the instance",
    ),
    "leftover-volume-creation-time-missing": (
        spot(volume_created={"v1": None}),
        "creation time unknown",
    ),
    "leftover-volume-attached-to-other-instance": (
        spot(
            instances=[inst("a"), inst("x", preemptible=False)],
            attachments={"a": [att("v1")], "x": [att("v1")]},
        ),
        "attached to another instance",
    ),
    "leftover-volume-attachment-read-fails": (
        spot(volume_attachment_errors={"v1": RuntimeError("down")}),
        "attachment list failed",
    ),
}


@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "dry-run"])
@pytest.mark.parametrize("case_id", sorted(BUG_255))
def test_bug_255_volume_not_owned_is_kept(
    case_id: str,
    dry_run: bool,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """BUG-255 diverges from the oracle: the instance is still terminated, the volume is kept."""
    regions, reason = BUG_255[case_id]
    case = Case(case_id, regions, dry_run=dry_run)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    for outcome in (_run_cleaner(case, monkeypatch, caplog, capsys),):
        assert old.volumes == ["v1"], "oracle no longer shows the bug"
        assert outcome.error is None
        assert outcome.acted == ["a"]
        assert outcome.volumes == []
        assert ("delete_volume", "r1", "v1") not in outcome.calls
        assert [c for c in outcome.calls if c[0] == "terminate_instance"] == (
            [] if dry_run else [("terminate_instance", "r1", "a", False)]
        )
        assert any(
            reason in message and level == logging.DEBUG for _, level, message in outcome.logs
        )
        assert not any(message.startswith("Deleted leftover") for _, _, message in outcome.logs)
