"""Drive the registered OCI disk Cleaner through `crc.core.engine.run` with injected fake APIs."""

import datetime
from collections.abc import Mapping
from typing import Any

from crc.core import engine
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import Registry
from crc.core.selection import Selection
from crc.oci.disk import DiskCleaner, DiskSettings

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)


def run_disk(
    apis: Mapping[str, Any],
    *,
    dry_run: bool,
    settings: DiskSettings,
    now: datetime.datetime = NOW,
) -> engine.RunOutcome:
    """Run `oci` / `disk` through the engine; a region whose entry is an exception raises it
    when its API is built, as `Base` does for a connectivity error."""

    def factory(region: str) -> Any:
        api = apis[region]
        if isinstance(api, Exception):
            raise api
        return api

    def build(context: RunContext) -> Cleaner:
        return DiskCleaner(factory, lambda: list(apis), settings, engine=True)

    registry = Registry()
    registry.register("oci", "disk")(build)
    context = RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=now)
    (outcome,) = engine.run(Selection((("oci", "disk"),)), context, registry)
    return outcome
