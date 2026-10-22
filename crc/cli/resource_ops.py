"""IP, keypair, NIC, disk, VPC and KMS operations of `CRC`.

The adapter classes are looked up in this module's globals at call time.
"""

from dataclasses import dataclass
from typing import Any

from crc.aws.disk import Disk as AWS_Disk
from crc.aws.elastic_ips import ElasticIPs
from crc.aws.keypairs import KeyPairs
from crc.aws.kms import Kms
from crc.aws.vpc import VPC
from crc.azu.disk import Disk
from crc.azu.ip import IP as AZU_IP
from crc.azu.nic import NIC
from crc.cli.constants import CLOUDS, DISKS, IPS, KEYPAIRS, KMS, NICS
from crc.cli.notify import NotifyMixin
from crc.gcp.disk import Disk as GCP_Disk
from crc.gcp.ip import IP as GCP_IP
from crc.oci.disk import Disk as OCI_Disk

Tags = dict[str, list[str]]


@dataclass(frozen=True)
class _DiskArgs:
    """Every argument `delete_disks` receives, so the per-cloud builders take one value."""

    filter_tags: Tags
    exception_tags: Tags
    age: Any
    custom_age_tag_key: Any
    detach_age: Any
    name_regex: list[str]
    exception_regex: list[str]
    slack_notify_users: bool
    slack_user_label: Any


