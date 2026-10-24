"""Parity: crc.py main() against main() of the oracle (`tests/legacy/crc_main.txt`).

The oracle is the exact `git show main:crc.py`. Both `main()` functions run with the same patched
`sys.argv` and environment, recording fake CRC and Snapshot classes, fake WebClient and
InfluxDBClient classes and a recording `notify_cleanup`. For every case the ordered fake calls
and constructor arguments, the printed output, the warnings logged, `os.environ["MAX_AGE"]`
after the run and the raised error (type and message) or exit code must be equal.
"""

import contextlib
import functools
import importlib
import importlib.util
import io
import itertools
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from crc.cli import bridge
from crc.cli import clients as cli_clients
from crc.cli import context as cli_context
from crc.cli import dispatch as cli_dispatch
from crc.cli import scan as cli_scan
from crc.cli.routes import skips
from crc.core import logger
from crc.core.cleaner import Candidate, Operation, Plan, Result, RunContext
from tests import helpers_ag_cli_added as added_table
from tests import helpers_ag_cli_args as added_args
from tests.helpers_cli_routes import FakeCleaners
from tests.helpers_oracle import load_legacy_crc
from tests.helpers_routed_calls import RoutedCall

ROOT = Path(__file__).resolve().parent.parent
ORACLE = ROOT / "tests" / "legacy" / "crc_main.txt"
ENV_KEYS = [
    "SLACK_BOT_TOKEN",
    "INFLUXDB_TOKEN",
    "MAX_AGE",
    "GOOGLE_CLOUD_PROJECT",
    "GCP_PROJECT",
]
DEFAULT_ENV: dict[str, str | None] = {
    "SLACK_BOT_TOKEN": "xoxb-token",
    "INFLUXDB_TOKEN": "influx-token",
    "MAX_AGE": None,
    "GOOGLE_CLOUD_PROJECT": None,
    "GCP_PROJECT": None,
}
CLOUD_ARGS = ["aws", "azure", "gcp", "oci", "all"]
# The kinds of the oracle: every case is compared with `tests/legacy/crc_main.txt`. A kind that
# is not in the oracle is listed in tests/helpers_ag_cli_added.py and has no oracle to compare
# with; `test_added_resources_reach_their_cleaner_through_main` covers it instead.
RESOURCE_ARGS = [
    "disk",
    "ip",
    "keypair",
    "vm",
    "kms",
    "nic",
    "spot_instance_requests",
    "spot_instances",
    "snapshot",
    "all",
]
KMS_FLAGS = ["--kms_key_description", "desc", "--kms_user", "arn:user"]
INFLUX_OK = "{'url': 'http://influx:8086', 'org': 'o', 'bucket': 'b'}"
RICH_HITS: list[Any] = [
    {
        "id": "i-1",
        "name": "vm1",
        "region": "us-west-2",
        "state": "running",
        "launch_time": "2024-01-01",
        "instance_type": "t3.micro",
    },
    {"instance_id": "i-2", "display_name": "disp", "zone": "us-central1-a"},
    {"id": "/subscriptions/s/rg/vm", "name": "az1", "location": "eastus"},
    {"resource_id": "/subscriptions/s/rg/other"},
    {"name": "gcp-1", "zone": "europe-west1-b", "state": "stopped"},
    {},
]
HITS = {
    "none": [],
    "none_value": None,
    "rich": RICH_HITS,
    "weird": [{"id": 12, "name": None, "region": 1.5, "state": "x"}],
    "bad_hit": ["not a dict"],
}
HIT_ERRORS = {
    "not_impl": NotImplementedError("not supported"),
    "scan_error": RuntimeError("scan boom"),
}
MIN_CASES = 8000
RUN_ORACLE = (
    "import sys; sys.argv = ['crc.py', *sys.argv[2:]]; "
    "from tests.helpers_oracle import load_legacy_crc; load_legacy_crc().main()"
)
RUN_NEW = (
    "import sys; script = sys.argv[1]; sys.argv = ['crc.py', *sys.argv[2:]]; "
    "from tests.helpers_ag_cli_args import run_script_without_additions as run; run(script)"
)
Events = list[Any]


@dataclass(frozen=True)
class Case:
    name: str
    argv: tuple[str, ...]
    env: dict[str, str | None] = field(default_factory=dict)
    hits: str = "none"
    notify_fail: bool = False
    crc_fail: str | None = None


@dataclass
class Outcome:
    events: Events
    stdout: str
    stderr: str
    max_age: str | None
    warnings: list[tuple[int, str]]
    result: tuple[Any, ...]


class Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.records: list[tuple[int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append((record.levelno, record.getMessage()))


def load_by_path(name: str, path: Path) -> ModuleType:
    loader = SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


ORACLE_MODULE = load_legacy_crc("crc_main_oracle_parity")
NEW_MODULE = load_by_path("crc_py_main_parity", ROOT / "crc.py")


def norm(value: Any) -> Any:
    tag = getattr(value, "fake_tag", None)
    if tag is not None:
        return tag
    if isinstance(value, dict):
        return {key: norm(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [norm(item) for item in value]
    return value


def positional(args: Any, kwargs: Any, index: int, name: str) -> Any:
    """The constructor argument at `index`, else the keyword `name`."""
    return args[index] if len(args) > index else kwargs.get(name)


def check_spot_target(cloud: Any, project_id: Any, resource_group: Any) -> None:
    """The real method raises before it builds the adapter; the routes gate likewise."""
    if cloud == "azure" and not resource_group:
        raise ValueError("resource_group is required for Azure Spot VM cleanup")
    if cloud == "gcp" and not project_id:
        raise ValueError("project_id is required for GCP Spot VM cleanup")


class Fakes:
    """The recording fakes of one run; every constructor and call lands in `events`."""

    def __init__(self, case: Case) -> None:
        self.case = case
        self.events: Events = []
        self.counter = itertools.count(1)
        self.WebClient = self.make_client("WebClient")
        self.InfluxDBClient = self.make_client("InfluxDBClient")
        self.CRC = self.make_crc()
        self.Snapshot = self.make_snapshot()
        self.cleaners: FakeCleaners | None = None

    def make_client(self, kind: str) -> type:
        fakes = self

        class FakeClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.fake_tag = f"{kind}#{next(fakes.counter)}"
                fakes.events.append((kind, norm(list(args)), norm(kwargs)))

        FakeClient.__name__ = f"Fake{kind}"
        return FakeClient

    def make_crc(self) -> type:
        fakes = self

        class FakeCRC:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.cloud = args[0] if args else kwargs.get("cloud")
                self.project_id = positional(args, kwargs, 5, "project_id")
                self.resource_group = positional(args, kwargs, 6, "resource_group")
                self.fake_tag = f"CRC#{next(fakes.counter)}"
                fakes.events.append(("CRC", norm(list(args)), norm(kwargs)))

            def __getattr__(self, name: str) -> Callable[..., Any]:
                if not name.startswith(("delete_", "stop_", "scan_", "get_msg")):
                    raise AttributeError(name)

                def record(*args: Any, **kwargs: Any) -> Any:
                    if name == "delete_spot_instances":
                        check_spot_target(self.cloud, self.project_id, self.resource_group)
                    fakes.events.append((f"crc.{name}", self.cloud, norm(list(args)), norm(kwargs)))
                    if fakes.case.crc_fail == name:
                        raise RuntimeError(f"{name} failed")
                    if name == "scan_instances_with_tag":
                        error = HIT_ERRORS.get(fakes.case.hits)
                        if error is not None:
                            raise error
                        return HITS[fakes.case.hits]
                    if name == "get_msg":
                        return f"msg:{norm(list(args))}"
                    return None

                return record

        return FakeCRC

    def make_snapshot(self) -> type:
        fakes = self

        class FakeSnapshot:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                fakes.events.append(("Snapshot", norm(list(args)), norm(kwargs)))
                self.age_days = kwargs.get("age_days")
                self.delete_count = 3
                self.get_deleted = ["snap-1"]

            def delete(self) -> None:
                fakes.events.append(("Snapshot.delete",))

        return FakeSnapshot

    def notify_cleanup(self, *args: Any, **kwargs: Any) -> None:
        self.events.append(("notify_cleanup", norm(list(args)), norm(kwargs)))
        if self.case.notify_fail:
            raise RuntimeError("slack down")


@contextmanager
def environment(values: dict[str, str | None]) -> Iterator[None]:
    saved = {key: os.environ.get(key) for key in ENV_KEYS}
    try:
        for key in ENV_KEYS:
            value = values.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def execute(main: Callable[[], None], case: Case, fakes: Fakes) -> Outcome:
    out, err = io.StringIO(), io.StringIO()
    collector = Collector()
    root = logging.getLogger()
    root.addHandler(collector)
    argv = ["crc.py", *case.argv]
    saved_argv = sys.argv
    result: tuple[Any, ...]
    max_age: str | None
    with environment({**DEFAULT_ENV, **case.env}):
        sys.argv = argv
        try:
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    main()
                    result = ("returned",)
                except SystemExit as exit_:
                    result = ("exit", exit_.code)
                except Exception as error:  # noqa: BLE001 - parity harness: the exception type and message are the outcome compared with the oracle
                    result = ("error", type(error), str(error))
        finally:
            sys.argv = saved_argv
            root.removeHandler(collector)
        max_age = os.environ.get("MAX_AGE")
    return Outcome(fakes.events, out.getvalue(), err.getvalue(), max_age, collector.records, result)


def run_oracle(case: Case, monkeypatch: pytest.MonkeyPatch) -> Outcome:
    fakes = Fakes(case)
    module = ORACLE_MODULE
    monkeypatch.setattr(module, "CRC", fakes.CRC)
    monkeypatch.setattr(module, "Snapshot", fakes.Snapshot)
    monkeypatch.setattr(module, "WebClient", fakes.WebClient)
    monkeypatch.setattr(module, "InfluxDBClient", fakes.InfluxDBClient)
    monkeypatch.setattr(module, "notify_cleanup", fakes.notify_cleanup)
    outcome = execute(module.main, case, fakes)
    outcome.events = strip_unused_args(outcome.events)
    return outcome


def run_new(
    case: Case, monkeypatch: pytest.MonkeyPatch, fakes: Fakes | None = None, strip: bool = True
) -> Outcome:
    fakes = fakes or Fakes(case)
    monkeypatch.setattr(cli_context, "CRC", fakes.CRC)
    monkeypatch.setattr(cli_clients, "WebClient", fakes.WebClient)
    monkeypatch.setattr(cli_clients, "InfluxDBClient", fakes.InfluxDBClient)
    monkeypatch.setattr(cli_scan, "notify_cleanup", fakes.notify_cleanup)
    monkeypatch.setattr(
        cli_scan,
        "scan_instances_with_tag",
        lambda crc, key, value: crc.scan_instances_with_tag(key, value),
    )
    install_routed_fakes(fakes, monkeypatch)
    with added_args.stripping() if strip else contextlib.nullcontext():
        return execute(NEW_MODULE.main, case, fakes)


EVENT_WITH_KWARGS = 3
ROUTED_CALLS: dict[str, RoutedCall] = {
    "keypair": RoutedCall("delete_keypairs", ("name_regex", "exception_regex", "age")),
    "ip": RoutedCall("delete_ip", ("filter_tags", "exception_tags", None, None)),
    "kms": RoutedCall(
        "delete_kms",
        (
            "filter_tags",
            "exception_tags",
            "kms_key_description",
            "kms_user",
            "kms_pending_window",
            "age",
            "custom_age_tag_key",
        ),
    ),
    "vm": RoutedCall(
        "delete_vm",
        ("filter_tags", "exception_tags", "age", "custom_age_tag_key", "instance_state"),
        stop="stop_vm",
    ),
    "spot_instance_requests": RoutedCall(
        "delete_spot_instance_requests",
        ("filter_tags", "exception_tags", "age", "custom_age_tag_key"),
    ),
    "disk": RoutedCall(
        "delete_disks",
        (
            "filter_tags",
            "exception_tags",
            "age",
            "custom_age_tag_key",
            "detach_age",
            "name_regex",
            "exception_regex",
            None,
            None,
        ),
    ),
    "snapshot": RoutedCall("Snapshot", ()),
}


def routed_call(cloud: str, kind: str) -> RoutedCall | None:
    """The oracle call of a routed pair: the shared (AWS) table, or the cloud's own module."""
    if cloud == "aws":
        return ROUTED_CALLS.get(kind)
    try:
        module = importlib.import_module(f"tests.helpers_routed_{cloud}")
    except ImportError:
        return None
    table: dict[str, RoutedCall] = module.ROUTED_CALLS
    return table.get(kind)


def strip_unused_args(events: Events) -> Events:
    """Drop the oracle call arguments that the routed adapters never read."""
    kept: Events = []
    for event in events:
        call = None
        if str(event[0]).startswith("crc.") and len(event) > EVENT_WITH_KWARGS:
            for kind in bridge.TABLES.get(str(event[1]), {}):
                candidate = routed_call(str(event[1]), kind)
                if candidate is not None and str(event[0]).removeprefix("crc.") in (
                    candidate.method,
                    candidate.stop,
                ):
                    call = candidate
        if call is not None:
            args = [a for a, key in zip(event[2], call.keys, strict=False) if key is not None]
            event = (event[0], event[1], args, event[3])
        kept.append(event)
    return kept


def snapshot_side_effect(fakes: Fakes) -> Callable[[RunContext], None]:
    def record(context: RunContext) -> None:
        kwargs = {
            "dry_run": context.dry_run,
            "age_days": context.options["age_days"],
            "exception_tags": context.options["exception_tags"],
        }
        fakes.events.append(("Snapshot", [], norm(kwargs)))
        fakes.events.append(("Snapshot.delete",))

    return record


def install_routed_fakes(fakes: Fakes, monkeypatch: pytest.MonkeyPatch) -> None:
    """Engine-routed pairs reach a fake cleaner that records what the oracle CRC fake recorded."""
    cleaners = FakeCleaners().install(monkeypatch)
    fakes.cleaners = cleaners
    pairs = [
        (cloud, kind)
        for cloud, table in bridge.TABLES.items()
        for kind, route in table.items()
        if route.options is not skips.no_options
        and (cloud, kind) not in added_table.added_routed_pairs()
    ]
    for cloud, kind in pairs:
        call = routed_call(cloud, kind)
        assert call is not None, f"no ROUTED_CALLS entry for {cloud} {kind}"
        if kind == "snapshot":
            cleaners.side_effect[(cloud, kind)] = snapshot_side_effect(fakes)
            cleaners.plans[(cloud, kind)] = Plan(
                candidates=tuple(Candidate(f"snap-{i}") for i in range(3))
            )
            cleaners.results[(cloud, kind)] = Result(succeeded=("snap-1",))
            continue

        def record(
            context: RunContext,
            cloud: str = cloud,
            kind: str = kind,
            call: RoutedCall = call,
        ) -> None:
            assert context.dry_run is ("--dry_run" in fakes.case.argv)
            if kind in {"vm", "spot_instance_requests", "spot_instances", "disk"} or (
                kind == "ip" and cloud in {"aws", "azure"}
            ):
                assert "notags" in context.options
            if cloud == "azure":
                assert "resource_group" in context.options
                assert context.options["resource_group"] == option_value(
                    fakes.case, "--resource_group"
                )
            if context.operation is Operation.STOP:
                assert "instance_state" not in context.options
            stop = call.stop if context.operation is Operation.STOP else None
            method = stop or call.method
            keys = [key for key in call.keys if key is not None]
            if stop:
                keys = keys[:4]
            args = [norm(context.options.get(key)) for key in keys]
            fakes.events.append((f"crc.{method}", cloud, args, {}))
            if fakes.case.crc_fail == method:
                raise RuntimeError(f"{method} failed")

        cleaners.side_effect[(cloud, kind)] = record


def base_argv(
    cloud: str, resource: str, operation: str = "delete", dry_run: bool = False
) -> list[str]:
    argv = ["--cloud", cloud, "--resource", resource, "--operation_type", operation]
    if dry_run:
        argv.append("--dry_run")
    return argv


Snippet = tuple[str, list[str], dict[str, str | None]]

GOOD_SNIPPETS: list[Snippet] = [
    ("plain", [], {}),
    ("max_age_int", ["--max_age", "7"], {}),
    ("max_age_dict", ["--max_age", '{"days": 1}'], {}),
    ("max_age_zero", ["--max_age", "0"], {}),
    ("max_age_env_set", ["--max_age", "5"], {"MAX_AGE": "99"}),
    ("max_age_env_only", [], {"MAX_AGE": "99"}),
    (
        "all_filters",
        [
            "--age",
            '{"days": 1}',
            "--detach_age",
            "{'days': 2}",
            "--filter_tags",
            "{'owner': ['a', 'b'], 'task': []}",
            "--exception_tags",
            '{"keep": ["yes"]}',
            "--name_regex",
            "['^test-.*']",
            "--exception_regex",
            "['^prod']",
            "--resource_states",
            "['running', 'stopped']",
            "--custom_age_tag_key",
            "crc_age",
            "--notags",
            "{'a': []}",
            "--project_id",
            "proj",
            "--resource_group",
            "rg",
            "--kms_pending_window",
            "30",
        ],
        {},
    ),
    ("age_only", ["-a", "{'days': 3, 'hours': 12}"], {}),
    ("slack", ["-m", "chan"], {}),
    ("slack_hash", ["-m", "#chan"], {}),
    ("slack_users", ["-m", "chan", "--slack_notify_users", "--slack_user_label", "owner"], {}),
    ("slack_no_token", ["-m", "chan"], {"SLACK_BOT_TOKEN": None}),
    ("slack_empty_token", ["-m", "chan"], {"SLACK_BOT_TOKEN": ""}),
    ("slack_empty_channel", ["-m", ""], {"SLACK_BOT_TOKEN": None}),
    ("token_without_channel", [], {"SLACK_BOT_TOKEN": None, "INFLUXDB_TOKEN": None}),
    ("users_no_label", ["--slack_notify_users"], {}),
    ("users_empty_label", ["--slack_notify_users", "--slack_user_label", ""], {}),
    ("label_only", ["--slack_user_label", "owner"], {}),
    ("influx", ["-i", INFLUX_OK], {}),
    (
        "influx_suffix",
        ["-i", "{'url': 'u', 'org': 'o', 'bucket': 'b', 'resource_suffix': 's'}"],
        {},
    ),
    ("influx_no_token", ["-i", INFLUX_OK], {"INFLUXDB_TOKEN": None}),
    ("influx_empty_token", ["-i", INFLUX_OK], {"INFLUXDB_TOKEN": ""}),
    ("influx_no_url", ["-i", "{'org': 'o', 'bucket': 'b'}"], {}),
    ("influx_no_org", ["-i", "{'url': 'u', 'bucket': 'b'}"], {}),
    ("influx_no_bucket", ["-i", "{'url': 'u', 'org': 'o'}"], {}),
    ("influx_empty_dict", ["-i", "{}"], {}),
    ("influx_not_dict", ["-i", "[1]"], {}),
    ("influx_scalar", ["-i", "5"], {}),
    ("slack_and_influx", ["-m", "chan", "-i", INFLUX_OK, "--max_age", "3"], {}),
    ("bad_resource_states", ["--resource_states", "{'a': 1}"], {}),
    ("bad_filter_tags_type", ["--filter_tags", "[1]"], {}),
    ("bad_filter_tags_value", ["--filter_tags", "{'k': 'v'}"], {}),
    ("bad_exception_tags_type", ["--exception_tags", "'x'"], {}),
    ("bad_exception_tags_value", ["--exception_tags", "{'k': 1}"], {}),
    ("bad_name_regex", ["--name_regex", "'abc'"], {}),
    ("bad_exception_regex", ["--exception_regex", "{'a': 1}"], {}),
    ("bad_age", ["--age", "[1]"], {}),
    ("bad_detach_age", ["--detach_age", "5"], {}),
    ("argparse_literal_error", ["--age", "{days: 1"], {}),
    ("argparse_unknown_flag", ["--bogus"], {}),
]

BAD_SNIPPETS: list[Snippet] = [
    ("slack_no_token", ["-m", "chan"], {"SLACK_BOT_TOKEN": None}),
    ("influx_no_token", ["-i", INFLUX_OK], {"INFLUXDB_TOKEN": None}),
    ("users_no_label", ["--slack_notify_users"], {}),
    ("bad_resource_states", ["--resource_states", "{'a': 1}"], {}),
    ("bad_filter_tags", ["--filter_tags", "{'k': 'v'}"], {}),
    ("bad_exception_tags", ["--exception_tags", "[1]"], {}),
    ("bad_name_regex", ["--name_regex", "'abc'"], {}),
    ("bad_exception_regex", ["--exception_regex", "{'a': 1}"], {}),
    ("bad_age", ["--age", "[1]"], {}),
    ("bad_detach_age", ["--detach_age", "5"], {}),
    ("influx_no_url", ["-i", "{'org': 'o', 'bucket': 'b'}"], {}),
    ("influx_not_dict", ["-i", "5"], {}),
    ("scan_no_equals", ["--scan_tag", "novalue"], {}),
    ("max_age", ["--max_age", "4"], {}),
]


def merge(snippets: tuple[Snippet, ...]) -> tuple[list[str], dict[str, str | None], str]:
    argv: list[str] = []
    env: dict[str, str | None] = {}
    for _, args, values in snippets:
        argv += args
        env.update(values)
    return argv, env, "+".join(name for name, _, _ in snippets)


def product_cases() -> list[Case]:
    cases: list[Case] = []
    for cloud, resource, operation, dry_run in itertools.product(
        CLOUD_ARGS, RESOURCE_ARGS, ["delete", "stop"], [False, True]
    ):
        for name, args, env in GOOD_SNIPPETS:
            argv = [*base_argv(cloud, resource, operation, dry_run), *KMS_FLAGS, *args]
            label = f"{cloud}-{resource}-{operation}-{'dry' if dry_run else 'wet'}-{name}"
            cases.append(Case(label, tuple(argv), env))
    return cases


def pair_cases() -> list[Case]:
    cases: list[Case] = []
    bases = [
        ("aws", "vm"),
        ("aws", "disk"),
        ("aws", "kms"),
        ("all", "all"),
        ("all", "disk"),
        ("azure", "vm"),
        ("aws", "all"),
    ]
    for (cloud, resource), operation in itertools.product(bases, ["delete", "stop"]):
        for first, second in itertools.permutations(BAD_SNIPPETS, 2):
            argv, env, label = merge((first, second))
            full = [*base_argv(cloud, resource, operation), *KMS_FLAGS, *argv]
            cases.append(Case(f"{cloud}-{resource}-{operation}-{label}", tuple(full), env))
    return cases


def kms_cases() -> list[Case]:
    variants = {
        "none": [],
        "desc": ["--kms_key_description", "desc"],
        "user": ["--kms_user", "arn:user"],
        "both": KMS_FLAGS,
        "empty_desc": ["--kms_key_description", "", "--kms_user", "arn:user"],
        "empty_user": ["--kms_key_description", "desc", "--kms_user", ""],
    }
    cases = []
    for cloud, resource, operation in itertools.product(
        CLOUD_ARGS, RESOURCE_ARGS, ["delete", "stop"]
    ):
        for name, flags in variants.items():
            argv = [*base_argv(cloud, resource, operation), *flags]
            cases.append(Case(f"kms-{cloud}-{resource}-{operation}-{name}", tuple(argv)))
            wet = [*argv, "--dry_run", "--slack_notify_users"]
            cases.append(Case(f"kms-users-{cloud}-{resource}-{operation}-{name}", tuple(wet)))
    return cases


def failure_cases() -> list[Case]:
    methods = [
        ("disk", "delete_disks"),
        ("ip", "delete_ip"),
        ("keypair", "delete_keypairs"),
        ("nic", "delete_nic"),
        ("vm", "delete_vm"),
        ("kms", "delete_kms"),
        ("spot_instance_requests", "delete_spot_instance_requests"),
        ("spot_instances", "delete_spot_instances"),
        ("snapshot", "get_msg"),
    ]
    cases = []
    for cloud, (resource, method), max_age in itertools.product(
        ["aws", "azure", "gcp", "oci"], methods, [None, "6"]
    ):
        argv = [*base_argv(cloud, resource), *KMS_FLAGS]
        if max_age:
            argv += ["--max_age", max_age]
        cases.append(Case(f"fail-{cloud}-{method}-{max_age}", tuple(argv), crc_fail=method))
    stop = [*base_argv("aws", "vm", "stop"), "--max_age", "2"]
    cases.append(Case("fail-stop-vm", tuple(stop), crc_fail="stop_vm"))
    return cases


def scan_cases() -> list[Case]:
    cases = []
    tags = ["k=v", "novalue", "k=", "=v", "a=b=c", "=", " ", ""]
    hit_modes = [*HITS, *HIT_ERRORS]
    channels: list[tuple[str, list[str], dict[str, str | None]]] = [
        ("noslack", [], {}),
        ("slack", ["-m", "chan"], {}),
        ("slack_hash", ["-m", "#chan"], {}),
        ("slack_notoken", ["-m", "chan"], {"SLACK_BOT_TOKEN": None}),
    ]
    for cloud, tag, dry_run, hit, (channel, flags, env) in itertools.product(
        CLOUD_ARGS, tags, [False, True], hit_modes, channels
    ):
        argv = [*base_argv(cloud, "vm", "delete", dry_run), "--scan_tag", tag, *flags]
        notify_fails = [False, True] if channel in ("slack", "slack_hash") else [False]
        for fail in notify_fails:
            name = f"scan-{cloud}-{tag}-{dry_run}-{hit}-{channel}-{fail}"
            cases.append(Case(name, tuple(argv), env, hit, fail))
    extra: list[tuple[str, list[str], dict[str, str | None]]] = [
        ("max_age", ["--max_age", "9"], {}),
        ("max_age_bad_tag", ["--max_age", "9", "--scan_tag", "nope"], {}),
        ("influx", ["-i", INFLUX_OK], {}),
        ("influx_bad", ["-i", "{'url': 'u'}"], {}),
        ("project", ["--project_id", "myproj"], {}),
        ("env_project", [], {"GOOGLE_CLOUD_PROJECT": "envproj"}),
        ("env_gcp_project", [], {"GCP_PROJECT": "gcpproj"}),
        ("stop_op", ["--operation_type", "stop"], {}),
        ("bad_age", ["--age", "[1]"], {}),
        ("notags", ["-t", "{'a': []}", "--resource_group", "rg"], {}),
    ]
    for cloud, hit, (name, flags, env), resource in itertools.product(
        CLOUD_ARGS, hit_modes, extra, ["vm", "disk", "all", "kms"]
    ):
        argv = [*base_argv(cloud, resource), *KMS_FLAGS, "--scan_tag", "k=v", *flags]
        cases.append(Case(f"scan-extra-{cloud}-{resource}-{hit}-{name}", tuple(argv), env, hit))
        if hit == "rich":
            slack = [*argv, "-m", "chan"]
            cases.append(
                Case(f"scan-extra-slack-{cloud}-{resource}-{name}", tuple(slack), env, hit)
            )
    return cases


TABLES: dict[str, list[Case]] = {
    "product": product_cases(),
    "pairs": pair_cases(),
    "kms": kms_cases(),
    "failures": failure_cases(),
    "scan": scan_cases(),
}
ALL_CASES = [case for table in TABLES.values() for case in table]


UNSUPPORTED_PAIR_CALLS: dict[str, tuple[tuple[str, ...], str]] = {
    "crc.delete_keypairs": (("aws",), "Keypair operation is only supported on AWS."),
    "crc.delete_nic": (("azure",), "NICs operation is only supported on Azure."),
    "crc.delete_kms": (("aws",), "KMS operation is only supported on AWS."),
    "crc.delete_ip": (
        ("aws", "azure", "gcp"),
        "Invalid cloud provided: {cloud}. Supported clouds are ['aws', 'azure', 'gcp', 'oci']",
    ),
}
SKIP_WARNING = "{resource} is not supported on {cloud}; skipped under --cloud all"
RESOURCE_OF_CALL = {
    "crc.delete_keypairs": "keypair",
    "crc.delete_nic": "nic",
    "crc.delete_kms": "kms",
    "crc.delete_ip": "ip",
}


def before_first_cleaner(events: Events) -> Events:
    index = next((i for i, event in enumerate(events) if event[0] == "CRC"), len(events))
    return events[:index]


def diverging_outcome(case: Case, expected: Outcome) -> tuple[str, Outcome] | None:
    """The (bug id, new outcome) of a case that triggers BUG-017 or BUG-018, else None.

    The trigger is read from what the oracle did, never from the new code: a pair the oracle
    handed to a CRC method that raises for that cloud, or the EBS breaker error under
    `--cloud all`. The new outcome is the oracle outcome with exactly the documented change.
    """
    cloud_all = list(case.argv[:2]) == ["--cloud", "all"]
    if expected.result[0] == "error" and expected.result[2] == dispatch_breaker_message():
        assert cloud_all
        events = before_first_cleaner(expected.events)
        return "BUG-018", without_reset(case, expected, events, expected.result)
    bad = [
        event
        for event in expected.events
        if event[0] in UNSUPPORTED_PAIR_CALLS
        and event[1] not in UNSUPPORTED_PAIR_CALLS[event[0]][0]
    ]
    if not bad:
        return None
    calls = [event for event in expected.events if event[0] in UNSUPPORTED_PAIR_CALLS]
    stops_main = calls.index(bad[0]) < len(calls) - len(bad)
    if not cloud_all or stops_main:
        assert cloud_all or len(bad) == 1
        message = UNSUPPORTED_PAIR_CALLS[bad[0][0]][1].format(cloud=bad[0][1])
        return "BUG-017", without_reset(
            case, expected, before_first_cleaner(expected.events), ("error", ValueError, message)
        )
    bad_clouds = {e[1] for e in bad}
    events = [
        event
        for event in expected.events
        if event not in bad and not (event[0] == "CRC" and event[1][0] in bad_clouds)
    ]
    warnings = [
        (logging.WARNING, SKIP_WARNING.format(resource=RESOURCE_OF_CALL[e[0]], cloud=e[1]))
        for e in bad
    ]
    return "BUG-017", Outcome(
        events,
        expected.stdout,
        expected.stderr,
        expected.max_age,
        [*expected.warnings, *warnings],
        expected.result,
    )


def without_reset(
    case: Case, expected: Outcome, events: Events, result: tuple[Any, ...]
) -> Outcome:
    """`main()` checks every pair before `MAX_AGE` is set, so an error leaves
    the environment untouched and nothing is printed."""
    original_max_age = {**DEFAULT_ENV, **case.env}["MAX_AGE"]
    return Outcome(events, "", expected.stderr, original_max_age, expected.warnings, result)


def dispatch_breaker_message() -> str:
    return cli_dispatch.AWS_DISK_OPT_IN_MESSAGE


def test_table_is_large_and_has_unique_names() -> None:
    assert len(ALL_CASES) >= MIN_CASES
    names = [case.name for case in ALL_CASES]
    assert len(set(names)) == len(names)


SCAN_FORMAT_ERROR = "--scan_tag must be in KEY=VALUE format, e.g. auto_clean=false"
BLANK_SCAN_ERROR = "--scan_tag must not be empty; use KEY=VALUE format, e.g. auto_clean=false"
AWS_ONLY = ("snapshot",)
SKIP_SUFFIX = "; skipped under --cloud all"
CLIENT_EVENTS = ("WebClient", "InfluxDBClient")


def option_value(case: Case, flag: str) -> str | None:
    argv = case.argv
    return argv[argv.index(flag) + 1] if flag in argv else None


def diverges(case: Case, expected: Outcome) -> str | None:
    """The bug ID whose fix changes the outcome of this case against the oracle, if any."""
    tag = option_value(case, "--scan_tag")
    if tag is not None:
        return blank_scan_diverges(tag, expected)
    if not any(str(event[0]).startswith("CRC") for event in expected.events):
        return None
    cloud, resource = option_value(case, "--cloud"), option_value(case, "--resource")
    if resource in AWS_ONLY and cloud in ("azure", "gcp", "oci"):
        return "BUG-009"
    if cloud == "all" and any(event[0] == "Snapshot" for event in expected.events):
        return "BUG-009"
    return None


def blank_scan_diverges(tag: str, expected: Outcome) -> str | None:
    if tag.strip():
        return None
    reached = any(str(event[0]).startswith("CRC") for event in expected.events)
    return "BUG-344" if reached or expected.result[2:] == (SCAN_FORMAT_ERROR,) else None


def snapshot_line(case: Case) -> str:
    if "--dry_run" in case.argv:
        return "DRY RUN: AWS Snapshots eligible for deletion (>90 days): 3"
    return "msg:['Snapshot', 'Deleted', ['snap-1']]"


def expected_fixed(case: Case, bug: str, oracle: Outcome) -> Outcome:
    """The outcome the fix must give, derived from the oracle outcome of the same case."""
    original_max_age = {**DEFAULT_ENV, **case.env}["MAX_AGE"]
    events = [event for event in oracle.events if event[0] in CLIENT_EVENTS]
    if bug == "BUG-344":
        result = ("error", ValueError, BLANK_SCAN_ERROR)
        return Outcome(events, "", oracle.stderr, original_max_age, [], result)
    resource = option_value(case, "--resource")
    if option_value(case, "--cloud") != "all":
        result = (
            "error",
            ValueError,
            f"--resource {resource} is AWS-only; use --cloud aws "
            f"(got --cloud {option_value(case, '--cloud')})",
        )
        return Outcome(events, "", oracle.stderr, original_max_age, [], result)
    return fixed_all(case, oracle)


def fixed_all(case: Case, oracle: Outcome) -> Outcome:
    """`--cloud all`: snapshot runs for AWS only; no CRC is built for the skipped clouds
    (the up-front pair check) and each logs a skip warning."""
    events: Events = []
    seen_snapshot = False
    drop_delete = False
    for event in oracle.events:
        if event[0] == "Snapshot":
            drop_delete = seen_snapshot
            seen_snapshot = True
            if drop_delete:
                continue
        if event[0] == "Snapshot.delete" and drop_delete:
            drop_delete = False
            continue
        if event[0] == "crc.get_msg" and event[1] != "aws":
            continue
        if event[0] == "CRC" and event[1][0] != "aws":
            continue
        events.append(event)
    line = snapshot_line(case)
    stdout_lines = oracle.stdout.splitlines(keepends=True)
    kept, seen_line = [], False
    for text in stdout_lines:
        if text == line + "\n":
            if seen_line:
                continue
            seen_line = True
        kept.append(text)
    return Outcome(events, "".join(kept), oracle.stderr, oracle.max_age, [], oracle.result)


@pytest.mark.parametrize("case", ALL_CASES, ids=[case.name for case in ALL_CASES])
def test_main_matches_oracle(case: Case, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = run_oracle(case, monkeypatch)
    actual = run_new(case, monkeypatch)
    bug = diverges(case, expected)
    if bug is None:
        diverged = diverging_outcome(case, expected)
        if diverged is not None:
            _bug, expected = diverged
        assert actual.result == expected.result
        assert actual.events == expected.events
        assert actual.stdout == expected.stdout
        assert actual.stderr == expected.stderr
        assert actual.warnings == expected.warnings
        assert actual.max_age == expected.max_age
        return
    fixed = expected_fixed(case, bug, expected)
    assert actual.result == fixed.result, bug
    assert actual.events == fixed.events, bug
    assert actual.stdout == fixed.stdout, bug
    assert actual.stderr == fixed.stderr, bug
    assert actual.max_age == fixed.max_age, bug
    assert actual.result != expected.result or actual.events != expected.events, bug
    if bug == "BUG-344" or option_value(case, "--cloud") != "all":
        assert actual.warnings == expected.warnings, bug
        return
    added = [msg for _, msg in actual.warnings if msg.endswith(SKIP_SUFFIX)]
    assert [rec for rec in actual.warnings if not rec[1].endswith(SKIP_SUFFIX)] == (
        expected.warnings
    ), bug
    resource = str(option_value(case, "--resource"))
    assert added == [
        SKIP_WARNING.format(resource=resource, cloud=cloud) for cloud in ("azure", "gcp", "oci")
    ], bug


def test_every_fixed_bug_has_diverging_cases() -> None:
    candidates = [
        case
        for case in ALL_CASES
        if option_value(case, "--resource") in AWS_ONLY
        or not (option_value(case, "--scan_tag") or "x").strip()
    ]
    seen: set[str] = set()
    mp = pytest.MonkeyPatch()
    try:
        for case in candidates[:: max(1, len(candidates) // 600)]:
            bug = diverges(case, run_oracle(case, mp))
            if bug:
                seen.add(bug)
    finally:
        mp.undo()
    assert seen == {"BUG-009", "BUG-344"}


def test_diverging_cases_exist_and_differ_from_the_oracle(monkeypatch: pytest.MonkeyPatch) -> None:
    bugs: dict[str, int] = {}
    for case in TABLES["product"]:
        expected = run_oracle(case, monkeypatch)
        diverged = diverging_outcome(case, expected)
        if diverged is None:
            continue
        bugs[diverged[0]] = bugs.get(diverged[0], 0) + 1
        actual = run_new(case, monkeypatch)
        assert (actual.events, actual.result, actual.warnings) != (
            expected.events,
            expected.result,
            expected.warnings,
        )
    assert set(bugs) == {"BUG-017", "BUG-018"}


def test_table_reaches_every_outcome_and_every_call() -> None:
    results: set[str] = set()
    calls: set[str] = set()
    mp = pytest.MonkeyPatch()
    try:
        for case in ALL_CASES[:: max(1, len(ALL_CASES) // 1500)] + TABLES["failures"]:
            outcome = run_oracle(case, mp)
            kind = outcome.result[0]
            results.add(kind if kind != "error" else f"error:{outcome.result[1].__name__}")
            calls.update(str(event[0]) for event in outcome.events)
    finally:
        mp.undo()
    assert {
        "returned",
        "exit",
        "error:ValueError",
        "error:OSError",
        "error:RuntimeError",
    } <= results
    assert {"WebClient", "InfluxDBClient", "CRC", "Snapshot", "Snapshot.delete"} <= calls
    assert {"notify_cleanup", "crc.scan_instances_with_tag", "crc.get_msg"} <= calls
    assert {"crc.delete_disks", "crc.delete_vm", "crc.stop_vm", "crc.delete_kms"} <= calls


@pytest.mark.parametrize(
    "argv",
    [["--help"], ["--cloud", "aws", "--resource", "all"], ["--cloud", "bogus"]],
    ids=["help", "all_resources_needs_all_clouds", "bad_choice"],
)
def test_script_entry_point_matches_oracle(argv: list[str]) -> None:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    for key in ENV_KEYS:
        env.pop(key, None)

    def run(command: list[str]) -> tuple[int, str, str]:
        done = subprocess.run(
            [sys.executable, *command, *argv],
            capture_output=True,
            text=True,
            env=env,
            cwd=ROOT,
            check=False,
        )
        stderr = done.stderr.strip().splitlines()
        return done.returncode, done.stdout, stderr[-1] if stderr else ""

    oracle = run(["-c", RUN_ORACLE, str(ORACLE)])
    if added_args.has_additions():
        new = run(["-c", RUN_NEW, str(ROOT / "crc.py")])
    else:
        new = run([str(ROOT / "crc.py")])
    assert new[0] == oracle[0]
    assert new[1] == oracle[1]
    assert new[2] == oracle[2]


def test_crc_py_reexports_the_names_the_contract_needs() -> None:
    names = [
        "main",
        "get_argparser",
        "is_valid_type",
        "is_valid_list",
        "is_valid_dict",
        "are_values_of_dict_lists",
        "validate_influxdb_inputs",
        "_validate_influxdb_input",
    ]
    for name in names:
        assert hasattr(NEW_MODULE, name), name
        assert hasattr(ORACLE_MODULE, name), name


def test_bug_360_main_logs_run_start_and_result_around_each_cleaner_pass(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(logger, "begin", functools.partial(logger.begin, clock=lambda: 0.0))
    single = Case(
        "single-pass",
        ("--cloud", "aws", "--resource", "keypair", "--age", '{"days": 1}'),
    )
    caplog.clear()
    oracle_single = run_oracle(single, monkeypatch)
    oracle_logs = [(r.levelno, r.getMessage()) for r in caplog.records]
    caplog.clear()
    new_single = run_new(single, monkeypatch)
    new_logs = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert new_single == oracle_single
    assert oracle_logs == []
    assert oracle_logs != new_logs
    assert new_logs == [
        (
            logging.INFO,
            'RUN START cloud=aws resource=keypair operation=delete dry_run=false age={"days":1}',
        ),
        (
            logging.INFO,
            "RESULT cloud=aws resource=keypair operation=delete dry_run=false selected=0 "
            "done=0 failed=0 skipped=0 regions=0 regions_skipped=0 duration_s=0",
        ),
    ]

    scan_case = Case("scan-pass", ("--cloud", "aws", "--resource", "vm", "--scan_tag", "k=v"))
    caplog.clear()
    oracle_scan = run_oracle(scan_case, monkeypatch)
    assert caplog.records == []
    caplog.clear()
    new_scan = run_new(scan_case, monkeypatch)
    assert new_scan == oracle_scan
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.INFO, "RUN START cloud=aws resource=vm operation=scan dry_run=true"),
        (
            logging.INFO,
            "RESULT cloud=aws resource=vm operation=scan dry_run=true selected=0 "
            "done=0 failed=0 skipped=0 regions=0 regions_skipped=0 duration_s=0",
        ),
    ]


@pytest.mark.parametrize(
    ("cloud", "kind"), sorted(added_table.added_routed_pairs()), ids=lambda v: str(v)
)
def test_added_resources_reach_their_cleaner_through_main(
    cloud: str, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A kind that is not in the oracle runs through `main()` and gets the parsed flags."""
    argv = (
        "--cloud",
        cloud,
        "--resource",
        kind,
        "--resource_group",
        "rg",
        "--project_id",
        "proj",
        "--dry_run",
        "--age",
        "{'days': 2}",
    )
    case = Case(f"added-{cloud}-{kind}", argv)
    fakes = Fakes(case)
    outcome = run_new(case, monkeypatch, fakes, strip=False)
    assert outcome.result in (("returned",), ("exit", 0)), outcome.result
    assert fakes.cleaners is not None
    pairs = [(c, k) for c, k, _ in fakes.cleaners.runs]
    assert pairs == [(cloud, kind)]
    assert fakes.cleaners.contexts[0].dry_run is True
