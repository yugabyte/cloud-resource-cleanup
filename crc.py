# Copyright (c) Yugabyte, Inc.

import argparse
import ast
import os
from typing import Dict, List, Union

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from slack_sdk import WebClient

# Import classes for interacting with different resources across different clouds
from crc.aws.elastic_ips import ElasticIPs
from crc.aws.keypairs import KeyPairs
from crc.aws.kms import Kms
from crc.aws.vm import VM as AWS_VM
from crc.aws.vpc import VPC
from crc.azu.disk import Disk
from crc.azu.nic import NIC
from crc.azu.ip import IP as AZU_IP
from crc.azu.vm import VM as AZU_VM
from crc.gcp.disk import Disk as GCP_Disk
from crc.gcp.ip import IP as GCP_IP
from crc.gcp.vm import VM as GCP_VM

# List of supported clouds and resources
CLOUDS = ["aws", "azure", "gcp"]
RESOURCES = ["disk", "ip", "keypair", "vm", "kms"]

DELETED = "Deleted"
STOPPED = "Stopped"

NICS = "NIC"
DISKS = "Disk"
VMS = "VM"
IPS = "IP"
KEYPAIRS = "Keypair"
KMS = "KMS"


class CRC:
    """
    Class for cleaning up resources across different clouds.
    This also supports sending notification to Slack Channels
    """

    def __init__(
        self,
        cloud: str,
        dry_run: bool,
        notags: dict,
        slack_client: object,
        influxdb_client: object,
        project_id: str = None,
        slack_channel: str = None,
        influxdb_conn: dict = None,
    ) -> None:
        """
        Initializes the object with required properties.

        Parameters:
        cloud (str): the name of the cloud platform ('aws', 'gcp' or 'azure')
        dry_run (bool): flag to indicate whether the operation is a dry run or not
        notags (dict): a dictionary containing a list of resources that don't have any tags
        slack_client (object): the Slack client instance used to send messages
        influxdb_client (object): the InfluxDB client instance used to send data
        project_id (str, optional): the ID of the project (mandatory for GCP)
        slack_channel (str, optional): the name of the Slack channel to send messages to
        influxdb_conn (dict, optional): the Influx DB Connection string
        """
        self.cloud = cloud
        if cloud == "gcp" and not project_id:
            raise ValueError("project_id is mandatory Parameter for GCP")
        self.dry_run = dry_run
        self.project_id = project_id
        self.notags = notags
        self.slack_client = slack_client
        self.influxdb_client = influxdb_client
        self.slack_channel = slack_channel
        if influxdb_conn:
            self.influxdb_bucket = influxdb_conn.get("bucket")
            self.resource_suffix = influxdb_conn.get("resource_suffix")

    def _delete_vm(self, vm, instance_state: List[str]):
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
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
    ):
        """
        Get the VM object for the specified cloud.

        :param filter_tags: Dictionary of tags to filter the VM.
        :param exception_tags: Dictionary of tags to exclude the VM.
        :param age: Dictionary of age conditions to filter the VM.
        :return: VM object
        """
        if self.cloud == "aws":
            return AWS_VM(self.dry_run, filter_tags, exception_tags, age, self.notags)
        if self.cloud == "azure":
            return AZU_VM(self.dry_run, filter_tags, exception_tags, age, self.notags)
        if self.cloud == "gcp":
            return GCP_VM(
                self.dry_run,
                self.project_id,
                filter_tags,
                exception_tags,
                age,
                self.notags,
            )
        raise ValueError(
            f"Invalid cloud provided: {self.cloud}. Supported clouds are {CLOUDS}"
        )

    def _get_ip(
        self,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        name_regex: List[str],
        exception_regex: List[str],
    ):
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
            return AZU_IP(self.dry_run, filter_tags, exception_tags, self.notags)
        if self.cloud == "gcp":
            return GCP_IP(self.dry_run, self.project_id, name_regex, exception_regex)
        raise ValueError(
            f"Invalid cloud provided: {self.cloud}. Supported clouds are {CLOUDS}"
        )

    def slack_lookup_user_by_email(self, email):
        """
        Get the Slack User Id by email

        :param email: String to search the user by email in Slack
        :return: User Id
        :rtype: str
        """
        try:
            user_info = self.slack_client.users_lookupByEmail(email=email)
            return user_info["user"]["id"]
        except:
            return "not_found"

    def get_user_groups_list(self):
        """
        Get the Slack User Groups Lists
        :return: User Groups List
        :rtype: list
        """
        try:
            user_groups = self.slack_client.usergroups_list()
            return user_groups["usergroups"]
        except:
            print("Something Went Wront! Could not get user_groups.")
            return "not_found"

    def ping_on_slack(self, resource: str, operation_type: str, operated_list: dict):
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
        operated_list_length = 0
        msg = ""
        user_groups = self.get_user_groups_list()
        for key in operated_list.keys():
            operated_list_length += len(operated_list[key])
        if self.dry_run:
            msg = f"`Dry Run`: Will be {operation_type}:"
        else:
            msg = f"{operation_type} the following"

        for key in operated_list.keys():
            print(f"Pinging '{key}'")
            member_id = self.slack_lookup_user_by_email(f"{key}@yugabyte.com")
            operated_list_length = len(operated_list[key])
            if member_id == "not_found":
                for user_group in user_groups:
                    if user_group["handle"] == key:
                        member_id = user_group["id"]
                        break

                if member_id == "not_found":
                    # Untagged
                    final_msg = (
                        msg
                        + f"`{operated_list_length}` {self.cloud} {resource}(s):\n*{key}* disks `{operated_list[key]}`\n\n"
                    )
                else:
                    # User Groups
                    final_msg = (
                        msg
                        + f"`{operated_list_length}` {self.cloud} {resource}(s):\n<!subteam^{member_id}> disks `{operated_list[key]}`\n\n"
                    )

                self.slack_client.chat_postMessage(
                    channel="#" + self.slack_channel, text=final_msg, link_names=True
                )
            else:
                # Individual User

                final_msg = (
                    msg
                    + f"`{operated_list_length}` {self.cloud} {resource}(s):\n<@{member_id}> disks `{operated_list[key]}`\n\n"
                )

                # Open Conversation between the bot and the user
                users_in_conversation = [member_id]
                response = self.slack_client.conversations_open(
                    users=users_in_conversation
                )
                channel_id = response["channel"]["id"]

                # Post Message
                self.slack_client.chat_postMessage(
                    channel=channel_id, text=final_msg, link_names=True
                )

    def get_msg(self, resource: str, operation_type: str, operated_list: list) -> str:
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
            return f"`Dry Run`: Will be {operation_type}: `{operated_list_length}` {self.cloud} {resource}(s):\n`{operated_list}`"

        return f"{operation_type} the following `{operated_list_length}` {self.cloud} {resource}(s):\n`{operated_list}`"

    def notify_deleted_nic_via_slack(self, nic: object):
        """
        Sends a notification message to the Slack channel about deleted network interfaces

        :param nic: Network interface object
        :type nic: object
        """
        msg = self.get_msg(NICS, DELETED, nic.get_deleted_nic)
        self.slack_client.chat_postMessage(channel="#" + self.slack_channel, text=msg)

    def notify_deleted_vm_via_slack(self, vm: object):
        """
        Sends a notification message to the Slack channel about deleted virtual machines

        :param vm: Virtual machine object
        :type vm: object
        """
        msg = self.get_msg(VMS, DELETED, vm.get_deleted)
        self.slack_client.chat_postMessage(channel="#" + self.slack_channel, text=msg)

        if self.cloud == "azure":
            self.notify_deleted_nic_via_slack(vm)

    def notify_stopped_vm_via_slack(self, vm: object):
        """
        Sends a notification message to the Slack channel about stopped virtual machines

        :param vm: Virtual machine object
        :type vm: object
        """
        msg = self.get_msg(VMS, STOPPED, vm.get_stopped)
        self.slack_client.chat_postMessage(channel="#" + self.slack_channel, text=msg)

    def notify_deleted_ip_via_slack(self, ip: object):
        """
        Sends a message to a Slack channel about deleted IP instances.

        Parameters:
        ip (object): the deleted IP instance
        """
        msg = self.get_msg(IPS, DELETED, ip.get_deleted)
        self.slack_client.chat_postMessage(channel="#" + self.slack_channel, text=msg)

    def notify_deleted_keypair_via_slack(self, keypair: object):
        """
        Sends a notification message to the Slack channel about deleted Key Pairs

        :param keypair: Key Pair object
        :type vm: object
        """
        msg = self.get_msg(KEYPAIRS, DELETED, keypair.get_deleted)
        self.slack_client.chat_postMessage(channel="#" + self.slack_channel, text=msg)

    def notify_deleted_disk_via_slack(self, disk: object):
        """
        Sends a notification message to the Slack channel about deleted Disks

        :param disk: Disk object
        :type vm: object
        """
        if type(disk.get_deleted) == list:
            # Send a one single message into the channel
            msg = self.get_msg(DISKS, DELETED, disk.get_deleted)
            self.slack_client.chat_postMessage(
                channel="#" + self.slack_channel, text=msg, link_names=True
            )
        elif type(disk.get_deleted) == dict:
            # Directly ping the individuals 1:1 and groups/untagged into channel
            self.ping_on_slack(DISKS, DELETED, disk.get_deleted)

    def notify_deleted_kms_via_slack(self, kms: object):
        """
        Sends a notification message to the Slack channel about deleted KMS

        :param keypair: KMS
        :type vm: object
        """
        msg = self.get_msg(KMS, DELETED, kms.get_deleted)
        self.slack_client.chat_postMessage(channel="#" + self.slack_channel, text=msg)

    def write_influxdb(self, resource_name: str, resources: List[str]) -> None:
        """
        Writes data to InfluxDB.

        Args:
            resource_name (str): The name of the resource being written to InfluxDB.
            resources (List[str]): A list of resources to be written to InfluxDB.
        """
        try:
            # Get the write API object from the InfluxDB client, with synchronous write options
            write_api = self.influxdb_client.write_api(write_options=SYNCHRONOUS)

            if self.resource_suffix:
                resource_name = resource_name + "_" + self.resource_suffix

            # Create a Point object with the resource name, tags for the names of the resources,
            # and a field for the count of resources
            point = (
                Point(self.cloud)
                .tag("resource", resource_name)
                .field("names", str(resources))
                .field("count", len(resources))
            )

            # Write the Point object to the InfluxDB bucket
            write_api.write(bucket=self.influxdb_bucket, record=point)
        except Exception as e:
            print(f"Unable to push data to influxDB: {e}")

    def delete_vm(
        self,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
        instance_state: List[str],
    ):
        """
        Delete virtual machines that match the specified criteria.

        :param filter_tags: Dictionary of tags to filter the VM.
        :param exception_tags: Dictionary of tags to exclude the VM.
        :param age: Dictionary of age conditions to filter the VM.
        :param instance_state: List of instance states that should be deleted.
        """
        vm = self._get_vm(filter_tags, exception_tags, age)
        self._delete_vm(vm, instance_state)

        if self.slack_client:
            self.notify_deleted_vm_via_slack(vm)

        if self.influxdb_client:
            self.write_influxdb(VMS, vm.get_deleted)
            if self.cloud == "azure":
                self.write_influxdb(NICS, vm.get_deleted_nic)

    def stop_vm(
        self,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
    ):
        """
        Stop virtual machines that match the specified criteria.

        :param filter_tags: Dictionary of tags to filter the VM.
        :param exception_tags: Dictionary of tags to exclude the VM.
        :param age: Dictionary of age conditions to filter the VM.
        """
        vm = self._get_vm(filter_tags, exception_tags, age)
        vm.stop()

        if self.slack_client:
            self.notify_stopped_vm_via_slack(vm)

        if self.influxdb_client:
            self.write_influxdb(VMS, vm.get_stopped)

    def delete_ip(
        self,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        name_regex: List[str],
        exception_regex: List[str],
    ):
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
        name_regex: List[str],
        exception_regex: List[str],
        age: Dict[str, int],
    ):
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
        name_regex: List[str],
        exception_regex: List[str],
    ):
        """
        Delete NICs that match the specified criteria.
        This method is only supported on Azure.

        :param name_regex: List of regex patterns to filter the nics.
        :param exception_regex: List of regex patterns to exclude the nics.
        """
        if self.cloud != "azure":
            raise ValueError("NICs operation is only supported on Azure.")

        nic = NIC(self.dry_run, name_regex, exception_regex)
        nic.delete()

        if self.slack_client:
            self.notify_deleted_keypair_via_slack(nic)

        if self.influxdb_client:
            self.write_influxdb(NICS, nic.get_deleted)

    def delete_disks(
        self,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
        detach_age: Dict[str, int],
        name_regex: List[str],
        exception_regex: List[str],
        slack_notify_users: bool,
        slack_user_label: str,
    ):
        """
        Delete Disks that match the specified criteria.
        This method is only supported on AZURE.

        :param filter_tags: Dictionary of tags to filter the disks.
        :param exception_tags: Dictionary of tags to exclude the disks.
        :param age: Dictionary of age conditions to filter the disks.
        :param detach_age: Dictionary of detach age
        :param name_regex: List of regex patterns to filter the disks.
        :param exception_regex: List of regex patterns to exclude the disks.
        :param slack_notify_users: Bool to ping the users/usergroups in the slack ping.
        :param slack_user_label: String to lookup for the disks by matching disk label.
        """
        if self.cloud not in ["azure", "gcp"]:
            raise ValueError(
                "Incorrect Cloud Provided. Disks operation is supported only on AZURE and GCP. AWS cleans the NICs, Disks along with VM"
            )
        if self.cloud == "azure":
            disk = Disk(self.dry_run, filter_tags, exception_tags, age, self.notags)
        if self.cloud == "gcp":
            disk = GCP_Disk(
                dry_run=self.dry_run,
                project_id=self.project_id,
                filter_labels=filter_tags,
                exception_labels=exception_tags,
                age=age,
                detach_age=detach_age,
                notags=self.notags,
                name_regex=name_regex,
                exception_regex=exception_regex,
                slack_notify_users=slack_notify_users,
                slack_user_label=slack_user_label,
            )

        disk.delete()

        if self.slack_client:
            self.notify_deleted_disk_via_slack(disk)

        if self.influxdb_client:
            self.write_influxdb(DISKS, disk.get_deleted)

    def delete_vpc(
        self, filter_tags: Dict[str, List[str]], exception_tags: Dict[str, List[str]]
    ):
        """
        Delete VPCs that match the specified criteria.

        :param filter_tags: Dictionary of tags to filter the VM.
        :param exception_tags: Dictionary of tags to exclude the VM.
        """
        vpc = VPC(self.dry_run, filter_tags, exception_tags, self.notags)
        vpc.delete()

    def delete_kms(
        self,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        kms_key_description: str,
        kms_user: str,
        kms_pending_window: int,
        age: Dict[str, int],
    ):
        """
        Delete KMS that match the specified criteria.

        :param filter_tags: Dictionary of tags to filter the KMS.
        :param exception_tage: Dictionary of tags to exclude the KMS.
        :param kms_key_description: String to be present in KMS key description.
        :param kms_user: AWS ARN of Jenkins slave for which associated keys will be deleted.
        :param kms_pending_window: Number of days till which keys will be scheduled for deletion.
        :param age: Time to live for keys.
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
        )
        kms.delete()

        if self.slack_client:
            self.notify_deleted_kms_via_slack(kms)

        if self.influxdb_client:
            self.write_influxdb(KMS, kms.get_deleted)


def get_argparser():
    """
    Method to parse and return command line arguments.

    :return: A dictionary containing all the command line arguments.
    """
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Cleanup Resources across different clouds",
    )

    # Add Argument for Cloud
    parser.add_argument(
        "-c",
        "--cloud",
        choices=["aws", "azure", "gcp", "all"],
        required=True,
        metavar="CLOUD",
        help="The cloud to operate on. Valid options are: 'aws', 'azure', 'gcp', 'all'. Example: -c or --cloud all",
    )

    # Add Argument for Resource Type
    parser.add_argument(
        "-r",
        "--resource",
        default="all",
        choices=["disk", "ip", "keypair", "vm", "vpc", "kms", "all"],
        metavar="RESOURCE",
        help="Type of resource to operate on. Valid options are: 'disk', 'ip', 'keypair', 'vm', 'all'. Default: 'all'. Example: -r or --resource vm",
    )

    # Add Argument for Project ID (GCP only)
    parser.add_argument(
        "-p",
        "--project_id",
        metavar="PROJECT_ID",
        help="Project ID for GCP. Required only for GCP. Example: --project_id testing",
    )

    # Add Argument for Operation Type
    parser.add_argument(
        "-o",
        "--operation_type",
        default="delete",
        choices=["delete", "stop"],
        metavar="OPERATION",
        help="Type of operation to perform on resource. Valid options are: 'delete', 'stop'. Default: 'delete'. Example: -o or --operation_type stop",
    )

    # Add argument for resource states
    parser.add_argument(
        "-s",
        "--resource_states",
        type=ast.literal_eval,
        metavar="['state1', 'state2']",
        help="State of the resource to filter. Example: --resource_states ['RUNNING', 'STOPPED']",
    )

    # Add argument for filter tags
    parser.add_argument(
        "-f",
        "--filter_tags",
        type=ast.literal_eval,
        metavar="{key1: [value1, value2], key2: [value3, value4]}",
        help="Tags to use for filtering resources. Example: --filter_tags {'test_task': ['test', 'stress-test']}",
    )

    # Add argument for exception tags
    parser.add_argument(
        "-e",
        "--exception_tags",
        type=ast.literal_eval,
        metavar="{key1: [value1, value2], key2: [value3, value4]}",
        help="Exception tags to use for filtering resources. Example: --exception_tags {'test_task': ['test-keep-resources', 'stress-test-keep-resources']}",
    )

    # Add Argument for Name Regex
    parser.add_argument(
        "-n",
        "--name_regex",
        type=ast.literal_eval,
        metavar="['REGEX1','REGEX2']",
        help="Name Regex used to filter resources. Only applies to AWS keypairs and GCP IPs. Example: -n or --name_regex ['perftest_','qa_']",
    )

    # Add Argument for Exception Regex
    parser.add_argument(
        "-x",
        "--exception_regex",
        type=ast.literal_eval,
        metavar="['REGEX1','REGEX2']",
        help="Exception Regex to filter out resources. Example: -x or --exception_regex ['perftest_keep_resources', 'test_keep_resources']",
    )

    # Add Argument for Age Threshold
    parser.add_argument(
        "-a",
        "--age",
        type=ast.literal_eval,
        metavar="{'days': value1, 'hours': value2}",
        help="Age Threshold for resources. Age is not respected for IPs. Example: -a or --age {'days': 3, 'hours': 12}",
    )

    # Add Argument for Age Threshold
    parser.add_argument(
        "--detach_age",
        type=ast.literal_eval,
        metavar="{'days': value1, 'hours': value2}",
        help="Age Threshold for last detached disk resources. Age is not respected for VM's & IPs. Example: --detach_age {'days': 3, 'hours': 12}",
    )

    # Add Argument for Dry Run Mode
    parser.add_argument(
        "-d",
        "--dry_run",
        action="store_true",
        help="Enable dry_run only mode",
    )

    # Add Argument for Tag not present
    parser.add_argument(
        "-t",
        "--notags",
        type=ast.literal_eval,
        help="Filter by Tags not Present. Leave value of Key empty to indicate 'any' value. Format: -t or --notags {'test_task': ['test'], 'test_owner': []}",
        metavar="{key1: [value1, value2], key2: [value3, value4]}",
    )

    # Add Argument for Slack Channel
    parser.add_argument(
        "-m",
        "--slack_channel",
        metavar="SLACK_CHANNEL",
        help="The Slack channel to send the notifications to. Example: --slack_channel testing",
    )

    # Add Argument for Slack Channel
    parser.add_argument(
        "--slack_notify_users",
        action="store_true",
        help="If true notify users in the Slack channel, currently only for GCP disk",
    )

    # Add Argument for label that need to be used for getting username
    parser.add_argument(
        "--slack_user_label",
        metavar="SLACK_USER_LABEL",
        help="The gcp label that can be used to get username. Example: --slack_user_label owner",
    )

    # Add Argument for InfluxDB
    parser.add_argument(
        "-i",
        "--influxdb",
        type=ast.literal_eval,
        metavar="{'url': 'http://localhost:8086', 'org': 'Test', 'bucket': 'CRC'}",
        help="InfluxDB connection details in the form of a dictionary. Example: -i or --influxdb {'url': 'http://localhost:8086', 'org': 'Test', 'bucket': 'CRC', 'resource_suffix': 'test'}",
    )

    # Add Argument for Pending Window
    parser.add_argument(
        "--kms_pending_window",
        type=int,
        default=7,
        choices=range(7, 31),
        metavar="KMS_PENDING_WINDOW",
        help="The pending window(days) to schedule KMS deletion after this duration. Must be between 7 and 30 inclusive.",
    )

    # Add Argument for Key Description
    parser.add_argument(
        "--kms_key_description",
        type=str,
        metavar="KMS_KEY_DESCRIPTION",
        help="The string/name to be present in Key description in the Key JSON policy.",
    )

    # Add Argument for Jenkins Username
    parser.add_argument(
        "--kms_user",
        type=str,
        metavar="JENKINS_USERNAME",
        help="The Jenkins username for which associated KMS keys will be deleted.",
    )

    return vars(parser.parse_args())


def is_valid_type(name: str, value, expected_type):
    """
    Check if the given value is of the expected type, raises a ValueError if it is not.
    :param name: name of the variable being checked
    :param value: the value of the variable being checked
    :param expected_type: the expected type of the variable
    """
    if not isinstance(value, expected_type):
        raise ValueError(
            f"{name} should be of type {expected_type}, but got {type(value)}"
        )


def is_valid_list(name: str, value):
    """
    Check if the given value is a list and raises a ValueError if it is not.
    :param name: name of the variable being checked
    :param value: the value of the variable being checked
    """
    if value is not None:
        is_valid_type(name, value, list)


def is_valid_dict(name: str, value):
    """
    Check if the given value is a dict and raises a ValueError if it is not.
    :param name: name of the variable being checked
    :param value: the value of the variable being checked
    """
    if value is not None:
        is_valid_type(name, value, dict)


def are_values_of_dict_lists(name: str, value):
    """
    Check if the values of the given dict are lists and raises a ValueError if any of them is not.
    :param name: name of the variable being checked
    :param value: the value of the variable being checked
    """
    if value is not None:
        is_valid_dict(name, value)
        for key, val in value.items():
            is_valid_list(f"Value of {name} with key {key}", val)


def _validate_influxdb_input(influxdb: dict, field: str):
    """
    Validates a required field in the InfluxDB input.

    Args:
        influxdb (dict): The InfluxDB input to validate.
        field (str): The field that is required in the InfluxDB input.

    Raises:
        ValueError: If the required field is not present in the InfluxDB input.
    """
    if field not in influxdb:
        raise ValueError(
            f"The field '{field}' is required in the InfluxDB input, but was not found."
        )


def validate_influxdb_inputs(influxdb: dict) -> None:
    """
    Validates the required fields in the InfluxDB input.

    Args:
        influxdb (dict): The InfluxDB input to validate.

    Raises:
        ValueError: If any of the required fields are not present in the InfluxDB input.
    """
    _validate_influxdb_input(influxdb, "url")
    _validate_influxdb_input(influxdb, "org")
    _validate_influxdb_input(influxdb, "bucket")


def main():
    """
    Main function to perform resource cleanup operations on the specified cloud(s) and resource(s).

    :return: None
    """
    args = get_argparser()
    clouds = args.get("cloud")
    project_id = args.get("project_id")
    resources = args.get("resource")
    operation_type = args.get("operation_type")
    resource_states = args.get("resource_states")
    filter_tags = args.get("filter_tags")
    exception_tags = args.get("exception_tags")
    name_regex = args.get("name_regex")
    exception_regex = args.get("exception_regex")
    age = args.get("age")
    detach_age = args.get("detach_age")
    dry_run = args.get("dry_run")
    notags = args.get("notags")
    slack_channel = args.get("slack_channel")
    slack_notify_users = args.get("slack_notify_users")
    slack_user_label = args.get("slack_user_label")
    influxdb = args.get("influxdb")
    kms_pending_window = args.get("kms_pending_window")
    kms_key_description = args.get("kms_key_description")
    kms_user = args.get("kms_user")

    INFLUXDB_TOKEN = os.environ.get("INFLUXDB_TOKEN")
    SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")

    slack_client = None
    influxdb_client = None

    if slack_channel and not SLACK_BOT_TOKEN:
        raise EnvironmentError("SLACK_BOT_TOKEN is not set")

    if influxdb and not INFLUXDB_TOKEN:
        raise EnvironmentError("INFLUXDB_TOKEN is not set")

    # Validate operation_type and resources
    if operation_type == "stop" and resources != "vm":
        raise ValueError("Stop is supported only for vm resource")

    # Validate resources and clouds
    if resources == "all" and clouds != "all":
        raise ValueError(
            "All Resources cleanup is supported only with all Clouds. Format: --cloud all --resources all"
        )

    if slack_notify_users and not slack_user_label:
        raise ValueError(
            "--slack_user_label is mandatory when passing --slack_notify_user"
        )

    if resources == "kms" or resources == "all":
        if not kms_key_description:
            raise ValueError(
                "Key description string is required for deleting KMS keys."
            )
        if not kms_user:
            raise ValueError(
                "Jenkins user ARN is reuired for deleting associated KMS keys."
            )

    # Process Cloud
    clouds = CLOUDS if clouds == "all" else [clouds]

    # Process Resources
    resources = RESOURCES if resources == "all" else [resources]

    # Validate Input Values
    is_valid_list("resource_states", resource_states)
    are_values_of_dict_lists("filter_tags", filter_tags)
    are_values_of_dict_lists("exception_tags", exception_tags)
    is_valid_list("name_regex", name_regex)
    is_valid_list("exception_regex", exception_regex)
    is_valid_dict("age", age)
    is_valid_dict("detach_age", detach_age)
    is_valid_dict("influxdb", influxdb)

    if slack_channel:
        slack_client = WebClient(token=SLACK_BOT_TOKEN)

    if influxdb:
        validate_influxdb_inputs(influxdb)
        influxdb_client = InfluxDBClient(
            url=influxdb["url"],
            token=INFLUXDB_TOKEN,
            org=influxdb["org"],
        )

    # Perform operations
    for cloud in clouds:
        crc = CRC(
            cloud,
            dry_run,
            notags,
            slack_client,
            influxdb_client,
            project_id,
            slack_channel,
            influxdb,
        )
        for resource in resources:
            if resource == "disk":
                crc.delete_disks(
                    filter_tags,
                    exception_tags,
                    age,
                    detach_age,
                    name_regex,
                    exception_regex,
                    slack_notify_users,
                    slack_user_label,
                )
            elif resource == "ip":
                crc.delete_ip(
                    filter_tags,
                    exception_tags,
                    name_regex,
                    exception_regex,
                )
            elif resource == "keypair":
                crc.delete_keypairs(name_regex, exception_regex, age)
            elif resource == "nic":
                crc.delete_nic(name_regex, exception_regex)
            elif resource == "vm":
                if operation_type == "delete":
                    crc.delete_vm(
                        filter_tags,
                        exception_tags,
                        age,
                        resource_states,
                    )
                elif operation_type == "stop":
                    crc.stop_vm(filter_tags, exception_tags, age)
            elif resource == "vpc":
                crc.delete_vpc(filter_tags, exception_tags)
            elif resource == "kms":
                crc.delete_kms(
                    filter_tags,
                    exception_tags,
                    kms_key_description,
                    kms_user,
                    kms_pending_window,
                    age,
                )


if __name__ == "__main__":
    main()
