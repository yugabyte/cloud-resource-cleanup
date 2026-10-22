"""Slack and InfluxDB side of `CRC`: the `notify_*` methods, `get_msg` and `write_influxdb`."""

from collections.abc import Sized
from datetime import UTC, datetime
from typing import Any

from crc.cli.base import CRCBase
from crc.cli.constants import (
    DELETED,
    DISKS,
    IPS,
    KEYPAIRS,
    KMS,
    NICS,
    OPERATIONS,
    SPOT_INSTANCE_REQUEST,
    STOPPED,
    VMS,
)
from crc.core.cleaner import RunContext
from crc.core.report import Report, Section
from crc.notify.influx import InfluxNotifier
from crc.notify.slack import SlackNotifier


def _section(label: str, ids: Any, **flags: Any) -> Section:
    # len() first: a None id list raised "object of type 'NoneType' has no len()" in get_msg
    len(ids)
    return Section(label, ids, **flags)


class NotifyMixin(CRCBase):
    """Notification methods of `CRC`; every operation calls them after the cleaner ran."""

    def slack_lookup_user_by_email(self, email: str) -> Any:
        """
        Get the Slack User Id by email

        :param email: String to search the user by email in Slack
        :return: User Id
        :rtype: str
        """
        return self._slack_notifier().lookup_user_by_email(email)

    def get_user_groups_list(self) -> Any:
        """
        Get the Slack User Groups Lists
        :return: User Groups List
        :rtype: list
        """
        return self._slack_notifier().user_groups()

    def _slack_notifier(self) -> SlackNotifier:
        return SlackNotifier(self.slack_client, self.slack_channel)

    def _slack_send(self, operation_type: str, sections: list[Section]) -> None:
        operation = OPERATIONS[operation_type]
        report = Report(self.cloud, operation, self.dry_run, tuple(sections))
        context = RunContext(self.dry_run, operation, {}, datetime.now(UTC))
        self._slack_notifier().send(report, context)

    def ping_on_slack(self, resource: str, operation_type: str, operated_list: Any) -> None:
        """
        Pings individuals 1:1 and groups/untagged into the channel on the Slack

        :param resource: Cloud resource type (e.g. "nics", "vms", "ips", "keypairs", "disks")
        :type resource: str
        :param operation_type: Operation type (e.g. "Deleted", "Stopped")
        :type operation_type: str
        :param operated_list: Dict of operated resources.
        :type operated_list: dict
        :return: Message to be sent to the Slack channel
        :rtype: str
        """
        # Groups first, then len() of every value: keeps the old call order and error text
        # for a non-dict operated_list or a value without len() (BUG-033).
        user_groups = self.get_user_groups_list()
        for key in operated_list.keys():  # noqa: SIM118 - AttributeError text of a non-dict
            len(operated_list[key])
        report = Report(
            self.cloud,
            OPERATIONS[operation_type],
            self.dry_run,
            (Section(resource, ()),),
            by_owner=operated_list,
        )
        self._slack_notifier().ping_owners(
            report, resource, self.slack_lookup_user_by_email, lambda: user_groups
        )

    def get_msg(self, resource: str, operation_type: str, operated_list: Any) -> str:
        """
        Returns a message to be sent to the Slack channel

        :param resource: Cloud resource type (e.g. "nics", "vms", "ips", "keypairs", "disks")
        :type resource: str
        :param operation_type: Operation type (e.g. "Deleted", "Stopped")
        :type operation_type: str
        :param operated_list: List of operated resources.
        :type operated_list: list
        :return: Message to be sent to the Slack channel
        :rtype: str
        """
        operated_list_length = len(operated_list)

        if self.dry_run:
            return (
                f"`Dry Run`: Will be {operation_type}: `{operated_list_length}` "
                f"{self.cloud} {resource}(s):\n`{operated_list}`"
            )

        return (
            f"{operation_type} the following `{operated_list_length}` "
            f"{self.cloud} {resource}(s):\n`{operated_list}`"
        )

    def notify_deleted_vm_via_slack(self, vm: Any) -> None:
        """
        Sends a notification message to the Slack channel about deleted virtual machines

        :param vm: Virtual machine object
        :type vm: object
        """
        self._slack_send(DELETED, [_section(VMS, vm.get_deleted)])

        if self.cloud == "azure":
            self.notify_deleted_nic_via_slack(vm)

    def notify_stopped_vm_via_slack(self, vm: Any) -> None:
        """
        Sends a notification message to the Slack channel about stopped virtual machines

        :param vm: Virtual machine object
        :type vm: object
        """
        self._slack_send(STOPPED, [_section(VMS, vm.get_stopped)])

    def notify_deleted_spot_request_via_slack(self, spot_instance_request: Any) -> None:
        """
        Sends a notification message to the Slack channel about deleted spot_instance_request

        :param spot_instance_request: spot_instance_request object
        :type spot_instance_request: object
        """
        sections = [_section(SPOT_INSTANCE_REQUEST, spot_instance_request.get_deleted)]
        volumes = getattr(spot_instance_request, "volumes_to_delete", None) or []
        sections.append(_section(DISKS, volumes, notify_empty=False))
        self._slack_send(DELETED, sections)

    def notify_deleted_ip_via_slack(self, ip: Any) -> None:
        """
        Sends a message to a Slack channel about deleted IP instances.

        Parameters:
        ip (object): the deleted IP instance
        """
        self._slack_send(DELETED, [_section(IPS, ip.get_deleted)])

    def notify_deleted_keypair_via_slack(self, keypair: Any) -> None:
        """
        Sends a notification message to the Slack channel about deleted Key Pairs

        :param keypair: Key Pair object
        :type vm: object
        """
        self._slack_send(DELETED, [_section(KEYPAIRS, keypair.get_deleted)])

    def notify_deleted_nic_via_slack(self, nic: Any) -> None:
        """
        Sends a notification message to the Slack channel about deleted NICs

        :param nic: NIC object
        :type vm: object
        """
        self._slack_send(DELETED, [_section(NICS, nic.get_deleted)])

    def notify_deleted_disk_via_slack(self, disk: Any) -> None:
        """
        Sends a notification message to the Slack channel about deleted Disks

        :param disk: Disk object
        :type vm: object
        """
        if type(disk.get_deleted) is list:
            # Send a one single message into the channel
            self._slack_send(DELETED, [_section(DISKS, disk.get_deleted, link_names=True)])
        elif type(disk.get_deleted) is dict:
            # Directly ping the individuals 1:1 and groups/untagged into channel
            self.ping_on_slack(DISKS, DELETED, disk.get_deleted)

    def notify_deleted_kms_via_slack(self, kms: Any) -> None:
        """
        Sends a notification message to the Slack channel about deleted KMS

        :param keypair: KMS
        :type vm: object
        """
        self._slack_send(DELETED, [_section(KMS, kms.get_deleted)])

    def write_influxdb(self, resource_name: str, resources: Sized) -> None:
        """
        Writes data to InfluxDB.

        Args:
            resource_name (str): The name of the resource being written to InfluxDB.
            resources (List[str]): A list of resources to be written to InfluxDB.
        """
        InfluxNotifier(self.influxdb_client, self).write(self.cloud, resource_name, resources)
