"""The bridge from `dispatch` to `crc.core.engine` for the pairs a route table marks as routed.

`route_for(cloud, kind)` reads the route tables in `crc/cli/routes/`. `run_routed` builds the
`RunContext` and `Selection` of one pair and calls `crc.core.engine.run`; the Slack then Influx
notifiers run after each pair. `build_registry` and `build_notifiers`
are looked up in this module at call time, the seam the CLI parity tests patch.
"""

from datetime import UTC, datetime
from typing import Any

from crc.cli.digest_target import read_digest_target
from crc.cli.inputs import Options, Targets
from crc.cli.routes import NotifyMode, Route
from crc.cli.routes import aws as aws_routes
from crc.cli.routes import azu as azu_routes
from crc.cli.routes import gcp as gcp_routes
from crc.cli.routes import oci as oci_routes
from crc.core import engine, registry
from crc.core.cleaner import Candidate, Cleaner, Operation, Plan, Result, RunContext
from crc.core.selection import Selection
from crc.notify.influx import InfluxNotifier
from crc.notify.slack import SlackNotifier
from crc.service import Service

TABLES: dict[str, dict[str, Route]] = {
    "aws": aws_routes.ROUTES,
    "azure": azu_routes.ROUTES,
    "gcp": gcp_routes.ROUTES,
    "oci": oci_routes.ROUTES,
}


def route_for(cloud: Any, kind: Any) -> Route | None:
    """The route of a pair, or None when `(cloud, kind)` is not in the route tables."""
    return TABLES.get(cloud, {}).get(kind)


def is_routed_kind(kind: Any) -> bool:
    """True when at least one cloud routes `kind`."""
    return any(kind in table for table in TABLES.values())


def init_run_logging() -> None:
    """Initialise the log file and root handlers (`Service.__init__`) before a pair runs."""
    Service()


def build_registry() -> registry.Registry:
    """The registry the cleaners come from (the default one; the route modules registered them)."""
    return registry.DEFAULT_REGISTRY


def build_notifiers(crc: Any, route: Route) -> list[engine.Notifier]:
    """Slack, Influx (each only when its client is set), then the route's extras; none for NONE."""
    if route.notify is NotifyMode.NONE:
        return []
    notifiers: list[engine.Notifier] = []
    if crc.slack_client:
        notifiers.append(SlackNotifier(crc.slack_client, crc.slack_channel))
    if crc.influxdb_client:
        notifiers.append(InfluxNotifier(crc.influxdb_client, crc))
    if route.extra_notifiers is not None:
        notifiers.extend(route.extra_notifiers(crc))
    return notifiers


def attach_digest(crc: Any, kind: Any, notifiers: list[engine.Notifier]) -> None:
    """Give each `SlackNotifier` the digest target when `CRC_SLACK_DIGEST_DIR` is set."""
    slack = [notifier for notifier in notifiers if isinstance(notifier, SlackNotifier)]
    if not slack:
        return
    # `resource_suffix` is set only with `--influxdb` (BUG-030), hence the default.
    target = read_digest_target(kind, getattr(crc, "resource_suffix", None))
    if target is None:
        return
    for notifier in slack:
        notifier.digest = target


class _ReportThenRaise:
    """Wraps a cleaner so its error is reported (notifiers) before it is raised again.

    `plan()` and `apply()` keep the first error and return what the cleaner exposes as
    `partial_succeeded` (a plan of those ids, or a result with them), else nothing;
    `run_routed` raises the error after `engine.run` ran the notifiers.
    """

    def __init__(self, inner: Cleaner) -> None:
        self.inner = inner
        self.cloud = inner.cloud
        self.kind = inner.kind
        self.error: Exception | None = None

    def supports(self, operation: Operation) -> bool:
        return self.inner.supports(operation)

    def plan(self, context: RunContext) -> Plan:
        try:
            return self.inner.plan(context)
        except Exception as e:  # noqa: BLE001 - error stored, run_routed re-raises it after the notifiers ran
            self.error = e
            return Plan(candidates=tuple(Candidate(id=i) for i in self._partial()))

    def apply(self, plan: Plan, context: RunContext) -> Result:
        if self.error is not None:
            return Result()
        try:
            return self.inner.apply(plan, context)
        except Exception as e:  # noqa: BLE001 - error stored, run_routed re-raises it after the notifiers ran
            self.error = e
            return Result(succeeded=tuple(self._partial()))

    def _partial(self) -> list[str]:
        return list(getattr(self.inner, "partial_succeeded", ()))


def _deferring(
    base: registry.Registry, cloud: str, kind: str
) -> tuple[registry.Registry, list[_ReportThenRaise]]:
    """A registry whose `(cloud, kind)` cleaner is wrapped in `_ReportThenRaise`."""
    wrapped = registry.Registry()
    made: list[_ReportThenRaise] = []

    def factory(context: RunContext) -> Cleaner:
        cleaner = _ReportThenRaise(base.get(cloud, kind)(context))
        made.append(cleaner)
        return cleaner

    wrapped.register(cloud, kind)(factory)
    return wrapped, made


def run_routed(
    crc: Any, cloud: Any, kind: Any, options: Options, targets: Targets, route: Route
) -> None:
    """Run one routed pair through the engine: precheck, plan, apply unless dry run, notify."""
    if route.skip is not None and route.skip(str(cloud), options, targets):
        return
    if route.precheck is not None:
        route.precheck(crc, options, targets)
    init_run_logging()
    context = RunContext(
        dry_run=bool(options.dry_run),
        operation=Operation(options.operation_type or "delete"),
        options=route.options(options),
        now=datetime.now(UTC),
    )
    selection = Selection(items=((str(cloud), str(kind)),))
    cleaners = build_registry()
    deferred: list[_ReportThenRaise] = []
    if route.notify is NotifyMode.REPORT_THEN_RERAISE:
        cleaners, deferred = _deferring(cleaners, str(cloud), str(kind))
    notifiers = build_notifiers(crc, route)
    attach_digest(crc, kind, notifiers)
    outcomes = engine.run(selection, context, cleaners, notifiers)
    for cleaner in deferred:
        if cleaner.error is not None:
            raise cleaner.error
    if route.after is not None:
        route.after(crc, options, outcomes)
