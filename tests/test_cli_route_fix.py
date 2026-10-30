"""BUG-009, BUG-084 (AWS-only snapshot; vpc removed by BUG-083) and BUG-344 (blank --scan_tag)."""

import logging
import os
import sys
from typing import Any

import pytest

from crc.cli import clients as cli_clients
from crc.cli import context as cli_context
from crc.cli import scan as cli_scan
from crc.cli.main import main
from tests.helpers_cli_routes import FakeCleaners

PROD_SNAPSHOT = [
    "--cloud",
    "aws",
    "--resource",
    "snapshot",
    "--age",
    '{"days": 90}',
    "--exception_tags",
    '{"autoclean": ["false", "False", "FALSE"], "auto_clean": ["false", "False", "FALSE"]}',
]
PROD_SCAN = ["--cloud", "aws", "--resource", "vm", "--scan_tag", "autoclean=false", "--dry_run"]
OTHER_CLOUDS = ("azure", "gcp", "oci")
SCAN_ERROR = "--scan_tag must not be empty; use KEY=VALUE format, e.g. auto_clean=false"


class Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[Any, ...]] = []

    def crc_class(self) -> type:
        recorder = self

        class FakeCRC:
            def __init__(self, cloud: str, *args: Any) -> None:
                self.cloud = cloud
                recorder.events.append(("CRC", cloud))

            def __getattr__(self, name: str) -> Any:
                def record(*args: Any, **kwargs: Any) -> Any:
                    recorder.events.append((name, self.cloud))
                    return "msg" if name == "get_msg" else []

                return record

        return FakeCRC


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    rec = Recorder()
    monkeypatch.setattr(cli_context, "CRC", rec.crc_class())
    monkeypatch.setattr(cli_clients, "WebClient", lambda **_: object())
    monkeypatch.setattr(cli_scan, "notify_cleanup", lambda *a, **k: None)

    def scan(crc: Any, key: str, value: str) -> list[Any]:
        rec.events.append(("scan_instances_with_tag", crc.cloud))
        return []

    monkeypatch.setattr(cli_scan, "scan_instances_with_tag", scan)
    monkeypatch.delenv("MAX_AGE", raising=False)
    cleaners = FakeCleaners().install(monkeypatch)

    def snapshot(context: Any) -> None:
        rec.events.append(("Snapshot", context.options["age_days"], context.dry_run))
        rec.events.append(("Snapshot.delete",))

    cleaners.side_effect[("aws", "snapshot")] = snapshot
    return rec


def run(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["crc.py", *argv])
    main()


@pytest.mark.parametrize("resource", ["snapshot"])
@pytest.mark.parametrize("cloud", ["azure", "gcp", "oci"])
def test_single_non_aws_cloud_raises_before_any_cleaner(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch, cloud: str, resource: str
) -> None:
    with pytest.raises(ValueError, match=f"--resource {resource} is AWS-only; use --cloud aws"):
        run(monkeypatch, ["--cloud", cloud, "--resource", resource, "--max_age", "5"])
    assert recorder.events == []


@pytest.mark.parametrize("dry", [[], ["--dry_run"]])
def test_aws_snapshot_runs_as_before(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch, dry: list[str]
) -> None:
    run(monkeypatch, [*PROD_SNAPSHOT, *dry])
    assert recorder.events[:3] == [
        ("CRC", "aws"),
        ("Snapshot", 90, bool(dry)),
        ("Snapshot.delete",),
    ]


ARGPARSE_USAGE_ERROR = 2


@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp", "oci", "all"])
def test_bug_083_resource_vpc_is_an_invalid_choice_and_runs_nothing(
    recorder: Recorder,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cloud: str,
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run(monkeypatch, ["--cloud", cloud, "--resource", "vpc"])
    assert exit_info.value.code == ARGPARSE_USAGE_ERROR
    assert "argument -r/--resource: invalid choice: 'vpc'" in capsys.readouterr().err
    assert recorder.events == []


def test_cloud_all_snapshot_runs_once_for_aws_and_warns(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    run(monkeypatch, ["--cloud", "all", "--resource", "snapshot", "--dry_run"])
    assert [e for e in recorder.events if e[0] == "Snapshot"] == [("Snapshot", 90, True)]
    assert [e for e in recorder.events if e[0] == "Snapshot.delete"] == [("Snapshot.delete",)]
    assert [r.getMessage() for r in caplog.records].count(
        "snapshot is not supported on azure; skipped under --cloud all"
    ) == 1


@pytest.mark.parametrize("tag", ["", " ", "\t \n"])
@pytest.mark.parametrize("resource", ["vm", "snapshot", "keypair"])
def test_blank_scan_tag_raises_before_any_cleaner(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch, tag: str, resource: str
) -> None:
    argv = ["--cloud", "aws", "--resource", resource, "--scan_tag", tag, "--max_age", "5"]
    with pytest.raises(ValueError, match="must not be empty"):
        run(monkeypatch, argv)
    assert recorder.events == []
    assert "MAX_AGE" not in os.environ


def test_blank_scan_tag_message(recorder: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError) as error:
        run(monkeypatch, ["--cloud", "aws", "--resource", "vm", "--scan_tag", ""])
    assert str(error.value) == SCAN_ERROR


def test_production_scan_still_scans_and_exits_zero(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit) as exit_:
        run(monkeypatch, PROD_SCAN)
    assert exit_.value.code == 0
    assert recorder.events == [("CRC", "aws"), ("scan_instances_with_tag", "aws")]


def test_scan_with_non_aws_snapshot_stays_read_only_scan(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = ["--cloud", "azure", "--resource", "snapshot", "--scan_tag", "k=v"]
    with pytest.raises(SystemExit) as exit_:
        run(monkeypatch, argv)
    assert exit_.value.code == 0
    assert recorder.events == [("CRC", "azure"), ("scan_instances_with_tag", "azure")]
