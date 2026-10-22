"""SlackNotifier and the moved Slack helpers: exact client calls for every path."""

from datetime import UTC, datetime, timedelta, tzinfo
from types import MappingProxyType
from typing import Any, Self
from unittest.mock import Mock, call

import pytest
from slack_sdk.errors import SlackApiError

import crc.notifications
from crc.core.cleaner import Operation, RunContext
from crc.core.engine import Notifier
from crc.core.report import DISK, NIC, SPOT_INSTANCE_REQUEST, VM, Report, Section
from crc.notify import slack
from crc.notify.slack import (
    SlackNotifier,
    human_readable_timedelta,
    notify_cleanup,
    send_slack_message,
)

IDS = ["i-1", "i-2"]
TWO = 2
PER_MESSAGE = 20
SAFE_CHARS = 2800
NOW = datetime(2026, 1, 10, 12, 0, 0, tzinfo=UTC)


def sec(label: str, ids: list[str], **flags: Any) -> Section:
    return Section(label, tuple(ids), **flags)


def context(dry_run: bool = False, operation: Operation = Operation.DELETE) -> RunContext:
    return RunContext(dry_run, operation, {}, NOW)


def report(
    sections: list[Section],
    cloud: str = "aws",
    dry_run: bool = False,
    operation: Operation = Operation.DELETE,
) -> Report:
    return Report(cloud, operation, dry_run, tuple(sections))


def send(
    client: Mock, rep: Report, channel: str = "chan", dry_run: bool = False
) -> None:
    SlackNotifier(client, channel).send(rep, context(dry_run, rep.operation))


def test_notifier_is_a_core_notifier() -> None:
    notifier: Notifier = SlackNotifier(Mock(), "chan")
    assert notifier.name == "slack"
    assert notifier.fail_open is False


@pytest.mark.parametrize(
    ("label", "expected_label"),
    [(VM, "VM"), (NIC, "NIC"), ("IP", "IP"), ("Keypair", "Keypair"), ("KMS", "KMS")],
)
@pytest.mark.parametrize("cloud", ["aws", "azure", "gcp", "oci"])
def test_single_section_message(cloud: str, label: str, expected_label: str) -> None:
    client = Mock()
    send(client, report([sec(label, IDS)], cloud))
    assert client.mock_calls == [
        call.chat_postMessage(
            channel="#chan",
            text=f"Deleted the following `2` {cloud} {expected_label}(s):\n`['i-1', 'i-2']`",
        )
    ]


def test_dry_run_message() -> None:
    client = Mock()
    send(client, report([sec(VM, IDS)], dry_run=True), dry_run=True)
    assert client.mock_calls == [
        call.chat_postMessage(
            channel="#chan",
            text="`Dry Run`: Will be Deleted: `2` aws VM(s):\n`['i-1', 'i-2']`",
        )
    ]


def test_stopped_message() -> None:
    client = Mock()
    send(client, report([sec(VM, IDS)], operation=Operation.STOP))
    assert client.mock_calls == [
        call.chat_postMessage(
            channel="#chan", text="Stopped the following `2` aws VM(s):\n`['i-1', 'i-2']`"
        )
    ]


def test_disk_list_sets_link_names() -> None:
    client = Mock()
    send(client, report([sec(DISK, ["v-1"], link_names=True)], "gcp"))
    assert client.mock_calls == [
        call.chat_postMessage(
            channel="#chan",
            text="Deleted the following `1` gcp Disk(s):\n`['v-1']`",
            link_names=True,
        )
    ]


def test_empty_list_is_still_sent() -> None:
    client = Mock()
    send(client, report([sec("Keypair", [])]))
    assert client.mock_calls == [
        call.chat_postMessage(
            channel="#chan", text="Deleted the following `0` aws Keypair(s):\n`[]`"
        )
    ]


def test_spot_request_sends_request_then_disks() -> None:
    client = Mock()
    sections = [sec(SPOT_INSTANCE_REQUEST, ["sir-1"]), sec(DISK, ["v-1"], notify_empty=False)]
    send(client, report(sections))
    assert client.mock_calls == [
        call.chat_postMessage(
            channel="#chan",
            text="Deleted the following `1` aws "
            "SPOT_INSTANCE_REQUEST_AND_ASSOCIATED_INSTANCES(s):\n`['sir-1']`",
        ),
        call.chat_postMessage(
            channel="#chan", text="Deleted the following `1` aws Disk(s):\n`['v-1']`"
        ),
    ]


def test_spot_request_without_volumes_sends_no_disk_message() -> None:
    client = Mock()
    sections = [sec(SPOT_INSTANCE_REQUEST, ["sir-1"]), sec(DISK, [], notify_empty=False)]
    send(client, report(sections))
    assert len(client.chat_postMessage.mock_calls) == 1


