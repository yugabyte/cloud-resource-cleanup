"""Routed side of `tests/test_notify_parity.py`: the notifiers as the CLI bridge builds them.

`routed_run` builds the real `crc.cli.runner.CRC` only as the context object the bridge reads
(`slack_client`, `slack_channel`, `influxdb_client`, `influxdb_bucket`, `resource_suffix`), asks
`crc.cli.bridge.build_notifiers` for the Slack and Influx notifiers and lets
`crc.core.engine.run` build the report from a stub cleaner's plan and result, as production does.
No `CRC.notify_*`, `write_influxdb` or `ping_on_slack` method is called.
"""

import contextlib
import io
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, Mock

from influxdb_client.client.write.point import Point

from crc.cli import bridge
from crc.cli.routes import Route
from crc.cli.runner import CRC
from crc.core.cleaner import Candidate, Operation, Plan, Result, RunContext
from crc.core.engine import run as run_engine
from crc.core.registry import Registry
from crc.core.selection import Selection

NOW = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)
Outcome = tuple[list[Any], list[Any], str, tuple[str, str] | None]

# Any route with the default REPORT mode: `build_notifiers` only reads `route.notify` and
# `route.extra_notifiers`.
REPORT_ROUTE = Route(options=lambda _options: {})


def line_protocol(point: Any) -> str:
    text: str = point.to_line_protocol()
    return text


def normalise(client: MagicMock) -> list[Any]:
    """Influx calls with each Point replaced by its line protocol (Point has no equality)."""
    out: list[Any] = []
    for name, args, kwargs in client.mock_calls:
        shown = {
            key: line_protocol(value) if isinstance(value, Point) else value
            for key, value in kwargs.items()
        }
        out.append((name, args, shown))
    return out


def context_crc(  # noqa: PLR0913, PLR0917 - one parameter per CRC constructor axis
    cloud: str,
    dry_run: bool,
    slack: Any,
    influx: Any,
    channel: str | None = "chan",
    suffix: str | None = None,
    conn: bool = True,
) -> CRC:
    """The `CRC` object the bridge reads its notifier settings from."""
    return CRC(
        cloud,
        dry_run,
        {},
        slack,
        influx,
        project_id="p",
        resource_group="rg",
        slack_channel=channel,
        influxdb_conn={"bucket": "bkt", "resource_suffix": suffix} if conn else None,
    )


def stub_registry(
    cloud: str,
    kind: str,
    operation: Operation,
    succeeded: Sequence[str],
    related: Mapping[str, Any],
    by_owner: Mapping[str, Any] | None,
) -> Registry:
    """A registry whose only cleaner plans and applies exactly the given ids."""
    registry = Registry()
    ids = tuple(succeeded)

    @registry.register(cloud, kind)
    class StubCleaner:
        def __init__(self, _ctx: RunContext) -> None:
            self.cloud = cloud
            self.kind = kind

        def supports(self, op: Operation) -> bool:
            return op is operation

        def plan(self, _ctx: RunContext) -> Plan:
            return Plan(tuple(Candidate(i) for i in ids), related=dict(related), by_owner=by_owner)

        def apply(self, _plan: Plan, _ctx: RunContext) -> Result:
            return Result(ids, related=dict(related), by_owner=by_owner)

    return registry


def routed_run(  # noqa: PLR0913 - one keyword per axis the parity tests vary
    cloud: str,
    kind: str,
    *,
    operation: Operation = Operation.DELETE,
    dry_run: bool = False,
    succeeded: Sequence[str] = (),
    related: Mapping[str, Any] | None = None,
    by_owner: Mapping[str, Any] | None = None,
    setup: Callable[[Mock], None] | None = None,
    channel: str | None = "chan",
    suffix: str | None = None,
    conn: bool = True,
    slack: bool = True,
    influx: bool = True,
) -> Outcome:
    """Slack calls, Influx calls, stdout and error of one engine run with the bridge notifiers."""
    slack_client = Mock() if slack else None
    influx_client = MagicMock() if influx else None
    if setup is not None and slack_client is not None:
        setup(slack_client)
    crc = context_crc(cloud, dry_run, slack_client, influx_client, channel, suffix, conn)
    registry = stub_registry(cloud, kind, operation, succeeded, related or {}, by_owner)
    context = RunContext(dry_run, operation, {}, NOW)
    notifiers = bridge.build_notifiers(crc, REPORT_ROUTE)
    if influx_client is not None:
        influx_client.reset_mock()  # `build_notifiers` tested the client's truth value
    stdout = io.StringIO()
    error: tuple[str, str] | None = None
    with contextlib.redirect_stdout(stdout):
        try:
            run_engine(Selection(((cloud, kind),)), context, registry, notifiers)
        except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
            error = (type(e).__name__, str(e))
    slack_calls = slack_client.mock_calls if slack_client is not None else []
    influx_calls = normalise(influx_client) if influx_client is not None else []
    return slack_calls, influx_calls, stdout.getvalue(), error


def routed_call(
    call: Callable[[dict[str, Any], CRC], Any],
    setup: Callable[[Mock], None] | None = None,
    **options: Any,
) -> Outcome:
    """Run `call(notifiers, crc)` with the notifiers `bridge.build_notifiers` returns.

    `notifiers` maps `"slack"` and `"influx"` to the bridge-built notifier; `crc` is the context
    object they were built from. Used where the oracle method has no engine-level counterpart
    (a bare lookup, one `write_influxdb` call).
    """
    cloud = options.get("cloud", "aws")
    dry_run = options.get("dry_run", False)
    slack, influx = Mock(), MagicMock()
    if setup is not None:
        setup(slack)
    crc = context_crc(
        cloud,
        dry_run,
        slack,
        influx,
        options.get("channel", "chan"),
        options.get("suffix"),
        options.get("conn", True),
    )
    notifiers = {n.name: n for n in bridge.build_notifiers(crc, REPORT_ROUTE)}
    influx.reset_mock()  # `build_notifiers` tested the client's truth value
    stdout = io.StringIO()
    error: tuple[str, str] | None = None
    with contextlib.redirect_stdout(stdout):
        try:
            call(notifiers, crc)
        except Exception as e:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
            error = (type(e).__name__, str(e))
    return slack.mock_calls, normalise(influx), stdout.getvalue(), error
