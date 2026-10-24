"""Drive a VM cleaner built by a test through `crc.core.engine.run`, delete or stop."""

import datetime

from crc.core import engine
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import Registry
from crc.core.selection import Selection

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical


def run_vm(
    cloud: str,
    cleaner: Cleaner,
    *,
    dry_run: bool = False,
    operation: Operation = Operation.DELETE,
    now: datetime.datetime = NOW,
) -> engine.RunOutcome:
    """Run `cleaner` as the registered `(cloud, "vm")` cleaner and return the outcome."""
    registry = Registry()
    registry.register(cloud, "vm")(lambda context: cleaner)
    context = RunContext(dry_run=dry_run, operation=operation, options={}, now=now)
    (outcome,) = engine.run(Selection(((cloud, "vm"),)), context, registry)
    return outcome


def done_ids(outcome: engine.RunOutcome) -> list[str]:
    """Ids the run acted on (the planned ones in a dry run)."""
    if outcome.result is None:
        return [c.id for c in outcome.plan.candidates]
    return list(outcome.result.succeeded)
