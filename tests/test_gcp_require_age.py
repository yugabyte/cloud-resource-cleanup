"""BUG-124, BUG-062, BUG-183: a real GCP disk, IP or VM run needs a positive age.

A dry run does not. The factories raise `ValueError` naming the flag before any client is built
(so before any cloud call); the IP cleaner gates each address on its `creationTimestamp`.
"""

import datetime
import types
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import pytest

from crc.cli.inputs import read_options
from crc.cli.routes import gcp as routes
from crc.core.cleaner import Operation, RunContext
from crc.core.registry import get
from crc.gcp.ip import AddressView, IpCleaner
from tests.helpers_wc_gcp_engine import deleted_ids, run_engine

UTC = datetime.UTC
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
PROJECT = "proj-1"
BAD_AGES = [None, {}, {"days": 0}, {"hours": 0}, 0]


def stamp(**delta: float) -> str:
    return (NOW - datetime.timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%S.%f%z")


def context(
    options: Mapping[str, Any], *, dry_run: bool = False, op: Operation = Operation.DELETE
) -> RunContext:
    return RunContext(dry_run=dry_run, operation=op, options=dict(options), now=NOW)


class Untouched:
    """A client that fails the test as soon as the cleaner is built or used."""

    def __init__(self) -> None:
        pytest.fail("a client was built before the age was checked")


@pytest.fixture
def no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    for target in ("ip_cleaner.ComputeIpClient", "disk_cleaner.ComputeDiskClient"):
        monkeypatch.setattr(f"crc.gcp.{target}", Untouched)
    monkeypatch.setattr("crc.gcp.vm_cleaner.ComputeVmClient", Untouched)


class Client:
    """One fake for the three kinds: a zone or region holds `items`."""

    def __init__(self, items: Sequence[Any]) -> None:
        self.items = list(items)
        self.calls: list[tuple[str, ...]] = []

    def list_zones(self, project_id: str) -> Sequence[Mapping[str, str]]:
        return [{"name": "z"}]

    def list_regions(self, project_id: str) -> Sequence[str]:
        return ["r"]

    def list_instances(self, project_id: str, zone: str) -> Iterable[Any]:
        return self.items

    def list_disks(self, project_id: str, zone: str) -> Iterable[Any]:
        return self.items

    def list_addresses(self, project_id: str, region: str) -> Iterable[AddressView]:
        return self.items

    def delete_instance(self, project_id: str, zone: str, name: str) -> object:
        self.calls.append(("delete_instance", name))
        return {"name": "op"}

    def stop_instance(self, project_id: str, zone: str, name: str) -> object:
        self.calls.append(("stop_instance", name))
        return {"name": "op"}

    def delete_disk(self, project_id: str, zone: str, name: str) -> object:
        self.calls.append(("delete_disk", name))
        return {"name": "op"}

    def get_zone_operation(self, project_id: str, zone: str, name: str) -> Mapping[str, Any]:
        return {"status": "DONE"}

    def get_operation(
        self, project_id: str, scope_kind: str, scope: str, name: str
    ) -> Mapping[str, Any]:
        return {"status": "DONE"}

    def delete_address(self, project_id: str, region: str, name: str) -> Any:
        self.calls.append(("delete_address", name))
        return types.SimpleNamespace(result=lambda: None)

    def close(self) -> None:
        return None


def instance(name: str, *, days: float, labels: Mapping[str, str] | None = None) -> Any:
    return types.SimpleNamespace(
        name=name,
        status="RUNNING",
        labels=dict(labels or {}),
        creation_timestamp=stamp(days=days),
    )


def address(name: str, created: str, status: str = "RESERVED") -> Any:
    return types.SimpleNamespace(name=name, status=status, creation_timestamp=created)


@pytest.mark.parametrize("age", BAD_AGES)
@pytest.mark.usefixtures("no_client")
def test_bug_124_real_disk_run_needs_a_positive_detach_age(age: Any) -> None:
    options = {"project_id": PROJECT, "detach_age": age, "age": {"days": 5}}
    with pytest.raises(ValueError, match="detach_age"):
        get("gcp", "disk")(context(options))


@pytest.mark.usefixtures("no_client")
def test_bug_124_the_age_flag_is_no_substitute_for_detach_age() -> None:
    with pytest.raises(ValueError, match="detach_age"):
        get("gcp", "disk")(context({"project_id": PROJECT, "age": {"days": 5}}))


def test_bug_124_dry_run_and_positive_detach_age_still_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    disks = [
        types.SimpleNamespace(
            name="young", users=[], labels={}, last_detach_timestamp=stamp(hours=1)
        ),
        types.SimpleNamespace(name="old", users=[], labels={}, last_detach_timestamp=stamp(days=2)),
    ]
    client = Client(disks)
    monkeypatch.setattr("crc.gcp.disk_cleaner.ComputeDiskClient", lambda: client)
    options: dict[str, Any] = {"project_id": PROJECT}
    dry = get("gcp", "disk")(context(options, dry_run=True))
    assert deleted_ids(run_engine(dry, "disk", dry_run=True, now=NOW)) == ["young", "old"]
    assert client.calls == []
    options["detach_age"] = {"hours": 2}
    real = get("gcp", "disk")(context(options))
    assert deleted_ids(run_engine(real, "disk", now=NOW)) == ["old"]
    assert client.calls == [("delete_disk", "old")]


@pytest.mark.parametrize("age", BAD_AGES)
@pytest.mark.parametrize("op", [Operation.DELETE, Operation.STOP])
@pytest.mark.usefixtures("no_client")
def test_bug_183_real_vm_run_needs_a_positive_age(age: Any, op: Operation) -> None:
    options = {"project_id": PROJECT, "age": age, "custom_age_tag_key": "ttl"}
    with pytest.raises(ValueError, match=r"^age "):
        get("gcp", "vm")(context(options, op=op))


@pytest.mark.parametrize("op", [Operation.DELETE, Operation.STOP])
def test_bug_183_dry_run_without_age_still_lists_every_instance(
    monkeypatch: pytest.MonkeyPatch, op: Operation
) -> None:
    client = Client([instance("young", days=0.001), instance("old", days=30)])
    monkeypatch.setattr("crc.gcp.vm_cleaner.ComputeVmClient", lambda: client)
    built = get("gcp", "vm")(context({"project_id": PROJECT}, dry_run=True, op=op))
    outcome = run_engine(built, "vm", dry_run=True, now=NOW)
    assert deleted_ids(outcome) == ["young", "old"]
    assert client.calls == []


def test_bug_183_custom_age_label_still_replaces_the_positive_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Client(
        [
            instance("young", days=0.1),
            instance("long-ttl", days=3, labels={"ttl": "{'days': 5}"}),
            instance("short-ttl", days=0.5, labels={"ttl": "{'hours': 1}"}),
            instance("no-label", days=3),
        ]
    )
    monkeypatch.setattr("crc.gcp.vm_cleaner.ComputeVmClient", lambda: client)
    options = {"project_id": PROJECT, "age": {"days": 1}, "custom_age_tag_key": "ttl"}
    outcome = run_engine(get("gcp", "vm")(context(options)), "vm", now=NOW)
    assert deleted_ids(outcome) == ["short-ttl", "no-label"]


@pytest.mark.parametrize("age", BAD_AGES)
@pytest.mark.usefixtures("no_client")
def test_bug_062_real_ip_run_needs_a_positive_age(age: Any) -> None:
    with pytest.raises(ValueError, match=r"^age "):
        get("gcp", "ip")(context({"project_id": PROJECT, "age": age}))


def test_bug_062_dry_run_without_age_lists_every_reserved_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = Client([address("a", stamp(minutes=1)), address("b", stamp(days=40))])
    monkeypatch.setattr("crc.gcp.ip_cleaner.ComputeIpClient", lambda: client)
    built = get("gcp", "ip")(context({"project_id": PROJECT}, dry_run=True))
    assert deleted_ids(run_engine(built, "ip", dry_run=True, now=NOW)) == ["a", "b"]
    assert client.calls == []


def test_bug_062_only_addresses_old_enough_are_deleted(monkeypatch: pytest.MonkeyPatch) -> None:
    client = Client(
        [
            address("young", stamp(hours=3)),
            address("old", stamp(days=2)),
            address("old-in-use", stamp(days=9), status="IN_USE"),
            address("exactly-a-day", stamp(days=1)),
        ]
    )
    monkeypatch.setattr("crc.gcp.ip_cleaner.ComputeIpClient", lambda: client)
    options = {"project_id": PROJECT, "age": {"days": 1}}
    outcome = run_engine(get("gcp", "ip")(context(options)), "ip", now=NOW)
    assert deleted_ids(outcome) == ["old", "exactly-a-day"]
    assert client.calls == [("delete_address", "old"), ("delete_address", "exactly-a-day")]


def test_bug_062_hours_age_and_another_utc_offset() -> None:
    created = (NOW - datetime.timedelta(hours=7)).astimezone(
        datetime.timezone(datetime.timedelta(hours=-8))
    )
    client = Client([address("a", created.strftime("%Y-%m-%dT%H:%M:%S.%f%z"))])
    keep = IpCleaner(client, PROJECT, None, None, age={"hours": 8})
    assert deleted_ids(run_engine(keep, "ip", dry_run=True, now=NOW)) == []
    take = IpCleaner(client, PROJECT, None, None, age={"hours": 6})
    assert deleted_ids(run_engine(take, "ip", dry_run=True, now=NOW)) == ["a"]


@pytest.mark.parametrize("created", ["", "yesterday", "2026-02-01T00:00:00-08:00", None])
def test_bug_062_an_unreadable_creation_timestamp_keeps_the_address(created: Any) -> None:
    client = Client([address("a", created), address("b", stamp(days=3))])
    cleaner = IpCleaner(client, PROJECT, None, None, age={"days": 1})
    outcome = run_engine(cleaner, "ip", now=NOW)
    assert deleted_ids(outcome) == ["b"]
    assert client.calls == [("delete_address", "b")]


def test_bug_062_the_route_passes_age_only_when_given() -> None:
    def options(**flags: Any) -> dict[str, Any]:
        return routes.ROUTES["ip"].options(
            read_options({"cloud": "gcp", "project_id": "p", **flags})
        )

    assert options(age={"days": 1})["age"] == {"days": 1}
    assert "age" not in options()
