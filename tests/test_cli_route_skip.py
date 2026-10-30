"""A Route can skip a pair as the legacy `_spot_instances` handler did (SAF-037)."""

import logging
from typing import Any

import pytest

from crc.cli import bridge, dispatch
from crc.cli.context import RunContext
from crc.cli.inputs import read_options, resolve_targets
from tests.helpers_cli_routes import FakeCleaners

CONTEXT = RunContext(
    dry_run=False,
    notags=None,
    slack_client=None,
    influxdb_client=None,
    project_id=None,
    resource_group=None,
    slack_channel=None,
    influxdb=None,
)

ALL_WARNING = (
    "GCP/OCI spot_instances require explicit --cloud {cloud} (not --cloud all); skipped. "
    "Azure spot_instances still runs under --cloud all for backward compatibility."
)


def run_with(monkeypatch: pytest.MonkeyPatch, cloud: str) -> None:
    monkeypatch.setattr(
        dispatch, "make_crc", lambda context, name: type("C", (), {"cloud": name})()
    )
    options = read_options(
        {"cloud": cloud, "resource": "spot_instances", "age": {"days": 1}, "resource_group": "rg"}
    )
    dispatch.run_dispatch(CONTEXT, options, resolve_targets(options))


def run(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    cloud: str,
    resource: str,
    **flags: Any,
) -> FakeCleaners:
    fakes = FakeCleaners().install(monkeypatch)
    monkeypatch.setattr(
        dispatch, "make_crc", lambda context, name: type("C", (), {"cloud": name})()
    )
    options = read_options(
        {
            "cloud": cloud,
            "resource": resource,
            "operation_type": "delete",
            "age": {"days": 1},
            "resource_group": "rg",
        }
        | flags
    )
    caplog.set_level(logging.INFO)
    dispatch.run_dispatch(CONTEXT, options, resolve_targets(options))
    return fakes


@pytest.mark.parametrize("cloud", ["gcp", "oci"])
def test_saf_037_spot_instances_under_cloud_all_warns_and_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, cloud: str
) -> None:
    fakes = run(monkeypatch, caplog, "all", "spot_instances", project_id="proj")
    assert (cloud, "spot_instances") not in [(c, k) for c, k, _ in fakes.runs]
    assert ALL_WARNING.format(cloud=cloud) in caplog.messages
    assert [(c, k) for c, k, _ in fakes.runs if c in {"gcp", "oci"}] == []


@pytest.mark.parametrize("cloud", ["gcp", "oci"])
def test_saf_037_spot_instances_with_an_explicit_cloud_runs_the_cleaner(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, cloud: str
) -> None:
    fakes = run(monkeypatch, caplog, cloud, "spot_instances", project_id="proj")
    assert [(c, k) for c, k, _ in fakes.runs] == [(cloud, "spot_instances")]
    assert fakes.applied == [(cloud, "spot_instances")]
    assert ALL_WARNING.format(cloud=cloud) not in caplog.messages


def test_gcp_spot_instances_without_a_project_raises_before_any_cleaner(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with pytest.raises(ValueError, match="project_id is required for GCP Spot VM cleanup"):
        run(monkeypatch, caplog, "gcp", "spot_instances")


@pytest.mark.parametrize(
    ("cloud", "resource", "message"),
    [
        (
            "aws",
            "spot_instances",
            "For AWS use --resource spot_instance_requests "
            "(cancel request before terminate). Skipping spot_instances.",
        ),
        (
            "azure",
            "spot_instance_requests",
            "spot_instance_requests is AWS-only; skipped for azure (use --resource spot_instances)",
        ),
        (
            "gcp",
            "spot_instance_requests",
            "spot_instance_requests is AWS-only; skipped for gcp (use --resource spot_instances)",
        ),
        (
            "oci",
            "spot_instance_requests",
            "spot_instance_requests is AWS-only; skipped for oci (use --resource spot_instances)",
        ),
    ],
)
def test_pairs_a_cloud_does_not_have_warn_and_run_nothing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    cloud: str,
    resource: str,
    message: str,
) -> None:
    fakes = run(monkeypatch, caplog, cloud, resource, project_id="proj", resource_group="rg")
    assert fakes.runs == []
    assert message in caplog.messages


def test_a_skipped_pair_builds_no_registry_and_no_notifier(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fakes = FakeCleaners().install(monkeypatch)
    built: list[tuple[str, str]] = []

    def notifiers(crc: Any, route: Any) -> list[Any]:
        built.append((crc.cloud, "notifiers"))
        return []

    def registry() -> Any:
        built.append(("registry", "registry"))
        return fakes.registry

    monkeypatch.setattr(bridge, "build_notifiers", notifiers)
    monkeypatch.setattr(bridge, "build_registry", registry)
    for cloud, expected_cloud in (("oci", "oci"), ("all", "azure")):
        built.clear()
        run_with(monkeypatch, cloud)
        assert built == [("registry", "registry"), (expected_cloud, "notifiers")]
