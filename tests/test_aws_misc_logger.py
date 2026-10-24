"""Run-log call sites of the AWS KMS, snapshot, keypair, Elastic IP cleaners.

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
from botocore.exceptions import ClientError, EndpointConnectionError

from crc.aws import _regionlog
from crc.aws.elastic_ips import ElasticIpCleaner
from crc.aws.keypairs import KeyPairCleaner
from crc.aws.kms import KmsCleaner, KmsSettings
from crc.aws.snapshot import SnapshotCleaner
from crc.core import engine, logger
from crc.core.cleaner import Cleaner, Operation, RunContext
from crc.core.registry import Registry
from crc.core.selection import Selection
from tests import test_aws_elastic_ips as eip_t
from tests import test_aws_keypairs as kp_t
from tests import test_aws_kms as kms_t
from tests import test_aws_snapshot as snap_t
from tests.helpers_wc_aws1_engine import run_cleaner

TWO_SKIPS = 2


def conn_error() -> Exception:
    error: Exception = EndpointConnectionError(endpoint_url="https://ec2.example")
    return error


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
    """K-denied cannot be read, k-other has no AWS principal, k-ok is ours."""

    def list_resource_tags(self, *, KeyId: str) -> Any:
        if KeyId == "k-denied":
            raise _client_error("ListResourceTags")
        return super().list_resource_tags(KeyId=KeyId)

    def get_key_policy(self, *, KeyId: str, PolicyName: str) -> Any:
        if KeyId == "k-other":
            return {"Policy": json.dumps({"Statement": [{"Principal": "*"}]})}
        return super().get_key_policy(KeyId=KeyId, PolicyName=PolicyName)


def _kms_cleaner(client_factory: Any) -> KmsCleaner:
    return KmsCleaner(
        client_factory,
        KmsSettings(
            kms_key_description="ci",
            kms_user=kms_t.USER,
            kms_pending_window=kms_t.WINDOW,
            age={"days": 1},
        ),
    )


def _kms(dry_run: bool) -> tuple[Any, list[tuple[str, ...]]]:
    keys = {"k-denied": kms_t.OLD, "k-other": kms_t.OLD, "k-ok": kms_t.OLD, "k-new": kms_t.YOUNG}
    calls: list[tuple[str, ...]] = []
    cleaner = _kms_cleaner(lambda region: MixedKms(keys, calls))
    return (lambda: run_cleaner("aws", "kms", cleaner, dry_run=dry_run, now=kms_t.NOW)), calls


def test_kms_dry_run_counts_skips_by_reason_and_lists_would_delete(run_lines: Any) -> None:
    begin, lines = run_lines
    begin("kms", dry_run=True)
    run, calls = _kms(dry_run=True)
    run()
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
    _kms(dry_run=True)[0]()
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

    cleaner = _kms_cleaner(lambda region: Failing(keys, []))
    with pytest.raises(ClientError):
        run_cleaner("aws", "kms", cleaner, now=kms_t.NOW)
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


def test_kms_key_with_a_later_statement_error_is_skipped_not_selected(
    run_lines: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """BUG-055: the first statement matches the user, the second has no AWS principal: the key
    is skipped (KEY SKIPPED at DEBUG, counted as `not_ours`) and never selected."""

    class MatchThenNoPrincipal(kms_t.FakeKms):
        def get_key_policy(self, *, KeyId: str, PolicyName: str) -> Any:
            statements = [{"Principal": {"AWS": kms_t.USER}}, {"Principal": "*"}]
            return {"Policy": json.dumps({"Statement": statements})}

    begin, lines = run_lines
    begin("kms", dry_run=True)
    keys = {"k1": kms_t.OLD}
    cleaner = _kms_cleaner(lambda region: MatchThenNoPrincipal(keys, []))
    run_cleaner("aws", "kms", cleaner, dry_run=True, now=kms_t.NOW)
    out = lines()
    records = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert (logging.DEBUG, "KEY SKIPPED k1") in records
    assert not _run_lines(out, "ACTION")
    assert "SKIPPED cloud=aws resource=kms reason=not_ours count=1" in "\n".join(out)
    assert " selected=0 done=0 failed=0 skipped=1 regions=1 regions_skipped=0 " in out[-1]


# Snapshot ----------------------------------------------------------------------------------


def _through_engine(kind: str, cleaner: Cleaner, *, dry_run: bool, now: datetime.datetime) -> None:
    registry = Registry()
    registry.register("aws", kind)(lambda ctx: cleaner)
    context = RunContext(dry_run=dry_run, operation=Operation.DELETE, options={}, now=now)
    engine.run(Selection((("aws", kind),)), context, registry)


class _Engine:
    """A cleaner and the engine run that drives it: `delete()` runs `engine.run`."""

    def __init__(self, kind: str, cleaner: Cleaner, *, dry_run: bool, now: datetime.datetime):
        self.kind, self.cleaner, self.dry_run, self.now = kind, cleaner, dry_run, now

    def delete(self) -> None:
        _through_engine(self.kind, self.cleaner, dry_run=self.dry_run, now=self.now)


def _snapshots(dry_run: bool, fail: dict[str, Exception] | None = None) -> _Engine:
    snaps = [
        snap_t.snap("old"),
        snap_t.snap("ami", tags=None),
        snap_t.snap("young", days=1),
        snap_t.snap("tagged", Tags=[{"Key": "keep", "Value": "1"}]),
    ]
    images = [{"BlockDeviceMappings": [{"Ebs": {"SnapshotId": "ami"}}]}]
    clients = {"us-west-2": snap_t.FakeClient(snaps, images, fail)}
    cleaner = SnapshotCleaner(
        lambda region: clients[region], lambda: list(clients), snap_t.AGE_DAYS, {"keep": []}
    )
    return _Engine("snapshot", cleaner, dry_run=dry_run, now=snap_t.NOW)


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

    cleaner = SnapshotCleaner(factory, lambda: ["us-west-2", "eu-west-1"], snap_t.AGE_DAYS, None)
    _Engine("snapshot", cleaner, dry_run=True, now=snap_t.NOW).delete()
    out = lines()
    assert not _run_lines(out, "REGION")
    assert " regions=0 regions_skipped=2 " in out[-1]


# Key pairs ---------------------------------------------------------------------------------


class _KeyPairsRun:
    """The key pair Cleaner run through the engine, with the clients of `aws`."""

    def __init__(self, aws: kp_t.Aws, *, dry_run: bool, client_factory: Any = None) -> None:
        self.dry_run = dry_run
        factory = client_factory or aws.client
        self.registry = Registry()
        self.registry.register("aws", "keypair")(
            lambda ctx: KeyPairCleaner(
                factory, aws.list_regions, ["^ci-"], ["^ci-keep"], {"hours": 5}
            )
        )

    def delete(self) -> None:
        context = RunContext(self.dry_run, Operation.DELETE, {}, kp_t.NOW)
        engine.run(Selection((("aws", "keypair"),)), context, self.registry)


def _keypairs(aws: kp_t.Aws, *, dry_run: bool) -> _KeyPairsRun:
    return _KeyPairsRun(aws, dry_run=dry_run)


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

    failing = _KeyPairsRun(
        aws,
        dry_run=False,
        client_factory=lambda region: Failing(aws.regions[region], aws.calls),
    )
    with pytest.raises(ClientError):
        failing.delete()
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


def _eips(aws: eip_t.Aws, *, dry_run: bool) -> Any:
    cleaner = ElasticIpCleaner(
        aws.client, aws.list_regions, {"team": ["qa"]}, {"keep": ["1"]}, None
    )
    return lambda: run_cleaner("aws", "ip", cleaner, dry_run=dry_run)


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
    _eips(eip_t.Aws({"us-west-2": _addresses()}), dry_run=True)()
    out = lines()
    assert _run_lines(out, "REGION", "ACTION", "SKIPPED") == [
        "REGION cloud=aws resource=ip region=us-west-2 scanned=4 selected=1 seconds=2",
        "ACTION would_delete cloud=aws resource=ip region=us-west-2 id=1.1.1.1 name=1.1.1.1",
        "SKIPPED cloud=aws resource=ip reason=exception_tag count=1",
        "SKIPPED cloud=aws resource=ip reason=no_filter_match count=1",
    ]
    begin("ip", dry_run=False)
    _eips(eip_t.Aws({"us-west-2": _addresses()}), dry_run=False)()
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

    cleaner = ElasticIpCleaner(
        lambda region: (
            Failing(aws.regions[region], aws.calls) if region == "us-west-2" else aws.client(region)
        ),
        aws.list_regions,
        {"team": ["qa"]},
        {"keep": ["1"]},
        None,
    )
    with pytest.raises(ClientError):
        run_cleaner("aws", "ip", cleaner)
    out = lines()
    assert "ERRORS count=1" in out
    assert any("FAILED cloud=aws resource=ip region=us-west-2 id=1.1.1.1" in m for m in out)


def test_unattached_run_logs_nothing_extra(caplog: pytest.LogCaptureFixture) -> None:
    """Without begin() the cleaners log only their own lines (no REGION, ACTION or RESULT)."""
    caplog.set_level(logging.DEBUG)
    _keypairs(kp_t.Aws({"us-west-2": _pairs()}), dry_run=True).delete()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert not any(word in text for word in ("REGION ", "ACTION ", "RESULT ", "SKIPPED "))


def test_engine_dry_run_logs_would_delete_for_kms_snapshot_and_ip(run_lines: Any) -> None:
    """Through `engine.run` (`plan()` only in dry run), kms, snapshot and ip log
    `ACTION would_delete` and count `selected` in `RESULT`."""
    begin, lines = run_lines
    registry = Registry()
    keys = {"k-denied": kms_t.OLD, "k-other": kms_t.OLD, "k-ok": kms_t.OLD, "k-new": kms_t.YOUNG}
    registry.register("aws", "kms")(
        lambda ctx: KmsCleaner(
            lambda region: MixedKms(keys, []),
            KmsSettings(
                kms_key_description="ci",
                kms_user=kms_t.USER,
                kms_pending_window=kms_t.WINDOW,
                age={"days": 1},
            ),
        )
    )
    snaps = [snap_t.snap("old"), snap_t.snap("young", days=1)]
    snap_client = snap_t.FakeClient(snaps, [])
    registry.register("aws", "snapshot")(
        lambda ctx: SnapshotCleaner(lambda r: snap_client, lambda: ["us-west-2"], 90, None)
    )
    eip_aws = eip_t.Aws({"us-west-2": _addresses()})
    registry.register("aws", "ip")(
        lambda ctx: ElasticIpCleaner(
            eip_aws.client, eip_aws.list_regions, {"team": ["qa"]}, {"keep": ["1"]}, None
        )
    )
    for kind, expected_id in (
        ("kms", "k-ok"),
        ("snapshot", "old"),
        ("ip", "1.1.1.1"),
    ):
        begin(kind, dry_run=True)
        ctx = RunContext(True, Operation.DELETE, {}, kms_t.NOW)
        engine.run(Selection((("aws", kind),)), ctx, registry)
        out = lines()
        assert any(
            m.startswith(f"ACTION would_delete cloud=aws resource={kind}")
            and f"id={expected_id}" in m
            for m in out
        ), (kind, out)
        assert "selected=1 done=0 failed=0" in out[-1], (kind, out[-1])