def test_azure_vm_sends_vm_names_as_nic_list() -> None:
    client = Mock()
    sections = [sec(VM, ["vm-1"]), sec(NIC, ["nic-1"], slack_ids=("vm-1",))]
    send(client, report(sections, "azure"))
    assert client.mock_calls == [
        call.chat_postMessage(
            channel="#chan", text="Deleted the following `1` azure VM(s):\n`['vm-1']`"
        ),
        call.chat_postMessage(
            channel="#chan", text="Deleted the following `1` azure NIC(s):\n`['vm-1']`"
        ),
    ]


def test_channel_with_hash_becomes_double_hash() -> None:
    client = Mock()
    send(client, report([sec(VM, IDS)]), channel="#chan")
    assert client.chat_postMessage.call_args.kwargs["channel"] == "##chan"


def test_failed_post_is_printed_and_swallowed(capsys: pytest.CaptureFixture[str]) -> None:
    client = Mock()
    client.chat_postMessage.side_effect = RuntimeError("boom")
    send(client, report([sec(VM, ["a"]), sec(NIC, ["b"])]))
    out = capsys.readouterr().out
    assert len(client.chat_postMessage.mock_calls) == TWO
    assert (
        "Failed to send message 'Deleted the following `1` aws VM(s):\n`['a']`' "
        "to Slack channel 'chan': boom\n"
    ) in out


def test_missing_channel_is_printed_not_raised(capsys: pytest.CaptureFixture[str]) -> None:
    client = Mock()
    notifier = SlackNotifier(client, None)  # type: ignore[arg-type]
    notifier.send(report([sec(VM, IDS)]), context())
    assert client.mock_calls == []
    assert "to Slack channel 'None': can only concatenate str" in capsys.readouterr().out


def owner_report(dry_run: bool = False) -> Report:
    return Report(
        "aws",
        Operation.DELETE,
        dry_run,
        (Section("Disk", ()),),
        by_owner=MappingProxyType({"alice": ("v-1", "v-2")}),
    )


def ping_client(
    user: str | None = None, groups: Any = None, group_error: bool = False
) -> Mock:
    client = Mock()
    if user is None:
        client.users_lookupByEmail.side_effect = RuntimeError("users_not_found")
    else:
        client.users_lookupByEmail.return_value = {"user": {"id": user}}
    if group_error:
        client.usergroups_list.side_effect = RuntimeError("no scope")
    else:
        client.usergroups_list.return_value = {"usergroups": groups or []}
    client.conversations_open.return_value = {"channel": {"id": "D1"}}
    return client


def test_owner_untagged_is_posted_to_channel(capsys: pytest.CaptureFixture[str]) -> None:
    client = ping_client()
    send(client, owner_report())
    assert client.mock_calls == [
        call.usergroups_list(),
        call.users_lookupByEmail(email="alice@yugabyte.com"),
        call.chat_postMessage(
            channel="#chan",
            text="Deleted the following`2` aws Disk(s):\n*alice* disks `['v-1', 'v-2']`\n\n",
            link_names=True,
        ),
    ]
    assert capsys.readouterr().out == "Pinging 'alice'\n"


def test_owner_group_is_mentioned_in_channel() -> None:
    client = ping_client(groups=[{"handle": "bob", "id": "S0"}, {"handle": "alice", "id": "S1"}])
    send(client, owner_report())
    assert client.chat_postMessage.mock_calls == [
        call(
            channel="#chan",
            text="Deleted the following`2` aws Disk(s):\n<!subteam^S1> disks `['v-1', 'v-2']`\n\n",
            link_names=True,
        )
    ]


def test_owner_user_gets_a_direct_message() -> None:
    client = ping_client(user="U9")
    send(client, owner_report(dry_run=True), dry_run=True)
    assert client.mock_calls == [
        call.usergroups_list(),
        call.users_lookupByEmail(email="alice@yugabyte.com"),
        call.conversations_open(users=["U9"]),
        call.chat_postMessage(
            channel="D1",
            text="`Dry Run`: Will be Deleted:`2` aws Disk(s):\n<@U9> disks `['v-1', 'v-2']`\n\n",
            link_names=True,
        ),
    ]


def test_owner_direct_message_failure_is_printed(capsys: pytest.CaptureFixture[str]) -> None:
    client = ping_client(user="U9")
    client.chat_postMessage.side_effect = RuntimeError("down")
    send(client, owner_report())
    assert "to Slack channel 'D1': down\n" in capsys.readouterr().out


def test_conversations_open_error_propagates() -> None:
    client = ping_client(user="U9")
    client.conversations_open.side_effect = RuntimeError("cannot open")
    with pytest.raises(RuntimeError, match="cannot open"):
        send(client, owner_report())


