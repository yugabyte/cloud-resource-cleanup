"""Report model built from cleaner results. Pure: no Slack or Influx clients, no I/O.

Strings and payloads are what `crc.notify` sends (`get_msg`, the Slack messages, the Influx
points and the owner pings). Sending stays with the notifiers.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from crc.core.cleaner import Failure, Operation, Plan, Result

NIC = "NIC"
DISK = "Disk"
VM = "VM"
IP = "IP"
KEYPAIR = "Keypair"
KMS = "KMS"
SNAPSHOT = "Snapshot"
SPOT_INSTANCE_REQUEST = "SPOT_INSTANCE_REQUEST_AND_ASSOCIATED_INSTANCES"

_PAST_TENSE = {Operation.DELETE: "Deleted", Operation.STOP: "Stopped"}
_INFLUX_LABEL = {SPOT_INSTANCE_REQUEST: VM}


@dataclass(frozen=True, slots=True)
class Section:
    """Resources of one label (`VM`, `NIC`, `Disk`, ...).

    `notify_empty` False skips the Slack message when `ids` is empty (the extra disk message
    of spot VMs); the Influx point is written either way. `link_names` is the Slack flag
    set only on the disk list message. `slack_ids` overrides `ids` in the Slack text
    when a different list goes to Slack than to Influx (Azure VM `NIC` notification
    sends `vm.get_deleted` while Influx writes `vm.get_deleted_nic`).
    """

    label: str
    ids: tuple[str, ...]
    notify_empty: bool = True
    link_names: bool = False
    slack_ids: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ids", tuple(self.ids))
        if self.slack_ids is not None:
            object.__setattr__(self, "slack_ids", tuple(self.slack_ids))


def _build_sections(
    cloud: str,
    label: str,
    primary_ids: tuple[str, ...],
    related: Mapping[str, tuple[str, ...]],
) -> tuple[Section, ...]:
    sections = [Section(label, primary_ids, link_names=label == DISK)]
    for related_label, ids in related.items():
        slack_ids = (
            primary_ids if cloud == "azure" and label == VM and related_label == NIC else None
        )
        sections.append(
            Section(
                related_label,
                ids,
                notify_empty=related_label != DISK,
                slack_ids=slack_ids,
            )
        )
    return tuple(sections)


@dataclass(frozen=True, slots=True)
class SlackMessage:
    """One `chat_postMessage` text; the channel is chosen by the sender."""

    text: str
    link_names: bool = False


@dataclass(frozen=True, slots=True)
class InfluxPoint:
    """One Influx point: measurement is the cloud, tag `resource`, fields `names`, `count`."""

    measurement: str
    resource: str
    names: str
    count: int


@dataclass(frozen=True, slots=True)
class Report:
    """What a run did (or, for a dry run, would do), grouped by resource label."""

    cloud: str
    operation: Operation
    dry_run: bool
    sections: tuple[Section, ...]
    failures: tuple[Failure, ...] = ()
    by_owner: Mapping[str, tuple[str, ...]] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sections", tuple(self.sections))
        object.__setattr__(self, "failures", tuple(self.failures))
        if self.by_owner is not None:
            owners = {owner: tuple(ids) for owner, ids in self.by_owner.items()}
            object.__setattr__(self, "by_owner", MappingProxyType(owners))

    @classmethod
    def from_result(
        cls, cloud: str, label: str, result: Result, operation: Operation, dry_run: bool
    ) -> "Report":
        """Primary section from `result.succeeded`, plus `result.related` and `result.by_owner`."""
        sections = _build_sections(cloud, label, result.succeeded, result.related)
        return cls(cloud, operation, dry_run, sections, result.failed, result.by_owner)

    @classmethod
    def from_plan(
        cls, cloud: str, label: str, plan: Plan, operation: Operation, dry_run: bool
    ) -> "Report":
        """Dry-run report: the plan's candidate ids, related ids and owner groups."""
        ids = tuple(candidate.id for candidate in plan.candidates)
        sections = _build_sections(cloud, label, ids, plan.related)
        return cls(cloud, operation, dry_run, sections, (), plan.by_owner)

    def slack_messages(self) -> tuple[SlackMessage, ...]:
        """Messages in section order, as `notify_*` sends them (`()` when owner-grouped)."""
        if self.by_owner is not None:
            return ()
        messages: list[SlackMessage] = []
        for section in self.sections:
            shown = section.ids if section.slack_ids is None else section.slack_ids
            if shown or section.notify_empty:
                text = get_msg(self.cloud, section.label, self.operation, list(shown), self.dry_run)
                messages.append(SlackMessage(text, section.link_names))
        return tuple(messages)

    def influx_points(self, resource_suffix: str | None = None) -> tuple[InfluxPoint, ...]:
        """One point per section, written even when empty (as `write_influxdb` is called)."""
        if self.by_owner is not None:
            raw_label = self.sections[0].label if self.sections else DISK
            label = _INFLUX_LABEL.get(raw_label, raw_label)
            as_dict = {owner: list(ids) for owner, ids in self.by_owner.items()}
            return (
                InfluxPoint(
                    self.cloud,
                    influx_resource_name(label, resource_suffix),
                    str(as_dict),
                    len(as_dict),
                ),
            )
        return tuple(
            InfluxPoint(
                self.cloud,
                influx_resource_name(
                    _INFLUX_LABEL.get(section.label, section.label), resource_suffix
                ),
                str(list(section.ids)),
                len(section.ids),
            )
            for section in self.sections
        )

    def influx_payload(self, resource_suffix: str | None = None) -> dict[str, list[str]]:
        """Resource name (with suffix) to ids, the arguments of `write_influxdb`."""
        payload: dict[str, list[str]] = {}
        for section in self.sections:
            label = _INFLUX_LABEL.get(section.label, section.label)
            name = influx_resource_name(label, resource_suffix)
            payload.setdefault(name, []).extend(section.ids)
        return payload


def get_msg(
    cloud: str, resource: str, operation: Operation, ids: Sequence[str], dry_run: bool
) -> str:
    """Slack text of one operation message, as `CRC.get_msg` prints it.

    `ids` is rendered as a list, so pass a list-like.
    """
    shown = list(ids)
    word = _PAST_TENSE[operation]
    if dry_run:
        return f"`Dry Run`: Will be {word}: `{len(shown)}` {cloud} {resource}(s):\n`{shown}`"
    return f"{word} the following `{len(shown)}` {cloud} {resource}(s):\n`{shown}`"


def influx_resource_name(label: str, resource_suffix: str | None) -> str:
    """Resource tag of `write_influxdb`: the label, plus `_suffix` when the suffix is truthy."""
    if resource_suffix:
        return label + "_" + resource_suffix
    return label


def format_owner_ping(
    report: Report,
    resource: str,
    owner: str,
    ids: Sequence[str],
    mention: str | None = None,
) -> str:
    """Text of one owner-ping message for `owner`'s `ids`.

    Cloud, operation and dry run come from `report`. `mention` is the ready-made Slack mention
    (`<@U123>` for a user, `<!subteam^S1>` for a group); None means untagged and the owner key
    is shown as `*key*`. The word "disks" is fixed whatever the resource is, and
    is kept.
    """
    word = _PAST_TENSE[report.operation]
    prefix = f"`Dry Run`: Will be {word}:" if report.dry_run else f"{word} the following"
    who = f"*{owner}*" if mention is None else mention
    return f"{prefix}`{len(ids)}` {report.cloud} {resource}(s):\n{who} disks `{list(ids)}`\n\n"
