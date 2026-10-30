"""Logger lines of the AWS VM, disk and spot instance request cleaners.

Each test begins a run with a fixed clock, drives the cleaner (dry run, real run, a failure, a
connectivity skip) and asserts the exact REGION / ACTION / SKIPPED / ERRORS / FAILED / RESULT
lines. Parity tests run without a begun run, so they never see these lines.
"""

import datetime
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from crc.aws.disk import DiskCleaner, DiskRuntime
from crc.aws.disk.settings import build_settings
from crc.aws.spot_instance_requests import SpotRequestCleaner, SpotRuntime
from crc.aws.spot_instance_requests.model import Waiter
from crc.aws.vm.cleaner import VmCleaner, VmSettings
from crc.core import logger, registry
from crc.core.cleaner import Operation, RunContext
from tests.helpers_ad_vm_engine import run_vm
from tests.helpers_failed_once import assert_failure_printed_once
from tests.test_aws_disk_cleaner import FakeCloudWatch, FakeEc2, volume
from tests.test_aws_spot_instance_requests_parity import (
    Case,
    FakeAws,
    Spec,
    ce,
    one,
    req,
    std,
    swept,
)
from tests.test_aws_vm import NOW as VM_NOW
from tests.test_aws_vm import FakeClient as VmFakeClient
from tests.test_aws_vm import instance

NOW = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=datetime.UTC)
SPOT = "cloud=aws resource=spot_instance_requests region=us-west-2"
PREFIXES = ("RUN START", "REGION", "ACTION", "SKIPPED", "ERRORS", "FAILED", "RESULT")


@pytest.fixture(autouse=True)
def _info_and_clean_run(caplog: pytest.LogCaptureFixture) -> Iterator[None]:
    caplog.set_level(logging.DEBUG)
    yield
    logger.finish()


def begin(
    caplog: pytest.LogCaptureFixture,
    resource: str,
    operation: str = "delete",
    dry_run: bool = False,
) -> None:
    caplog.clear()
    logger.begin("aws", resource, operation, dry_run, {}, clock=lambda: 0.0)


def lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    logger.finish()
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(PREFIXES)]


def conn_error() -> Exception:
    error: Exception = EndpointConnectionError(endpoint_url="https://ec2.example")
    return error


def result_line(resource: str, operation: str, dry_run: bool, counts: str) -> str:
    return (
        f"RESULT cloud=aws resource={resource} operation={operation} "
        f"dry_run={'true' if dry_run else 'false'} {counts}"
    )


def vm_context(operation: Operation, dry_run: bool) -> RunContext:
    return RunContext(dry_run=dry_run, operation=operation, options={}, now=VM_NOW)


def vm_cleaner(client: VmFakeClient, settings: VmSettings | None = None) -> VmCleaner:
    default = VmSettings({}, {}, {}, {"days": 1}, None, ["running"])
    return VmCleaner(lambda region: client, lambda: ["us-west-2"], settings or default)


# ---- VM ----


def test_vm_dry_run_lines(caplog: pytest.LogCaptureFixture) -> None:
    begin(caplog, "vm", "stop", True)
    client = VmFakeClient([instance("a"), instance("b")])
    vm_cleaner(client).plan(vm_context(Operation.STOP, True))
    assert lines(caplog) == [
        "RUN START cloud=aws resource=vm operation=stop dry_run=true",
        "REGION cloud=aws resource=vm region=us-west-2 scanned=2 selected=2 seconds=0",
        "ACTION would_stop cloud=aws resource=vm region=us-west-2 id=a name=n-a",
        "ACTION would_stop cloud=aws resource=vm region=us-west-2 id=b name=n-b",
        result_line(
            "vm",
            "stop",
            True,
            "selected=2 done=0 failed=0 skipped=0 regions=1 regions_skipped=0 duration_s=0",
        ),
    ]