def test_user_groups_failure_breaks_unknown_owner(capsys: pytest.CaptureFixture[str]) -> None:
    """BUG-041: the "not_found" string is iterated as if it were a list of groups."""
    client = ping_client(group_error=True)
    with pytest.raises(TypeError, match="string indices must be integers"):
        send(client, owner_report())
    assert "Something Went Wront! Could not get user_groups.\n" in capsys.readouterr().out
    assert client.chat_postMessage.mock_calls == []


def test_user_groups_failure_is_harmless_for_known_user() -> None:
    client = ping_client(user="U9", group_error=True)
    send(client, owner_report())
    assert len(client.chat_postMessage.mock_calls) == 1


def test_ping_owners_uses_given_lookups() -> None:
    client = ping_client()
    lookup = Mock(return_value="not_found")
    groups = Mock(return_value=[{"handle": "alice", "id": "S2"}])
    SlackNotifier(client, "chan").ping_owners(owner_report(), "Disk", lookup, groups)
    lookup.assert_called_once_with("alice@yugabyte.com")
    assert client.users_lookupByEmail.mock_calls == []
    assert "<!subteam^S2>" in client.chat_postMessage.call_args.kwargs["text"]


def test_lookup_user_by_email() -> None:
    client = ping_client(user="U1")
    assert SlackNotifier(client, "c").lookup_user_by_email("a@b.c") == "U1"
    assert client.users_lookupByEmail.mock_calls == [call(email="a@b.c")]


@pytest.mark.parametrize("error", [RuntimeError("x"), KeyboardInterrupt()])
def test_lookup_user_by_email_swallows_everything(error: BaseException) -> None:
    client = Mock()
    client.users_lookupByEmail.side_effect = error
    assert SlackNotifier(client, "c").lookup_user_by_email("a@b.c") == "not_found"


def test_user_groups() -> None:
    client = ping_client(groups=[{"handle": "g", "id": "S"}])
    assert SlackNotifier(client, "c").user_groups() == [{"handle": "g", "id": "S"}]


def item(**fields: Any) -> dict[str, Any]:
    return {"name": "n", "id": "i", "region": "r", "state": "STOPPED", **fields}


def test_notify_cleanup_blocks_for_stopped_item() -> None:
    client = Mock()
    notify_cleanup(client, "#chan", "delete", "aws", "vm", [item(console_url="http://c")], False)
    header = "DELETE — 1 AWS VM"
    assert client.mock_calls == [
        call.chat_postMessage(
            channel="#chan",
            text=header,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": f"*{header}*"}},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": "aws • vm"}]},
                {"type": "divider"},
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Stopped / Other instances*\n"
                        "• *n* | `i` | - | r — STOPPED — <http://c|View>",
                    },
                },
            ],
        )
    ]


def test_notify_cleanup_running_first_and_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slack, "human_readable_timedelta", lambda start: "1h 00m 00s")
    client = Mock()
    items = [item(id="s", state=""), item(id="r", state="running", instance_type="m5")]
    notify_cleanup(client, "#chan", "stop", "gcp", "vm", items, True)
    kwargs = client.chat_postMessage.call_args.kwargs
    assert kwargs["text"] == "DRY RUN: STOP — 2 GCP VM"
    assert kwargs["blocks"][3]["text"]["text"] == (
        "*Running instances*\n"
        "• *n* | `r` | m5 | r — RUNNING (1h 00m 00s)\n"
        "\n"
        "*Stopped / Other instances*\n"
        "• *n* | `s` | - | r"
    )


def test_notify_cleanup_requires_client() -> None:
    with pytest.raises(ValueError, match="slack_client is required"):
        notify_cleanup(None, "#chan", "delete", "aws", "vm", [], False)


def test_notify_cleanup_without_items_sends_nothing() -> None:
    client = Mock()
    notify_cleanup(client, "#chan", "delete", "aws", "vm", [], False)
    assert client.mock_calls == []


def test_notify_cleanup_splits_into_parts() -> None:
    client = Mock()
    items = [item(id=str(n)) for n in range(21)]
    notify_cleanup(client, "#chan", "delete", "aws", "vm", items, False)
    calls = client.chat_postMessage.call_args_list
    assert len(calls) == TWO
    assert calls[0].kwargs["blocks"][0]["text"]["text"] == "*DELETE — 21 AWS VM* — Part 1/2"
    assert calls[1].kwargs["blocks"][0]["text"]["text"] == "*DELETE — 21 AWS VM* — Part 2/2"
    assert calls[0].kwargs["text"] == "DELETE — 21 AWS VM"
    first_lines = calls[0].kwargs["blocks"][3]["text"]["text"].split("\n")
    assert len(first_lines) == PER_MESSAGE
    assert calls[1].kwargs["blocks"][3]["text"]["text"].split("\n") == [
        "• *n* | `19` | - | r — STOPPED",
        "• *n* | `20` | - | r — STOPPED",
    ]


