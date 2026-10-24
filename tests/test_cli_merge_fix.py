"""The up-front pair check and the AWS-only snapshot rule hold together."""

import logging
import os
import re

import pytest

from crc.cli import dispatch as cli_dispatch
from tests.test_cli_route_fix import PROD_SCAN, PROD_SNAPSHOT, Recorder, recorder, run

__all__ = ["recorder"]

UNSUPPORTED_ONE_CLOUD = [
    ("azure", "snapshot", "--resource snapshot is AWS-only; use --cloud aws (got --cloud azure)"),
    ("azure", "keypair", "Keypair operation is only supported on AWS."),
    ("aws", "nic", "NICs operation is only supported on Azure."),
    ("oci", "kms", "KMS operation is only supported on AWS."),
]


def test_aws_only_resources_use_the_shared_pair_table() -> None:
    assert cli_dispatch.SUPPORTED_CLOUDS["snapshot"] == ("aws",)
    assert "vpc" not in cli_dispatch.SUPPORTED_CLOUDS
    assert not hasattr(cli_dispatch, "check_aws_only_resources")
    assert not cli_dispatch.is_supported_pair("azure", "snapshot")


@pytest.mark.parametrize(("cloud", "resource", "message"), UNSUPPORTED_ONE_CLOUD)
def test_every_unsupported_single_cloud_pair_fails_before_state_changes(
    recorder: Recorder,
    monkeypatch: pytest.MonkeyPatch,
    cloud: str,
    resource: str,
    message: str,
) -> None:
    extra = ["--kms_key_description", "d", "--kms_user", "u"] if resource == "kms" else []
    with pytest.raises(ValueError, match=re.escape(message)):
        run(monkeypatch, ["--cloud", cloud, "--resource", resource, "--max_age", "5", *extra])
    assert recorder.events == []
    assert "MAX_AGE" not in os.environ


def test_cloud_all_disk_fails_before_state_changes(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="AWS EBS volume cleanup requires"):
        run(monkeypatch, ["--cloud", "all", "--resource", "disk", "--max_age", "5"])
    assert recorder.events == []
    assert "MAX_AGE" not in os.environ


@pytest.mark.parametrize("resource", ["snapshot"])
def test_cloud_all_aws_only_runs_aws_once_and_builds_no_other_crc(
    recorder: Recorder,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    resource: str,
) -> None:
    caplog.set_level(logging.WARNING)
    run(monkeypatch, ["--cloud", "all", "--resource", resource, "--dry_run"])
    assert [e for e in recorder.events if e[0] == "CRC"] == [("CRC", "aws")]
    assert [r.getMessage() for r in caplog.records] == [
        f"{resource} is not supported on {cloud}; skipped under --cloud all"
        for cloud in ("azure", "gcp", "oci")
    ]


def test_cloud_all_nic_still_fails_closed(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match=re.escape("NICs operation is only supported on Azure.")):
        run(monkeypatch, ["--cloud", "all", "--resource", "nic", "--resource_group", "rg"])
    assert recorder.events == []


def test_blank_scan_tag_beats_unsupported_pair(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="--scan_tag must not be empty"):
        run(monkeypatch, ["--cloud", "azure", "--resource", "snapshot", "--scan_tag", " "])
    assert recorder.events == []


def test_scan_path_is_not_blocked_by_pair_validation(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit) as stop:
        run(monkeypatch, ["--cloud", "azure", "--resource", "snapshot", "--scan_tag", "k=v"])
    assert stop.value.code == 0


def test_production_commands_run_unchanged(
    recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    run(monkeypatch, PROD_SNAPSHOT)
    assert recorder.events[:3] == [("CRC", "aws"), ("Snapshot", 90, False), ("Snapshot.delete",)]
    recorder.events.clear()
    with pytest.raises(SystemExit) as stop:
        run(monkeypatch, PROD_SCAN)
    assert stop.value.code == 0
