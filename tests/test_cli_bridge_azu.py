"""Parity: the Azure kinds routed through `crc.core.engine` against `main`'s `CRC` (the oracle).

The oracle side is `tests/legacy/crc_main.txt` with the Azure adapters bound to their module
oracles (`tests.helpers_oracle.load_legacy_crc`), driven like `main()` did, on the same fake Azure
SDK. The routed side runs `dispatch._run_pair` (`bridge.run_routed` and the registered cleaner) on
the current `CRC` as the context object. For every kind, a dry run and a real run, and every Slack
and Influx mode, the Slack calls and recipients, the Influx points, the console output, the SDK
mutations in order and the raised error are equal, except where a fixed bug makes `main` differ:

- BUG-358: the raw tag dicts `main` logs at INFO per listed VM or disk are no longer logged.
- BUG-356: the per-leftover-disk "Skipping leftover Spot disk" lines are DEBUG, not INFO.
- BUG-003: Azure VM stop deallocates (`begin_deallocate`) where `main` powers off.
- BUG-013 / BUG-077: an untagged IP no longer raises `TypeError` and aborts the run.
- BUG-113: `name_regex` is matched as a regex (`main` matched a substring).
- BUG-174: a failing disk delete is isolated per disk and logged (`main` aborted the run).

The oracle emits no run-log lines, so RUN START, REGION, ACTION, SKIPPED, ERRORS, FAILED and
RESULT are asserted against the data frozen from the former legacy handlers
(`tests/helpers_cl2_azu_frozen.py`). Where a fixed bug changes the whole outcome, the frozen file
also holds the pre-routing outcome of the field (`FIXED`), and the test asserts that it differs
from `main`. The REGION lines are compared as a set: the cleaner logs them from `plan()`.
"""

import logging
import re
from contextlib import redirect_stdout
from datetime import UTC, datetime
from io import StringIO
from typing import Any

import pytest

from crc.cli import bridge
from crc.cli import dispatch as cli_dispatch
from crc.cli.inputs import Targets, read_options
from crc.cli.routes import NotifyMode, skips
from crc.cli.runner import CRC
from crc.core import logger
from tests.helpers_cl2_azu import FULL, Logs, Scenario, build, oracle_module, run_oracle_pair
from tests.helpers_cl2_azu_frozen import FIXED, RUN_LOG
from tests.helpers_cli_azu import KINDS, Kind, case_key

PREFIXES = ("RUN START", "REGION", "ACTION", "SKIPPED", "ERRORS", "FAILED", "RESULT")
NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
MODES = [
    ("off", "off"),
    ("on", "on"),
    ("post_fail", "write_error"),
    ("on", "api_error"),
    ("no_channel", "on_suffix"),
]


def _flags(kind: Kind, dry_run: bool) -> dict[str, Any]:
    extra = {"age": {"days": 1}} if kind.resource == "nic" and not dry_run else {}
    return {
        "cloud": "azure",
        "resource": kind.resource,
        "operation_type": kind.operation,
        "dry_run": dry_run,
        "resource_group": "rg",
        **extra,
        **kind.flags,
    }


ROUTED_RUNS: list[str] = []


def _crc(crc_class: Any, scenario: Scenario, logs: Logs, options: Any) -> Any:
    """A `CRC` with `notags` and `resource_group` from the flags, as in `cli/main.py`."""
    crc = build(crc_class, scenario, logs)
    crc.notags, crc.resource_group = options.notags, options.resource_group
    return crc


class Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _run(
    kind: Kind, variant: str, scenario: Scenario, routed: bool, mp: pytest.MonkeyPatch
) -> dict[str, Any]:
    azure = kind.make(variant)
    azure.install(mp)
    logger.finish()
    mp.setattr(bridge, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: NOW)}))
    real_run_routed = bridge.run_routed

    def counting(*args: Any) -> None:
        ROUTED_RUNS.append(kind.resource)
        real_run_routed(*args)

    mp.setattr(cli_dispatch, "run_routed", counting, raising=True)
    logs, out = Logs(), StringIO()
    result: Any = ("ok",)
    handler = Collect()
    records = handler.records
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    flags = _flags(kind, bool(scenario.dry_run))
    options = read_options(flags)
    targets = Targets("azure", False, ["azure"], [kind.resource])
    try:
        with redirect_stdout(out):
            try:
                if routed:
                    crc = _crc(CRC, scenario, logs, options)
                    cli_dispatch._run_pair(crc, "azure", kind.resource, options, targets)
                else:
                    crc = _crc(oracle_module().CRC, scenario, logs, options)
                    run_oracle_pair(crc, kind.resource, options)
            except Exception as exc:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
                result = ("error", type(exc).__name__, str(exc))
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        logger.finish()
    lines = [r.getMessage() for r in records if r.getMessage().startswith(PREFIXES)]
    lines = [re.sub(r"(seconds|duration_s)=\d+", r"\1=S", line) for line in lines]
    console_logs = [
        (r.levelno, r.getMessage())
        for r in records
        if r.levelno >= logging.INFO and not r.getMessage().startswith(PREFIXES)
    ]
    mutating = [
        c for c in azure.calls if any(w in str(c[0]) for w in ("delete", "dealloc", "power_off"))
    ]
    return {
        "result": result,
        "slack": logs.slack,
        "influx": logs.influx,
        "stdout": out.getvalue(),
        "logs": console_logs,
        "ordered": [x for x in lines if not x.startswith("REGION")],
        "regions": sorted(x for x in lines if x.startswith("REGION")),
        "mutations": mutating,
    }


ECHO = re.compile(r"^\{.*\}$")
SKIP_LINE = "Skipping leftover Spot disk"