def test_vm_real_run_with_a_failure_lists_it_at_the_end(caplog: pytest.LogCaptureFixture) -> None:
    begin(caplog, "vm")
    client = VmFakeClient([instance("a"), instance("b"), instance("c")], fail={"b"})
    cleaner = vm_cleaner(client)
    cleaner.apply(
        cleaner.plan(vm_context(Operation.DELETE, False)), vm_context(Operation.DELETE, False)
    )
    out = lines(caplog)
    assert "ACTION deleted cloud=aws resource=vm region=us-west-2 id=a name=n-a" in out
    assert "ACTION deleted cloud=aws resource=vm region=us-west-2 id=c name=n-c" in out
    assert not any("id=b" in line and line.startswith("ACTION") for line in out)
    assert out[-2:] == [
        "ERRORS count=1",
        result_line(
            "vm",
            "delete",
            False,
            "selected=2 done=2 failed=1 skipped=0 regions=1 regions_skipped=0 duration_s=0",
        ),
    ]
    assert caplog.records[-1].levelno == logging.INFO
    errors = [r.levelno for r in caplog.records if r.getMessage().startswith(("ERRORS", "FAILED"))]
    assert errors == [logging.ERROR]
    assert_failure_printed_once(caplog, "instance b: denied")


def test_vm_connectivity_skip_counts_the_region(caplog: pytest.LogCaptureFixture) -> None:
    begin(caplog, "vm")

    def factory(region: str) -> Any:
        raise conn_error()

    cleaner = VmCleaner(
        factory, lambda: ["us-west-2"], VmSettings({}, {}, {}, {"days": 1}, "", ["running"])
    )
    run_vm("aws", cleaner, now=NOW)
    assert "skipped ec2 vm delete" in caplog.text
    assert lines(caplog)[-1] == result_line(
        "vm",
        "delete",
        False,
        "selected=0 done=0 failed=0 skipped=0 regions=0 regions_skipped=1 duration_s=0",
    )


def test_vm_engine_real_run_and_skip_reasons(caplog: pytest.LogCaptureFixture) -> None:
    begin(caplog, "vm")
    client = VmFakeClient(
        [
            instance("old"),
            {"InstanceId": "bare"},
            {
                **instance("keep"),
                "Tags": [{"Key": "Name", "Value": "k"}, {"Key": "keep", "Value": ""}],
            },
            {**instance("noname"), "Tags": [{"Key": "env", "Value": "x"}]},
        ]
    )
    cleaner = VmCleaner(
        lambda r: client,
        lambda: ["us-west-2"],
        VmSettings({}, {"keep": []}, {}, {"days": 1}, "", ["running"]),
    )
    run_vm("aws", cleaner, operation=Operation.STOP, now=NOW)
    assert lines(caplog)[1:] == [
        "REGION cloud=aws resource=vm region=us-west-2 scanned=4 selected=1 seconds=0",
        "ACTION stopped cloud=aws resource=vm region=us-west-2 id=old name=n-old",
        "SKIPPED cloud=aws resource=vm reason=exception_tag count=1",
        "SKIPPED cloud=aws resource=vm reason=no_tags count=1",
        "ERRORS count=1",
        "FAILED cloud=aws resource=vm region=us-west-2 id=noname "
        "error=instance doesn't have Name Tag",
        result_line(
            "vm",
            "delete",
            False,
            "selected=1 done=1 failed=1 skipped=2 regions=1 regions_skipped=0 duration_s=0",
        ),
    ]


def test_vm_tag_dump_is_debug(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    vm_cleaner(VmFakeClient([instance("a")])).plan(vm_context(Operation.DELETE, False))
    assert "Key" not in caplog.text
    caplog.set_level(logging.DEBUG)
    vm_cleaner(VmFakeClient([instance("a")])).plan(vm_context(Operation.DELETE, False))
    assert any(
        r.levelno == logging.DEBUG and "'Key': 'Name'" in r.getMessage() for r in caplog.records
    )


# ---- disk ----


def disk_cleaner(
    ec2: FakeEc2, regions: tuple[str, ...] = ("us-west-2",), **options: Any
) -> DiskCleaner:
    runtime = DiskRuntime(
        ec2_factory=lambda region: ec2,
        cloudwatch_factory=lambda region: FakeCloudWatch(),
        list_regions=lambda: regions,
    )
    return DiskCleaner(build_settings({"age": {"days": 1}, **options}), runtime)


def disk_context(dry_run: bool) -> RunContext:
    return RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=NOW)


