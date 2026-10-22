"""CLI wiring of crc.core.logger: one RUN START / RESULT pair per cloud x resource pass."""

import logging
from typing import Any
from unittest.mock import Mock

import pytest

from crc.cli import dispatch, scan
from crc.cli.context import RunContext
from crc.cli.inputs import Options, Targets
from crc.core import logger

_FIELDS = {name: None for name in Options.__dataclass_fields__}


def _options(**values: Any) -> Options:
    return Options(**{**_FIELDS, "operation_type": "delete", "dry_run": False, **values})


def _targets(clouds: list[str], resources: list[str], opt_in: bool = False) -> Targets:
    return Targets(
        cloud_arg="all" if len(clouds) > 1 else clouds[0],
        aws_disk_opt_in=opt_in,
        clouds=clouds,
        resources=resources,
    )


@pytest.fixture(autouse=True)
def _fake_crc(monkeypatch: pytest.MonkeyPatch) -> Mock:
    crc = Mock()
    monkeypatch.setattr(dispatch, "make_crc", lambda context, cloud: crc)
    logger.finish()
    return crc


def _run_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if r.getMessage().startswith(("RUN START", "RESULT"))
    ]


def test_each_cloud_and_resource_pass_gets_a_header_and_a_result(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    options = _options(name_regex=["^t-"], age={"days": 1}, resource_group="rg")
    dispatch.run_dispatch(Mock(), options, _targets(["aws", "gcp"], ["keypair", "ip"]))
    lines = _run_lines(caplog)
    assert len(lines) == len(["start", "result"]) * 4
    starts = [line for line in lines if line.startswith("RUN START")]
    assert [line.split()[2:5] for line in starts] == [
        ["cloud=aws", "resource=keypair", "operation=delete"],
        ["cloud=aws", "resource=ip", "operation=delete"],
        ["cloud=gcp", "resource=keypair", "operation=delete"],
        ["cloud=gcp", "resource=ip", "operation=delete"],
    ]
    assert starts[0] == (
        "RUN START cloud=aws resource=keypair operation=delete dry_run=false "
        'age={"days":1} name_regex=["^t-"] resource_group="rg"'
    )
    assert lines[1].startswith(
        "RESULT cloud=aws resource=keypair operation=delete dry_run=false selected=0 done=0 "
        "failed=0 skipped=0 regions=0 regions_skipped=0 duration_s="
    )
    assert [line.startswith("RUN START") for line in lines] == [True, False] * 4


def test_dry_run_and_stop_are_reported(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    options = _options(operation_type="stop", dry_run=True, filter_tags={"a": ["b"]})
    dispatch.run_dispatch(Mock(), options, _targets(["azure"], ["vm"]))
    lines = _run_lines(caplog)
    assert lines[0] == (
        'RUN START cloud=azure resource=vm operation=stop dry_run=true filter_tags={"a":["b"]}'
    )
    assert lines[1].startswith("RESULT cloud=azure resource=vm operation=stop dry_run=true ")


def test_result_is_logged_and_the_error_propagates_when_the_cleaner_raises(
    caplog: pytest.LogCaptureFixture, _fake_crc: Mock
) -> None:
    caplog.set_level(logging.INFO)
    _fake_crc.delete_keypairs.side_effect = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"):
        dispatch.run_dispatch(Mock(), _options(), _targets(["aws"], ["keypair", "ip"]))
    lines = _run_lines(caplog)
    assert [line.split()[0] for line in lines] == ["RUN", "RESULT"]
    assert logger.current().attached is False
    _fake_crc.delete_ip.assert_not_called()


def test_aws_disk_opt_in_refusal_still_closes_the_run(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    with pytest.raises(ValueError, match="AWS EBS volume cleanup requires"):
        dispatch.run_dispatch(Mock(), _options(), _targets(["aws"], ["disk"]))
    assert [line.split()[0] for line in _run_lines(caplog)] == ["RUN", "RESULT"]


def test_cleaner_run_lines_land_between_header_and_result(
    caplog: pytest.LogCaptureFixture, _fake_crc: Mock
) -> None:
    caplog.set_level(logging.INFO)

    def cleaner(*args: Any) -> None:
        run = logger.current()
        run.region("us-east-1", 4, 1, 0.4)
        run.action("deleted", "us-east-1", "kp-1", "kp")
        run.skip("too_young")
        run.fail("us-east-1", "kp-2", ValueError("bad"))

    _fake_crc.delete_keypairs.side_effect = cleaner
    dispatch.run_dispatch(Mock(), _options(), _targets(["aws"], ["keypair"]))
    messages = caplog.messages
    assert [m.split()[0] for m in messages] == [
        "RUN",
        "REGION",
        "ACTION",
        "SKIPPED",
        "ERRORS",
        "FAILED",
        "RESULT",
    ]
    assert messages[-1].startswith(
        "RESULT cloud=aws resource=keypair operation=delete dry_run=false selected=1 done=1 "
        "failed=1 skipped=1 regions=1 regions_skipped=0 duration_s="
    )


def test_unknown_resource_has_no_run(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    dispatch.run_dispatch(Mock(), _options(), _targets(["aws"], ["nothing"]))
    assert caplog.records == []


def test_scan_logs_run_start_and_result_per_cloud(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO)
    crc = Mock()
    crc.scan_instances_with_tag.return_value = []
    monkeypatch.setattr(scan, "make_crc", lambda context, cloud: crc)
    context = RunContext(False, None, None, None, "proj", "rg", None, None)
    options = _options(scan_tag="auto_clean=false", project_id="proj", resource_group="rg")
    with pytest.raises(SystemExit) as exc_info:
        scan.run_scan(context, options, _targets(["aws", "gcp"], ["vm"]))
    assert exc_info.value.code == 0
    lines = _run_lines(caplog)
    assert [line.split()[:5] for line in lines] == [
        ["RUN", "START", "cloud=aws", "resource=vm", "operation=scan"],
        ["RESULT", "cloud=aws", "resource=vm", "operation=scan", "dry_run=true"],
        ["RUN", "START", "cloud=gcp", "resource=vm", "operation=scan"],
        ["RESULT", "cloud=gcp", "resource=vm", "operation=scan", "dry_run=true"],
    ]
    assert 'resource_group="rg" project_id="proj"' in lines[0]
