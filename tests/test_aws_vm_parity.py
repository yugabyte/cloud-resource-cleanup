"""Parity: `crc.aws.vm.VM` against main's version (`tests/legacy/aws_vm.txt`).

Both run against the same fake `boto3.client`; SDK calls (in order), results, log records,
stdout and raised errors must be identical for every case. The table runs twice: through the
legacy adapter class and through the registered Cleaner (`plan()` then `apply()`, or `plan()`
alone in a dry run).
"""

import dataclasses
import datetime
import logging
import re
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError

from crc.aws.vm import VM
from crc.aws.vm.cleaner import VmCleaner, build_vm_cleaner, dry_run_names
from crc.core import registry
from crc.core.cleaner import Candidate, Operation, RunContext
from tests.helpers_log_fix import expected_logs
from tests.log_fix_aws_compute import RULES

ORACLE = Path(__file__).parent / "legacy" / "aws_vm.txt"
NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
FIXED_BUGS = {"BUG-358"}
MUTATING = ("terminate_instances", "stop_instances")
MUTATE_LOG_PREFIXES = ("Error occured while delete", "Error occurred while stop", "Instance: ")
SUMMARY_LOG_PREFIXES = ("No AWS instances", "number of AWS", "List of AWS")


def tag(key: str, value: str = "") -> dict[str, str]:
    return {"Key": key, "Value": value}


def named(name: str, *extra: dict[str, str]) -> list[dict[str, str]]:
    return [tag("Name", name), *extra]


def inst(
    iid: str | None, tags: Any = "default", hours: float | None = 48, **extra: Any
) -> dict[str, Any]:
    item: dict[str, Any] = dict(extra)
    if iid is not None:
        item["InstanceId"] = iid
        item.setdefault("NetworkInterfaces", [{"NetworkInterfaceId": f"eni-{iid}"}])
        item.setdefault("_hours", hours)
    if tags == "default":
        tags = named(f"vm-{iid}")
    if tags is not None:
        item["Tags"] = tags
    return item


def conn_error() -> Exception:
    error: Exception = EndpointConnectionError(endpoint_url="https://ec2.example")
    return error


def client_error(operation: str) -> Exception:
    error: Exception = ClientError({"Error": {"Code": "AuthFailure", "Message": "x"}}, operation)
    return error


@dataclass
class Region:
    reservations: Any = field(default_factory=list)
    describe_error: Exception | None = None
    nic_errors: Mapping[str, Exception] = field(default_factory=dict)
    nic_responses: Mapping[str, Any] = field(default_factory=dict)
    client_error: Exception | None = None


@dataclass
class Case:
    id: str
    regions: Mapping[str, Region]
    operation: str = "delete"
    dry_run: bool = False
    filter_tags: Any = field(default_factory=dict)
    exception_tags: Any = field(default_factory=dict)
    notags: Any = field(default_factory=dict)
    age: Any = field(default_factory=lambda: {"days": 1})
    custom_age_tag_key: Any = None
    instance_state: Any = None
    op_errors: Mapping[tuple[str, str], Exception] = field(default_factory=dict)
    regions_error: Exception | None = None
    env: Mapping[str, str] = field(default_factory=dict)


def one(*instances: dict[str, Any], region: str = "us-west-2", **kw: Any) -> dict[str, Region]:
    return {region: Region([{"Instances": list(instances)}], **kw)}


def _many(count: int) -> list[dict[str, Any]]:
    return [inst(f"i-{i}", hours=30 + i) for i in range(count)]


def both(case_id: str, regions: Mapping[str, Region], **kw: Any) -> list[Case]:
    return [
        Case(f"{case_id}-delete", regions, **kw),
        Case(f"{case_id}-stop", regions, operation="stop", **kw),
    ]


