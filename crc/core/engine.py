"""Engine: run registered cleaners for a selection. Pure orchestration; `crc/cli` calls it.

For each `(cloud, kind)` the engine builds the cleaner from the registry, checks
`supports(operation)`, calls `plan()` and calls `apply()` only when `context.dry_run` is
False. Exceptions from a cleaner propagate (no catch-all). Notifiers run
after each cleaner in the order given; only notifiers marked `fail_open` have their errors
logged and swallowed (Influx and per-message Slack failures).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from crc.core.cleaner import Plan, Result, RunContext
from crc.core.kinds import KINDS
from crc.core.registry import Registry
from crc.core.report import Report
from crc.core.selection import Selection

logger = logging.getLogger(__name__)

# Report label of each resource kind, the labels the notifiers receive (table: crc/core/kinds.py).
KIND_LABELS: dict[str, str] = {kind.name: kind.label for kind in KINDS}


class Notifier(Protocol):
    """Sends a report (Slack, Influx). `fail_open` True means errors are logged and swallowed."""

    name: str
    fail_open: bool

    def send(self, report: Report, context: RunContext) -> None:
        """Deliver `report`; may raise."""
        ...


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What one cleaner did: its plan, its result (`None` in a dry run) and its report."""

    cloud: str
    kind: str
    plan: Plan
    result: Result | None
    report: Report


def _label_for(kind: str) -> str:
    label = KIND_LABELS.get(kind)
    if label is None:
        raise ValueError(f"no report label for resource kind {kind!r}")
    return label


def _notify(notifiers: Sequence[Notifier], report: Report, context: RunContext) -> None:
    for notifier in notifiers:
        try:
            notifier.send(report, context)
        except Exception as e:
            if not notifier.fail_open:
                raise
            logger.error("Notifier %s failed for %s report: %s", notifier.name, report.cloud, e)


def _run_one(
    cloud: str,
    kind: str,
    context: RunContext,
    registry: Registry,
    notifiers: Sequence[Notifier],
) -> RunOutcome:
    cleaner = registry.get(cloud, kind)(context)
    if not cleaner.supports(context.operation):
        raise ValueError(
            f"{cloud} {kind} cleaner does not support operation {context.operation.value!r}"
        )
    plan = cleaner.plan(context)
    result = None if context.dry_run else cleaner.apply(plan, context)
    label = _label_for(kind)
    if result is None:
        report = Report.from_plan(cloud, label, plan, context.operation, context.dry_run)
    else:
        report = Report.from_result(cloud, label, result, context.operation, context.dry_run)
    _notify(notifiers, report, context)
    return RunOutcome(cloud, kind, plan, result, report)


def run(
    selection: Selection,
    context: RunContext,
    registry: Registry,
    notifiers: Sequence[Notifier] = (),
) -> list[RunOutcome]:
    """Run every selected cleaner in order; the first exception from a cleaner ends the run."""
    for warning in selection.warnings:
        logger.warning("%s", warning)
    return [_run_one(cloud, kind, context, registry, notifiers) for cloud, kind in selection.items]
