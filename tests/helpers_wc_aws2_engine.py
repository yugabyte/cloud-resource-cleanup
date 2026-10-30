"""Drive a registered Cleaner through `crc.core.engine.run` for the WC-AWS2 parity tests."""

import datetime
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from crc.core import engine
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import Registry, get
from crc.core.selection import Selection


@dataclass
class EngineRun:
    """Result of one `engine.run`: ids deleted (listed in a dry run) and the raised error."""

    deleted: list[str]
    error: tuple[str, str] | None
    cleaner: Cleaner | None


def run_through_engine(
    cloud: str,
    kind: str,
    options: Mapping[str, Any],
    *,
    dry_run: bool,
    now: datetime.datetime,
    configure: Callable[[Cleaner], None] | None = None,
) -> EngineRun:
    """Run the registered `(cloud, kind)` cleaner for one context; `configure` may adjust the
    built cleaner (for example to remove a sleep) before the engine uses it."""
    ctx = RunContext(dry_run=dry_run, operation=Operation.DELETE, options=dict(options), now=now)
    factory = get(cloud, kind)
    built: list[Cleaner] = []

    def build(context: RunContext) -> Cleaner:
        cleaner = factory(context)
        if configure is not None:
            configure(cleaner)
        built.append(cleaner)
        return cleaner

    registry = Registry()
    registry.register(cloud, kind)(build)
    try:
        (outcome,) = engine.run(Selection(((cloud, kind),)), ctx, registry)
    except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
        return EngineRun([], (type(e).__name__, str(e)), built[0] if built else None)
    ids = (
        [c.id for c in outcome.plan.candidates]
        if outcome.result is None
        else list(outcome.result.succeeded)
    )
    return EngineRun(ids, None, built[0])