CASES: list[Case] = [
    *both(
        "real",
        {
            "us-west-2": Region([{"Instances": [inst("a"), inst("b", hours=1)]}]),
            "eu-west-1": Region([{"Instances": [inst("c")]}]),
        },
    ),
    *both(
        "dry-run",
        {
            "us-west-2": Region([{"Instances": [inst("a"), inst("b", hours=1)]}]),
            "eu-west-1": Region([{"Instances": [inst("c")]}]),
        },
        dry_run=True,
    ),
    *both("no-regions", {}),
    *both("empty-region", {"us-west-2": Region([]), "eu-west-1": Region([{"Instances": []}])}),
    *both("empty-region-dry", {"us-west-2": Region([])}, dry_run=True),
    *both("age-equal", one(inst("a", hours=24))),
    *both("age-just-under", one(inst("a", hours=23.99))),
    *both("age-just-over", one(inst("a", hours=24.01))),
    *both("age-hours", one(inst("a", hours=5), inst("b", hours=3)), age={"hours": 4}),
    *both(
        "age-days-hours", one(inst("a", hours=30), inst("b", hours=20)), age={"days": 1, "hours": 2}
    ),
    *both("age-empty-matches-all", one(inst("a", hours=0.01)), age={}),
    *both("age-future-attach", one(inst("a", hours=-5))),
    *both(
        "max-age-env",
        one(inst("a", hours=2), inst("b", hours=0.5)),
        env={"MAX_AGE": "{'hours': 1}"},
    ),
    *both("naive-attach-time", one(inst("a", hours=None)), age={"days": 1}),
    *both("untagged-no-key", one(inst("a", tags=None), inst("b"))),
    *both("tags-empty-list", one(inst("a", tags=[]), inst("b"))),
    *both("tags-none", one(inst("a", tags=None, Tags=None), inst("b"))),
    *both("no-name-tag", one(inst("a", tags=[tag("env", "x")]), inst("b"))),
    *both("empty-name", one(inst("a", tags=[tag("Name", "")]), inst("b"))),
    *both("name-none", one(inst("a", tags=[{"Key": "Name", "Value": None}]), inst("b"))),
    *both("first-name-wins", one(inst("a", tags=[tag("Name", "one"), tag("Name", "two")]))),
    *both("tag-missing-value-no-filters", one(inst("a", tags=[{"Key": "k"}]), inst("b"))),
    *both("tag-missing-key-no-filters", one(inst("a", tags=[{"Value": "v"}]), inst("b"))),
    *both(
        "exception-key-only",
        one(inst("a", tags=named("a", tag("keep"))), inst("b")),
        exception_tags={"keep": []},
    ),
    *both(
        "exception-value-match",
        one(
            inst("a", tags=named("a", tag("env", "prod"))),
            inst("b", tags=named("b", tag("env", "dev"))),
        ),
        exception_tags={"env": ["prod"]},
    ),
    *both(
        "exception-value-none",
        one(inst("a", tags=named("a", tag("env", "x")))),
        exception_tags={"env": None},
    ),
    *both(
        "exception-first-duplicate-wins",
        one(inst("a", tags=named("a", tag("env", "prod"), tag("env", "dev")))),
        exception_tags={"env": ["prod"]},
    ),
    *both(
        "exception-after-malformed-tag-raises",
        one(inst("a", tags=named("a", {"Key": "x"}, tag("keep"))), inst("b")),
        exception_tags={"keep": []},
    ),
    *both(
        "exception-before-malformed-tag-returns",
        one(inst("a", tags=named("a", tag("keep"), {"Key": "x"})), inst("b")),
        exception_tags={"keep": []},
    ),
    *both(
        "exception-string-values",
        one(inst("a", tags=named("a", tag("env", "pro")))),
        exception_tags={"env": "production"},
    ),
    *both(
        "notags-all-match-skips",
        one(inst("a", tags=named("a", tag("x"), tag("y", "1"))), inst("b")),
        notags={"x": [], "y": ["1"]},
    ),
    *both(
        "notags-partial-not-skipped",
        one(inst("a", tags=named("a", tag("x")))),
        notags={"x": [], "y": []},
    ),
    *both(
        "notags-value-mismatch", one(inst("a", tags=named("a", tag("x", "2")))), notags={"x": ["1"]}
    ),
    *both(
        "notags-last-duplicate-wins",
        one(inst("a", tags=named("a", tag("env", "prod"), tag("env", "dev")))),
        notags={"env": ["prod"]},
    ),
    *both(
        "notags-and-exception",
        one(inst("a", tags=named("a", tag("x"))), inst("b")),
        notags={"x": []},
        exception_tags={"keep": []},
    ),
    *both("notags-none", one(inst("a")), notags=None, exception_tags=None),
    *both("filter-tags", one(inst("a")), filter_tags={"env": ["dev", "qa"], "team": ["x"]}),
    *both("filter-tags-empty-values", one(inst("a")), filter_tags={"env": []}),
    *both("filter-tags-none", one(inst("a")), filter_tags=None),
    *both("filter-tags-dry", one(inst("a")), filter_tags={"env": ["dev"]}, dry_run=True),
    *both(
        "custom-age-valid",
        one(
            inst("a", tags=named("a", tag("ttl", "{'hours': 100}")), hours=50), inst("b", hours=50)
        ),
        custom_age_tag_key="ttl",
    ),
    *both(
        "custom-age-shorter",
        one(inst("a", tags=named("a", tag("ttl", "{'hours': 2}")), hours=3)),
        custom_age_tag_key="ttl",
    ),
    *both(
        "custom-age-unparseable-falls-back",
        one(inst("a", tags=named("a", tag("ttl", "oops")), hours=50)),
        custom_age_tag_key="ttl",
    ),
    *both(
        "custom-age-empty-falls-back",
        one(inst("a", tags=named("a", tag("ttl", "")), hours=50)),
        custom_age_tag_key="ttl",
    ),
    *both(
        "custom-age-zero",
        one(inst("a", tags=named("a", tag("ttl", "{'days': 0}")), hours=1)),
        custom_age_tag_key="ttl",
    ),
    *both("custom-age-key-absent", one(inst("a", hours=50)), custom_age_tag_key="ttl"),
    *both("instance-id-missing", one(inst(None, tags=named("x")), inst("b"))),
    *both("network-interfaces-missing", one(inst("a", NetworkInterfaces=None), inst("b"))),
    *both("network-interfaces-empty", one(inst("a", NetworkInterfaces=[]), inst("b"))),
    *both("network-interface-id-missing", one(inst("a", NetworkInterfaces=[{}]), inst("b"))),
    *both(
        "nic-no-attachment",
        one(inst("a"), inst("b"), nic_responses={"eni-a": {"NetworkInterfaces": [{}]}}),
    ),
    *both(
        "nic-empty-list",
        one(inst("a"), inst("b"), nic_responses={"eni-a": {"NetworkInterfaces": []}}),
    ),
    *both(
        "nic-client-error-isolated",
        one(inst("a"), inst("b"), nic_errors={"eni-a": client_error("DescribeNetworkInterfaces")}),
    ),
    *both(
        "nic-connection-error-not-skipped",
        one(inst("a"), inst("b"), nic_errors={"eni-a": conn_error()}),
    ),
    *both(
        "describe-client-error-propagates",
        {
            "us-west-2": Region([{"Instances": [inst("a")]}]),
            "eu-west-1": Region(describe_error=client_error("DescribeInstances")),
            "ap-south-1": Region([{"Instances": [inst("c")]}]),
        },
    ),
    *both(
        "describe-connection-error-skips",
        {
            "us-west-2": Region(describe_error=conn_error()),
            "eu-west-1": Region([{"Instances": [inst("c")]}]),
        },
    ),
    *both(
        "describe-timeout-skips",
        {
            "us-west-2": Region(describe_error=ConnectTimeoutError(endpoint_url="https://x")),
            "eu-west-1": Region([{"Instances": [inst("c")]}]),
        },
    ),
    *both("describe-unexpected-error", {"us-west-2": Region(describe_error=ValueError("boom"))}),
    *both(
        "client-creation-connection-error-skips",
        {
            "us-west-2": Region(client_error=conn_error()),
            "eu-west-1": Region([{"Instances": [inst("c")]}]),
        },
    ),
    *both(
        "client-creation-other-error", {"us-west-2": Region(client_error=ValueError("no creds"))}
    ),
    *both("reservations-key-missing", {"us-west-2": Region({})}),
    *both("instances-key-missing", {"us-west-2": Region([{"Instances": [inst("a")]}, {}])}),
    *both(
        "two-reservations",
        {"us-west-2": Region([{"Instances": [inst("a")]}, {"Instances": [inst("b")]}])},
    ),
    *both(
        "connection-error-after-earlier-region",
        {
            "us-west-2": Region([{"Instances": [inst("a")]}]),
            "eu-west-1": Region(describe_error=conn_error()),
            "ap-south-1": Region([{"Instances": [inst("c")]}]),
        },
    ),
    *both(
        "op-error-isolated", one(*_many(4)), op_errors={("us-west-2", "i-1"): client_error("Op")}
    ),
    *both(
        "op-connection-error-not-skipped",
        {
            "us-west-2": Region([{"Instances": _many(3)}]),
            "eu-west-1": Region([{"Instances": [inst("c")]}]),
        },
        op_errors={("us-west-2", "i-1"): conn_error()},
    ),
    *both(
        "op-error-on-every-instance",
        one(*_many(3)),
        op_errors={("us-west-2", f"i-{i}"): ValueError(f"no {i}") for i in range(3)},
    ),
    *both(
        "op-error-dry-run-never-called",
        one(inst("a")),
        dry_run=True,
        op_errors={("us-west-2", "a"): client_error("Op")},
    ),
    *both("duplicate-instance", one(inst("a"), inst("a", hours=60))),
    *both(
        "same-name-two-instances", one(inst("a", tags=named("same")), inst("b", tags=named("same")))
    ),
    *both(
        "many",
        {
            "us-west-2": Region([{"Instances": _many(25)}]),
            "eu-west-1": Region([{"Instances": _many(9)}]),
        },
    ),
    *both("many-dry", {"us-west-2": Region([{"Instances": _many(25)}])}, dry_run=True),
    *both(
        "mixed",
        one(
            inst("old"),
            inst("young", hours=1),
            inst("prot", tags=named("p", tag("keep"))),
            inst("noname", tags=[tag("x")]),
            inst(None, tags=None),
        ),
        exception_tags={"keep": []},
    ),
    Case("delete-custom-state", one(inst("a")), instance_state=["running", "stopped"]),
    Case("delete-custom-state-dry", one(inst("a")), instance_state=["stopped"], dry_run=True),
    Case("delete-empty-state-list", one(inst("a")), instance_state=[]),
    *both(
        "describe-regions-client-error-propagates",
        one(inst("a")),
        regions_error=client_error("DescribeRegions"),
    ),
    *both(
        "describe-regions-connection-error-falls-back", one(inst("a")), regions_error=conn_error()
    ),
]


