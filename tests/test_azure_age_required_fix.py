"""BUG-075 (IP age gate), BUG-112 (NIC age gate), BUG-173 (disk age required).

A real run of the Azure disk, IP and NIC cleaners needs a positive `--age`; a dry run does not.
IP and NIC creation times come from the resource management client (`expand="createdTime"`),
read once per run and resource type; a resource without one, or any resource when the lookup
fails, is kept.
"""

import datetime
import types
from typing import Any

import pytest

from crc.cli.routes import azu as azu_routes
from tests.helpers_azure_disk import FakeAzure as DiskAzure
from tests.helpers_azure_disk import ago as disk_ago
from tests.helpers_azure_disk import disk
from tests.helpers_azure_ip import FakeAzure as IpAzure
from tests.helpers_azure_ip import ip
from tests.helpers_azure_nic import FakeAzure as NicAzure
from tests.helpers_azure_nic import nic
from tests.helpers_wc_azu_engine import candidate_ids, engine_run

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
ROOT = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Network"
TAGS = {"env": "ci"}
FILTER = {"env": ["ci"]}


def _ago(**delta: int) -> datetime.datetime:
    return NOW - datetime.timedelta(**delta)


class FakeResources:
    def __init__(self, items: list[Any], error: Exception | None = None) -> None:
        self.items = items
        self.error = error
        self.calls: list[tuple[Any, ...]] = []

    @property
    def resources(self) -> "FakeResources":
        return self

    def list_by_resource_group(self, group: str, *, filter: str, expand: str) -> list[Any]:
        self.calls.append((group, filter, expand))
        if self.error is not None:
            raise self.error
        return self.items


def _resource(resource_id: str, created: datetime.datetime | None) -> types.SimpleNamespace:
    return types.SimpleNamespace(id=resource_id, created_time=created)


def _install_resources(monkeypatch: pytest.MonkeyPatch, resources: FakeResources) -> None:
    monkeypatch.setattr(
        "crc.azu._base.ResourceManagementClient", lambda credential, subscription: resources
    )


def _ip_item(name: str, resource_id: str | None = None) -> types.SimpleNamespace:
    item = ip(name, TAGS)
    item.id = resource_id if resource_id is not None else f"{ROOT}/publicIPAddresses/{name}"
    return item


def _nic_item(name: str, resource_id: str | None = None) -> types.SimpleNamespace:
    item = nic(name)
    item.id = resource_id if resource_id is not None else f"{ROOT}/networkInterfaces/{name}"
    return item


def _ip_options(**extra: Any) -> dict[str, Any]:
    return {"resource_group": "rg", "filter_tags": FILTER, **extra}


def _nic_options(**extra: Any) -> dict[str, Any]:
    return {"resource_group": "rg", "name_regex": ["nic"], **extra}


@pytest.mark.parametrize("age", [None, {}, {"days": 0}, {"hours": 0}, {"weeks": 1}])
def test_bug_075_real_ip_run_without_a_positive_age_raises_before_any_call(
    monkeypatch: pytest.MonkeyPatch, age: Any
) -> None:
    azure = IpAzure([_ip_item("a")])
    azure.install(monkeypatch)
    options = _ip_options() if age is None else _ip_options(age=age)
    with pytest.raises(ValueError, match="--age"):
        engine_run("ip", options, dry_run=False, now=NOW)
    assert azure.calls == []


@pytest.mark.parametrize("age", [None, {}, {"days": 0}])
def test_bug_112_real_nic_run_without_a_positive_age_raises_before_any_call(
    monkeypatch: pytest.MonkeyPatch, age: Any
) -> None:
    azure = NicAzure([_nic_item("nic-a")])
    azure.install(monkeypatch)
    options = _nic_options() if age is None else _nic_options(age=age)
    with pytest.raises(ValueError, match="--age"):
        engine_run("nic", options, dry_run=False, now=NOW)
    assert azure.calls == []


@pytest.mark.parametrize("age", [None, {}, {"days": 0}, {"hours": 0}])
def test_bug_173_real_disk_run_without_a_positive_age_raises_before_any_call(
    monkeypatch: pytest.MonkeyPatch, age: Any
) -> None:
    azure = DiskAzure([disk("a", created=disk_ago(days=9))])
    azure.install(monkeypatch)
    options: dict[str, Any] = {"resource_group": "rg", "filter_tags": FILTER}
    if age is not None:
        options["age"] = age
    with pytest.raises(ValueError, match="--age"):
        engine_run("disk", options, dry_run=False, now=NOW)
    assert azure.calls == []


def test_dry_runs_work_without_an_age(monkeypatch: pytest.MonkeyPatch) -> None:
    ips = IpAzure([_ip_item("a")])
    ips.install(monkeypatch)
    resources = FakeResources([])
    _install_resources(monkeypatch, resources)
    assert candidate_ids(engine_run("ip", _ip_options(), dry_run=True, now=NOW)) == ["a"]
    nics = NicAzure([_nic_item("nic-a")])
    nics.install(monkeypatch)
    assert candidate_ids(engine_run("nic", _nic_options(), dry_run=True, now=NOW)) == ["nic-a"]
    assert resources.calls == []


