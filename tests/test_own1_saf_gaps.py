"""Engine-path tests that lock the SAF rules which had no test: SAF-002, 004, 016, 022, 023, 024
and 034. Each test runs the production code (the registered cleaner, `crc.core.engine` or the CLI
`main`) on fakes; no cloud API is called."""

import datetime
import logging
import pathlib
import re
import sys
from collections.abc import Mapping
from typing import Any

import pytest
from botocore.exceptions import ClientError

from crc.aws.disk import DiskCleaner, DiskRuntime
from crc.aws.disk.client import all_regions
from crc.aws.disk.detach import DetachGuard
from crc.aws.disk.settings import build_settings
from crc.cli import clients as cli_clients
from crc.cli import context as cli_context
from crc.cli import scan as cli_scan
from crc.cli.main import main
from crc.core.cleaner import Operation, RunContext
from crc.core.registry import get
from tests import test_gcp_disk
from tests.helpers_cli_routes import FakeCleaners
from tests.test_aws_disk_engine import (
    FakeEc2,
    ScriptedCloudWatch,
    cleaner,
    complete,
    ids_for,
    run_engine,
    volume,
)
from tests.test_aws_spot_instance_requests_engine import mutations
from tests.test_aws_spot_instance_requests_engine import run_engine as run_spot_engine
from tests.test_aws_spot_instance_requests_parity import (
    Case,
    Spec,
    ce,
    one,
    tagged,
    vol,
)
from tests.test_aws_spot_instance_requests_parity import (
    FakeEc2 as FakeSpotEc2,
)

REPO = pathlib.Path(__file__).resolve().parents[1]
NOW = test_gcp_disk.NOW


def test_saf_002_the_only_explicit_exit_is_the_scan_tag_path() -> None:
    exits = {
        str(path.relative_to(REPO)): len(re.findall(r"sys\.exit\(", path.read_text("utf-8")))
        for path in [REPO / "crc.py", *sorted((REPO / "crc").rglob("*.py"))]
        if "sys.exit(" in path.read_text("utf-8")
    }
    assert exits == {"crc/cli/scan.py": 2}
    source = (REPO / "crc/cli/scan.py").read_text("utf-8")
    assert re.findall(r"sys\.exit\((.*?)\)", source) == ["0", "0"]


def test_saf_002_scan_tag_forces_a_dry_run_and_exits_zero_without_cleaning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dry_runs: list[object] = []

    class FakeCrc:
        def __init__(self, cloud: str, dry_run: object, *rest: object) -> None:
            dry_runs.append(dry_run)

    monkeypatch.setattr(cli_context, "CRC", FakeCrc)
    monkeypatch.setattr(cli_clients, "WebClient", lambda **_: object())
    monkeypatch.setattr(cli_scan, "notify_cleanup", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli_scan, "scan_instances_with_tag", lambda crc, key, value: [])
    monkeypatch.delenv("MAX_AGE", raising=False)
    fakes = FakeCleaners().install(monkeypatch)
    argv = ["crc.py", "--cloud", "aws", "--resource", "vm", "--scan_tag", "autoclean=false"]
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exit_info:
        main()
    assert exit_info.value.code == 0
    assert dry_runs == [True]
    assert fakes.runs == []
    assert fakes.applied == []


def test_saf_004_an_auth_failure_listing_regions_raises_out_of_the_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DeniedEc2:
        def describe_regions(self) -> Mapping[str, Any]:
            raise ClientError({"Error": {"Code": "AuthFailure", "Message": "denied"}}, "x")

    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: DeniedEc2())
    ec2 = FakeEc2([volume()])
    runtime = DiskRuntime(
        ec2_factory=lambda region: ec2,
        cloudwatch_factory=lambda region: ScriptedCloudWatch(),
        list_regions=all_regions,
    )
    disk = DiskCleaner(build_settings({"age": {"hours": 6}}), runtime)
    with pytest.raises(ClientError, match="AuthFailure"):
        run_engine(disk, dry_run=False)
    assert ec2.deleted == []


def test_saf_016_a_zero_detach_window_says_it_deletes_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    guard = DetachGuard(lambda region: ScriptedCloudWatch(), lambda: None)
    caplog.set_level(logging.DEBUG)
    volumes: list[Mapping[str, Any]] = [volume("a"), volume("b")]
    kept = guard.drop_recently_attached("us-west-2", volumes, datetime.timedelta(), NOW)
    assert kept == []
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "Region us-west-2: detach_age window is zero, not deleting any of 2 volume(s)"
    ]
    assert not any("keep" in message.lower() for message in messages)


