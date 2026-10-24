"""InfluxNotifier: exact write_api and Point calls for every report shape, dry run and failures."""

from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any
from unittest.mock import MagicMock, call

import pytest
from influxdb_client.client.write_api import SYNCHRONOUS

from crc.core.cleaner import Operation, RunContext
from crc.core.engine import Notifier
from crc.core.report import DISK, NIC, SPOT_INSTANCE_REQUEST, VM, Report, Section
from crc.notify.influx import InfluxConfig, InfluxNotifier

NOW = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
IDS = ("i-1", "i-2")
THREE = 3


def context(dry_run: bool = False) -> RunContext:
    return RunContext(dry_run, Operation.DELETE, {}, NOW)


def written(client: MagicMock) -> list[tuple[Any, Any, Any, Any]]:
    """(measurement, tags, fields, bucket) of every point written, in order."""
    api = client.write_api.return_value
    return [
        (
            c.kwargs["record"]._name,
            c.kwargs["record"]._tags,
            c.kwargs["record"]._fields,
            c.kwargs["bucket"],
        )
        for c in api.write.mock_calls
    ]


def notifier(
    client: MagicMock, suffix: str | None = None, bucket: str | None = "bkt"
) -> InfluxNotifier:
    return InfluxNotifier(client, InfluxConfig(bucket, suffix))


def test_notifier_is_a_core_notifier() -> None:
    instance: Notifier = notifier(MagicMock())
    assert instance.name == "influx"
    assert instance.fail_open is True


def test_write_point_fields_and_tags() -> None:
    client = MagicMock()
    notifier(client).write("aws", "VM", ["i-1", "i-2"])
    assert client.write_api.mock_calls[0] == call(write_options=SYNCHRONOUS)
    assert written(client) == [
        ("aws", {"resource": "VM"}, {"names": "['i-1', 'i-2']", "count": 2}, "bkt")
    ]


@pytest.mark.parametrize(
    ("suffix", "resource"), [(None, "VM"), ("", "VM"), ("jenkins", "VM_jenkins")]
)
def test_resource_suffix(suffix: str | None, resource: str) -> None:
    client = MagicMock()
    notifier(client, suffix).write("gcp", "VM", ["a"])
    assert written(client) == [
        ("gcp", {"resource": resource}, {"names": "['a']", "count": 1}, "bkt")
    ]


def test_empty_list_is_written() -> None:
    client = MagicMock()
    notifier(client).write("oci", "Disk", [])
    assert written(client) == [("oci", {"resource": "Disk"}, {"names": "[]", "count": 0}, "bkt")]


def test_dict_is_written_as_dict_with_owner_count() -> None:
    client = MagicMock()
    notifier(client).write("aws", "Disk", {"alice": ["v-1", "v-2"], "bob": ["v-3"]})
    assert written(client) == [
        (
            "aws",
            {"resource": "Disk"},
            {"names": "{'alice': ['v-1', 'v-2'], 'bob': ['v-3']}", "count": 2},
            "bkt",
        )
    ]


def test_write_failure_is_printed_and_swallowed(capsys: pytest.CaptureFixture[str]) -> None:
    client = MagicMock()
    client.write_api.return_value.write.side_effect = RuntimeError("influx down")
    notifier(client).write("aws", "VM", ["a"])
    assert capsys.readouterr().out == "Unable to push data to influxDB: influx down\n"


def test_write_api_failure_is_printed_and_swallowed(capsys: pytest.CaptureFixture[str]) -> None:
    client = MagicMock()
    client.write_api.side_effect = RuntimeError("no api")
    notifier(client).write("aws", "VM", ["a"])
    assert capsys.readouterr().out == "Unable to push data to influxDB: no api\n"


def test_none_resources_are_swallowed_after_write_api(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = MagicMock()
    notifier(client).write("aws", "VM", None)  # type: ignore[arg-type]
    assert client.write_api.call_count == 1
    assert client.write_api.return_value.write.mock_calls == []
    assert "Unable to push data to influxDB: object of type 'NoneType' has no len()" in (
        capsys.readouterr().out
    )


class NoSettings:
    """BUG-030: `CRC` has no bucket or suffix without `influxdb_conn`."""


def test_missing_settings_are_swallowed_after_write_api(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = MagicMock()
    InfluxNotifier(client, NoSettings()).write("aws", "VM", ["a"])  # type: ignore[arg-type]
    assert client.write_api.call_count == 1
    assert client.write_api.return_value.write.mock_calls == []
    assert (
        "Unable to push data to influxDB: 'NoSettings' object has no attribute 'resource_suffix'"
        in capsys.readouterr().out
    )


def test_send_writes_one_point_per_section_even_when_empty() -> None:
    client = MagicMock()
    sections = (
        Section(SPOT_INSTANCE_REQUEST, IDS),
        Section(NIC, ()),
        Section(DISK, ("v-1",), notify_empty=False),
    )
    notifier(client, "sfx").send(Report("aws", Operation.DELETE, False, sections), context())
    assert written(client) == [
        ("aws", {"resource": "VM_sfx"}, {"names": "['i-1', 'i-2']", "count": 2}, "bkt"),
        ("aws", {"resource": "NIC_sfx"}, {"names": "[]", "count": 0}, "bkt"),
        ("aws", {"resource": "Disk_sfx"}, {"names": "['v-1']", "count": 1}, "bkt"),
    ]
    assert client.write_api.call_count == THREE


def test_send_writes_in_dry_run() -> None:
    client = MagicMock()
    report = Report("azure", Operation.DELETE, True, (Section(VM, IDS),))
    notifier(client).send(report, context(dry_run=True))
    assert written(client) == [
        ("azure", {"resource": "VM"}, {"names": "['i-1', 'i-2']", "count": 2}, "bkt")
    ]


def test_send_owner_report_writes_one_dict_point() -> None:
    client = MagicMock()
    report = Report(
        "aws",
        Operation.DELETE,
        False,
        (Section(DISK, ()),),
        by_owner=MappingProxyType({"alice": ("v-1",), "bob": ("v-2", "v-3")}),
    )
    notifier(client, "sfx").send(report, context())
    assert written(client) == [
        (
            "aws",
            {"resource": "Disk_sfx"},
            {"names": "{'alice': ['v-1'], 'bob': ['v-2', 'v-3']}", "count": 2},
            "bkt",
        )
    ]


def test_send_failure_does_not_stop_later_points(capsys: pytest.CaptureFixture[str]) -> None:
    client = MagicMock()
    api = client.write_api.return_value
    api.write.side_effect = [RuntimeError("first"), None]
    report = Report("aws", Operation.DELETE, False, (Section(VM, IDS), Section(NIC, IDS)))
    notifier(client).send(report, context())
    assert capsys.readouterr().out == "Unable to push data to influxDB: first\n"
    assert len(api.write.mock_calls) == len(report.sections)


def test_settings_are_read_at_write_time() -> None:
    client = MagicMock()
    config = MagicMock(influxdb_bucket="b1", resource_suffix=None)
    instance = InfluxNotifier(client, config)
    config.influxdb_bucket = "b2"
    config.resource_suffix = "late"
    instance.write("aws", "VM", [])
    assert written(client) == [("aws", {"resource": "VM_late"}, {"names": "[]", "count": 0}, "b2")]
