"""BUG-017 and BUG-018, the up-front check of every (cloud, resource) pair."""

import logging
from typing import Any

import pytest

from crc.cli import dispatch
from crc.cli.constants import CLOUDS, RESOURCES
from crc.cli.context import RunContext
from crc.cli.inputs import Options, Targets, read_options, resolve_targets
from tests.helpers_cli_routes import FakeCleaners

CONTEXT = RunContext(
    dry_run=True,
    notags=None,
    slack_client=None,
    influxdb_client=None,
    project_id=None,
    resource_group=None,
    slack_channel=None,
    influxdb=None,
)


def make_options(cloud: str, resource: str) -> Options:
    return read_options({"cloud": cloud, "resource": resource, "operation_type": "delete"})


class Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = self

        class FakeCRC:
            def __init__(self, cloud: str) -> None:
                self.cloud = cloud
                recorder.calls.append(("CRC", cloud))

        def routed(
            crc: Any, cloud: Any, kind: Any, options: Options, targets: Targets, route: Any
        ) -> None:
            recorder.calls.append((kind, cloud))

        monkeypatch.setattr(dispatch, "make_crc", lambda context, cloud: FakeCRC(cloud))
        monkeypatch.setattr(dispatch, "run_routed", routed)


def run(cloud: str, resource: str) -> None:
    options = make_options(cloud, resource)
    dispatch.run_dispatch(CONTEXT, options, resolve_targets(options))


@pytest.mark.parametrize(
    ("resource", "bad_cloud", "message"),
    [
        ("keypair", "azure", "Keypair operation is only supported on AWS."),
        ("keypair", "oci", "Keypair operation is only supported on AWS."),
        ("nic", "aws", "NICs operation is only supported on Azure."),
        ("nic", "gcp", "NICs operation is only supported on Azure."),
        ("kms", "gcp", "KMS operation is only supported on AWS."),
        ("ip", "oci", f"Invalid cloud provided: oci. Supported clouds are {CLOUDS}"),
    ],
)
def test_bug_017_explicit_cloud_raises_before_any_cleaner(
    monkeypatch: pytest.MonkeyPatch, resource: str, bad_cloud: str, message: str
) -> None:
    recorder = Recorder()
    recorder.install(monkeypatch)
    with pytest.raises(ValueError, match=message.replace("[", r"\[").replace("]", r"\]")):
        run(bad_cloud, resource)
    assert recorder.calls == []


@pytest.mark.parametrize(
    ("resource", "supported"),
    [
        ("keypair", ["aws"]),
        ("kms", ["aws"]),
        ("ip", ["aws", "azure", "gcp"]),
    ],
)
def test_bug_017_cloud_all_skips_unsupported_pairs_with_a_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    resource: str,
    supported: list[str],
) -> None:
    recorder = Recorder()
    recorder.install(monkeypatch)
    options = make_options("all", resource)
    targets = resolve_targets(options)
    with caplog.at_level(logging.INFO):
        dispatch.run_dispatch(CONTEXT, options, targets)
    assert recorder.calls == [
        pair
        for cloud in CLOUDS
        if cloud in supported
        for pair in (("CRC", cloud), (resource, cloud))
    ]
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warned == [
        f"{resource} is not supported on {cloud}; skipped under --cloud all"
        for cloud in CLOUDS
        if cloud not in supported
    ]
    skipped_logs = [
        r.getMessage()
        for r in caplog.records
        if r.name == "crc.run" and r.getMessage().startswith("SKIPPED ")
    ]
    assert skipped_logs == [
        f"SKIPPED cloud={cloud} resource={resource} reason=unsupported_cloud_resource count=1"
        for cloud in CLOUDS
        if cloud not in supported
    ]


@pytest.mark.parametrize(
    ("resource", "expected_cloud"),
    [
        ("keypair", "aws"),
        ("kms", "aws"),
    ],
)
def test_bug_017_cloud_all_real_crc_does_not_build_skipped_clouds(
    monkeypatch: pytest.MonkeyPatch,
    resource: str,
    expected_cloud: str,
) -> None:
    deleted: list[str] = []
    cleaners = FakeCleaners().install(monkeypatch)
    cleaners.side_effect[(expected_cloud, resource)] = lambda ctx: deleted.append(expected_cloud)
    options = read_options(
        {
            "cloud": "all",
            "resource": resource,
            "operation_type": "delete",
            "kms_key_description": "desc",
            "kms_user": "arn:user",
        }
    )
    dispatch.run_dispatch(CONTEXT, options, resolve_targets(options))
    assert deleted == [expected_cloud]
    assert [(c, k) for c, k, _ in cleaners.runs] == [(expected_cloud, resource)]


def test_bug_017_cloud_all_nic_raises_before_any_cleaner(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    recorder.install(monkeypatch)
    with pytest.raises(ValueError, match=r"NICs operation is only supported on Azure\."):
        run("all", "nic")
    assert recorder.calls == []


def test_bug_017_cloud_all_nic_real_crc_deletes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    deleted: list[str] = []
    cleaners = FakeCleaners().install(monkeypatch)
    cleaners.side_effect[("azure", "nic")] = lambda ctx: deleted.append("azure")
    with pytest.raises(ValueError, match=r"NICs operation is only supported on Azure\."):
        run("all", "nic")
    assert deleted == []
    assert cleaners.runs == []


@pytest.mark.parametrize("resource", ["disk", "all"])
def test_bug_018_cloud_all_with_disk_raises_before_any_cleaner(
    monkeypatch: pytest.MonkeyPatch, resource: str
) -> None:
    recorder = Recorder()
    recorder.install(monkeypatch)
    options = make_options("all", resource)
    with pytest.raises(ValueError, match="AWS EBS volume cleanup requires"):
        dispatch.run_dispatch(CONTEXT, options, resolve_targets(options))
    assert recorder.calls == []


def test_aws_disk_with_explicit_cloud_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    recorder.install(monkeypatch)
    run("aws", "disk")
    assert recorder.calls == [("CRC", "aws"), ("disk", "aws")]


@pytest.mark.parametrize(
    ("cloud", "resource"),
    [
        (cloud, resource)
        for cloud in CLOUDS
        for resource in RESOURCES
        if dispatch.is_supported_pair(cloud, resource)
    ],
)
def test_supported_explicit_pairs_run_unchanged(
    monkeypatch: pytest.MonkeyPatch, cloud: str, resource: str
) -> None:
    recorder = Recorder()
    recorder.install(monkeypatch)
    run(cloud, resource)
    assert recorder.calls == [("CRC", cloud), (resource, cloud)]
