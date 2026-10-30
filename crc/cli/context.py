"""The settings every CRC object of one run shares, and the CRC factory."""

import os
from dataclasses import dataclass
from typing import Any

from crc.cli.runner import CRC
from crc.core import age


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


def read_max_age_env() -> str | None:
    """The raw `MAX_AGE` text: the variable `set_max_age` exports from `--max_age`, or one a
    Jenkins job sets itself. Read at each age decision, like the original `is_old`."""
    return os.environ.get("MAX_AGE")


def install_max_age_source() -> None:
    """Give `crc.core.age` the `MAX_AGE` reader; `crc/core` itself reads no environment."""
    age.set_max_age_source(read_max_age_env)
