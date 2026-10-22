"""The settings every CRC object of one run shares, and the CRC factory."""

from dataclasses import dataclass
from typing import Any

from crc.cli.runner import CRC


@dataclass(frozen=True, slots=True)
class RunContext:
    """What `main` passes to every `CRC(...)` constructor besides the cloud."""

    dry_run: Any
    notags: Any
    slack_client: Any
    influxdb_client: Any
    project_id: Any
    resource_group: Any
    slack_channel: Any
    influxdb: Any


def make_crc(context: RunContext, cloud: Any) -> CRC:
    """Build the CRC object of one cloud, with the positional arguments of `main`."""
    return CRC(
        cloud,
        context.dry_run,
        context.notags,
        context.slack_client,
        context.influxdb_client,
        context.project_id,
        context.resource_group,
        context.slack_channel,
        context.influxdb,
    )
