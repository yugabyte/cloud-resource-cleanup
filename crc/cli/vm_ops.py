"""VM, spot instance and stop operations of `CRC`.

The adapter classes are looked up in this module's globals at call time.
"""

from typing import Any

from crc.aws.spot_instance_requests import SpotInstanceRequests
from crc.aws.vm import VM as AWS_VM
from crc.azu.spot_vm import SpotVM as AZU_SpotVM
from crc.azu.vm import VM as AZU_VM
from crc.cli.constants import CLOUDS, DELETED, DISKS, NICS, VMS
from crc.cli.notify import NotifyMixin
from crc.gcp.spot_vm import SpotVM as GCP_SpotVM
from crc.gcp.vm import VM as GCP_VM
from crc.oci.spot_vm import SpotVM as OCI_SpotVM
from crc.oci.vm import VM as OCI_VM

Tags = dict[str, list[str]]


class VmOps(NotifyMixin):
    """VM, spot VM, spot instance request and stop operations."""

    def _delete_vm(self, vm: Any, instance_state: list[str] | None) -> None:
        """
        Delete the specified vm.

        :param vm: The vm object to delete.
        :param instance_state: List of instance states that should be deleted.
        """
        if not instance_state:
            vm.delete()
        else:
            vm.delete(instance_state)

    def _get_vm(
        self,
        filter_tags: Tags,
        exception_tags: Tags,
        age: Any,
        custom_age_tag_key: Any,
    ) -> Any:
        """
        Get the VM object for the specified cloud.

        :param filter_tags: Dictionary of tags to filter the VM.
        :param exception_tags: Dictionary of tags to exclude the VM.
        :param age: Dictionary of age conditions to filter the VM.
        :param custom_age_tag_key: Tag name to overwrite the age condition.
        :return: VM object
        """
        if self.cloud == "aws":
            return AWS_VM(
                self.dry_run,
                filter_tags,
                exception_tags,
                age,
                custom_age_tag_key,
                self.notags,
            )
        if self.cloud == "azure":
            return AZU_VM(
                self.resource_group,
                self.dry_run,
                filter_tags,
                exception_tags,
                age,
                custom_age_tag_key,
                self.notags,
            )
        if self.cloud == "gcp":
            return GCP_VM(
                self.dry_run,
                self.project_id,
                filter_tags,
                exception_tags,
                age,
                custom_age_tag_key,
                self.notags,
            )
        if self.cloud == "oci":
            return OCI_VM(
                self.dry_run,
                filter_tags,
                exception_tags,
                age,
                custom_age_tag_key,
                self.notags,
            )
        raise ValueError(f"Invalid cloud provided: {self.cloud}. Supported clouds are {CLOUDS}")

    def scan_instances_with_tag(self, tag_key: str, tag_value: str) -> Any:
        vm = self._get_vm(filter_tags={}, exception_tags={}, age={}, custom_age_tag_key=None)
        if hasattr(vm, "find_instances_with_tag"):
            return vm.find_instances_with_tag(tag_key, tag_value)
        raise NotImplementedError(f"{type(vm)} does not implement find_instances_with_tag")

    def delete_vm(
        self,
        filter_tags: Tags,
        exception_tags: Tags,
        age: Any,
        custom_age_tag_key: Any,
        instance_state: list[str] | None,
    ) -> None:
        """
        Delete virtual machines that match the specified criteria.

        :param filter_tags: Dictionary of tags to filter the VM.
        :param exception_tags: Dictionary of tags to exclude the VM.
        :param age: Dictionary of age conditions to filter the VM.
        :param custom_age_tag_key: Tag name to overwrite the age condition.
        :param instance_state: List of instance states that should be deleted.
        """
        vm = self._get_vm(filter_tags, exception_tags, age, custom_age_tag_key)
        self._delete_vm(vm, instance_state)

        if self.slack_client:
            self.notify_deleted_vm_via_slack(vm)

        if self.influxdb_client:
            self.write_influxdb(VMS, vm.get_deleted)
            if self.cloud == "azure":
                self.write_influxdb(NICS, vm.get_deleted_nic)

    def _send_spot_disk_message(self, disks: list[str]) -> None:
        if not disks:
            return
        disk_msg = self.get_msg(DISKS, DELETED, disks)
        # A missing channel must keep raising TypeError inside the try.
        try:
            self.slack_client.chat_postMessage(channel="#" + self.slack_channel, text=disk_msg)
        except Exception as e:
            print(
                f"Failed to send message '{disk_msg}' to Slack channel '{self.slack_channel}': {e}"
            )

    def _report_spot_delete(self, spot_vm: Any, disks_attr: str, with_nics: bool) -> None:
        """Slack then Influx after a spot VM delete; `disks_attr` is the leftover list name."""
        if self.slack_client:
            self.notify_deleted_vm_via_slack(spot_vm)
            self._send_spot_disk_message(getattr(spot_vm, disks_attr) or [])
        if self.influxdb_client:
            self.write_influxdb(VMS, spot_vm.get_deleted)
            if with_nics:
                self.write_influxdb(NICS, spot_vm.get_deleted_nic)
            self.write_influxdb(DISKS, getattr(spot_vm, disks_attr))

    def _delete_azure_spot(
        self, filter_tags: Tags, exception_tags: Tags, age: Any, custom_age_tag_key: Any
    ) -> None:
        if not self.resource_group:
            raise ValueError("resource_group is required for Azure Spot VM cleanup")
        spot_vm = AZU_SpotVM(
            self.resource_group,
            self.dry_run,
            filter_tags,
            exception_tags,
            age,
            custom_age_tag_key,
            self.notags,
        )
        spot_vm.delete()
        self._report_spot_delete(spot_vm, "get_deleted_disks", True)

    def _delete_gcp_spot(
        self, filter_tags: Tags, exception_tags: Tags, age: Any, custom_age_tag_key: Any
    ) -> None:
        if not self.project_id:
            raise ValueError("project_id is required for GCP Spot VM cleanup")
        spot_vm = GCP_SpotVM(
            self.dry_run,
            self.project_id,
            filter_tags,
            exception_tags,
            age,
            custom_age_tag_key,
            self.notags,
        )
        spot_vm.delete()
        self._report_spot_delete(spot_vm, "get_deleted_disks", False)

    def _delete_oci_spot(
        self, filter_tags: Tags, exception_tags: Tags, age: Any, custom_age_tag_key: Any
    ) -> None:
        spot_vm = OCI_SpotVM(
            self.dry_run,
            filter_tags,
            exception_tags,
            age,
            custom_age_tag_key,
            self.notags,
        )
        spot_vm.delete()
        self._report_spot_delete(spot_vm, "get_deleted_volumes", False)

    def delete_spot_instances(
        self,
        filter_tags: Tags,
        exception_tags: Tags,
        age: Any,
        custom_age_tag_key: Any,
    ) -> None:
        """
        Delete Spot / preemptible VMs on Azure, GCP, or OCI.

        Azure: Spot VMs that are stopped/deallocated; leftover Detach disks removed.
        GCP: Spot/preemptible VMs; leftover autoDelete=false disks removed.
        OCI: Preemptible instances; leftover attached block volumes removed.

        AWS spot cleanup uses ``delete_spot_instance_requests`` instead (cancel
        request first, then terminate — required for Type=persistent).
        """
        if self.cloud == "azure":
            self._delete_azure_spot(filter_tags, exception_tags, age, custom_age_tag_key)
        elif self.cloud == "gcp":
            self._delete_gcp_spot(filter_tags, exception_tags, age, custom_age_tag_key)
        elif self.cloud == "oci":
            self._delete_oci_spot(filter_tags, exception_tags, age, custom_age_tag_key)
        else:
            raise ValueError(
                "spot_instances is supported on azure, gcp, and oci. "
                "For AWS use --resource spot_instance_requests "
                "(cancel request before terminate)."
            )

    def delete_azure_spot_instances(
        self,
        filter_tags: Tags,
        exception_tags: Tags,
        age: Any,
        custom_age_tag_key: Any,
    ) -> None:
        """Backward-compatible alias for delete_spot_instances on Azure."""
        if self.cloud != "azure":
            raise ValueError("delete_azure_spot_instances is Azure-only")
        self.delete_spot_instances(filter_tags, exception_tags, age, custom_age_tag_key)

    def delete_spot_instance_requests(
        self,
        filter_tags: Tags,
        exception_tags: Tags,
        age: Any,
        custom_age_tag_key: Any,
    ) -> None:
        """
        Delete AWS Spot Instance Requests and associated instances.

        Cancels the spot request first, then terminates the instance. Reversing
        that order on Type=persistent relaunches a replacement. Tags are matched
        on the spot request (instances may be untagged). Volumes with
        DeleteOnTermination=false are deleted after terminate.
        """
        if self.cloud != "aws":
            raise ValueError(
                "spot_instance_requests is AWS-only. "
                "Use --resource spot_instances for azure/gcp/oci."
            )
        spot_instance_requests = SpotInstanceRequests(
            self.dry_run,
            filter_tags,
            exception_tags,
            age,
            custom_age_tag_key,
            self.notags,
        )
        spot_instance_requests.delete()

        if self.slack_client:
            self.notify_deleted_spot_request_via_slack(spot_instance_requests)

        if self.influxdb_client:
            self.write_influxdb(VMS, spot_instance_requests.get_deleted)
            self.write_influxdb(DISKS, spot_instance_requests.volumes_to_delete)

    def stop_vm(
        self,
        filter_tags: Tags,
        exception_tags: Tags,
        age: Any,
        custom_age_tag_key: Any,
    ) -> None:
        """
        Stop virtual machines that match the specified criteria.

        :param filter_tags: Dictionary of tags to filter the VM.
        :param exception_tags: Dictionary of tags to exclude the VM.
        :param age: Dictionary of age conditions to filter the VM.
        :param custom_age_tag_key: Tag name to overwrite the age condition.
        """
        vm = self._get_vm(filter_tags, exception_tags, age, custom_age_tag_key)
        vm.stop()

        if self.slack_client:
            self.notify_stopped_vm_via_slack(vm)

        if self.influxdb_client:
            self.write_influxdb(VMS, vm.get_stopped)
