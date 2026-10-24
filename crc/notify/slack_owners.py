"""Slack posting, user lookups and owner pings shared by `SlackNotifier`."""

from collections.abc import Callable, Sequence
from typing import Any

from crc.core.report import Report, format_owner_ping

SlackClient = Any

NOT_FOUND = "not_found"


class SlackPoster:
    """A Slack client and channel with the posting and owner-ping calls."""

    def __init__(self, client: SlackClient, channel: str) -> None:
        self.client = client
        self.channel = channel

    def post(self, text: str, link_names: bool = False) -> None:
        """Post `text` to `#<channel>`; a failure is printed and swallowed.

        The channel is always prefixed with `#`, so a channel that already starts with `#`
        becomes `##name` (BUG-034).
        """
        try:
            if link_names:
                self.client.chat_postMessage(channel="#" + self.channel, text=text, link_names=True)
            else:
                self.client.chat_postMessage(channel="#" + self.channel, text=text)
        except Exception as e:  # noqa: BLE001 - a failing notification sink must not abort the run
            print(f"Failed to send message '{text}' to Slack channel '{self.channel}': {e}")

    def lookup_user_by_email(self, email: str) -> Any:
        """Slack user id of `email`, or "not_found" on any error."""
        try:
            user_info = self.client.users_lookupByEmail(email=email)
            return user_info["user"]["id"]
        except:  # noqa: E722 - the bare except is kept, it also catches BaseException
            return NOT_FOUND

    def user_groups(self) -> Any:
        """Slack user groups, or the string "not_found" on any error (callers iterate it)."""
        try:
            user_groups = self.client.usergroups_list()
            return user_groups["usergroups"]
        except:  # noqa: E722 - the bare except is kept, it also catches BaseException
            print("Something Went Wront! Could not get user_groups.")
            return NOT_FOUND

    def ping_owners(
        self,
        report: Report,
        resource: str,
        lookup_user: Callable[[str], Any] | None = None,
        get_groups: Callable[[], Any] | None = None,
    ) -> None:
        """Ping each owner of `report.by_owner`: users 1:1, groups and untagged in the channel.

        `lookup_user` and `get_groups` default to this notifier's own lookups; a caller may pass
        its own to override or patch them.
        """
        lookup = lookup_user or self.lookup_user_by_email
        groups = get_groups or self.user_groups
        owners = report.by_owner or {}
        user_groups = groups()
        for owner, ids in owners.items():
            print(f"Pinging '{owner}'")
            member_id = lookup(f"{owner}@yugabyte.com")
            if member_id == NOT_FOUND:
                self._ping_in_channel(report, resource, owner, ids, user_groups)
            else:
                self._ping_user(report, resource, owner, ids, member_id)

    def _ping_in_channel(
        self, report: Report, resource: str, owner: str, ids: Sequence[str], user_groups: Any
    ) -> None:
        member_id = NOT_FOUND
        for user_group in user_groups:
            if user_group["handle"] == owner:
                member_id = user_group["id"]
                break
        mention = None if member_id == NOT_FOUND else f"<!subteam^{member_id}>"
        text = format_owner_ping(report, resource, owner, ids, mention)
        self.post(text, link_names=True)

    def _ping_user(
        self, report: Report, resource: str, owner: str, ids: Sequence[str], member_id: str
    ) -> None:
        text = format_owner_ping(report, resource, owner, ids, f"<@{member_id}>")
        response = self.client.conversations_open(users=[member_id])
        channel_id = response["channel"]["id"]
        try:
            self.client.chat_postMessage(channel=channel_id, text=text, link_names=True)
        except Exception as e:  # noqa: BLE001 - a failing notification sink must not abort the run
            print(f"Failed to send message '{text}' to Slack channel '{channel_id}': {e}")