class FakeEc2:
    def __init__(self, aws: "FakeAws", region: str) -> None:
        self.aws = aws
        self.region = region
        self.exceptions = types.SimpleNamespace(ClientError=ClientError)

    def describe_regions(self) -> dict[str, Any]:
        self.aws.calls.append(("describe_regions", self.region))
        if self.aws.case.regions_error is not None:
            raise self.aws.case.regions_error
        return {"Regions": [{"RegionName": r} for r in self.aws.case.regions]}

    def describe_instances(self, *, Filters: Sequence[Any]) -> Any:
        self.aws.calls.append(("describe_instances", self.region, repr(Filters)))
        region = self.aws.case.regions[self.region]
        if region.describe_error is not None:
            raise region.describe_error
        return (
            region.reservations
            if isinstance(region.reservations, dict)
            else {"Reservations": [self._strip(r) for r in region.reservations]}
        )

    @staticmethod
    def _strip(reservation: Mapping[str, Any]) -> dict[str, Any]:
        out = dict(reservation)
        if "Instances" in out:
            out["Instances"] = [
                {k: v for k, v in i.items() if k != "_hours"} for i in out["Instances"]
            ]
        return out

    def describe_network_interfaces(self, *, NetworkInterfaceIds: Sequence[str]) -> Any:
        eni = NetworkInterfaceIds[0]
        self.aws.calls.append(
            ("describe_network_interfaces", self.region, tuple(NetworkInterfaceIds))
        )
        region = self.aws.case.regions[self.region]
        if eni in region.nic_errors:
            raise region.nic_errors[eni]
        if eni in region.nic_responses:
            return region.nic_responses[eni]
        hours = self._hours(region, eni)
        attach = (
            NOW - datetime.timedelta(hours=hours)
            if hours is not None
            else datetime.datetime(2020, 1, 1)
        )
        return {"NetworkInterfaces": [{"Attachment": {"AttachTime": attach}}]}

    @staticmethod
    def _hours(region: Region, eni: str) -> float | None:
        for reservation in region.reservations if isinstance(region.reservations, list) else []:
            for i in reservation.get("Instances", []):
                if f"eni-{i.get('InstanceId')}" == eni:
                    return i.get("_hours")  # type: ignore[no-any-return]
        return 1

    def _operation(self, name: str, ids: Sequence[str]) -> dict[str, Any]:
        self.aws.calls.append((name, self.region, tuple(ids)))
        error = self.aws.case.op_errors.get((self.region, ids[0]))
        if error is not None:
            raise error
        return {"ResponseMetadata": {}}

    def terminate_instances(self, *, InstanceIds: Sequence[str]) -> dict[str, Any]:
        return self._operation("terminate_instances", InstanceIds)

    def stop_instances(self, *, InstanceIds: Sequence[str]) -> dict[str, Any]:
        return self._operation("stop_instances", InstanceIds)


