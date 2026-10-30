"""Fake cleaners for the CLI parity tests: the seam of `crc.cli.bridge`.

`install` patches `bridge.build_registry` so every engine-routed pair builds a recording fake
cleaner instead of the real one, and `bridge.build_notifiers` so no Slack or Influx is sent.
The fake records `(cloud, kind, options)` of each run and calls `side_effect` from `plan()`.
"""

from collections.abc import Callable, Mapping
from typing import Any

import pytest

from crc.cli import bridge
from crc.core.cleaner import Cleaner, Operation, Plan, Result, RunContext
from crc.core.registry import Registry


class FakeCleaner:
    def __init__(self, cloud: str, kind: str, fakes: "FakeCleaners", context: RunContext) -> None:
        self.cloud = cloud
        self.kind = kind
        self._fakes = fakes
        self._context = context

    def supports(self, operation: Operation) -> bool:
        return True

    def plan(self, context: RunContext) -> Plan:
        self._fakes.runs.append((self.cloud, self.kind, dict(context.options)))
        self._fakes.contexts.append(context)
        effect = self._fakes.side_effect.get((self.cloud, self.kind))
        if effect is not None:
            effect(context)
        return self._fakes.plans.get((self.cloud, self.kind), Plan())

    def apply(self, plan: Plan, context: RunContext) -> Result:
        self._fakes.applied.append((self.cloud, self.kind))
        return self._fakes.results.get((self.cloud, self.kind), Result())


class FakeCleaners:
    """Recording replacement of the registry the bridge reads at call time."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, str, dict[str, Any]]] = []
        self.contexts: list[RunContext] = []
        self.applied: list[tuple[str, str]] = []
        self.side_effect: dict[tuple[str, str], Callable[[RunContext], None]] = {}
        self.plans: dict[tuple[str, str], Plan] = {}
        self.results: dict[tuple[str, str], Result] = {}
        self.registry = Registry()
        for cloud, table in bridge.TABLES.items():
            for kind in table:
                self.registry.register(cloud, kind)(self._factory(cloud, kind))

    def _factory(self, cloud: str, kind: str) -> Callable[[RunContext], Cleaner]:
        return lambda context: FakeCleaner(cloud, kind, self, context)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "FakeCleaners":
        monkeypatch.setattr(bridge, "init_run_logging", lambda: None)
        monkeypatch.setattr(bridge, "build_registry", lambda: self.registry)
        monkeypatch.setattr(bridge, "build_notifiers", lambda crc, route: [])
        return self

    def options_of(self, cloud: str, kind: str) -> Mapping[str, Any]:
        return next(options for c, k, options in self.runs if (c, k) == (cloud, kind))
