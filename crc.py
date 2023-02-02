# Copyright (c) Yugabyte, Inc.

import argparse
import ast
import os
from typing import Dict, List

from slack_sdk import WebClient

# Import classes for interacting with different resources across different clouds
from crc.aws.elastic_ips import ElasticIPs
from crc.aws.keypairs import KeyPairs
from crc.aws.vm import VM as AWS_VM
from crc.azu.disk import Disk
from crc.azu.ip import IP as AZU_IP
from crc.azu.vm import VM as AZU_VM
from crc.gcp.ip import IP as GCP_IP
from crc.gcp.vm import VM as GCP_VM

# List of supported clouds and resources
CLOUDS = ["aws", "azure", "gcp"]
RESOURCES = ["disk", "ip", "keypair", "vm"]

DELETED = "Deleted"
STOPPED = "Stopped"

NICS = "NICs"
DISKS = "Disks"
VMS = "VMs"
IPS = "IPs"
KEYPAIRS = "Keypairs"


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
        project_id: str = None,
        slack_channel: str = None,
    ) -> None:
        """
        Initializes the object with required properties.

        Parameters:
        cloud (str): the name of the cloud platform ('aws', 'gcp' or 'azure')
        dry_run (bool): flag to indicate whether the operation is a dry run or not
        notags (dict): a dictionary containing a list of resources that don't have any tags
        slack_client (object): the Slack client instance used to send messages
        project_id (str, optional): the ID of the project (mandatory for GCP)
        slack_channel (str, optional): the name of the Slack channel to send messages to
        """
        self.cloud = cloud
        if cloud == "gcp" and not project_id:
            raise ValueError("project_id is mandatory Parameter for GCP")
        self.dry_run = dry_run
        self.project_id = project_id
        self.notags = notags
        self.slack_client = slack_client
        self.slack_channel = slack_channel

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

    def get_msg(
        self, resource: str, operation_type: str, operated_list: List[str]
    ) -> str:
        """
        Returns a message to be sent to the Slack channel

        :param resource: Cloud resource type (e.g. "nics", "vms", "ips", "keypairs", "disks")
        :type resource: str
        :param operation_type: Operation type (e.g. "Deleted", "Stopped")
        :type operation_type: str
        :param operated_list: List of operated resources
        :type operated_list: list
        :return: Message to be sent to the Slack channel
        :rtype: str
        """
        if self.dry_run:
            return f"Dry Run: {operation_type} the following {self.cloud} {resource}: {operated_list}"

        return (
            f"{operation_type} the following {self.cloud} {resource}: {operated_list}"
        )

    def notify_deleted_nic_via_slack(self, nic: object):
        """
        Sends a notification message to the Slack channel about deleted network interfaces

        :param nic: Network interface object
        :type nic: object
        """
        if not nic.get_deleted_nic:
            return

        msg = self.get_msg(NICS, DELETED, nic.get_deleted_nic)
        self.slack_client.chat_postMessage(channel="#" + self.slack_channel, text=msg)

    def notify_deleted_vm_via_slack(self, vm: object):
        """
        Sends a notification message to the Slack channel about deleted virtual machines

        :param vm: Virtual machine object
        :type vm: object
        """
        if not vm.get_deleted:
            return

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
        if not vm.get_stopped:
            return

        msg = self.get_msg(VMS, STOPPED, vm.get_stopped)
        self.slack_client.chat_postMessage(channel="#" + self.slack_channel, text=msg)

    def notify_deleted_ip_via_slack(self, ip: object):
        """
        Sends a message to a Slack channel about deleted IP instances.

        Parameters:
        ip (object): the deleted IP instance
        """
        if not ip.get_deleted:
            return

        msg = self.get_msg(IPS, DELETED, ip.get_deleted)
        self.slack_client.chat_postMessage(channel="#" + self.slack_channel, text=msg)

    def notify_deleted_keypair_via_slack(self, keypair: object):
        """
        Sends a notification message to the Slack channel about deleted Key Pairs

        :param keypair: Key Pair object
        :type vm: object
        """
        if not keypair.get_deleted:
            return

        msg = self.get_msg(KEYPAIRS, DELETED, keypair.get_deleted)
        self.slack_client.chat_postMessage(channel="#" + self.slack_channel, text=msg)

    def notify_deleted_disk_via_slack(self, disk: object):
        """
        Sends a notification message to the Slack channel about deleted Disks

        :param disk: Disk object
        :type vm: object
        """
        if not disk.get_deleted:
            return

        msg = self.get_msg(DISKS, DELETED, disk.get_deleted)
        self.slack_client.chat_postMessage(channel="#" + self.slack_channel, text=msg)

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

    def delete_disks(
        self,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
    ):
        """
        Delete Disks that match the specified criteria.
        This method is only supported on AZURE.

        :param filter_tags: Dictionary of tags to filter the disks.
        :param exception_tags: Dictionary of tags to exclude the disks.
        :param age: Dictionary of age conditions to filter the disks.
        """
        if self.cloud != "azure":
            raise ValueError(
                "Incorrect Cloud Provided. Disks operation is supported only on AZURE. AWS, GCP clean the NICs, Disks along with VM"
            )

        disk = Disk(self.dry_run, filter_tags, exception_tags, age, self.notags)
        disk.delete()
        if self.slack_client:
            self.notify_deleted_disk_via_slack(disk)


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
        choices=["disk", "ip", "keypair", "vm", "all"],
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
        metavar="{'days': 3, 'hours': 12}",
        help="Age Threshold for resources. Age is not respected for IPs. Example: -a or --age {'days': 3, 'hours': 12}",
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
    dry_run = args.get("dry_run")
    notags = args.get("notags")
    slack_channel = args.get("slack_channel")
    
    SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
    
    slack_client = None

    if slack_channel and not SLACK_BOT_TOKEN:
        raise EnvironmentError("SLACK_BOT_TOKEN is not set")

    # Validate operation_type and resources
    if operation_type == "stop" and resources != "vm":
        raise ValueError("Stop is supported only for vm resource")

    # Validate resources and clouds
    if resources == "all" and clouds != "all":
        raise ValueError(
            "All Resources cleanup is supported only with all Clouds. Format: --cloud all --resources all"
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

    if slack_channel:
        slack_client = WebClient(token=SLACK_BOT_TOKEN)

    # Perform operations
    for cloud in clouds:
        crc = CRC(cloud, dry_run, notags, slack_client, project_id, slack_channel)
        for resource in resources:
            if resource == "disk":
                crc.delete_disks(filter_tags, exception_tags, age)
            elif resource == "ip":
                crc.delete_ip(
                    filter_tags,
                    exception_tags,
                    name_regex,
                    exception_regex,
                )
            elif resource == "keypair":
                crc.delete_keypairs(name_regex, exception_regex, age)
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


if __name__ == "__main__":
    main()
