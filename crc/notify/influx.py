"""InfluxDB sink: one point per resource label, sent through an injected client.

The client is injected (an `influxdb_client.InfluxDBClient` in production, a fake in tests).
"""

from collections.abc import Callable, Sized
from dataclasses import dataclass
from typing import Any, Protocol

from influxdb_client.client.write.point import Point
from influxdb_client.client.write_api import SYNCHRONOUS

from crc.core.cleaner import RunContext
from crc.core.report import Report

InfluxClient = Any
_new_point: Callable[[str], Any] = Point


def _fixed(names: str, count: int) -> Callable[[], tuple[str, int]]:
    return lambda: (names, count)


class InfluxSettings(Protocol):
    """Where the bucket and resource suffix come from; read at write time."""

    @property
    def influxdb_bucket(self) -> str | None:
        """Bucket the points are written to."""
        ...

    @property
    def resource_suffix(self) -> str | None:
        """Suffix added to the `resource` tag as `_suffix` when truthy."""
        ...


@dataclass(frozen=True, slots=True)
class InfluxConfig:
    """Plain `InfluxSettings` for callers that are not the CLI `CRC` object."""

    influxdb_bucket: str | None
    resource_suffix: str | None = None


class InfluxNotifier:
    """Writes the Influx points of a `Report`.

    `fail_open` is True: every write is wrapped in a catch-all that prints the error and goes
    on (as `write_influxdb` does), so an Influx failure never stops a run. Points are written
    in dry run too (BUG-032).
    """

    name = "influx"
    fail_open = True

    def __init__(self, client: InfluxClient, settings: InfluxSettings) -> None:
        self.client = client
        self.settings = settings

    def send(self, report: Report, context: RunContext) -> None:
        """Write one point per section of `report` (owner-grouped: one point for all owners)."""
        for point in report.influx_points(None):
            self._emit(point.measurement, point.resource, _fixed(point.names, point.count))

    def write(self, cloud: str, resource_name: str, resources: Sized) -> None:
        """Write `resources` (a list or dict) as one point of measurement `cloud`."""
        self._emit(cloud, resource_name, lambda: (str(resources), len(resources)))

    def _emit(self, cloud: str, resource_name: str, fields: Callable[[], tuple[str, int]]) -> None:
        try:
            write_api = self.client.write_api(write_options=SYNCHRONOUS)

            if self.settings.resource_suffix:
                resource_name = resource_name + "_" + self.settings.resource_suffix

            names, count = fields()
            point = (
                _new_point(cloud)
                .tag("resource", resource_name)
                .field("names", names)
                .field("count", count)
            )

            write_api.write(bucket=self.settings.influxdb_bucket, record=point)
        except Exception as e:  # noqa: BLE001 - a failing notification sink must not abort the run
            print(f"Unable to push data to influxDB: {e}")