def _oracle_view(kind: Kind, old: dict[str, Any]) -> dict[str, Any]:
    """`main`'s outcome with only the log-only and stop differences of fixed bugs applied.

    BUG-358: VM and disk raw tag dict echoes are gone. BUG-356: the leftover-disk skip lines are
    DEBUG. BUG-003: stop deallocates instead of powering off.
    """
    view = dict(old)
    if kind.resource in ("vm", "disk"):
        view["logs"] = [x for x in old["logs"] if not ECHO.match(x[1])]
    elif kind.resource == "spot_instances":
        view["logs"] = [x for x in old["logs"] if not x[1].startswith(SKIP_LINE)]
    if kind.resource == "vm" and kind.operation == "stop":
        view["mutations"] = [
            (str(c[0]).replace("begin_power_off", "begin_deallocate"), *c[1:])
            for c in old["mutations"]
        ]
    return view


def _expected(
    kind: Kind, variant: str, dry: bool, slack: str, influx: str, old: dict[str, Any]
) -> dict[str, Any]:
    """The routed outcome: `main`'s, with the fixed-bug divergences, and the frozen run log."""
    view = _oracle_view(kind, old)
    run_log = RUN_LOG[(case_key(kind), variant, dry)]
    ordered = list(run_log[0])
    if kind.resource == "nic" and not dry:
        ordered[0] = ordered[0].replace("dry_run=false ", 'dry_run=false age={"days":1} ')
    expected = {**view, "ordered": ordered, "regions": list(run_log[1])}
    fixed = FIXED.get((case_key(kind), variant, dry, slack, influx), {})
    for field, value in fixed.items():
        assert value != view[field], f"{field}: not a divergence from main"
        expected[field] = value
    return expected


PARAMS = [
    pytest.param(
        kind,
        variant,
        dry,
        slack,
        influx,
        id=f"{kind.resource}-{kind.operation}-{variant}-dry{int(dry)}-{slack}-{influx}",
    )
    for kind in KINDS
    for variant in kind.variants
    for dry in (False, True)
    for slack, influx in MODES
]


@pytest.mark.parametrize(("kind", "variant", "dry", "slack", "influx"), PARAMS)
def test_routed_azure_pair_matches_main(
    kind: Kind, variant: str, dry: bool, slack: str, influx: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = Scenario(dict(FULL), dry_run=dry, slack=slack, influx=influx)
    ROUTED_RUNS.clear()
    with monkeypatch.context() as legacy_patch:
        old = _run(kind, variant, scenario, False, legacy_patch)
    assert ROUTED_RUNS == []
    with monkeypatch.context() as routed_patch:
        new = _run(kind, variant, scenario, True, routed_patch)
    assert [kind.resource] == ROUTED_RUNS
    expected = _expected(kind, variant, dry, slack, influx, old)
    for field in expected:
        assert new[field] == expected[field], field
    assert set(new) == set(expected)
    ordered = new["ordered"]
    assert ordered[0].startswith("RUN START")
    assert ordered[-1].startswith("RESULT")
    if variant == "plain":
        assert any(x.startswith("ACTION") for x in ordered)
    if variant == "plain" and slack == "on":
        assert expected["slack"]
    if variant == "plain" and influx == "on":
        assert expected["influx"]


def test_every_azure_route_is_covered_and_notifies_as_the_legacy_handler() -> None:
    table = {k: r for k, r in bridge.TABLES["azure"].items() if r.options is not skips.no_options}
    assert {k.resource for k in KINDS} == set(table)
    assert set(bridge.TABLES["azure"]) - set(table) == {"spot_instance_requests"}
    modes = {kind: route.notify for kind, route in table.items()}
    assert modes.pop("disk") is NotifyMode.REPORT_THEN_RERAISE
    assert set(modes.values()) == {NotifyMode.REPORT}


def test_spot_without_a_resource_group_raises_before_the_cleaner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kind = next(k for k in KINDS if k.resource == "spot_instances")
    flags = {**_flags(kind, True), "resource_group": None}
    route = bridge.route_for("azure", "spot_instances")
    assert route is not None and route.precheck is not None
    crc = build(CRC, Scenario(dict(FULL), True), Logs())
    options = read_options(flags)
    targets = Targets("azure", False, ["azure"], ["spot_instances"])
    with pytest.raises(ValueError, match="resource_group is required for Azure Spot VM cleanup"):
        route.precheck(crc, options, targets)


FIXED_BY_BUG = {
    ("ip-delete", "plain"): "BUG-013/BUG-077",
    ("ip-delete", "delete_error"): "BUG-013/BUG-077",
    ("nic-delete", "plain"): "BUG-113",
    ("nic-delete", "delete_error"): "BUG-113",
    ("disk-delete", "delete_error"): "BUG-174",
}


def test_frozen_divergences_are_all_marked_with_a_bug() -> None:
    assert {(k[0], k[1]) for k in FIXED} == set(FIXED_BY_BUG)
    assert all(FIXED[k] for k in FIXED)


@pytest.mark.parametrize("kind", [k for k in KINDS if k.variants[0] == "plain"])
def test_main_logs_what_the_fixed_bugs_removed_and_stop_powers_off(
    kind: Kind, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BUG-358 and BUG-356 (log lines) and BUG-003 (power off) are present in `main`."""
    scenario = Scenario(dict(FULL), dry_run=False, slack="off", influx="off")
    old = _run(kind, "plain", scenario, False, monkeypatch)
    view = _oracle_view(kind, old)
    if kind.resource in ("vm", "disk", "spot_instances"):
        assert len(view["logs"]) < len(old["logs"])
    if kind.operation == "stop":
        assert any("power_off" in str(c[0]) for c in old["mutations"])
        assert not any("power_off" in str(c[0]) for c in view["mutations"])