def test_disk_dry_run_lines_and_skip_reasons(caplog: pytest.LogCaptureFixture) -> None:
    begin(caplog, "disk", dry_run=True)
    ec2 = FakeEc2(
        [volume("vol-a"), volume("vol-young", days=0.1), volume("vol-att", Attachments=[{"x": 1}])]
    )
    disk_cleaner(ec2).plan(disk_context(True))
    assert lines(caplog)[1:] == [
        "REGION cloud=aws resource=disk region=us-west-2 scanned=3 selected=1 seconds=0",
        "ACTION would_delete cloud=aws resource=disk region=us-west-2 id=vol-a name=-",
        "SKIPPED cloud=aws resource=disk reason=attached count=1",
        "SKIPPED cloud=aws resource=disk reason=too_young count=1",
        result_line(
            "disk",
            "delete",
            True,
            "selected=1 done=0 failed=0 skipped=2 regions=1 regions_skipped=0 duration_s=0",
        ),
    ]
    skip_levels = {
        r.levelno for r in caplog.records if r.getMessage().startswith("Skipping volume")
    }
    assert skip_levels == {logging.DEBUG}


def test_disk_real_run_with_delete_failure(caplog: pytest.LogCaptureFixture) -> None:
    begin(caplog, "disk")
    denied = ClientError(
        {"Error": {"Code": "UnauthorizedOperation", "Message": "no"}}, "DeleteVolume"
    )
    ec2 = FakeEc2([volume("vol-a"), volume("vol-b")], errors={"vol-b": denied})
    cleaner = disk_cleaner(ec2)
    cleaner.apply(cleaner.plan(disk_context(False)), disk_context(False))
    out = lines(caplog)
    assert "ACTION deleted cloud=aws resource=disk region=us-west-2 id=vol-a name=-" in out
    assert out[-2] == "ERRORS count=1"
    assert_failure_printed_once(caplog, "vol-b")
    assert out[-1] == result_line(
        "disk",
        "delete",
        False,
        "selected=1 done=1 failed=1 skipped=0 regions=1 regions_skipped=0 duration_s=0",
    )


def test_disk_connectivity_skip_and_region_error(caplog: pytest.LogCaptureFixture) -> None:
    begin(caplog, "disk", dry_run=True)
    good = FakeEc2([])

    def factory(region: str) -> FakeEc2:
        if region == "eu-west-1":
            raise conn_error()
        if region == "ap-south-1":
            raise RuntimeError("boom")
        return good

    runtime = DiskRuntime(
        ec2_factory=factory,
        cloudwatch_factory=lambda region: FakeCloudWatch(),
        list_regions=lambda: ["us-west-2", "eu-west-1", "ap-south-1"],
    )
    DiskCleaner(build_settings({"age": {"days": 1}}), runtime).plan(disk_context(True))
    out = lines(caplog)
    assert_failure_printed_once(caplog, "ap-south-1")
    assert out[-2:] == [
        "ERRORS count=1",
        result_line(
            "disk",
            "delete",
            True,
            "selected=0 done=0 failed=1 skipped=0 regions=1 regions_skipped=1 duration_s=0",
        ),
    ]


# ---- spot instance requests ----


