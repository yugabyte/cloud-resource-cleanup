"""LOG-AWS2: run-log call sites of the AWS KMS, snapshot, keypair, Elastic IP and VPC cleaners.

Each test begins a run with a fake clock, drives one cleaner (dry run, real run, a failure or a
skipped region) and asserts the exact INFO+ lines, `REGION ... ACTION ... SKIPPED ... RESULT`,
plus the DEBUG level of the per-resource skip lines (BUG-357, BUG-359, BUG-361, BUG-362).
"""

import datetime
import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from botocore.exceptions import ClientError

from crc.aws import _regionlog
from crc.aws.elastic_ips import ElasticIpRuntime, ElasticIPs
from crc.aws.keypairs import KeyPairRuntime, KeyPairs
from crc.aws.kms import Kms, KmsCleaner, KmsRuntime, KmsSettings
from crc.aws.snapshot import Snapshot, SnapshotRuntime
from crc.aws.vpc import VPC, VpcCleaner, VpcRuntime
from crc.core import logger
from crc.core.cleaner import Operation, RunContext
from tests import test_aws_elastic_ips as eip_t
from tests import test_aws_keypairs as kp_t
from tests import test_aws_kms as kms_t
from tests import test_aws_snapshot as snap_t
from tests.test_aws_vpc_parity import W2, Case, FakeAws, FakeClient, FakeResource, conn_error, full

TWO_SKIPS = 2