def test_chunking_by_characters_and_header_not_mutated() -> None:
    header: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": "h"}}]
    lines = ["a" * 10, "b" * 10, "c" * 10]
    parts = slack._chunk_lines_into_messages(header, lines, max_chars=25, max_items=20)
    assert [p[1]["text"]["text"] for p in parts] == ["a" * 10 + "\n" + "b" * 10, "c" * 10]
    assert parts[0][0]["text"]["text"] == "h — Part 1/2"
    assert header[0]["text"]["text"] == "h"


def test_notify_cleanup_failed_part_does_not_stop_the_next() -> None:
    client = Mock()
    client.chat_postMessage.side_effect = [RuntimeError("x"), None]
    notify_cleanup(client, "#c", "delete", "aws", "vm", [item(id=str(n)) for n in range(21)], False)
    assert len(client.chat_postMessage.mock_calls) == TWO


@pytest.mark.parametrize(("channel", "sent_to"), [("chan", "#chan"), ("#chan", "#chan")])
def test_send_cleanup_adds_hash_only_when_missing(channel: str, sent_to: str) -> None:
    client = Mock()
    SlackNotifier(client, channel).send_cleanup("delete", "aws", "vm", [item()], False)
    assert client.chat_postMessage.call_args.kwargs["channel"] == sent_to


def slack_error(message: str = "ratelimited") -> SlackApiError:
    make: Any = SlackApiError
    error: SlackApiError = make("failed", {"error": message})
    return error


def test_send_slack_message_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("crc.notify.slack.time.sleep", sleeps.append)
    client = Mock()
    client.chat_postMessage.side_effect = [slack_error(), slack_error(), "ok"]
    assert send_slack_message(client, "#c", "t", []) == "ok"
    assert sleeps == [2, 3]
    assert client.chat_postMessage.mock_calls == [call(channel="#c", text="t", blocks=[])] * 3


def test_send_slack_message_gives_up(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("crc.notify.slack.time.sleep", sleeps.append)
    client = Mock()
    client.chat_postMessage.side_effect = slack_error()
    with pytest.raises(SlackApiError):
        send_slack_message(client, "#c", "t", [], max_retries=2)
    assert sleeps == [2]
    assert len(client.chat_postMessage.mock_calls) == TWO


def test_send_slack_message_other_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("crc.notify.slack.time.sleep", Mock(side_effect=AssertionError("slept")))
    client = Mock()
    client.chat_postMessage.side_effect = RuntimeError("bad")
    with pytest.raises(RuntimeError, match="bad"):
        send_slack_message(client, "#c", "t", [])
    assert len(client.chat_postMessage.mock_calls) == 1


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> Self:
        return cls(2026, 1, 10, 12, 0, 0, tzinfo=tz)


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (None, "-"),
        ("", "-"),
        ("not a date", "-"),
        (datetime(2026, 1, 10, 12, 0, 0), "00s"),
        (datetime(2026, 1, 10, 12, 0, 1), "0s"),
        (datetime(2026, 1, 10, 11, 59, 55), "05s"),
        (datetime(2026, 1, 10, 11, 0, 0), "01h 00s"),
        (datetime(2026, 1, 10, 11, 0, 5), "59m 55s"),
        (datetime(2026, 1, 7, 7, 47, 55), "3d 04h 12m 05s"),
        (datetime(2026, 1, 9, 12, 0, 0), "1d 00s"),
        ("2026-01-09T12:00:00+00:00", "1d 00s"),
        ("2026-01-09T12:00:00", "1d 00s"),
        (datetime(2026, 1, 9, 12, 0, 5), "23h 59m 55s"),
        (NOW - timedelta(days=2, seconds=30), "2d 00h 30s"),
    ],
)
def test_human_readable_timedelta(
    monkeypatch: pytest.MonkeyPatch, start: datetime | str | None, expected: str
) -> None:
    monkeypatch.setattr(slack, "datetime", FrozenDatetime)
    assert human_readable_timedelta(start) == expected


def test_notifications_shim_reexports_the_moved_functions() -> None:
    assert crc.notifications.notify_cleanup is slack.notify_cleanup
    assert crc.notifications.send_slack_message is slack.send_slack_message
    assert crc.notifications.human_readable_timedelta is slack.human_readable_timedelta
    assert crc.notifications._chunk_lines_into_messages is slack._chunk_lines_into_messages
    assert crc.notifications.SLACK_SAFE_CHARS == SAFE_CHARS
    assert crc.notifications.ITEMS_PER_MESSAGE == PER_MESSAGE
