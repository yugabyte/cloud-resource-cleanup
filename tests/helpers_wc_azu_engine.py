"""Run an Azure cleaner through `crc.core.engine.run`, the production path (WC-AZU)."""

from datetime import datetime
from typing import Any

import crc.azu.disk
import crc.azu.ip
import crc.azu.nic  # noqa: F401
from crc.core import engine
from crc.core.cleaner import Operation, RunContext
from crc.core.registry import Registry, get
from crc.core.selection import Selection


def engine_run(
    kind: str, options: dict[str, Any], *, dry_run: bool, now: datetime
) -> engine.RunOutcome:
    """Run the registered `azure`/`kind` cleaner through the engine and return its outcome."""
    ctx = RunContext(dry_run=dry_run, operation=Operation.DELETE, options=options, now=now)
    registry = Registry()
    registry.register("azure", kind)(get("azure", kind))
    (outcome,) = engine.run(Selection((("azure", kind),)), ctx, registry)
    return outcome


def candidate_ids(outcome: engine.RunOutcome) -> list[str]:
    """Names acted on: the plan in a dry run, the succeeded ids of a real run."""
    if outcome.result is None:
        return [c.id for c in outcome.plan.candidates]
    return list(outcome.result.succeeded)
