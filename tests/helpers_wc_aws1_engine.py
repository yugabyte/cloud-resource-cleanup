"""Drive a built Cleaner through `crc.core.engine.run`, as production does."""

import datetime
from collections.abc import Callable, Mapping
from typing import Any

from crc.core import engine
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import Registry
from crc.core.selection import Selection

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0)  # noqa: DTZ001 - naive fixture time kept: an aware value is not provably identical


def context(
    *,
    dry_run: bool = False,
    options: Mapping[str, Any] | None = None,
    now: datetime.datetime = NOW,
) -> RunContext:
    return RunContext(
        dry_run=dry_run, operation=Operation.DELETE, options=dict(options or {}), now=now
    )


def run_engine(
    cloud: str,
    kind: str,
    factory: Callable[[RunContext], Cleaner],
    ctx: RunContext,
) -> engine.RunOutcome:
    """Run `factory`'s cleaner for one `(cloud, kind)` through the engine."""
    registry = Registry()
    registry.register(cloud, kind)(factory)
    (outcome,) = engine.run(Selection(((cloud, kind),)), ctx, registry)
    return outcome


def run_cleaner(
    cloud: str,
    kind: str,
    cleaner: Cleaner,
    *,
    dry_run: bool = False,
    now: datetime.datetime = NOW,
) -> engine.RunOutcome:
    """Run an already built cleaner through the engine."""
    return run_engine(cloud, kind, lambda _ctx: cleaner, context(dry_run=dry_run, now=now))


def deleted_ids(outcome: engine.RunOutcome) -> list[str]:
    """Ids released or deleted (listed in a dry run)."""
    if outcome.result is None:
        return [c.id for c in outcome.plan.candidates]
    return list(outcome.result.succeeded)