class FakeAws:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.calls: list[tuple[Any, ...]] = []

    def client(self, service: str, region_name: str | None = None) -> FakeEc2:
        self.calls.append(("client", service, region_name))
        assert region_name is not None
        error = (
            self.case.regions[region_name].client_error
            if region_name in self.case.regions
            else None
        )
        if error is not None:
            raise error
        return FakeEc2(self, region_name)

    def session(self) -> Any:
        self.calls.append(("session",))
        return types.SimpleNamespace(get_available_regions=lambda service: list(self.case.regions))


@dataclass
class Outcome:
    calls: list[tuple[Any, ...]]
    deleted: list[str]
    count: int
    logs: list[tuple[str, int, str]]
    stdout: str
    error: tuple[str, str] | None


def _load_oracle() -> Any:
    module = types.ModuleType("legacy_aws_vm")
    exec(compile(ORACLE.read_text(), str(ORACLE), "exec"), module.__dict__)
    module.__dict__["datetime"] = types.SimpleNamespace(
        datetime=types.SimpleNamespace(now=lambda tz=None: NOW),
        timezone=datetime.timezone,
        timedelta=datetime.timedelta,
    )
    return module


def _args(case: Case) -> tuple[Any, ...]:
    return (
        case.dry_run,
        case.filter_tags,
        case.exception_tags,
        case.age,
        case.custom_age_tag_key,
        case.notags,
    )