class ResourceOps(NotifyMixin):
    """IP, keypair, NIC, disk, VPC and KMS operations."""

    def _get_ip(
        self,
        filter_tags: Tags,
        exception_tags: Tags,
        name_regex: list[str],
        exception_regex: list[str],
    ) -> Any:
        """
        Get the IP object for the specified cloud.

        :param filter_tags: Dictionary of tags to filter the IP.
        :param exception_tags: Dictionary of tags to exclude the IP.
        :param name_regex: List of regex patterns to filter the IP.
        :param exception_regex: List of regex patterns to exclude the IP.
        :return: IP object
        """
        if self.cloud == "aws":
            return ElasticIPs(self.dry_run, filter_tags, exception_tags, self.notags)
        if self.cloud == "azure":
            return AZU_IP(
                self.resource_group,
                self.dry_run,
                filter_tags,
                exception_tags,
                self.notags,
            )
        if self.cloud == "gcp":
            return GCP_IP(self.dry_run, self.project_id, name_regex, exception_regex)
        raise ValueError(f"Invalid cloud provided: {self.cloud}. Supported clouds are {CLOUDS}")

    def delete_ip(
        self,
        filter_tags: Tags,
        exception_tags: Tags,
        name_regex: list[str],
        exception_regex: list[str],
    ) -> None:
        """
        Delete IPs that match the specified criteria.

        :param filter_tags: Dictionary of tags to filter the IP.
        :param exception_tags: Dictionary of tags to exclude the IP.
        :param name_regex: List of regex patterns to filter the IP.
        :param exception_regex: List of regex patterns to exclude the IP.
        """
        ip = self._get_ip(
            filter_tags,
            exception_tags,
            name_regex,
            exception_regex,
        )
        ip.delete()

        if self.slack_client:
            self.notify_deleted_ip_via_slack(ip)

        if self.influxdb_client:
            self.write_influxdb(IPS, ip.get_deleted)

    def delete_keypairs(
        self,
        name_regex: list[str],
        exception_regex: list[str],
        age: Any,
    ) -> None:
        """
        Delete KeyPairs that match the specified criteria.
        This method is only supported on AWS.

        :param name_regex: List of regex patterns to filter the keypairs.
        :param exception_regex: List of regex patterns to exclude the keypairs.
        :param age: Dictionary of age conditions to filter the keypairs.
        """
        if self.cloud != "aws":
            raise ValueError("Keypair operation is only supported on AWS.")

        keypair = KeyPairs(self.dry_run, name_regex, exception_regex, age)
        keypair.delete()

        if self.slack_client:
            self.notify_deleted_keypair_via_slack(keypair)

        if self.influxdb_client:
            self.write_influxdb(KEYPAIRS, keypair.get_deleted)

    def delete_nic(
        self,
        name_regex: list[str],
        exception_regex: list[str],
    ) -> None:
        """
        Delete NICs that match the specified criteria.
        This method is only supported on Azure.

        :param name_regex: List of regex patterns to filter the nics.
        :param exception_regex: List of regex patterns to exclude the nics.
        """
        if self.cloud != "azure":
            raise ValueError("NICs operation is only supported on Azure.")

        nic = NIC(self.resource_group, self.dry_run, name_regex, exception_regex)
        nic.delete()

        if self.slack_client:
            self.notify_deleted_nic_via_slack(nic)

        if self.influxdb_client:
            self.write_influxdb(NICS, nic.get_deleted)

    def _make_disk(self, args: _DiskArgs) -> Any:
        """The disk adapter of `self.cloud`, built with the arguments that cloud takes."""
        if self.cloud == "aws":
            return AWS_Disk(
                self.dry_run,
                args.filter_tags,
                args.exception_tags,
                args.age,
                args.custom_age_tag_key,
                self.notags,
                args.name_regex,
                args.exception_regex,
                args.detach_age,
            )
        if self.cloud == "oci":
            return OCI_Disk(
                self.dry_run,
                args.filter_tags,
                args.exception_tags,
                args.age,
                args.custom_age_tag_key,
                self.notags,
            )
        if self.cloud == "azure":
            return Disk(
                self.resource_group,
                self.dry_run,
                args.filter_tags,
                args.exception_tags,
                args.age,
                args.custom_age_tag_key,
                self.notags,
            )
        return GCP_Disk(
            dry_run=self.dry_run,
            project_id=self.project_id,
            filter_labels=args.filter_tags,
            exception_labels=args.exception_tags,
            age=args.age,
            custom_age_tag_key=args.custom_age_tag_key,
            detach_age=args.detach_age,
            notags=self.notags,
            name_regex=args.name_regex,
            exception_regex=args.exception_regex,
            slack_notify_users=args.slack_notify_users,
            slack_user_label=args.slack_user_label,
        )

    def delete_disks(  # noqa: PLR0913, PLR0917 - the nine parameters are the public signature
        self,
        filter_tags: Tags,
        exception_tags: Tags,
        age: Any,
        custom_age_tag_key: Any,
        detach_age: Any,
        name_regex: list[str],
        exception_regex: list[str],
        slack_notify_users: bool,
        slack_user_label: Any,
    ) -> None:
        """
        Delete Disks that match the specified criteria.
        Supported on AWS (available/unattached EBS), Azure, GCP, and OCI.

        :param filter_tags: Dictionary of tags to filter the disks.
        :param exception_tags: Dictionary of tags to exclude the disks.
        :param age: Dictionary of age conditions to filter the disks.
        :param custom_age_tag_key: Tag name to overwrite the age condition.
        :param detach_age: Dictionary of detach age
        :param name_regex: List of regex patterns to filter the disks.
        :param exception_regex: List of regex patterns to exclude the disks.
        :param slack_notify_users: Bool to ping the users/usergroups in the slack ping.
        :param slack_user_label: String to lookup for the disks by matching disk label.
        """
        if self.cloud not in ["azure", "gcp", "aws", "oci"]:
            raise ValueError(
                "Incorrect Cloud Provided. "
                "Disks operation is supported on AWS, Azure, GCP, and OCI."
            )
        disk = self._make_disk(
            _DiskArgs(
                filter_tags,
                exception_tags,
                age,
                custom_age_tag_key,
                detach_age,
                name_regex,
                exception_regex,
                slack_notify_users,
                slack_user_label,
            )
        )

        # Report even if delete() raises unexpectedly; AWS Disk.delete() no
        # longer raises on partial sweep errors (logs instead).
        delete_error = None
        try:
            disk.delete()
        except Exception as e:
            delete_error = e

        if self.slack_client:
            self.notify_deleted_disk_via_slack(disk)

        if self.influxdb_client:
            self.write_influxdb(DISKS, disk.get_deleted)

        if delete_error is not None:
            raise delete_error

    def delete_vpc(self, filter_tags: Tags, exception_tags: Tags) -> None:
        """
        Delete VPCs that match the specified criteria.

        :param filter_tags: Dictionary of tags to filter the VM.
        :param exception_tags: Dictionary of tags to exclude the VM.
        """
        vpc = VPC(self.dry_run, filter_tags, exception_tags, self.notags)
        vpc.delete()

    def delete_kms(  # noqa: PLR0913, PLR0917 - the seven parameters are the public signature
        self,
        filter_tags: Tags,
        exception_tags: Tags,
        kms_key_description: str,
        kms_user: str,
        kms_pending_window: int,
        age: Any,
        custom_age_tag_key: Any,
    ) -> None:
        """
        Delete KMS that match the specified criteria.

        :param filter_tags: Dictionary of tags to filter the KMS.
        :param exception_tage: Dictionary of tags to exclude the KMS.
        :param kms_key_description: String to be present in KMS key description.
        :param kms_user: AWS ARN of Jenkins slave for which associated keys will be deleted.
        :param kms_pending_window: Number of days till which keys will be scheduled for deletion.
        :param age: Time to live for keys.
        :param custom_age_tag_key: Tag name to overwrite the age condition.
        """

        if self.cloud != "aws":
            raise ValueError("KMS operation is only supported on AWS.")

        kms = Kms(
            self.dry_run,
            filter_tags,
            exception_tags,
            kms_key_description,
            kms_user,
            kms_pending_window,
            age,
            custom_age_tag_key,
        )
        kms.delete()

        if self.slack_client:
            self.notify_deleted_kms_via_slack(kms)

        if self.influxdb_client:
            self.write_influxdb(KMS, kms.get_deleted)
