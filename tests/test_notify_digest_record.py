"""The Slack digest record writer: record content, switch, fail-safe and the env threading."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from crc.cli import bridge
from crc.cli.digest_target import read_digest_target
from crc.cli.routes import NotifyMode, Route
from crc.core.cleaner import Failure, Operation, RunContext
from crc.core.report import DISK, NIC, VM, Report, Section
from crc.notify.digest_record import DigestTarget, build_record, record_name, write_record
from crc.notify.slack import SlackNotifier

NOW = datetime(2026, 10, 26, 7, 31, 2, tzinfo=UTC)
JENKINS = {
    "STAGE_NAME": "AWS VMs 6 Hours old",
    "BUILD_URL": "https://jenkins.example/job/x/16654/",
    "BUILD_NUMBER": "16654",
    "JOB_NAME": "cloud-resource-cleanups/cloud-resource-cleanups",
}
NAME_RE = re.compile(r"^20261026T073102-\d+-[0-9a-f]{32}\.json$")


def ctx(operation: Operation = Operation.DELETE) -> RunContext:
    return RunContext(False, operation, {}, NOW)


def rep(*sections: Section, **kw: Any) -> Report:
    return Report("aws", Operation.DELETE, False, tuple(sections), **kw)


def target(directory: Path, **kw: Any) -> DigestTarget:
    return DigestTarget(str(directory), kind="vm", **kw)


def records(directory: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text("utf-8")) for p in sorted(directory.iterdir())]


def notifier(client: Mock, digest: DigestTarget | None) -> SlackNotifier:
    n = SlackNotifier(client, "chan")
    n.digest = digest
    return n


def test_record_for_a_normal_report() -> None:
    t = DigestTarget("/d", "vm", "ONLY_YBA", "AWS VMs 6 Hours old", "https://b/1/", "1", "job/name")
    report = rep(Section(VM, ("i-1", "i-2")), failures=(Failure("i-0abc", "Denied: x"),))
    assert build_record(report, "chan", t, NOW) == {
        "version": 1,
        "written_at": "2026-10-26T07:31:02Z",
        "channel": "chan",
        "cloud": "aws",
        "kind": "vm",
        "operation": "delete",
        "dry_run": False,
        "resource_suffix": "ONLY_YBA",
        "stage": "AWS VMs 6 Hours old",
        "build_url": "https://b/1/",
        "build_number": "1",
        "job_name": "job/name",
        "sections": [{"label": "VM", "ids": ["i-1", "i-2"]}],
        "failures": [{"id": "i-0abc", "error": "Denied: x"}],
        "by_owner": None,
    }


def test_slack_ids_preferred_over_ids_and_empty_sections_kept() -> None:
    report = rep(Section(VM, ("a",)), Section(NIC, ("n1",), slack_ids=("a",)), Section(DISK, ()))
    got = build_record(report, "chan", DigestTarget("/d"), NOW)
    assert got["sections"] == [
        {"label": "VM", "ids": ["a"]},
        {"label": "NIC", "ids": ["a"]},
        {"label": "Disk", "ids": []},
    ]


def test_stop_dry_run_and_missing_jenkins_env_are_lower_case_and_null() -> None:
    report = Report("gcp", Operation.STOP, True, (Section(VM, ()),))
    got = build_record(report, None, DigestTarget("/d"), NOW)
    assert (got["operation"], got["dry_run"], got["channel"]) == ("stop", True, None)
    for key in ("kind", "resource_suffix", "stage", "build_url", "build_number", "job_name"):
        assert got[key] is None


def test_naive_clock_is_taken_as_utc() -> None:
    got = build_record(rep(), "c", DigestTarget("/d"), datetime(2026, 1, 2, 3, 4, 5))  # noqa: DTZ001 - naive on purpose
    assert got["written_at"] == "2026-01-02T03:04:05Z"


def test_write_is_atomic_named_and_creates_the_directory(tmp_path: Path) -> None:
    directory = tmp_path / "a" / "b"
    path = write_record(str(directory), {"version": 1, "x": "é"}, NOW)
    assert NAME_RE.match(path.name)
    assert [p.name for p in directory.iterdir()] == [path.name]
    assert path.read_text("utf-8") == '{"version": 1, "x": "\\u00e9"}'
    assert record_name(NOW) != record_name(NOW)


def test_failed_replace_leaves_no_temp_file(tmp_path: Path) -> None:
    with patch("os.replace", side_effect=OSError("boom")), pytest.raises(OSError, match="boom"):
        write_record(str(tmp_path), {"version": 1}, NOW)
    assert list(tmp_path.iterdir()) == []


def test_digest_on_writes_record_and_posts_nothing(tmp_path: Path) -> None:
    client = Mock()
    report = rep(Section(VM, ("i-1",)))
    notifier(client, target(tmp_path)).send(report, ctx())
    client.chat_postMessage.assert_not_called()
    (record,) = records(tmp_path)
    assert record["sections"] == [{"label": "VM", "ids": ["i-1"]}]
    assert record["channel"] == "chan"


def test_empty_report_still_writes_a_record(tmp_path: Path) -> None:
    client = Mock()
    notifier(client, target(tmp_path)).send(rep(Section(VM, ())), ctx())
    client.chat_postMessage.assert_not_called()
    assert records(tmp_path)[0]["sections"] == [{"label": "VM", "ids": []}]


def test_digest_off_posts_as_before_and_writes_nothing(tmp_path: Path) -> None:
    client = Mock()
    report = rep(Section(VM, ("i-1",)))
    notifier(client, None).send(report, ctx())
    expected = [m.text for m in report.slack_messages()]
    assert [c.kwargs["text"] for c in client.chat_postMessage.call_args_list] == expected
    assert list(tmp_path.iterdir()) == []


def test_owner_report_pings_and_writes_record(tmp_path: Path) -> None:
    client = Mock()
    client.users_lookupByEmail.return_value = {"user": {"id": "U1"}}
    client.conversations_open.return_value = {"channel": {"id": "D1"}}
    report = rep(Section(DISK, ()), by_owner={"bob": ("d1", "d2")})
    notifier(client, target(tmp_path)).send(report, ctx())
    (call,) = client.chat_postMessage.call_args_list
    assert call.kwargs["channel"] == "D1"
    assert "d1" in call.kwargs["text"]
    assert records(tmp_path)[0]["by_owner"] == {"bob": ["d1", "d2"]}


def test_write_failure_warns_once_and_posts_directly(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    client = Mock()
    report = rep(Section(VM, ("i-1",)))
    with caplog.at_level("WARNING"):
        notifier(client, target(blocker / "sub")).send(report, ctx())
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "digest record not written" in warnings[0].getMessage()
    assert "OSError" in warnings[0].getMessage() or "Error" in warnings[0].getMessage()
    expected = [m.text for m in report.slack_messages()]
    assert [c.kwargs["text"] for c in client.chat_postMessage.call_args_list] == expected


def test_serialisation_error_falls_back_to_direct_post(tmp_path: Path) -> None:
    client = Mock()
    report = rep(Section(VM, ("i-1",)))
    with patch("crc.notify.slack.build_record", side_effect=TypeError("nope")):
        notifier(client, target(tmp_path)).send(report, ctx())
    assert client.chat_postMessage.call_count == 1
    assert list(tmp_path.iterdir()) == []


def test_read_digest_target_unset_or_empty_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRC_SLACK_DIGEST_DIR", raising=False)
    assert read_digest_target("vm", None) is None
    monkeypatch.setenv("CRC_SLACK_DIGEST_DIR", "")
    assert read_digest_target("vm", None) is None


def test_read_digest_target_reads_the_jenkins_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRC_SLACK_DIGEST_DIR", "/dd")
    for key, value in JENKINS.items():
        monkeypatch.setenv(key, value)
    got = read_digest_target("vm", "ONLY_YBA")
    assert got == DigestTarget(
        "/dd",
        "vm",
        "ONLY_YBA",
        *(JENKINS[k] for k in ("STAGE_NAME", "BUILD_URL", "BUILD_NUMBER", "JOB_NAME")),
    )


def test_read_digest_target_missing_jenkins_env_is_null(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CRC_SLACK_DIGEST_DIR", "/dd")
    for key in JENKINS:
        monkeypatch.delenv(key, raising=False)
    got = read_digest_target("disk", None)
    assert got == DigestTarget("/dd", "disk")


def _crc(**kw: Any) -> Any:
    return SimpleNamespace(slack_client=Mock(), slack_channel="chan", influxdb_client=None, **kw)


def test_bridge_attaches_target_only_when_env_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    route = Route(options=lambda o: {}, notify=NotifyMode.REPORT)
    monkeypatch.delenv("CRC_SLACK_DIGEST_DIR", raising=False)
    notifiers = bridge.build_notifiers(_crc(resource_suffix="S"), route)
    bridge.attach_digest(_crc(resource_suffix="S"), "vm", notifiers)
    assert [getattr(n, "digest", None) for n in notifiers] == [None]
    monkeypatch.setenv("CRC_SLACK_DIGEST_DIR", str(tmp_path))
    bridge.attach_digest(_crc(resource_suffix="S"), "vm", notifiers)
    digest = notifiers[0].digest  # type: ignore[attr-defined]
    assert (digest.directory, digest.kind, digest.resource_suffix) == (str(tmp_path), "vm", "S")
