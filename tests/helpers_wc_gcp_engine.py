"""Drive a GCP cleaner built by a test through `crc.core.engine.run`."""

import datetime
from typing import Any

from crc.core import engine
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import Registry
from crc.core.selection import Selection

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical


def run_engine(
    cleaner: Cleaner,
    kind: str,
    *,
    dry_run: bool = False,
    options: dict[str, Any] | None = None,
    now: datetime.datetime = NOW,
) -> engine.RunOutcome:
    """Run `cleaner` as the registered `("gcp", kind)` cleaner and return the outcome."""
    registry = Registry()
    registry.register("gcp", kind)(lambda context: cleaner)
    context = RunContext(
        dry_run=dry_run, operation=Operation.DELETE, options=options or {}, now=now
    )
    (outcome,) = engine.run(Selection((("gcp", kind),)), context, registry)
    return outcome


def deleted_ids(outcome: engine.RunOutcome) -> list[str]:
    """Ids the run deleted (the planned ones in a dry run)."""
    if outcome.result is None:
        return [c.id for c in outcome.plan.candidates]
    return list(outcome.result.succeeded)
