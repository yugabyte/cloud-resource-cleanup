"""Oracle side of `tests/test_cli_bridge_azu.py`: main's `CRC` driven the way main's `main()` did.

`load_legacy_crc()` gives `tests/legacy/crc_main.txt` with every Azure adapter bound to its
module oracle in `tests/legacy/`. `run_oracle_pair` repeats the Azure branches of the resource
loop of `main()` on that `CRC` (no run-log lines: the frozen `main` code emits none). The fake
Slack and Influx clients record every call so both sides can be compared.
"""

from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

from influxdb_client.client.write_api import SYNCHRONOUS

from tests.helpers_oracle import load_legacy_crc

FULL = {
    "get_deleted": ["a", "b"],
    "get_stopped": ["a"],
    "get_deleted_nic": ["n1"],
    "get_deleted_disks": ["d1", "d2"],
    "get_deleted_volumes": ["v1"],
    "volumes_to_delete": ["v1", "v2"],
}


@dataclass
class Scenario:
    values: dict[str, Any]
    dry_run: bool = False
    slack: str = "on"
    influx: str = "on"
    resource_group: str | None = "rg"
    project_id: str | None = "proj"
    slack_users: dict[str, str] = field(default_factory=lambda: {"alice@yugabyte.com": "U1"})
    slack_groups: list[dict[str, str]] = field(
        default_factory=lambda: [{"handle": "team", "id": "G1"}]
    )


class Logs:
    def __init__(self) -> None:
        self.slack: list[Any] = []
        self.influx: list[Any] = []


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
        return {"usergroups": self.scenario.slack_groups}

    def conversations_open(self, **kwargs: Any) -> dict[str, Any]:
        self.logs.slack.append(("conversations_open", kwargs))
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
        shown = {k: "SYNCHRONOUS" if v is SYNCHRONOUS else v for k, v in kwargs.items()}
        self.logs.influx.append(("write_api", shown))
        if self.scenario.influx == "api_error":
            raise OSError("api down")
        return FakeWriter(self.scenario, self.logs)


def build(crc_class: Any, scenario: Scenario, logs: Logs) -> Any:
    """A `CRC` (the oracle's, or the context object of the routed path) on the fake clients."""
    slack = FakeSlack(scenario, logs) if scenario.slack != "off" else None
    influx = FakeInflux(scenario, logs) if scenario.influx != "off" else None
    conn: dict[str, Any] | None = {"bucket": "bkt"}
    if scenario.influx == "on_suffix":
        conn = {"bucket": "bkt", "resource_suffix": "sfx"}
    return crc_class(
        "azure",
        scenario.dry_run,
        {"owner": ["x"]},
        slack,
        influx,
        project_id=scenario.project_id,
        resource_group=scenario.resource_group,
        slack_channel=None if scenario.slack == "no_channel" else "chan",
        influxdb_conn=conn,
    )


def oracle_module() -> ModuleType:
    return load_legacy_crc("legacy_crc_main_azu")


def run_oracle_pair(crc: Any, resource: str, options: Any) -> None:
    """The Azure branches of `main()`'s resource loop, on main's `CRC`."""
    if resource == "disk":
        crc.delete_disks(
            options.filter_tags,
            options.exception_tags,
            options.age,
            options.custom_age_tag_key,
            options.detach_age,
            options.name_regex,
            options.exception_regex,
            options.slack_notify_users,
            options.slack_user_label,
        )
    elif resource == "ip":
        crc.delete_ip(
            options.filter_tags, options.exception_tags, options.name_regex, options.exception_regex
        )
    elif resource == "nic":
        crc.delete_nic(options.name_regex, options.exception_regex)
    elif resource == "vm":
        if options.operation_type == "delete":
            crc.delete_vm(
                options.filter_tags,
                options.exception_tags,
                options.age,
                options.custom_age_tag_key,
                options.resource_states,
            )
        elif options.operation_type == "stop":
            crc.stop_vm(
                options.filter_tags, options.exception_tags, options.age, options.custom_age_tag_key
            )
    elif resource == "spot_instances":
        crc.delete_spot_instances(
            options.filter_tags, options.exception_tags, options.age, options.custom_age_tag_key
        )
