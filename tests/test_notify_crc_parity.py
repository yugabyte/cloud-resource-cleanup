"""Parity: the notification helpers of `crc.cli.notify` (main) against what production calls.

Moved out of `tests/test_cli_crc_parity.py`: `get_msg`, `slack_lookup_user_by_email`,
`get_user_groups_list`, `write_influxdb` and `ping_on_slack` of the `CRC` of main
(`load_legacy_crc()`) against `crc.core.report.get_msg`, `SlackNotifier` (with
`crc.notify.slack_owners`) and `InfluxNotifier`. Same inputs, same fake Slack and Influx clients;
the Slack calls, the Influx points, stdout and the return value or error are compared. The current
`crc.cli.runner.CRC` is not used on either side.

A divergence is asserted, not skipped:
- BUG-030: without `influxdb_conn` the write fails with `AttributeError`, printed and swallowed;
  both sides print the same text (the settings object is named `CRC`).
- BUG-033: `ping_on_slack` on `main` fetched user groups first and ran the dead
  `for key in operated_list.keys(): operated_list_length += len(operated_list[key])` loop before
  pinging; `Report` freezes `by_owner` into tuples in `__post_init__` before `ping_owners` runs,
  so non-dict / non-iterable owner inputs (`ping_list`, `ping_no_len`) fail there without calling
  `usergroups_list`.
- `get_msg(None)`: main raised `TypeError` from `len()`, `get_msg` of the report module raises
  `TypeError` from `list()`.
- `get_msg` with an operation word other than "Deleted"/"Stopped": main prints any word, the
  report module takes an `Operation`, so the current side raises `KeyError` mapping the word.
"""

import itertools
from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest

from tests.helpers_cl2_crc import (
    FULL,
    GROUPS_FAIL,
    MIN_SEEN,
    OPEN_FAIL,
    Case,
    Recorded,
    Scenario,
    execute,
    oracle,
)
from tests.helpers_wa1_notify import (
    BUG_033_PING_DIVERGENT,
    CRC,
    assert_bug_033_ping,
    current_get_msg,
    current_ping,
    execute_current,
    scenario_grid,
)

__all__ = ["oracle"]

COMPARED = ("result", "slack", "influx", "stdout")
EXPECTED_CASES = 2160
NONE_LEN = ("error", "TypeError", "object of type 'NoneType' has no len()")
NONE_ITER = ("error", "TypeError", "'NoneType' object is not iterable")

# name, cloud, legacy action on main's CRC, current action on the notifier context
CASES: list[tuple[str, str, Callable[[Any], Any], Callable[[CRC], Any]]] = [
    (
        "get_msg_list",
        "aws",
        lambda c: c.get_msg("VM", "Deleted", ["a"]),
        lambda c: current_get_msg(c, "VM", "Deleted", ["a"]),
    ),
    (
        "get_msg_empty",
        "gcp",
        lambda c: c.get_msg("Disk", "Stopped", []),
        lambda c: current_get_msg(c, "Disk", "Stopped", []),
    ),
    (
        "get_msg_none",
        "aws",
        lambda c: c.get_msg("VM", "Deleted", None),
        lambda c: current_get_msg(c, "VM", "Deleted", None),  # type: ignore[arg-type]
    ),
    (
        "get_msg_bad_operation",
        "aws",
        lambda c: c.get_msg("VM", "Gone", ["a"]),
        lambda c: current_get_msg(c, "VM", "Gone", ["a"]),
    ),
    (
        "lookup_known",
        "aws",
        lambda c: c.slack_lookup_user_by_email("alice@yugabyte.com"),
        lambda c: c.slack().lookup_user_by_email("alice@yugabyte.com"),
    ),
    (
        "lookup_unknown",
        "aws",
        lambda c: c.slack_lookup_user_by_email("zed@yugabyte.com"),
        lambda c: c.slack().lookup_user_by_email("zed@yugabyte.com"),
    ),
    ("groups", "aws", lambda c: c.get_user_groups_list(), lambda c: c.slack().user_groups()),
    (
        "write_list",
        "aws",
        lambda c: c.write_influxdb("VM", ["a", "b"]),
        lambda c: c.influx().write(c.cloud, "VM", ["a", "b"]),
    ),
    (
        "write_empty",
        "gcp",
        lambda c: c.write_influxdb("Disk", []),
        lambda c: c.influx().write(c.cloud, "Disk", []),
    ),
    (
        "write_dict",
        "aws",
        lambda c: c.write_influxdb("Disk", {"a": ["x"]}),
        lambda c: c.influx().write(c.cloud, "Disk", {"a": ["x"]}),
    ),
    (
        "write_none",
        "aws",
        lambda c: c.write_influxdb("VM", None),
        lambda c: c.influx().write(c.cloud, "VM", None),  # type: ignore[arg-type]
    ),
    (
        "ping_dict",
        "aws",
        lambda c: c.ping_on_slack("Disk", "Deleted", {"alice": ["d"], "team": ["e"], "z": ["f"]}),
        lambda c: current_ping(c, "Disk", "Deleted", {"alice": ["d"], "team": ["e"], "z": ["f"]}),
    ),
    (
        "ping_empty",
        "aws",
        lambda c: c.ping_on_slack("Disk", "Deleted", {}),
        lambda c: current_ping(c, "Disk", "Deleted", {}),
    ),
    (
        "ping_list",
        "aws",
        lambda c: c.ping_on_slack("Disk", "Deleted", ["a"]),
        lambda c: current_ping(c, "Disk", "Deleted", ["a"]),
    ),
    (
        "ping_no_len",
        "aws",
        lambda c: c.ping_on_slack("Disk", "Deleted", {"alice": 3}),
        lambda c: current_ping(c, "Disk", "Deleted", {"alice": 3}),
    ),
]