class Ticker:
    """Fake clock: every reading advances by two seconds."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 2.0
        return self.now


@pytest.fixture
def run_lines(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(_regionlog, "clock", Ticker())

    def begin(resource: str, *, dry_run: bool) -> None:
        operation = "delete"
        logger.begin("aws", resource, operation, dry_run, {}, clock=Ticker())

    def lines(level: int = logging.INFO) -> list[str]:
        logger.finish()
        return [r.getMessage() for r in caplog.records if r.levelno >= level]

    yield begin, lines
    logger.finish()


def _run_lines(lines: list[str], *prefixes: str) -> list[str]:
    return [m for m in lines if m.startswith(prefixes)]


def _client_error(operation: str) -> ClientError:
    return ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, operation)


# KMS ---------------------------------------------------------------------------------------


class MixedKms(kms_t.FakeKms):
    """k-denied cannot be read, k-other has no AWS principal, k-ok is ours."""

    def list_resource_tags(self, *, KeyId: str) -> Any:
        if KeyId == "k-denied":
            raise _client_error("ListResourceTags")
        return super().list_resource_tags(KeyId=KeyId)

    def get_key_policy(self, *, KeyId: str, PolicyName: str) -> Any:
        if KeyId == "k-other":
            return {"Policy": json.dumps({"Statement": [{"Principal": "*"}]})}
        return super().get_key_policy(KeyId=KeyId, PolicyName=PolicyName)


def _kms(dry_run: bool) -> tuple[Kms, list[tuple[str, ...]]]:
    keys = {"k-denied": kms_t.OLD, "k-other": kms_t.OLD, "k-ok": kms_t.OLD, "k-new": kms_t.YOUNG}
    calls: list[tuple[str, ...]] = []
    runtime = KmsRuntime(lambda region: MixedKms(keys, calls), lambda: kms_t.NOW)
    kms = Kms(dry_run, None, None, "ci", kms_t.USER, kms_t.WINDOW, {"days": 1}, "", runtime)
    return kms, calls


def test_kms_dry_run_counts_skips_by_reason_and_lists_would_delete(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("kms", dry_run=True)
    kms, calls = _kms(dry_run=True)
    kms.delete()
    out = lines()
    assert _run_lines(out, "REGION", "ACTION", "SKIPPED") == [
        "REGION cloud=aws resource=kms region=us-west-2 scanned=4 selected=1 seconds=2",
        "ACTION would_delete cloud=aws resource=kms region=us-west-2 id=k-ok name=-",
        "SKIPPED cloud=aws resource=kms reason=not_ours count=1",
        "SKIPPED cloud=aws resource=kms reason=unreadable count=1",
    ]
    assert out[-1].startswith(
        "RESULT cloud=aws resource=kms operation=delete dry_run=true selected=1 done=0 failed=0 "
        "skipped=2 regions=1 regions_skipped=0 duration_s="
    )
    assert not [c for c in calls if c[0] == "schedule_key_deletion"]


def test_kms_key_skipped_is_debug_with_unchanged_text(
    run_lines: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """BUG-357: `KEY SKIPPED <id>` is DEBUG now (WARNING on main); the text is the same."""
    begin, lines = run_lines
    begin("kms", dry_run=True)
    _kms(dry_run=True)[0].delete()
    lines()
    skipped = [(r.levelno, r.getMessage()) for r in caplog.records if "KEY SKIPPED" in r.message]
    assert skipped == [
        (logging.DEBUG, "KEY SKIPPED k-denied"),
        (logging.DEBUG, "KEY SKIPPED k-other"),
    ]


def test_kms_real_run_reports_deleted_and_a_failure(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("kms", dry_run=False)
    keys = {"k1": kms_t.OLD}

    class Failing(kms_t.FakeKms):
        def schedule_key_deletion(self, *, KeyId: str, PendingWindowInDays: int) -> Any:
            raise _client_error("ScheduleKeyDeletion")

    runtime = KmsRuntime(lambda region: Failing(keys, []), lambda: kms_t.NOW)
    kms = Kms(False, None, None, "ci", kms_t.USER, kms_t.WINDOW, {"days": 1}, "", runtime)
    with pytest.raises(ClientError):
        kms.delete()
    out = lines()
    assert "ERRORS count=1" in out
    failed = [m for m in out if m.startswith("FAILED")]
    assert failed == [
        "FAILED cloud=aws resource=kms region=us-west-2 id=k1 error=ClientError: "
        "An error occurred (AccessDenied) when calling the ScheduleKeyDeletion operation: no"
    ]


def test_kms_cleaner_apply_reports_deleted() -> None:
    aws = kms_t.Aws({"old": kms_t.OLD})
    cleaner = KmsCleaner(
        aws.client,
        KmsSettings(
            kms_key_description="ci",
            kms_user=kms_t.USER,
            kms_pending_window=kms_t.WINDOW,
            age={"days": 1},
        ),
    )
    context = RunContext(dry_run=False, operation=Operation.DELETE, options={}, now=kms_t.NOW)
    run = logger.begin("aws", "kms", "delete", False, {}, clock=Ticker())
    try:
        cleaner.apply(cleaner.plan(context), context)
        assert run._done == 1
    finally:
        logger.finish()


def test_kms_unreachable_region_is_counted(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("kms", dry_run=True)
    aws = kms_t.Aws({})
    aws.error = conn_error()
    context = RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=kms_t.NOW)
    KmsCleaner(aws.client, KmsSettings()).plan(context)
    out = lines()
    assert any("KMS cleanup skipped" in m for m in out)
    assert " selected=0 done=0 failed=0 skipped=0 regions=0 regions_skipped=1 " in out[-1]


def test_kms_key_selected_before_the_error_is_not_counted_as_skipped(
    run_lines: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """The first statement matches the user, the second has no AWS principal: the key stays
    selected (KEY SKIPPED is still logged at DEBUG) and is not also counted as skipped."""

    class MatchThenNoPrincipal(kms_t.FakeKms):
        def get_key_policy(self, *, KeyId: str, PolicyName: str) -> Any:
            statements = [{"Principal": {"AWS": kms_t.USER}}, {"Principal": "*"}]
            return {"Policy": json.dumps({"Statement": statements})}

    begin, lines = run_lines
    begin("kms", dry_run=True)
    keys = {"k1": kms_t.OLD}
    runtime = KmsRuntime(lambda region: MatchThenNoPrincipal(keys, []), lambda: kms_t.NOW)
    Kms(True, None, None, "ci", kms_t.USER, kms_t.WINDOW, {"days": 1}, "", runtime).delete()
    out = lines()
    records = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert (logging.DEBUG, "KEY SKIPPED k1") in records
    assert "ACTION would_delete cloud=aws resource=kms region=us-west-2 id=k1 name=-" in out
    assert not _run_lines(out, "SKIPPED")
    assert " selected=1 done=0 failed=0 skipped=0 regions=1 regions_skipped=0 " in out[-1]


# Snapshot ----------------------------------------------------------------------------------


def _snapshots(dry_run: bool, fail: dict[str, Exception] | None = None) -> Snapshot:
    snaps = [
        snap_t.snap("old"),
        snap_t.snap("ami", tags=None),
        snap_t.snap("young", days=1),
        snap_t.snap("tagged", Tags=[{"Key": "keep", "Value": "1"}]),
    ]
    images = [{"BlockDeviceMappings": [{"Ebs": {"SnapshotId": "ami"}}]}]
    clients = {"us-west-2": snap_t.FakeClient(snaps, images, fail)}
    runtime = SnapshotRuntime(
        client_factory=lambda region: clients[region],
        list_regions=lambda: list(clients),
        clock=lambda: snap_t.NOW,
    )
    return Snapshot(dry_run, snap_t.AGE_DAYS, {"keep": []}, runtime)


def test_snapshot_dry_run_lines(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("snapshot", dry_run=True)
    _snapshots(dry_run=True).delete()
    out = lines()
    assert _run_lines(out, "REGION", "ACTION", "SKIPPED") == [
        "ACTION would_delete cloud=aws resource=snapshot region=us-west-2 id=old name=-",
        "REGION cloud=aws resource=snapshot region=us-west-2 scanned=4 selected=1 seconds=2",
        "SKIPPED cloud=aws resource=snapshot reason=exception_tag count=1",
        "SKIPPED cloud=aws resource=snapshot reason=too_young count=1",
        "SKIPPED cloud=aws resource=snapshot reason=used_by_ami count=1",
    ]
    assert out[-1].startswith(
        "RESULT cloud=aws resource=snapshot operation=delete dry_run=true selected=1 done=0 "
        "failed=0 skipped=3 regions=1 regions_skipped=0"
    )


def test_snapshot_real_run_failure_is_listed_and_ami_skip_is_debug(
    run_lines: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """BUG-359: no INFO line per snapshot used by an AMI; BUG-362: the failed delete is listed."""
    begin, lines = run_lines
    begin("snapshot", dry_run=False)
    _snapshots(dry_run=False, fail={"old": ValueError("busy")}).delete()
    out = lines()
    assert not [m for m in out if "used by AMI" in m]
    assert (logging.DEBUG, "Skipping snapshot ami, used by AMI") in [
        (r.levelno, r.getMessage()) for r in caplog.records
    ]
    assert out[-3:-1] == [
        "ERRORS count=1",
        "FAILED cloud=aws resource=snapshot region=us-west-2 id=old error=ValueError: busy",
    ]
    assert " selected=0 done=0 failed=1 skipped=3 " in out[-1]


def test_snapshot_real_run_counts_deleted(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("snapshot", dry_run=False)
    _snapshots(dry_run=False).delete()
    out = lines()
    assert "ACTION deleted cloud=aws resource=snapshot region=us-west-2 id=old name=-" in out
    assert " selected=1 done=1 failed=0 skipped=3 regions=1 regions_skipped=0 " in out[-1]


def test_snapshot_skipped_region_is_counted(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("snapshot", dry_run=True)

    def factory(region: str) -> Any:
        raise conn_error()

    runtime = SnapshotRuntime(factory, lambda: ["us-west-2", "eu-west-1"], lambda: snap_t.NOW)
    Snapshot(True, snap_t.AGE_DAYS, None, runtime).delete()
    out = lines()
    assert not _run_lines(out, "REGION")
    assert " regions=0 regions_skipped=2 " in out[-1]


# Key pairs ---------------------------------------------------------------------------------


def _keypairs(aws: kp_t.Aws, *, dry_run: bool) -> KeyPairs:
    runtime = KeyPairRuntime(aws.client, aws.list_regions, lambda: kp_t.NOW)
    return KeyPairs(dry_run, ["^ci-"], ["^ci-keep"], {"hours": 5}, runtime)


def _pairs() -> list[dict[str, Any]]:
    return [
        kp_t.pair("ci-old", 10),
        kp_t.pair("ci-young", 1),
        kp_t.pair("ci-keep", 10),
        kp_t.pair("other", 10),
    ]


def test_keypairs_dry_run_lines(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("keypair", dry_run=True)
    _keypairs(kp_t.Aws({"us-west-2": _pairs()}), dry_run=True).delete()
    out = lines()
    assert _run_lines(out, "REGION", "ACTION", "SKIPPED") == [
        "REGION cloud=aws resource=keypair region=us-west-2 scanned=4 selected=1 seconds=2",
        "ACTION would_delete cloud=aws resource=keypair region=us-west-2 id=ci-old name=ci-old",
        "SKIPPED cloud=aws resource=keypair reason=exception_regex count=1",
        "SKIPPED cloud=aws resource=keypair reason=name_regex_mismatch count=1",
        "SKIPPED cloud=aws resource=keypair reason=too_young count=1",
    ]


def test_keypairs_per_keypair_skip_lines_are_debug(
    run_lines: Any, caplog: pytest.LogCaptureFixture
) -> None:
    begin, lines = run_lines
    begin("keypair", dry_run=True)
    _keypairs(kp_t.Aws({"us-west-2": _pairs()}), dry_run=True).delete()
    lines()
    levels = {
        r.getMessage(): r.levelno for r in caplog.records if r.getMessage().startswith("Keypair")
    }
    assert set(levels.values()) == {logging.DEBUG}
    assert len(levels) == TWO_SKIPS


def test_keypairs_real_run_failure_is_recorded_and_still_raises(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("keypair", dry_run=False)
    aws = kp_t.Aws({"us-west-2": _pairs()})

    class Failing(kp_t.FakeEc2):
        def delete_key_pair(self, *, KeyName: str) -> Any:
            raise _client_error("DeleteKeyPair")

    runtime = KeyPairRuntime(
        lambda region: Failing(aws.regions[region], aws.calls), aws.list_regions, lambda: kp_t.NOW
    )
    with pytest.raises(ClientError):
        KeyPairs(False, ["^ci-"], ["^ci-keep"], {"hours": 5}, runtime).delete()
    out = lines()
    assert "ERRORS count=1" in out
    assert any(
        m.startswith("FAILED cloud=aws resource=keypair region=us-west-2 id=ci-old") for m in out
    )


def test_keypairs_real_run_and_skipped_region(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("keypair", dry_run=False)
    aws = kp_t.Aws({"us-west-2": _pairs(), "eu-west-1": []})
    aws.errors["eu-west-1"] = conn_error()
    _keypairs(aws, dry_run=False).delete()
    out = lines()
    assert "ACTION deleted cloud=aws resource=keypair region=us-west-2 id=ci-old name=ci-old" in out
    assert " selected=1 done=1 failed=0 skipped=3 regions=1 regions_skipped=1 " in out[-1]


# Elastic IPs -------------------------------------------------------------------------------


def _eips(aws: eip_t.Aws, *, dry_run: bool) -> ElasticIPs:
    runtime = ElasticIpRuntime(aws.client, aws.list_regions)
    return ElasticIPs(dry_run, {"team": ["qa"]}, {"keep": ["1"]}, None, runtime)


def _addresses() -> list[dict[str, Any]]:
    return [
        eip_t.tagged("1.1.1.1", team="qa"),
        eip_t.tagged("2.2.2.2", team="qa", keep="1"),
        eip_t.tagged("3.3.3.3", other="x"),
        {"PublicIp": "4.4.4.4", "AllocationId": "a4"},
    ]


def test_elastic_ips_dry_run_and_real_run_lines(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("ip", dry_run=True)
    _eips(eip_t.Aws({"us-west-2": _addresses()}), dry_run=True).delete()
    out = lines()
    assert _run_lines(out, "REGION", "ACTION", "SKIPPED") == [
        "REGION cloud=aws resource=ip region=us-west-2 scanned=4 selected=1 seconds=2",
        "ACTION would_delete cloud=aws resource=ip region=us-west-2 id=1.1.1.1 name=1.1.1.1",
        "SKIPPED cloud=aws resource=ip reason=exception_tag count=1",
        "SKIPPED cloud=aws resource=ip reason=no_filter_match count=1",
    ]
    begin("ip", dry_run=False)
    _eips(eip_t.Aws({"us-west-2": _addresses()}), dry_run=False).delete()
    out = lines()
    assert "ACTION deleted cloud=aws resource=ip region=us-west-2 id=1.1.1.1 name=1.1.1.1" in out
    assert " selected=1 done=1 failed=0 skipped=2 regions=1 regions_skipped=0 " in out[-1]


def test_elastic_ips_release_failure_and_skipped_region(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("ip", dry_run=False)
    aws = eip_t.Aws({"us-west-2": _addresses(), "eu-west-1": []})
    aws.errors["eu-west-1"] = conn_error()

    class Failing(eip_t.FakeEc2):
        def release_address(self, *, AllocationId: str) -> Any:
            raise _client_error("ReleaseAddress")

    runtime = ElasticIpRuntime(
        lambda region: (
            Failing(aws.regions[region], aws.calls) if region == "us-west-2" else aws.client(region)
        ),
        aws.list_regions,
    )
    with pytest.raises(ClientError):
        ElasticIPs(False, {"team": ["qa"]}, {"keep": ["1"]}, None, runtime).delete()
    out = lines()
    assert "ERRORS count=1" in out
    assert any("FAILED cloud=aws resource=ip region=us-west-2 id=1.1.1.1" in m for m in out)


# VPC ---------------------------------------------------------------------------------------


def _vpc(aws: FakeAws, *, dry_run: bool) -> VPC:
    runtime = VpcRuntime(
        lambda region: FakeClient(aws, region),
        lambda region: FakeResource(aws, region),
        lambda: list(aws.case.regions),
    )
    return VPC(dry_run, None, None, None, runtime)


def test_vpc_dry_run_and_real_run_lines(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("vpc", dry_run=True)
    _vpc(FakeAws(Case("u", {W2: [full("vpc-1")]}, dry_run=True)), dry_run=True).delete()
    out = lines()
    assert _run_lines(out, "REGION", "ACTION") == [
        "ACTION would_delete cloud=aws resource=vpc region=us-west-2 id=vpc-1 name=-",
        "REGION cloud=aws resource=vpc region=us-west-2 scanned=1 selected=1 seconds=2",
    ]
    begin("vpc", dry_run=False)
    _vpc(FakeAws(Case("u", {W2: [full("vpc-1")]})), dry_run=False).delete()
    out = lines()
    assert "ACTION deleted cloud=aws resource=vpc region=us-west-2 id=vpc-1 name=-" in out
    assert " selected=1 done=1 failed=0 skipped=0 regions=1 regions_skipped=0 " in out[-1]


def test_vpc_delete_failure_after_retries_is_listed(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("vpc", dry_run=False)
    errors = {("delete_vpc", W2, "vpc-1"): RuntimeError("DependencyViolation")}
    _vpc(FakeAws(Case("u", {W2: [full("vpc-1")]}, errors=errors)), dry_run=False).delete()
    out = lines()
    assert not _run_lines(out, "ACTION")
    assert "ERRORS count=1" in out
    assert (
        "FAILED cloud=aws resource=vpc region=us-west-2 id=vpc-1 "
        "error=RuntimeError: DependencyViolation"
    ) in out
    assert " selected=0 done=0 failed=1 " in out[-1]


def test_vpc_teardown_error_is_recorded_and_propagates(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("vpc", dry_run=False)
    errors = {("describe_vpc_endpoints", W2, "vpc-1"): RuntimeError("boom")}
    vpc = _vpc(FakeAws(Case("u", {W2: [full("vpc-1")]}, errors=errors)), dry_run=False)
    with pytest.raises(RuntimeError):
        vpc.delete()
    out = lines()
    assert "FAILED cloud=aws resource=vpc region=us-west-2 id=vpc-1 error=RuntimeError: boom" in out


def test_vpc_cleaner_plan_apply_and_skipped_region(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("vpc", dry_run=False)
    aws = FakeAws(Case("u", {W2: [full("vpc-1")], "eu-west-1": []}, errors={}))
    aws.case = Case("u", {W2: [full("vpc-1")]})
    cleaner = VpcCleaner(
        lambda region: FakeClient(aws, region),
        lambda region: FakeResource(aws, region),
        lambda: [W2],
        None,
        None,
        None,
    )
    context = RunContext(
        dry_run=False,
        operation=Operation.DELETE,
        options={},
        now=datetime.datetime(2026, 3, 1, 12, 0, 0),
    )
    cleaner.apply(cleaner.plan(context), context)
    out = lines()
    assert _run_lines(out, "REGION") == [
        "REGION cloud=aws resource=vpc region=us-west-2 scanned=1 selected=1 seconds=2"
    ]
    assert "ACTION deleted cloud=aws resource=vpc region=us-west-2 id=vpc-1 name=-" in out


def test_unattached_run_logs_nothing_extra(caplog: pytest.LogCaptureFixture) -> None:
    """Without begin() the cleaners log only their own lines (no REGION, ACTION or RESULT)."""
    caplog.set_level(logging.DEBUG)
    _keypairs(kp_t.Aws({"us-west-2": _pairs()}), dry_run=True).delete()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert not any(word in text for word in ("REGION ", "ACTION ", "RESULT ", "SKIPPED "))

