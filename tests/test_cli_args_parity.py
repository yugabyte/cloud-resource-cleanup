"""Parity: crc.cli (argument parsing and validators) against main (`tests/legacy/crc_main.txt`).

The oracle is the exact `git show main:crc.py`. The same `sys.argv` runs through the oracle
`get_argparser` and through `crc.cli.args.get_argparser`: the returned dict (keys in order),
or the `SystemExit` code with the captured stdout and stderr, must be equal. Every validator is
run on a table of values and compared on the return value, the exception type and its message.
"""

import argparse
import importlib.util
import sys
from collections.abc import Callable
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from crc.cli import args as cli_args
from crc.cli import validate as cli_validate

ORACLE = Path(__file__).parent / "legacy" / "crc_main.txt"
ROOT = Path(__file__).resolve().parent.parent
BASE = ["-c", "aws"]
MIN_CASES = 600
DEFAULTS_EXPECTED: dict[str, Any] = {
    "cloud": "aws",
    "resource": "all",
    "project_id": None,
    "operation_type": "delete",
    "resource_states": None,
    "filter_tags": None,
    "exception_tags": None,
    "name_regex": None,
    "exception_regex": None,
    "age": None,
    "custom_age_tag_key": None,
    "max_age": None,
    "detach_age": None,
    "dry_run": False,
    "notags": None,
    "slack_channel": None,
    "slack_notify_users": False,
    "slack_user_label": None,
    "influxdb": None,
    "kms_pending_window": 7,
    "kms_key_description": None,
    "kms_user": None,
    "resource_group": None,
    "scan_tag": None,
}


