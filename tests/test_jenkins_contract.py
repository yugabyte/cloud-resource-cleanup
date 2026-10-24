"""Contract: every production Jenkins `crc.py` command still parses, validates, routes and runs.

`tests/fixtures/jenkins_commands.txt` holds the 47 commands of the `cloud-resource-cleanup-triggers`
Jenkinsfile. For each one this test shlex-splits the line, parses it with
`crc.cli.args.get_argparser`, runs the input validation and `validate_targets`, resolves the
routes, then runs `crc.cli.main.main` offline (fake Slack and Influx clients, `FakeCleaners`, no
credentials) and compares exit code, the (cloud, kind) pairs that ran and the options each
cleaner received with `tests/helpers_ag_cli_jenkins_expected.json`.

A change that breaks a production command fails here. When a change must alter a command's
effect on purpose, the owner refreshes the expected file: it is data, not a loosened check.
Refresh the fixture itself from the triggers repo when its jobs change.
"""

import ast
import json
import sys
from typing import Any

import pytest

from crc.cli import args as cli_args
from crc.cli import bridge
from crc.cli.dispatch import validate_targets
from crc.cli.inputs import (
    Options,
    Targets,
    check_flag_combinations,
    check_scan_tag,
    check_tokens,
    read_options,
    read_tokens,
    resolve_targets,
    validate_values,
)
from tests.helpers_ag_cli_jenkins import (
    EXPECTED,
    Command,
    describe,
    load_commands,
    run_command,
)

COMMANDS = load_commands()
FROZEN: dict[str, dict[str, Any]] = json.loads(EXPECTED.read_text())
EXPECTED_COMMANDS = 47
LITERAL_FLAGS = {
    "--filter_tags": "filter_tags",
    "--exception_tags": "exception_tags",
    "--age": "age",
    "--detach_age": "detach_age",
    "--name_regex": "name_regex",
    "--notags": "notags",
}


def _ids() -> list[str]:
    return [command.label for command in COMMANDS]


def _parse(command: Command, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(sys, "argv", ["crc.py", *command.argv])
    return cli_args.get_argparser()


def _flag_value(command: Command, flag: str) -> str | None:
    argv = command.argv
    return argv[argv.index(flag) + 1] if flag in argv else None


def test_fixture_and_expected_data_cover_the_same_47_commands() -> None:
    assert len(COMMANDS) == EXPECTED_COMMANDS
    assert sorted(FROZEN) == sorted(command.key for command in COMMANDS), (
        "tests/fixtures/jenkins_commands.txt and tests/helpers_ag_cli_jenkins_expected.json "
        "disagree: the owner refreshes the expected data when the fixture is refreshed"
    )
    assert len({command.key for command in COMMANDS}) == EXPECTED_COMMANDS


@pytest.mark.parametrize("command", COMMANDS, ids=_ids())
def test_command_parses_validates_and_routes(
    command: Command, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-offline")
    monkeypatch.delenv("INFLUXDB_TOKEN", raising=False)
    parsed = _parse(command, monkeypatch)
    options: Options = read_options(parsed)
    check_tokens(options, read_tokens())
    check_flag_combinations(options)
    targets: Targets = resolve_targets(options)
    validate_values(options)
    check_scan_tag(options)
    if options.scan_tag is None:
        validate_targets(targets)
    assert targets.clouds == [_flag_value(command, "--cloud")]
    assert targets.resources == [_flag_value(command, "--resource") or "all"]
    for flag, name in LITERAL_FLAGS.items():
        raw = _flag_value(command, flag)
        assert getattr(options, name) == (None if raw is None else ast.literal_eval(raw))
    if options.scan_tag is None:
        for cloud in targets.clouds:
            for kind in targets.resources:
                assert bridge.route_for(cloud, kind) is not None, f"no route for {cloud} {kind}"


@pytest.mark.parametrize("command", COMMANDS, ids=_ids())
def test_command_runs_offline_as_frozen(command: Command, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = run_command(command, monkeypatch)
    assert seen.error is None, f"{command.label} raised {seen.error}"
    assert seen.exit_code == 0
    actual = json.loads(json.dumps(describe(seen)))
    expected = {k: v for k, v in FROZEN[command.key].items() if k != "label"}
    assert actual == expected


@pytest.mark.parametrize("command", COMMANDS, ids=_ids())
def test_command_reaches_its_cleaner_with_the_flags(
    command: Command, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent of the frozen file: the flags of the line are what the cleaner receives."""
    seen = run_command(command, monkeypatch)
    cloud, resource = _flag_value(command, "--cloud"), _flag_value(command, "--resource")
    if _flag_value(command, "--scan_tag") is not None:
        key, _, value = str(_flag_value(command, "--scan_tag")).partition("=")
        assert seen.runs == []
        assert seen.scanned == [(key, value)]
        return
    assert [(c, k) for c, k, _ in seen.runs] == [(cloud, resource)]
    options = seen.runs[0][2]
    for flag, name in LITERAL_FLAGS.items():
        raw = _flag_value(command, flag)
        if raw is not None and (resource, name) != ("snapshot", "age"):
            assert options[name] == ast.literal_eval(raw)
    assert seen.dry_runs == [("--dry_run" in command.argv or "-d" in command.argv)]
    assert seen.channels == [_flag_value(command, "--slack_channel")]
    max_age = _flag_value(command, "--max_age")
    assert seen.max_age == [max_age]
    assert seen.max_age_after is None