def _legacy(case: Case) -> Any:
    return _load_oracle().VM(*_args(case))


def _new(case: Case) -> Any:
    vm = VM(*_args(case))
    vm.runtime = dataclass_replace_clock(vm.runtime)
    return vm


def dataclass_replace_clock(runtime: Any) -> Any:
    import dataclasses

    return dataclasses.replace(runtime, clock=lambda: NOW)


def _prepare(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> FakeAws:
    aws = FakeAws(case)
    monkeypatch.setattr("boto3.client", aws.client)
    monkeypatch.setattr("boto3.session.Session", aws.session)
    monkeypatch.setattr("crc.service.init_logging", lambda *args, **kwargs: None)
    monkeypatch.delenv("MAX_AGE", raising=False)
    for key, value in case.env.items():
        monkeypatch.setenv(key, value)
    caplog.clear()
    capsys.readouterr()
    return aws


_ADDRESS = re.compile(r"0x[0-9a-f]+")


def _logs(caplog: pytest.LogCaptureFixture) -> list[tuple[str, int, str]]:
    return [(r.name, r.levelno, _ADDRESS.sub("0x?", r.getMessage())) for r in caplog.records]


def _run(
    build: Callable[[Case], Any],
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    aws = _prepare(case, monkeypatch, caplog, capsys)
    vm = build(case)
    error: tuple[str, str] | None = None
    try:
        if case.operation == "stop":
            vm.stop()
        elif case.instance_state is None:
            vm.delete()
        else:
            vm.delete(case.instance_state)
    except Exception as e:
        error = (type(e).__name__, str(e))
    logs = _logs(caplog)
    stdout = capsys.readouterr().out
    stop = case.operation == "stop"
    names = list(vm.get_stopped if stop else vm.get_deleted)
    count = vm.stopped_count if stop else vm.delete_count
    return Outcome(aws.calls, names, count, logs, stdout, error)


def _run_cleaner(
    case: Case,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> Outcome:
    aws = _prepare(case, monkeypatch, caplog, capsys)
    operation = Operation(case.operation)
    options: dict[str, Any] = {
        "filter_tags": case.filter_tags,
        "exception_tags": case.exception_tags,
        "notags": case.notags,
        "age": case.age,
        "custom_age_tag_key": case.custom_age_tag_key,
    }
    if case.instance_state is not None:
        options["instance_state"] = case.instance_state
    ctx = RunContext(dry_run=case.dry_run, operation=operation, options=options, now=NOW)
    cleaner = registry.get("aws", "vm")(ctx)
    error: tuple[str, str] | None = None
    names: list[str] = []
    try:
        plan = cleaner.plan(ctx)
        if case.dry_run:
            names = dry_run_names(operation, plan.candidates)
        else:
            names = list(cleaner.apply(plan, ctx).succeeded)
    except Exception as e:
        error = (type(e).__name__, str(e))
    logs = _logs(caplog)
    return Outcome(aws.calls, names, len(names), logs, capsys.readouterr().out, error)


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    old = dataclasses.replace(old, logs=expected_logs(old.logs, FIXED_BUGS, rules=RULES))
    assert new.calls == old.calls
    assert new.error == old.error
    assert new.deleted == old.deleted
    assert new.count == old.count
    assert new.logs == old.logs
    assert new.stdout == old.stdout


def test_bug_358_tag_dump_is_debug_only_in_new_code(
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = next(c for c in CASES if c.id == "real-delete")
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    dump = "[{'Key': 'Name', 'Value': 'vm-a'}]"
    assert ("root", logging.INFO, dump) in old.logs
    assert ("root", logging.DEBUG, dump) in new.logs
    assert ("root", logging.INFO, dump) not in new.logs
    assert old.logs != new.logs
    assert new.logs == expected_logs(old.logs, {"BUG-358"}, rules=RULES)


def _is_mutating(call: tuple[Any, ...]) -> bool:
    return call[0] in MUTATING


def _is_mutate_log(record: tuple[str, int, str]) -> bool:
    message = record[2]
    return message.startswith(MUTATE_LOG_PREFIXES) or (
        message.startswith("Instance ") and message.endswith(" stopped.")
    )


def _is_summary_log(record: tuple[str, int, str]) -> bool:
    return record[2].startswith(SUMMARY_LOG_PREFIXES)


def _stream(
    logs: list[tuple[str, int, str]], pick: Callable[[tuple[str, int, str]], bool]
) -> list[tuple[str, int, str]]:
    return [r for r in logs if pick(r)]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_cleaner_protocol_matches_main(
    case: Case,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The Cleaner lists every region before it mutates, so only the interleaving of mutating
    calls and their log lines with the listing differs from main; each stream is equal, and
    with one region (or a dry run) the whole log is equal."""
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run_cleaner(case, monkeypatch, caplog, capsys)
    old = dataclasses.replace(old, logs=expected_logs(old.logs, FIXED_BUGS, rules=RULES))
    assert new.error == old.error
    assert new.stdout == old.stdout
    assert [c for c in new.calls if not _is_mutating(c)] == [
        c for c in old.calls if not _is_mutating(c)
    ]
    if new.error is not None:
        assert not any(_is_mutating(c) for c in new.calls)
        assert new.logs == _stream(
            old.logs, lambda r: not _is_mutate_log(r) and not _is_summary_log(r)
        )
        return
    assert [c for c in new.calls if _is_mutating(c)] == [c for c in old.calls if _is_mutating(c)]
    assert new.deleted == old.deleted
    if len(case.regions) <= 1 or case.dry_run:
        assert new.logs == old.logs
    listing = _stream(old.logs, lambda r: not _is_mutate_log(r) and not _is_summary_log(r))
    mutating = _stream(old.logs, _is_mutate_log)
    summary = _stream(old.logs, _is_summary_log)
    assert new.logs == [*listing, *mutating, *summary]


QUIRKS: Sequence[tuple[str, Callable[[Outcome], bool]]] = [
    ("dry-run-stop", lambda o: o.deleted == ["a", "c"]),
    ("dry-run-delete", lambda o: o.deleted == ["vm-a", "vm-c"]),
    ("real-stop", lambda o: o.deleted == ["vm-a", "vm-c"]),
    ("age-equal-delete", lambda o: o.deleted == ["vm-a"]),
    ("age-just-under-delete", lambda o: o.deleted == []),
    ("age-empty-matches-all-delete", lambda o: o.deleted == ["vm-a"]),
    ("max-age-env-delete", lambda o: o.deleted == ["vm-a"]),
    (
        "naive-attach-time-delete",
        lambda o: (
            o.deleted == []
            and any(
                r[1] == logging.ERROR and "Error occurred while processing vm-a instance" in r[2]
                for r in o.logs
            )
        ),
    ),
    ("untagged-no-key-delete", lambda o: o.deleted == ["vm-b"]),
    (
        "tags-empty-list-delete",
        lambda o: (
            o.deleted == ["vm-b"]
            and any(r[2] == "None instance doesn't have Name Tag. Skipping it" for r in o.logs)
        ),
    ),
    (
        "empty-name-delete",
        lambda o: any(r[2] == " instance doesn't have Name Tag. Skipping it" for r in o.logs),
    ),
    ("tags-none-delete", lambda o: o.deleted == ["vm-b"]),
    ("exception-key-only-delete", lambda o: o.deleted == ["vm-b"]),
    ("exception-value-match-delete", lambda o: o.deleted == ["b"]),
    ("exception-first-duplicate-wins-delete", lambda o: o.deleted == []),
    (
        "exception-after-malformed-tag-raises-delete",
        lambda o: (
            o.deleted == ["vm-b"]
            and any(r[1] == logging.ERROR and "'Value'" in r[2] for r in o.logs)
        ),
    ),
    ("exception-before-malformed-tag-returns-delete", lambda o: o.deleted == ["vm-b"]),
    ("exception-string-values-delete", lambda o: o.deleted == []),
    ("notags-all-match-skips-delete", lambda o: o.deleted == ["vm-b"]),
    ("notags-partial-not-skipped-delete", lambda o: o.deleted == ["a"]),
    ("notags-last-duplicate-wins-delete", lambda o: o.deleted == ["a"]),
    (
        "filter-tags-empty-values-delete",
        lambda o: any("'Values': []" in c[2] for c in o.calls if c[0] == "describe_instances"),
    ),
    ("custom-age-unparseable-falls-back-delete", lambda o: o.deleted == ["a"]),
    ("custom-age-empty-falls-back-delete", lambda o: o.deleted == ["a"]),
    ("custom-age-valid-delete", lambda o: o.deleted == ["vm-b"]),
    (
        "nic-connection-error-not-skipped-delete",
        lambda o: o.deleted == ["vm-b"] and not any("skipped" in r[2] for r in o.logs),
    ),
    ("describe-connection-error-skips-delete", lambda o: o.deleted == ["vm-c"]),
    ("describe-client-error-propagates-delete", lambda o: o.error is not None),
    ("reservations-key-missing-delete", lambda o: o.error == ("KeyError", "'Reservations'")),
    ("op-error-isolated-delete", lambda o: o.deleted == ["vm-i-0", "vm-i-2", "vm-i-3"]),
    ("op-error-isolated-stop", lambda o: o.deleted == ["vm-i-0", "vm-i-2", "vm-i-3"]),
    ("op-connection-error-not-skipped-delete", lambda o: o.deleted == ["vm-i-0", "vm-i-2", "vm-c"]),
    ("op-error-on-every-instance-delete", lambda o: o.error is None and o.deleted == []),
    ("duplicate-instance-delete", lambda o: o.deleted == ["vm-a", "vm-a"]),
    (
        "delete-empty-state-list",
        lambda o: any("'Values': []" in c[2] for c in o.calls if c[0] == "describe_instances"),
    ),
]


@pytest.mark.parametrize(("case_id", "check"), QUIRKS, ids=[q[0] for q in QUIRKS])
def test_quirk_is_preserved(
    case_id: str,
    check: Callable[[Outcome], bool],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = next(c for c in CASES if c.id == case_id)
    old = _run(_legacy, case, monkeypatch, caplog, capsys)
    new = _run(_new, case, monkeypatch, caplog, capsys)
    assert check(old), "oracle no longer shows the quirk"
    assert check(new)


def test_cases_cover_both_dry_run_and_real_runs() -> None:
    assert any(c.dry_run for c in CASES)
    assert any(not c.dry_run for c in CASES)
    assert {c.operation for c in CASES} == {"delete", "stop"}


def test_factory_is_registered() -> None:
    ctx = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW)
    cleaner = registry.get("aws", "vm")(ctx)
    assert isinstance(cleaner, VmCleaner)
    assert registry.get("aws", "vm") is build_vm_cleaner
    assert (cleaner.cloud, cleaner.kind) == ("aws", "vm")
    assert cleaner.supports(Operation.DELETE)
    assert cleaner.supports(Operation.STOP)
    assert Candidate(id="x").id == "x"


SCAN_REGIONS = {
    "us-west-2": Region([{"Instances": [inst("a", tags=named("one", tag("k", "v")))]}]),
    "eu-west-1": Region(describe_error=conn_error()),
    "ap-south-1": Region(describe_error=client_error("DescribeInstances")),
    "sa-east-1": Region(describe_error=ValueError("boom")),
    "ca-central-1": Region(client_error=ValueError("no client")),
}


class ScanPaginator:
    def __init__(self, aws: FakeAws, region: str) -> None:
        self.aws, self.region = aws, region

    def paginate(self, *, Filters: Sequence[Any]) -> Any:
        self.aws.calls.append(("paginate", self.region, repr(Filters)))
        region = self.aws.case.regions[self.region]
        if region.describe_error is not None:
            raise region.describe_error
        return [
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-1",
                                "Tags": [tag("Name", "n1"), tag("k", "v")],
                                "State": {"Name": "running"},
                                "InstanceType": "m5",
                                "LaunchTime": NOW,
                            },
                            {"InstanceId": "i-2", "State": {}},
                            {"InstanceId": "i-3", "Tags": [tag("other", "x")]},
                        ]
                    }
                ]
            },
            {},
        ]


class ScanEc2(FakeEc2):
    def get_paginator(self, name: str) -> ScanPaginator:
        self.aws.calls.append(("get_paginator", self.region, name))
        return ScanPaginator(self.aws, self.region)


SCAN_CASES = [
    ("regions-from-session", SCAN_REGIONS, None),
    ("session-no-regions-falls-back", {"us-west-2": Region()}, "empty-session"),
    ("session-raises-falls-back", {"us-west-2": Region()}, "bad-session"),
    ("describe-regions-fails-too", {"us-west-2": Region()}, "all-fail"),
]


def _scan_run(
    build: Callable[[], Any],
    regions: Mapping[str, Region],
    mode: str | None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> tuple[Any, list[tuple[Any, ...]], list[tuple[str, int, str]]]:
    aws = FakeAws(Case("scan", regions, regions_error=conn_error() if mode == "all-fail" else None))
    monkeypatch.setattr("boto3.client", lambda s, region_name=None: _scan_client(aws, region_name))
    session = {
        "empty-session": lambda: types.SimpleNamespace(get_available_regions=lambda s: []),
        "bad-session": lambda: (_ for _ in ()).throw(RuntimeError("no session")),
    }.get(mode or "", aws.session)
    monkeypatch.setattr("boto3.session.Session", session)
    monkeypatch.setattr("crc.service.init_logging", lambda *a, **k: None)
    caplog.clear()
    try:
        result: Any = build().find_instances_with_tag("k", "v")
    except Exception as e:
        result = (type(e).__name__, str(e))
    return result, aws.calls, _logs(caplog)


def _scan_client(aws: FakeAws, region: str | None) -> ScanEc2:
    aws.calls.append(("client", "ec2", region))
    assert region is not None
    error = aws.case.regions[region].client_error
    if error is not None:
        raise error
    return ScanEc2(aws, region)


@pytest.mark.parametrize(("name", "regions", "mode"), SCAN_CASES, ids=[c[0] for c in SCAN_CASES])
def test_find_instances_with_tag_matches_main(
    name: str,
    regions: Mapping[str, Region],
    mode: str | None,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    legacy = _scan_run(
        lambda: _load_oracle().VM(*([False, {}, {}, {}, None, {}])),
        regions,
        mode,
        monkeypatch,
        caplog,
    )
    new = _scan_run(lambda: VM(False, {}, {}, {}, "", {}), regions, mode, monkeypatch, caplog)
    assert new == legacy
    if name == "regions-from-session":
        assert [r["id"] for r in new[0]] == ["i-1", "i-2", "i-3"]


@pytest.mark.parametrize(
    "tags",
    [[tag("Name", "x")], [], [tag("k")], [{"Key": "Name"}], [{"Value": "v"}], [tag("Name", "")]],
)
def test_helpers_match_main(
    tags: list[dict[str, str]],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("crc.service.init_logging", lambda *a, **k: None)
    args: tuple[Any, ...] = (False, {"env": ["dev"]}, {"k": []}, {"days": 1}, None, {"x": []})
    old, new = _load_oracle().VM(*args), VM(*args)

    def outcome(fn: Callable[[], Any]) -> Any:
        caplog.clear()
        try:
            return ("ok", fn(), _logs(caplog))
        except Exception as e:
            return (type(e).__name__, str(e), _logs(caplog))

    assert outcome(lambda: new._get_instance_name(tags)) == outcome(
        lambda: old._get_instance_name(tags)
    )
    assert outcome(lambda: new._should_skip_instance(tags)) == outcome(
        lambda: old._should_skip_instance(tags)
    )
    assert outcome(lambda: new._get_filter(["running"])) == outcome(
        lambda: old._get_filter(["running"])
    )


FILTERED_CASES = [
    "real-delete",
    "age-just-under-delete",
    "untagged-no-key-delete",
    "no-name-tag-delete",
    "exception-key-only-delete",
    "custom-age-valid-delete",
    "nic-client-error-isolated-delete",
    "reservations-key-missing-delete",
]


@pytest.mark.parametrize("case_id", FILTERED_CASES)
def test_get_filtered_instances_matches_main(
    case_id: str,
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = next(c for c in CASES if c.id == case_id)

    def run_helper(build: Callable[[Case], Any]) -> Any:
        aws = _prepare(case, monkeypatch, caplog, capsys)
        vm = build(case)
        ec2 = aws.client("ec2", "us-west-2")
        details = ec2.describe_instances(Filters=[])
        aws.calls.clear()
        caplog.clear()
        try:
            result: Any = ("ok", vm._get_filtered_instances(ec2, details))
        except Exception as e:
            result = (type(e).__name__, str(e))
        return result, aws.calls, _logs(caplog)

    result, calls, logs = run_helper(_legacy)
    assert run_helper(_new) == (result, calls, expected_logs(logs, FIXED_BUGS, rules=RULES))


@pytest.mark.parametrize(
    "steps",
    [
        ("delete", "delete", True),
        ("delete", "delete", False),
        ("delete", "stop", False),
        ("stop", "delete", False),
    ],
)
def test_repeated_operations_match_main(
    steps: tuple[str, str, bool],
    debug_logs: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first_op, second_op, second_has_instances = steps
    first = Case("first", one(inst("a")))
    second = Case("second", one(inst("b")) if second_has_instances else {"us-west-2": Region([])})

    def run_twice(build: Callable[[Case], Any]) -> Outcome:
        aws = _prepare(first, monkeypatch, caplog, capsys)
        vm = build(first)
        getattr(vm, first_op)()
        aws.case = second
        getattr(vm, second_op)()
        logs = _logs(caplog)
        stdout = capsys.readouterr().out
        names = [*vm.get_deleted, *vm.get_stopped]
        count = vm.delete_count + vm.stopped_count
        return Outcome(aws.calls, names, count, logs, stdout, None)

    old = run_twice(_legacy)
    old = dataclasses.replace(old, logs=expected_logs(old.logs, FIXED_BUGS, rules=RULES))
    assert run_twice(_new) == old