@pytest.fixture(scope="module")
def oracle() -> ModuleType:
    loader = SourceFileLoader("legacy_crc_main", str(ORACLE))
    spec = importlib.util.spec_from_file_location("legacy_crc_main", ORACLE, loader=loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _run(
    parse: Callable[[], dict[str, Any]],
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[Any, ...]:
    monkeypatch.setattr(sys, "argv", ["crc.py", *argv])
    monkeypatch.setenv("COLUMNS", "100")
    capsys.readouterr()
    try:
        result = parse()
    except SystemExit as exc:
        captured = capsys.readouterr()
        return ("exit", exc.code, captured.out, captured.err)
    except Exception as exc:
        captured = capsys.readouterr()
        return ("raised", type(exc), str(exc), captured.out, captured.err)
    captured = capsys.readouterr()
    return ("ok", list(result.items()), captured.out, captured.err)


def _compare(
    oracle: ModuleType,
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[Any, ...]:
    old = _run(oracle.get_argparser, argv, monkeypatch, capsys)
    new = _run(cli_args.get_argparser, argv, monkeypatch, capsys)
    assert new == old
    return new


LITERAL_FLAGS = [
    ("-s", "--resource_states"),
    ("-f", "--filter_tags"),
    ("-e", "--exception_tags"),
    ("-n", "--name_regex"),
    ("-x", "--exception_regex"),
    ("-a", "--age"),
    ("--detach_age", "--detach_age"),
    ("-t", "--notags"),
    ("-i", "--influxdb"),
]
LITERAL_VALUES = [
    '{"days": 1}',
    "{'days': 3, 'hours': 12}",
    '{"days": 1, "hours": 2}',
    "{'days': 1.5}",
    "{}",
    "[]",
    "3",
    "-5",
    "0",
    "1+2",
    "'text'",
    '"text"',
    "None",
    "True",
    "False",
    "(1, 2)",
    "{1, 2}",
    "['RUNNING', 'STOPPED']",
    '["running"]',
    "['a','b']",
    "[ 'a' , \"b\" ]",
    "{'test_task': ['test', 'stress-test']}",
    '{"test_task": ["test"], "test_owner": []}',
    "{'k': 'v'}",
    "{'k': ['v'], 'j': 'x'}",
    "{'url': 'http://localhost:8086', 'org': 'Test', 'bucket': 'CRC'}",
    "{'url': 'u', 'org': 'o'}",
    "{'k': [1, 2], 'n': {'x': [3]}}",
    "{'a': None}",
    "  {'days': 1}  ",
    "{'days': 1}\n",
    "{days: 1}",
    "{'days': }",
    "{'days': 1",
    "[",
    "]",
    "",
    " ",
    "abc",
    "true",
    "null",
    "__import__('os')",
    "open('x')",
    "[x for x in range(3)]",
    "{'a': 1} {'b': 2}",
    "1 2",
    "0x10",
    "1e3",
    "1j",
    "b'bytes'",
    "'unterminated",
    "{'a': [1, 2,]}",
    "[1,\n2]",
    "(",
    "'a' 'b'",
    "-",
    "+1",
    "--5",
    "not_a_literal",
    "{'a': set()}",
    "{'a': {1: 2}}",
    "{'é': ['ü']}",
]
PLAIN_FLAGS = [
    ("-p", "--project_id"),
    ("--custom_age_tag_key", "--custom_age_tag_key"),
    ("--max_age", "--max_age"),
    ("-m", "--slack_channel"),
    ("--slack_user_label", "--slack_user_label"),
    ("--kms_key_description", "--kms_key_description"),
    ("--kms_user", "--kms_user"),
    ("--resource_group", "--resource_group"),
    ("--scan_tag", "--scan_tag"),
]
PLAIN_VALUES = [
    "x",
    "",
    " ",
    "testing",
    "#testing",
    "retention_age",
    "auto_clean=false",
    "a=b=c",
    "{'days': 3, 'hours': 6}",
    '{"days": 1}',
    "3",
    "-1",
    "None",
    "é",
    "with space",
    "%s %(prog)s",
]
SWITCH_FLAGS = [("-d", "--dry_run"), ("--slack_notify_users", "--slack_notify_users")]
KMS_WINDOWS = [
    "7",
    "8",
    "15",
    "29",
    "30",
    "6",
    "31",
    "0",
    "-1",
    "100",
    "abc",
    "7.5",
    "",
    " 9 ",
    "1e1",
    "+9",
]


def _literal_cases() -> list[list[str]]:
    cases: list[list[str]] = []
    for short, long in LITERAL_FLAGS:
        for value in LITERAL_VALUES:
            cases.append([*BASE, long, value])
            cases.append([*BASE, f"{long}={value}"])
        cases.extend([*BASE, short, value] for value in LITERAL_VALUES[:8])
    return cases


def _plain_cases() -> list[list[str]]:
    cases: list[list[str]] = []
    for short, long in PLAIN_FLAGS:
        for value in PLAIN_VALUES:
            cases.append([*BASE, long, value])
            cases.append([*BASE, f"{long}={value}"])
        cases.append([*BASE, short, "v"])
        cases.append([*BASE, long])
    return cases


def _choice_cases() -> list[list[str]]:
    cases: list[list[str]] = []
    for cloud in ["aws", "azure", "gcp", "oci", "all", "AWS", "Azure", "", "azu", "ibm", "alibaba"]:
        cases.append(["--cloud", cloud])
        cases.append(["-c", cloud])
        cases.append([f"--cloud={cloud}"])
    resources = [
        "disk",
        "ip",
        "keypair",
        "vm",
        "vpc",
        "kms",
        "nic",
        "spot_instance_requests",
        "spot_instances",
        "snapshot",
        "all",
        "DISK",
        "volume",
        "spot",
        "",
        "keypairs",
        "bucket",
    ]
    for resource in resources:
        cases.append([*BASE, "--resource", resource])
        cases.append([*BASE, "-r", resource])
    for operation in ["delete", "stop", "start", "DELETE", "", "terminate"]:
        cases.append([*BASE, "--operation_type", operation])
        cases.append([*BASE, "-o", operation])
    for window in KMS_WINDOWS:
        cases.append([*BASE, "--kms_pending_window", window])
        cases.append([*BASE, f"--kms_pending_window={window}"])
    return cases


def _structure_cases() -> list[list[str]]:
    return [
        [],
        ["--resource", "disk"],
        ["-r", "disk", "-d"],
        ["-p", "proj"],
        ["-c"],
        ["--cloud"],
        ["-c", "-d"],
        ["-c", "--dry_run"],
        ["-c", "aws", "-c", "gcp"],
        ["-c", "aws", "--cloud", "oci"],
        ["-c", "aws", "-r", "disk", "-r", "vm"],
        ["-c", "aws", "-d", "-d"],
        ["-c", "aws", "-dd"],
        ["-caws"],
        ["-caws", "-rdisk"],
        ["-c=aws"],
        ["--cloud=aws"],
        ["--cloud", "aws", "extra"],
        ["extra"],
        ["-c", "aws", "--unknown"],
        ["-c", "aws", "--unknown", "1"],
        ["-c", "aws", "-z"],
        ["-c", "aws", "--"],
        ["-c", "aws", "--", "extra"],
        ["--", "-c", "aws"],
        ["--clo", "aws"],
        ["--cl", "aws"],
        ["--c", "aws"],
        ["--res", "disk"],
        ["--r", "disk"],
        ["-c", "aws", "--resource_s", "['a']"],
        ["-c", "aws", "--resource_st", "['a']"],
        ["-c", "aws", "--resource_g", "rg"],
        ["-c", "aws", "--resource_", "x"],
        ["-c", "aws", "--kms", "x"],
        ["-c", "aws", "--kms_u", "x"],
        ["-c", "aws", "--kms_k", "x"],
        ["-c", "aws", "--kms_p", "9"],
        ["-c", "aws", "--slack", "x"],
        ["-c", "aws", "--slack_n"],
        ["-c", "aws", "--slack_c", "x"],
        ["-c", "aws", "--slack_u", "x"],
        ["-c", "aws", "--dry"],
        ["-c", "aws", "--dry_run=1"],
        ["-c", "aws", "--slack_notify_users=1"],
        ["-c", "aws", "--det", "{'days': 1}"],
        ["-c", "aws", "--cus", "k"],
        ["-c", "aws", "--max", "3"],
        ["-c", "aws", "--scan", "a=b"],
        ["-c", "aws", "--ag", "{'days': 1}"],
        ["-c", "aws", "--age", "-1"],
        ["-c", "aws", "--age", "--dry_run"],
        ["-c", "aws", "--age", "-d"],
        ["-c", "aws", "-a", "-5"],
        ["-c", "aws", "--max_age", "-5"],
        ["-c", "aws", "--max_age", "--dry_run"],
        ["-c", "aws", "--filter_tags", "-d"],
        ["-c", "aws", "--scan_tag", "-x"],
        ["-c", "aws", "--scan_tag", "-1"],
        ["-c", "aws", "-pproj"],
        ["-c", "aws", "-dm", "chan"],
        ["-c", "aws", "-dmchan"],
        ["-c", "aws", "-mchan", "-d"],
        ["-c", "aws", "-a{'days': 1}"],
        ["-c", "aws", "-a={'days': 1}"],
        ["-c", "aws", "--age", "{'days': 1}", "--age", "{'days': 2}"],
        ["-h"],
        ["--help"],
        ["-c", "aws", "-h"],
        ["-c", "aws", "--help"],
        ["--he"],
        ["-c", "bad", "-h"],
        ["-h", "-c", "bad"],
        ["-c", "aws", "--age", "bad", "-h"],
        ["-c", "aws", "-V"],
        ["-c", "aws", "--version"],
    ]


def _combined_cases() -> list[list[str]]:
    full = [
        "--cloud",
        "gcp",
        "--resource",
        "vm",
        "--project_id",
        "testing",
        "--operation_type",
        "stop",
        "--resource_states",
        "['RUNNING', 'STOPPED']",
        "--filter_tags",
        "{'test_task': ['test', 'stress-test']}",
        "--exception_tags",
        "{'keep': ['true']}",
        "--name_regex",
        "['perftest_','qa_']",
        "--exception_regex",
        "['perftest_keep_resources']",
        "--age",
        '{"days": 1}',
        "--custom_age_tag_key",
        "retention_age",
        "--max_age",
        "{'days': 3, 'hours': 6}",
        "--detach_age",
        "{'days': 3, 'hours': 12}",
        "--dry_run",
        "--notags",
        "{'test_task': ['test'], 'test_owner': []}",
        "--slack_channel",
        "testing",
        "--slack_notify_users",
        "--slack_user_label",
        "owner",
        "--influxdb",
        "{'url': 'http://localhost:8086', 'org': 'Test', 'bucket': 'CRC', 'resource_suffix': 't'}",
        "--kms_pending_window",
        "30",
        "--kms_key_description",
        "jenkins key",
        "--kms_user",
        "arn:aws:iam::1:user/jenkins",
        "--resource_group",
        "test-rg",
        "--scan_tag",
        "auto_clean=false",
    ]
    short = [
        "-c", "azure", "-r", "kms", "-p", "p", "-o", "delete", "-s", "['a']",
        "-f", "{'a': ['b']}", "-e", "{'c': ['d']}", "-n", "['n']", "-x", "['x']",
        "-a", "{'hours': 5}", "-d", "-t", "{'t': []}", "-m", "chan",
        "-i", "{'url': 'u', 'org': 'o', 'bucket': 'b'}",
    ]  # fmt: skip
    cases = [full, short, full[::-1], [*short, "--cloud", "oci"]]
    for cloud in ["aws", "azure", "gcp", "oci", "all"]:
        for resource in ["disk", "ip", "keypair", "vm", "kms", "nic", "snapshot", "vpc"]:
            cases.extend([["--cloud", cloud, "--resource", resource, "-d"]])
    for flag, _ in SWITCH_FLAGS:
        cases.append([*BASE, flag])
    for _, long in SWITCH_FLAGS:
        cases.append([*BASE, long])
    cases.append([*BASE])
    cases.append(["--cloud", "aws"])
    return cases


ARGV_CASES = (
    _literal_cases() + _plain_cases() + _choice_cases() + _structure_cases() + _combined_cases()
)


def _case_id(argv: list[str]) -> str:
    return " ".join(argv)[:80] or "<empty>"


@pytest.mark.parametrize("argv", ARGV_CASES, ids=[_case_id(a) for a in ARGV_CASES])
def test_get_argparser_matches_main(
    oracle: ModuleType,
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _compare(oracle, argv, monkeypatch, capsys)


def test_table_is_large() -> None:
    assert len(ARGV_CASES) >= MIN_CASES


def test_defaults_are_as_documented(
    oracle: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = _compare(oracle, BASE, monkeypatch, capsys)
    assert outcome[0] == "ok"
    assert outcome[1] == list(DEFAULTS_EXPECTED.items())


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([*BASE, "--age", '{"days": 1}'], {"days": 1}),
        ([*BASE, "--age", "{'days': 3, 'hours': 12}"], {"days": 3, "hours": 12}),
        ([*BASE, "--age", "3"], 3),
    ],
)
def test_age_literal_values(
    oracle: ModuleType,
    argv: list[str],
    expected: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = _compare(oracle, argv, monkeypatch, capsys)
    assert dict(outcome[1])["age"] == expected


@pytest.mark.parametrize(
    ("argv", "code", "needle"),
    [
        (["-c", "aws", "--age", "{days: 1}"], 2, "invalid literal_eval value"),
        (["--cloud", "ibm"], 2, "invalid choice: 'ibm'"),
        ([], 2, "the following arguments are required: -c/--cloud"),
        (["-c", "aws", "--kms_pending_window", "31"], 2, "invalid choice: 31"),
        (["-c", "aws", "--kms_pending_window", "x"], 2, "invalid int value: 'x'"),
    ],
)
def test_error_text_is_pinned(
    oracle: ModuleType,
    argv: list[str],
    code: int,
    needle: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = _compare(oracle, argv, monkeypatch, capsys)
    assert outcome[0] == "exit"
    assert outcome[1] == code
    assert needle in outcome[3]


def test_help_lists_every_flag(
    oracle: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome = _compare(oracle, ["-h"], monkeypatch, capsys)
    assert outcome[:2] == ("exit", 0)
    for _, long in LITERAL_FLAGS + PLAIN_FLAGS + SWITCH_FLAGS:
        assert long in outcome[2]
    assert "--cloud" in outcome[2]
    assert "--kms_pending_window" in outcome[2]


class _Built(Exception):
    def __init__(self, parser: argparse.ArgumentParser) -> None:
        super().__init__()
        self.parser = parser


def _built_parser(
    parse: Callable[[], dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> argparse.ArgumentParser:
    def stop(self: argparse.ArgumentParser, *_: Any, **__: Any) -> None:
        raise _Built(self)

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", stop)
    with pytest.raises(_Built) as built:
        parse()
    monkeypatch.undo()
    return built.value.parser


ACTION_FIELDS = ["option_strings", "dest", "nargs", "const", "default", "type", "choices"]
ACTION_FIELDS += ["required", "help", "metavar"]
PARSER_FIELDS = ["prog", "usage", "description", "epilog", "formatter_class", "prefix_chars"]
PARSER_FIELDS += ["argument_default", "conflict_handler", "add_help", "allow_abbrev"]


def test_raw_parser_definition_matches_main(
    oracle: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = _built_parser(oracle.get_argparser, monkeypatch)
    new = _built_parser(cli_args.get_argparser, monkeypatch)
    assert [getattr(new, f) for f in PARSER_FIELDS] == [getattr(old, f) for f in PARSER_FIELDS]
    assert len(new._actions) == len(old._actions) == len(DEFAULTS_EXPECTED) + 1
    for old_action, new_action in zip(old._actions, new._actions, strict=True):
        assert type(new_action) is type(old_action)
        for field in ACTION_FIELDS:
            assert getattr(new_action, field) == getattr(old_action, field), (old_action, field)


def test_crc_py_reexports_the_same_functions() -> None:
    spec = importlib.util.spec_from_file_location("crc_py_reexport_check", ROOT / "crc.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["crc_py_reexport_check"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        del sys.modules["crc_py_reexport_check"]
    assert module.get_argparser is cli_args.get_argparser
    for name in (
        "is_valid_type",
        "is_valid_list",
        "is_valid_dict",
        "are_values_of_dict_lists",
        "_validate_influxdb_input",
        "validate_influxdb_inputs",
    ):
        assert getattr(module, name) is getattr(cli_validate, name)
    assert callable(module.main)


# ---------------------------------------------------------------------------
# validators
# ---------------------------------------------------------------------------


class _Weird:
    def __repr__(self) -> str:
        return "weird"


VALUES: list[Any] = [
    None,
    [],
    [1],
    ["a", "b"],
    {},
    {"a": []},
    {"a": ["x"], "b": ["y", "z"]},
    {"a": "x"},
    {"a": None},
    {"a": ["x"], "b": "y"},
    {"a": ("x",)},
    {"a": {"b": ["c"]}},
    {1: [1], 2: 2},
    "text",
    "",
    0,
    1,
    -1,
    1.5,
    True,
    False,
    (1,),
    (),
    {1, 2},
    b"bytes",
    _Weird(),
    {"days": 1},
    {"days": 1, "hours": 2},
]
TYPES: list[Any] = [
    list,
    dict,
    str,
    int,
    bool,
    float,
    tuple,
    object,
    type(None),
    (list, dict),
    (int, str),
    (),
]
NAMES = ["age", "filter_tags", "Value of x with key y", "", "n%s"]


def _outcome(call: Callable[[], Any]) -> tuple[Any, ...]:
    try:
        value = call()
    except Exception as exc:
        return ("raised", type(exc), str(exc))
    return ("returned", value)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("value", VALUES, ids=repr)
@pytest.mark.parametrize("expected_type", TYPES, ids=repr)
def test_is_valid_type_matches_main(
    oracle: ModuleType, name: str, value: Any, expected_type: Any
) -> None:
    old = _outcome(lambda: oracle.is_valid_type(name, value, expected_type))
    new = _outcome(lambda: cli_validate.is_valid_type(name, value, expected_type))
    assert new == old


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("value", VALUES, ids=repr)
@pytest.mark.parametrize("function", ["is_valid_list", "is_valid_dict", "are_values_of_dict_lists"])
def test_value_validators_match_main(
    oracle: ModuleType, name: str, value: Any, function: str
) -> None:
    old = _outcome(lambda: getattr(oracle, function)(name, value))
    new = _outcome(lambda: getattr(cli_validate, function)(name, value))
    assert new == old


@pytest.mark.parametrize(
    ("function", "value", "kind", "message"),
    [
        (
            "is_valid_list",
            "x",
            ValueError,
            "age should be of type <class 'list'>, but got <class 'str'>",
        ),
        (
            "is_valid_dict",
            [1],
            ValueError,
            "age should be of type <class 'dict'>, but got <class 'list'>",
        ),
        (
            "are_values_of_dict_lists",
            {"k": "v"},
            ValueError,
            "Value of age with key k should be of type <class 'list'>, but got <class 'str'>",
        ),
        ("are_values_of_dict_lists", None, None, None),
        ("is_valid_list", None, None, None),
        ("is_valid_dict", {}, None, None),
    ],
)
def test_validator_messages_are_pinned(
    function: str, value: Any, kind: type[Exception] | None, message: str | None
) -> None:
    result = _outcome(lambda: getattr(cli_validate, function)("age", value))
    if kind is None:
        assert result == ("returned", None)
    else:
        assert result == ("raised", kind, message)


INFLUX_VALUES: list[Any] = [
    {"url": "u", "org": "o", "bucket": "b"},
    {"url": "u", "org": "o", "bucket": "b", "resource_suffix": "s"},
    {"url": "u", "org": "o"},
    {"url": "u", "bucket": "b"},
    {"org": "o", "bucket": "b"},
    {"url": "u"},
    {"org": "o"},
    {"bucket": "b"},
    {},
    {"URL": "u", "ORG": "o", "BUCKET": "b"},
    {"url": None, "org": None, "bucket": None},
    ["url", "org", "bucket"],
    ["url", "org"],
    [],
    "url org bucket",
    "urlorgbucket",
    "",
    ("url", "org", "bucket"),
    {"url", "org", "bucket"},
    None,
    5,
    1.5,
    True,
    _Weird(),
]


@pytest.mark.parametrize("value", INFLUX_VALUES, ids=repr)
def test_validate_influxdb_inputs_matches_main(oracle: ModuleType, value: Any) -> None:
    old = _outcome(lambda: oracle.validate_influxdb_inputs(value))
    new = _outcome(lambda: cli_validate.validate_influxdb_inputs(value))
    assert new == old


@pytest.mark.parametrize("field", ["url", "org", "bucket", "other", "", "URL", "resource_suffix"])
@pytest.mark.parametrize("value", INFLUX_VALUES, ids=repr)
def test_validate_influxdb_input_matches_main(oracle: ModuleType, field: str, value: Any) -> None:
    old = _outcome(lambda: oracle._validate_influxdb_input(value, field))
    new = _outcome(lambda: cli_validate._validate_influxdb_input(value, field))
    assert new == old


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (
            {"org": "o", "bucket": "b"},
            "The field 'url' is required in the InfluxDB input, but was not found.",
        ),
        (
            {"url": "u", "bucket": "b"},
            "The field 'org' is required in the InfluxDB input, but was not found.",
        ),
        (
            {"url": "u", "org": "o"},
            "The field 'bucket' is required in the InfluxDB input, but was not found.",
        ),
        ({}, "The field 'url' is required in the InfluxDB input, but was not found."),
    ],
)
def test_influx_messages_are_pinned(value: dict[str, str], message: str) -> None:
    assert _outcome(lambda: cli_validate.validate_influxdb_inputs(value)) == (
        "raised",
        ValueError,
        message,
    )
    assert _outcome(
        lambda: cli_validate.validate_influxdb_inputs({"url": "u", "org": "o", "bucket": "b"})
    ) == (
        "returned",
        None,
    )