# name -> (result of main, result of the current side); every other key stays equal.
DIVERGENT: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "get_msg_none": (NONE_LEN, NONE_ITER),
}
BAD_WORD_TEXT = {
    False: "Gone the following `1` aws VM(s):\n`['a']`",
    True: "`Dry Run`: Will be Gone: `1` aws VM(s):\n`['a']`",
}


def legacy_act(name: str) -> Callable[[Any], Any]:
    return next(act for n, _, act, _ in CASES if n == name)


def compare(oracle_module: ModuleType, case: Case, name: str) -> Recorded:
    act_current = next(act for n, _, _, act in CASES if n == name)
    old = execute(oracle_module.CRC, [oracle_module], case)
    new = execute_current(case, act_current)
    assert old["adapters"] == [] == old["reads"], case.name
    if name in BUG_033_PING_DIVERGENT:
        assert_bug_033_ping(case, name, old, new)
        return old
    for key in COMPARED:
        if key == "result" and (name in DIVERGENT or name == "get_msg_bad_operation"):
            continue
        assert new[key] == old[key], f"{case.name}: {key} differs"
    if name in DIVERGENT:
        expected_old, expected_new = DIVERGENT[name]
        assert old["result"] == expected_old, case.name
        assert new["result"] == expected_new, case.name
    if name == "get_msg_bad_operation":
        assert old["result"] == ("ok", repr(BAD_WORD_TEXT[case.scenario.dry_run])), case.name
        assert new["result"] == ("error", "KeyError", "'Gone'"), case.name
    return old


def notify_cases() -> list[Any]:
    cases = []
    for (name, cloud, act, _), variant in itertools.product(CASES, range(3)):
        overrides: dict[str, Any] = {}
        if variant == GROUPS_FAIL:
            overrides = {"groups_fail": True}
        elif variant == OPEN_FAIL:
            overrides = {"open_fail": True}
        for label, scenario in scenario_grid(FULL, **overrides):
            case = Case(f"{name}-{cloud}-v{variant}-{label}", cloud, act, scenario, routed=name)
            cases.append(pytest.param(case, id=case.name))
    return cases


NOTIFY_CASES = notify_cases()


@pytest.mark.parametrize("case", NOTIFY_CASES)
def test_notify_matches_main(oracle: ModuleType, case: Case) -> None:
    assert case.routed is not None
    compare(oracle, case, case.routed)


def test_bug_030_missing_influx_settings_print_the_same_error(oracle: ModuleType) -> None:
    case = Case("bug030", "aws", legacy_act("write_list"), Scenario(dict(FULL), influx="no_conn"))
    old = compare(oracle, case, "write_list")
    assert old["stdout"] == (
        "Unable to push data to influxDB: 'CRC' object has no attribute 'resource_suffix'\n"
    )
    assert old["influx"][0][0] == "write_api"


def test_bug_033_ping_checks_groups_then_lengths(oracle: ModuleType) -> None:
    case = Case("bug033", "aws", legacy_act("ping_no_len"), Scenario(dict(FULL)))
    old = compare(oracle, case, "ping_no_len")
    assert old["result"] == ("error", "TypeError", "object of type 'int' has no len()")
    assert [call[0] for call in old["slack"]] == ["usergroups_list"]


def test_cases_are_not_vacuous(oracle: ModuleType) -> None:
    assert len(NOTIFY_CASES) == EXPECTED_CASES
    seen = {"slack": 0, "influx": 0, "stdout": 0, "error": 0, "ok": 0}
    for param in NOTIFY_CASES[::7]:
        outcome = execute(oracle.CRC, [oracle], param.values[0])
        seen["slack"] += bool(outcome["slack"])
        seen["influx"] += bool(outcome["influx"])
        seen["stdout"] += bool(outcome["stdout"])
        seen[outcome["result"][0]] += 1
    assert all(count > MIN_SEEN for count in seen.values()), seen