def spot_cleaner(
    case: Case, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> tuple[SpotRequestCleaner, RunContext]:
    aws = FakeAws(case)
    monkeypatch.setattr("boto3.client", aws.client)
    options = {"filter_tags": {"yb_owner": ["qa"]}, "age": {"days": 1}}
    ctx = RunContext(dry_run, Operation.DELETE, options, NOW)
    cleaner = registry.get("aws", "spot_instance_requests")(ctx)
    assert isinstance(cleaner, SpotRequestCleaner)
    cleaner.runtime = SpotRuntime(
        client_factory=cleaner.runtime.client_factory,
        list_regions=lambda: list(case.regions),
        waiter=Waiter(aws.sleep, aws.monotonic),
    )
    return cleaner, ctx


def test_spot_dry_run_lines(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    begin(caplog, "spot_instance_requests", dry_run=True)
    cleaner, ctx = spot_cleaner(Case("x", one(std()), dry_run=True), monkeypatch, True)
    cleaner.plan(ctx)
    assert lines(caplog)[1:] == [
        f"REGION {SPOT} scanned=1 selected=1 seconds=0",
        f"ACTION would_delete {SPOT} id=sir-1 name=-",
        f"ACTION would_delete {SPOT} id=i-1 name=-",
        f"ACTION would_delete {SPOT} id=vol-1 name=-",
        result_line(
            "spot_instance_requests",
            "delete",
            True,
            "selected=3 done=0 failed=0 skipped=0 regions=1 regions_skipped=0 duration_s=0",
        ),
    ]


def test_spot_real_run_lines(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    begin(caplog, "spot_instance_requests")
    cleaner, ctx = spot_cleaner(Case("x", one(std())), monkeypatch, False)
    cleaner.apply(cleaner.plan(ctx), ctx)
    assert lines(caplog)[1:] == [
        f"REGION {SPOT} scanned=1 selected=1 seconds=0",
        f"ACTION deleted {SPOT} id=sir-1 name=-",
        f"ACTION deleted {SPOT} id=i-1 name=-",
        f"ACTION deleted {SPOT} id=vol-1 name=-",
        result_line(
            "spot_instance_requests",
            "delete",
            False,
            "selected=3 done=3 failed=0 skipped=0 regions=1 regions_skipped=0 duration_s=0",
        ),
    ]


def test_spot_cancel_failure_is_listed(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    begin(caplog, "spot_instance_requests")
    spec = std(errors={"cancel_spot_instance_requests": ce("UnauthorizedOperation")})
    cleaner, ctx = spot_cleaner(Case("x", one(spec)), monkeypatch, False)
    cleaner.apply(cleaner.plan(ctx), ctx)
    out = lines(caplog)
    assert out[-2] == "ERRORS count=1"
    assert_failure_printed_once(caplog, "sir-1")
    assert out[-1].endswith(
        "selected=0 done=0 failed=1 skipped=0 regions=1 regions_skipped=0 duration_s=0"
    )


def test_spot_skip_reasons_and_connectivity_skip(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    begin(caplog, "spot_instance_requests")
    young = Spec([req("sir-y", days=0.1)])
    case = Case(
        "x",
        {"us-west-2": young, "eu-west-1": std()},
        client_errors={"eu-west-1": conn_error()},
    )
    cleaner, ctx = spot_cleaner(case, monkeypatch, False)
    cleaner.apply(cleaner.plan(ctx), ctx)
    out = lines(caplog)
    assert "SKIPPED cloud=aws resource=spot_instance_requests reason=too_young count=1" in out
    assert out[-1].endswith(
        "selected=0 done=0 failed=0 skipped=1 regions=1 regions_skipped=1 duration_s=0"
    )
    assert any("Region eu-west-1: skipped" in r.getMessage() for r in caplog.records)


def test_spot_volume_and_sweep_failures_carry_region(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    begin(caplog, "spot_instance_requests")
    spec = swept(
        key_errors={("describe_spot_instance_requests", "sir-gone"): ce("RequestLimitExceeded")}
    )
    cleaner, ctx = spot_cleaner(Case("x", one(spec)), monkeypatch, False)
    cleaner.apply(cleaner.plan(ctx), ctx)
    out = lines(caplog)
    assert "ERRORS count=1" in out
    assert_failure_printed_once(caplog, "sir-gone")
    assert out[-1].endswith(
        "selected=0 done=0 failed=1 skipped=1 regions=1 regions_skipped=0 duration_s=0"
    )
