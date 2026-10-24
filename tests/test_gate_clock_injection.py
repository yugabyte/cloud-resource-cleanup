"""F-GATE: the cloud packages read no wall clock; `now` is `RunContext.now`.

The GCP Spot VM leftover-disk age check uses the injected clock and fails closed (keeps the
disk) when none is injected; the AWS spot runtime has no clock of its own.
"""

import dataclasses
import datetime
from typing import Any

from crc.aws.spot_instance_requests import SpotRuntime
from crc.gcp.spot_vm import SpotSettings
from crc.gcp.spot_vm import disks as leftover_disks

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
REF = {"project": "p", "scope": "zone", "location": "z", "name": "d1"}


class _Client:
    def __init__(self, created: datetime.datetime) -> None:
        self._created = created

    def get_disk(self, project: str, scope: str, location: str, disk: str) -> dict[str, Any]:
        return {
            "labels": {"retention": "{'days': 2}"},
            "creationTimestamp": self._created.strftime("%Y-%m-%dT%H:%M:%S.%f%z"),
        }


def _settings() -> SpotSettings:
    return SpotSettings(
        project_id="p",
        filter_labels={},
        exception_labels={},
        age={"days": 1},
        custom_age_tag_key="retention",
        notags={},
        poll_sec=0,
    )


def _ok(created: datetime.datetime, clock: Any) -> bool:
    return leftover_disks.disk_ok_to_delete(_Client(created), _settings(), REF, "z", clock)  # type: ignore[arg-type]


def test_old_disk_is_deleted_with_the_injected_clock() -> None:
    assert _ok(NOW - datetime.timedelta(days=3), lambda: NOW) is True


def test_young_disk_is_kept_with_the_injected_clock() -> None:
    assert _ok(NOW - datetime.timedelta(days=1), lambda: NOW) is False


def test_disk_is_kept_when_no_clock_is_injected() -> None:
    assert _ok(NOW - datetime.timedelta(days=30), None) is False


def test_aws_spot_runtime_has_no_clock() -> None:
    assert "clock" not in {f.name for f in dataclasses.fields(SpotRuntime)}