def test_bug_075_ip_age_gate_deletes_old_and_keeps_young_unknown_and_unlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [
        _ip_item("old"),
        _ip_item("young"),
        _ip_item("unknown"),
        _ip_item("unlisted"),
        _ip_item("mixed", f"{ROOT.upper()}/PUBLICIPADDRESSES/MIXED"),
    ]
    azure = IpAzure(items)
    azure.install(monkeypatch)
    resources = FakeResources(
        [
            _resource(f"{ROOT}/publicIPAddresses/old", _ago(days=3)),
            _resource(f"{ROOT}/publicIPAddresses/young", _ago(hours=2)),
            _resource(f"{ROOT}/publicIPAddresses/unknown", None),
            _resource(f"{ROOT}/publicIPAddresses/mixed", _ago(days=5)),
        ]
    )
    _install_resources(monkeypatch, resources)
    outcome = engine_run("ip", _ip_options(age={"days": 1}), dry_run=False, now=NOW)
    assert candidate_ids(outcome) == ["old", "mixed"]
    assert [c[2] for c in azure.calls if c[0] == "begin_delete"] == ["old", "mixed"]
    assert resources.calls == [
        ("rg", "resourceType eq 'Microsoft.Network/publicIPAddresses'", "createdTime")
    ]
    assert {s.id: s.reason for s in outcome.plan.skipped} == {
        "young": "not older than the age threshold",
        "unknown": "no creation time",
        "unlisted": "no creation time",
    }


def test_bug_075_ip_lookup_failure_keeps_every_address(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = IpAzure([_ip_item("a"), _ip_item("b")])
    azure.install(monkeypatch)
    resources = FakeResources([], error=RuntimeError("arm down"))
    _install_resources(monkeypatch, resources)
    outcome = engine_run("ip", _ip_options(age={"days": 1}), dry_run=False, now=NOW)
    assert candidate_ids(outcome) == []
    assert [c for c in azure.calls if c[0] == "begin_delete"] == []
    assert {s.reason for s in outcome.plan.skipped} == {"creation time lookup failed"}
    assert len(resources.calls) == 1


def test_bug_075_dry_run_with_an_age_applies_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = IpAzure([_ip_item("old"), _ip_item("young")])
    azure.install(monkeypatch)
    _install_resources(
        monkeypatch,
        FakeResources(
            [
                _resource(f"{ROOT}/publicIPAddresses/old", _ago(days=3)),
                _resource(f"{ROOT}/publicIPAddresses/young", _ago(hours=1)),
            ]
        ),
    )
    outcome = engine_run("ip", _ip_options(age={"hours": 6}), dry_run=True, now=NOW)
    assert candidate_ids(outcome) == ["old"]


def test_bug_112_nic_age_gate_deletes_old_and_keeps_young_unknown_and_unlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    items = [
        _nic_item("nic-old"),
        _nic_item("nic-young"),
        _nic_item("nic-unknown"),
        _nic_item("nic-unlisted"),
        _nic_item("nic-mixed", f"{ROOT.lower()}/networkinterfaces/NIC-MIXED"),
    ]
    azure = NicAzure(items)
    azure.install(monkeypatch)
    resources = FakeResources(
        [
            _resource(f"{ROOT}/networkInterfaces/nic-old", _ago(days=3)),
            _resource(f"{ROOT}/networkInterfaces/nic-young", _ago(hours=2)),
            _resource(f"{ROOT}/networkInterfaces/nic-unknown", None),
            _resource(f"{ROOT}/networkInterfaces/NIC-mixed", _ago(days=5)),
        ]
    )
    _install_resources(monkeypatch, resources)
    outcome = engine_run("nic", _nic_options(age={"days": 1}), dry_run=False, now=NOW)
    assert candidate_ids(outcome) == ["nic-old", "nic-mixed"]
    assert resources.calls == [
        ("rg", "resourceType eq 'Microsoft.Network/networkInterfaces'", "createdTime")
    ]
    assert {s.id: s.reason for s in outcome.plan.skipped} == {
        "nic-young": "not older than the age threshold",
        "nic-unknown": "no creation time",
        "nic-unlisted": "no creation time",
    }


def test_bug_112_nic_lookup_failure_keeps_every_nic(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = NicAzure([_nic_item("nic-a")])
    azure.install(monkeypatch)
    _install_resources(monkeypatch, FakeResources([], error=RuntimeError("arm down")))
    outcome = engine_run("nic", _nic_options(age={"days": 1}), dry_run=False, now=NOW)
    assert candidate_ids(outcome) == []
    assert [c for c in azure.calls if c[0] == "begin_delete"] == []
    assert {s.reason for s in outcome.plan.skipped} == {"creation time lookup failed"}


def test_bug_173_disk_age_still_gates_a_real_run(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = DiskAzure([disk("old", TAGS, disk_ago(days=3)), disk("young", TAGS, disk_ago(hours=1))])
    azure.install(monkeypatch)
    options = {"resource_group": "rg", "filter_tags": FILTER, "age": {"days": 1}}
    assert candidate_ids(engine_run("disk", options, dry_run=False, now=NOW)) == ["old"]


def test_routes_pass_the_age_option_for_ip_and_nic() -> None:
    unset = ("exception_regex", "filter_tags", "exception_tags", "notags")
    options: Any = types.SimpleNamespace(
        age={"days": 1}, resource_group="rg", name_regex=["n"], **dict.fromkeys(unset)
    )
    assert (azu_routes._ip(options)["age"], azu_routes._nic(options)["age"]) == ({"days": 1},) * 2