def test_saf_016_only_dropped_volumes_are_logged_as_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    results = [complete(q) for q in ids_for(0)]
    results += [complete(ids_for(1)[0], [1.0]), *[complete(q) for q in ids_for(1)[1:]]]
    disk = cleaner(
        FakeEc2([volume("vol-idle"), volume("vol-busy")]),
        ScriptedCloudWatch({"MetricDataResults": results}),
        detach_age={"days": 3},
    )
    caplog.set_level(logging.DEBUG)
    plan = disk.plan(RunContext(dry_run=True, operation=Operation.DELETE, options={}, now=NOW))
    assert [c.extra["VolumeId"] for c in plan.candidates] == ["vol-idle"]
    skipped = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Skipping")]
    assert len(skipped) == 1
    assert "vol-busy" in skipped[0]
    assert "vol-idle" not in skipped[0]


def _gcp_cleaner(monkeypatch: pytest.MonkeyPatch, client: Any, **options: Any) -> Any:
    monkeypatch.setattr("crc.gcp.disk_cleaner.ComputeDiskClient", lambda: client)
    context = RunContext(
        dry_run=False,
        operation=Operation.DELETE,
        options={"project_id": test_gcp_disk.PROJECT, **options},
        now=NOW,
    )
    return get("gcp", "disk")(context), context


def test_saf_022_gcp_disk_never_reads_age(monkeypatch: pytest.MonkeyPatch) -> None:
    client = test_gcp_disk.FakeClient({"z": [test_gcp_disk.disk("d", days=3)]})
    disk, context = _gcp_cleaner(monkeypatch, client, age={"days": 100}, detach_age={"days": 1})
    assert [c.id for c in disk.plan(context).candidates] == ["d"]


def test_saf_023_gcp_disk_dry_run_without_detach_age_lists_every_detached_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    never_detached = test_gcp_disk.disk("never")
    never_detached.last_detach_timestamp = None
    client = test_gcp_disk.FakeClient(
        {
            "z": [
                test_gcp_disk.disk("fresh", days=0.001),
                test_gcp_disk.disk("old", days=400),
                test_gcp_disk.disk("attached", users=["vm"]),
                never_detached,
            ]
        }
    )
    monkeypatch.setattr("crc.gcp.disk_cleaner.ComputeDiskClient", lambda: client)
    context = RunContext(
        dry_run=True,
        operation=Operation.DELETE,
        options={"project_id": test_gcp_disk.PROJECT},
        now=NOW,
    )
    disk = get("gcp", "disk")(context)
    assert [c.id for c in disk.plan(context).candidates] == ["fresh", "old"]


def test_saf_023_gcp_disk_without_detach_age_deletes_every_detached_disk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = test_gcp_disk.FakeClient({"z": [test_gcp_disk.disk("old", days=400)]})
    with pytest.raises(ValueError, match="detach_age"):
        _gcp_cleaner(monkeypatch, client)
    assert client.calls == []


@pytest.mark.parametrize(
    ("label", "days", "expected"),
    [("{'hours': 1}", 0.5, ["d"]), ("{'days': 5}", 3, []), ("{'days': 2}", 3, ["d"])],
)
def test_saf_024_a_custom_age_label_replaces_detach_age(
    monkeypatch: pytest.MonkeyPatch, label: str, days: float, expected: list[str]
) -> None:
    client = test_gcp_disk.FakeClient(
        {"z": [test_gcp_disk.disk("d", days=days, labels={"ttl": label})]}
    )
    disk, context = _gcp_cleaner(
        monkeypatch, client, custom_age_tag_key="ttl", detach_age={"days": 1}
    )
    assert [c.id for c in disk.plan(context).candidates] == expected


def test_saf_034_the_sweep_isolates_a_failing_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = Spec(
        [],
        volumes={
            "vol-a": vol("available", tagged("sir-gone"), 10, None),
            "vol-b": vol("available", tagged("sir-gone"), 10, None),
        },
    )
    real_delete = FakeSpotEc2.delete_volume

    def flaky(self: FakeSpotEc2, *, VolumeId: str) -> Any:
        if VolumeId == "vol-a":
            self._rec("delete_volume", VolumeId=VolumeId)
            raise ce("VolumeInUse", "DeleteVolume")
        return real_delete(self, VolumeId=VolumeId)

    monkeypatch.setattr(FakeSpotEc2, "delete_volume", flaky)
    aws, outcome = run_spot_engine(Case("x", one(spec)), monkeypatch, dry_run=False)
    assert [m for m in mutations(aws) if m[0] == "delete_volume"] == [
        ("delete_volume", {"VolumeId": "vol-a"}),
        ("delete_volume", {"VolumeId": "vol-b"}),
    ]
    assert outcome.result is not None
    assert outcome.result.related["Disk"] == ("vol-b",)
