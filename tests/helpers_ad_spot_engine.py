"""Run one registered cleaner factory through `crc.core.engine.run` (the production path)."""

from crc.core import engine
from crc.core.cleaner import RunContext
from crc.core.registry import CleanerFactory, Registry
from crc.core.selection import Selection


def run_through_engine(
    cloud: str, kind: str, factory: CleanerFactory, context: RunContext
) -> engine.RunOutcome:
    """Plan and (unless `context.dry_run`) apply the single pair, as `crc.cli.bridge` does."""
    registry = Registry()
    registry.register(cloud, kind)(factory)
    (outcome,) = engine.run(Selection(((cloud, kind),)), context, registry)
    return outcome
