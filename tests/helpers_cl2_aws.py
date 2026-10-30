"""Fakes and the frozen `main` oracle for `tests/test_cli_bridge_aws.py`.

Self-contained copy of the helpers that test used to import from `tests/test_cli_crc_parity.py`,
so it no longer depends on a module that imports the legacy CLI code. The legacy side is
`tests/helpers_oracle.load_legacy_crc()`: `main`'s CLI driving `main`'s adapters.
"""

import itertools
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

import pytest

from tests.helpers_oracle import load_legacy_crc

FULL = {
    "get_deleted": ["a", "b"],
    "get_stopped": ["a"],
    "get_deleted_nic": ["n1"],
    "get_deleted_disks": ["d1", "d2"],
    "get_deleted_volumes": ["v1"],
    "volumes_to_delete": ["v1", "v2"],
}
EMPTY: dict[str, Any] = {key: [] for key in FULL}
SLACK_MODES = ["off", "on", "post_fail", "no_channel"]
INFLUX_MODES = ["off", "on", "on_suffix", "api_error", "write_error", "no_conn"]


@dataclass
class Scenario:
    values: dict[str, Any]
    dry_run: bool = False
    slack: str = "on"
    influx: str = "on"
    delete_error: Exception | None = None
    scan_supported: bool = True
    resource_group: str | None = "rg"
    project_id: str | None = "proj"
    slack_users: dict[str, str] = field(default_factory=lambda: {"alice@yugabyte.com": "U1"})
    slack_groups: list[dict[str, str]] = field(
        default_factory=lambda: [{"handle": "team", "id": "G1"}]
    )
    groups_fail: bool = False
    open_fail: bool = False


class Logs:
    def __init__(self) -> None:
        self.adapters: list[Any] = []
        self.reads: list[Any] = []
        self.slack: list[Any] = []
        self.influx: list[Any] = []


def make_fake(name: str, scenario: Scenario, logs: Logs) -> type:
    def init(self: Any, *args: Any, **kwargs: Any) -> None:
        logs.adapters.append(("new", name, args, kwargs))

    def delete(self: Any, *args: Any) -> None:
        logs.adapters.append((name, "delete", args))
        if scenario.delete_error is not None:
            raise scenario.delete_error

    def stop(self: Any) -> None:
        logs.adapters.append((name, "stop"))

    def find(self: Any, key: str, value: str) -> list[dict[str, str]]:
        logs.adapters.append((name, "find_instances_with_tag", (key, value)))
        return [{"id": "i-1", "name": "vm-1", "region": "r"}]

    def getter(attr: str) -> Callable[[Any], Any]:
        def read(self: Any) -> Any:
            logs.reads.append((name, attr))
            return scenario.values[attr]

        return read

    body: dict[str, Any] = {"__init__": init, "delete": delete, "stop": stop}
    if scenario.scan_supported:
        body["find_instances_with_tag"] = find
    for attr in FULL:
        body[attr] = property(getter(attr))
    return type(name, (), body)


class FakeSlack:
    def __init__(self, scenario: Scenario, logs: Logs) -> None:
        self.scenario, self.logs = scenario, logs

    def chat_postMessage(self, **kwargs: Any) -> None:
        self.logs.slack.append(("chat_postMessage", kwargs))
        if self.scenario.slack == "post_fail":
            raise RuntimeError("slack down")

    def users_lookupByEmail(self, **kwargs: Any) -> dict[str, Any]:
        self.logs.slack.append(("users_lookupByEmail", kwargs))
        return {"user": {"id": self.scenario.slack_users[kwargs["email"]]}}

    def usergroups_list(self) -> dict[str, Any]:
        self.logs.slack.append(("usergroups_list", {}))
        if self.scenario.groups_fail:
            raise RuntimeError("groups down")
        return {"usergroups": self.scenario.slack_groups}

    def conversations_open(self, **kwargs: Any) -> dict[str, Any]:
        self.logs.slack.append(("conversations_open", kwargs))
        if self.scenario.open_fail:
            raise RuntimeError("open down")
        return {"channel": {"id": "D-" + kwargs["users"][0]}}


class FakeWriter:
    def __init__(self, scenario: Scenario, logs: Logs) -> None:
        self.scenario, self.logs = scenario, logs

    def write(self, *args: Any, **kwargs: Any) -> None:
        record: Any = kwargs.get("record")
        shown = record.to_line_protocol() if hasattr(record, "to_line_protocol") else record
        self.logs.influx.append(("write", args, {**kwargs, "record": shown}))
        if self.scenario.influx == "write_error":
            raise OSError("write down")


class FakeInflux:
    def __init__(self, scenario: Scenario, logs: Logs) -> None:
        self.scenario, self.logs = scenario, logs

    def write_api(self, **kwargs: Any) -> FakeWriter:
        self.logs.influx.append(("write_api", kwargs))
        if self.scenario.influx == "api_error":
            raise OSError("api down")
        return FakeWriter(self.scenario, self.logs)


@dataclass
class Case:
    name: str
    cloud: str
    act: Callable[[Any], Any]
    scenario: Scenario
    constructor_error: bool = False


@pytest.fixture(scope="module")
def oracle() -> ModuleType:
    return load_legacy_crc("legacy_crc_main_cl2_aws")


def build(crc_class: Any, case: Case, logs: Logs) -> Any:
    scenario = case.scenario
    slack = FakeSlack(scenario, logs) if scenario.slack != "off" else None
    influx = FakeInflux(scenario, logs) if scenario.influx != "off" else None
    conn: dict[str, Any] | None = {"bucket": "bkt"}
    if scenario.influx == "on_suffix":
        conn = {"bucket": "bkt", "resource_suffix": "sfx"}
    elif scenario.influx == "no_conn":
        conn = None
    return crc_class(
        case.cloud,
        scenario.dry_run,
        {"owner": ["x"]},
        slack,
        influx,
        project_id=scenario.project_id,
        resource_group=scenario.resource_group,
        slack_channel=None if scenario.slack == "no_channel" else "chan",
        influxdb_conn=conn,
    )


def scenario_grid(values: dict[str, Any], **overrides: Any) -> Iterator[tuple[str, Scenario]]:
    for dry, slack, influx in itertools.product([False, True], SLACK_MODES, INFLUX_MODES):
        yield (
            f"dry={dry}-slack={slack}-influx={influx}",
            Scenario(dict(values), dry_run=dry, slack=slack, influx=influx, **overrides),
        )


AGE = {"days": 1}
