"""The command line definition of crc.py: flags, defaults, choices and literal values."""

import argparse
import ast
from typing import Any


def _add_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-c",
        "--cloud",
        choices=["aws", "azure", "gcp", "oci", "all"],
        required=True,
        metavar="CLOUD",
        help=(
            "The cloud to operate on. Valid options are: 'aws', 'azure', 'gcp', 'oci', 'all'. "
            "Example: -c or --cloud all"
        ),
    )

    parser.add_argument(
        "-r",
        "--resource",
        default="all",
        choices=[
            "disk",
            "ip",
            "keypair",
            "vm",
            "vpc",
            "kms",
            "nic",
            "spot_instance_requests",
            "spot_instances",
            "snapshot",
            "all",
        ],
        metavar="RESOURCE",
        help=(
            "Type of resource to operate on. Valid options include: 'disk', 'ip', "
            "'keypair', 'vm', 'kms', 'nic', 'snapshot', 'spot_instance_requests' "
            "(AWS only: cancel request then terminate), 'spot_instances' "
            "(Azure/GCP/OCI Spot or preemptible VMs), 'all'. Default: 'all'."
        ),
    )

    parser.add_argument(
        "-p",
        "--project_id",
        metavar="PROJECT_ID",
        help="Project ID for GCP. Required only for GCP. Example: --project_id testing",
    )

    parser.add_argument(
        "-o",
        "--operation_type",
        default="delete",
        choices=["delete", "stop"],
        metavar="OPERATION",
        help=(
            "Type of operation to perform on resource. Valid options are: 'delete', 'stop'. "
            "Default: 'delete'. Example: -o or --operation_type stop"
        ),
    )


def _add_filter_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-s",
        "--resource_states",
        type=ast.literal_eval,
        metavar="['state1', 'state2']",
        help="State of the resource to filter. Example: --resource_states ['RUNNING', 'STOPPED']",
    )

    parser.add_argument(
        "-f",
        "--filter_tags",
        type=ast.literal_eval,
        metavar="{key1: [value1, value2], key2: [value3, value4]}",
        help=(
            "Tags to use for filtering resources. "
            "Example: --filter_tags {'test_task': ['test', 'stress-test']}"
        ),
    )

    parser.add_argument(
        "-e",
        "--exception_tags",
        type=ast.literal_eval,
        metavar="{key1: [value1, value2], key2: [value3, value4]}",
        help=(
            "Exception tags to use for filtering resources. "
            "Example: --exception_tags "
            "{'test_task': ['test-keep-resources', 'stress-test-keep-resources']}"
        ),
    )

    parser.add_argument(
        "-n",
        "--name_regex",
        type=ast.literal_eval,
        metavar="['REGEX1','REGEX2']",
        help=(
            "Name Regex used to filter resources. Only applies to AWS keypairs and GCP IPs. "
            "Example: -n or --name_regex ['perftest_','qa_']"
        ),
    )

    parser.add_argument(
        "-x",
        "--exception_regex",
        type=ast.literal_eval,
        metavar="['REGEX1','REGEX2']",
        help=(
            "Exception Regex to filter out resources. "
            "Example: -x or --exception_regex ['perftest_keep_resources', 'test_keep_resources']"
        ),
    )


def _add_age_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-a",
        "--age",
        type=ast.literal_eval,
        metavar="{'days': value1, 'hours': value2}",
        help=(
            "Age Threshold for resources. Age is not respected for IPs. "
            "Example: -a or --age {'days': 3, 'hours': 12}"
        ),
    )

    parser.add_argument(
        "--custom_age_tag_key",
        metavar="CUSTOM_AGE_TAG_KEY",
        help=(
            "Define a custom tag key for the age threshold of resources. This tag is "
            "ignored for IPs and Keypairs. Example usage: --custom_age_tag_key retention_age. "
            "The value can be specified in a dictionary format "
            "(e.g., {'days': 3, 'hours': 6}) or as an "
            "integer, which will be interpreted as days by default."
        ),
    )

    parser.add_argument(
        "--max_age",
        metavar="MAX_AGE",
        help=(
            "Specify the maximum age threshold for resources. This value will override "
            "the value of the tag specified by 'custom_age_tag_key'. "
            "Provide the age in dictionary format, e.g., {'days': 3, 'hours': 6}."
        ),
    )

    parser.add_argument(
        "--detach_age",
        type=ast.literal_eval,
        metavar="{'days': value1, 'hours': value2}",
        help=(
            "Detached-age gate for disk cleanup (AWS and GCP; not used for VMs/IPs). "
            "AWS: inferred from AWS/EBS CloudWatch metrics while attached to a "
            "*running* instance — not a true last-detach timestamp, and blind to "
            "stopped-instance attachments. On AWS, --age is optional; if omitted, "
            "detach_age itself is also used as the CreateTime floor. "
            "GCP: uses disk.last_detach_timestamp (a real last-detach time). "
            "On GCP, --age is ignored for disks; --detach_age is the only age gate, "
            "and omitting it (with no custom age label) deletes every matching "
            "detached disk. A custom age label, if present, replaces detach_age "
            "on GCP. On AWS the custom tag never changes the CloudWatch "
            "detach_age window; it only affects the CreateTime floor, and "
            "max(tag, detach_age) applies only in detach_age-only mode "
            "(when --age is omitted). "
            "Example: --detach_age {'days': 3, 'hours': 12}"
        ),
    )


def _add_notify_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "-d",
        "--dry_run",
        action="store_true",
        help="Enable dry_run only mode",
    )

    parser.add_argument(
        "-t",
        "--notags",
        type=ast.literal_eval,
        help=(
            "Filter by Tags not Present. Leave value of Key empty to indicate 'any' value. "
            "Format: -t or --notags {'test_task': ['test'], 'test_owner': []}"
        ),
        metavar="{key1: [value1, value2], key2: [value3, value4]}",
    )

    parser.add_argument(
        "-m",
        "--slack_channel",
        metavar="SLACK_CHANNEL",
        help=("The Slack channel to send the notifications to. Example: --slack_channel testing"),
    )

    parser.add_argument(
        "--slack_notify_users",
        action="store_true",
        help="If true notify users in the Slack channel, currently only for GCP disk",
    )

    parser.add_argument(
        "--slack_user_label",
        metavar="SLACK_USER_LABEL",
        help=("The gcp label that can be used to get username. Example: --slack_user_label owner"),
    )

    parser.add_argument(
        "-i",
        "--influxdb",
        type=ast.literal_eval,
        metavar="{'url': 'http://localhost:8086', 'org': 'Test', 'bucket': 'CRC'}",
        help=(
            "InfluxDB connection details in the form of a dictionary. "
            "Example: -i or --influxdb {'url': 'http://localhost:8086', 'org': 'Test', "
            "'bucket': 'CRC', 'resource_suffix': 'test'}"
        ),
    )


def _add_resource_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--kms_pending_window",
        type=int,
        default=7,
        choices=range(7, 31),
        metavar="KMS_PENDING_WINDOW",
        help=(
            "The pending window(days) to schedule KMS deletion after this duration. "
            "Must be between 7 and 30 inclusive."
        ),
    )

    parser.add_argument(
        "--kms_key_description",
        type=str,
        metavar="KMS_KEY_DESCRIPTION",
        help="The string/name to be present in Key description in the Key JSON policy.",
    )

    parser.add_argument(
        "--kms_user",
        type=str,
        metavar="JENKINS_USERNAME",
        help="The Jenkins username for which associated KMS keys will be deleted.",
    )

    parser.add_argument(
        "--resource_group",
        metavar="RESOURCE_GROUP",
        help=(
            "Resource group for Azure. Required only for Azure. Example: --resource_group test-rg"
        ),
    )

    parser.add_argument(
        "--scan_tag",
        metavar="KEY=VALUE",
        help=(
            "Scan VMs for a specific tag key/value pair (read-only). "
            "Example: --scan_tag auto_clean=false"
        ),
    )


def get_argparser() -> dict[str, Any]:
    """
    Method to parse and return command line arguments.

    :return: A dictionary containing all the command line arguments.
    """
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Cleanup Resources across different clouds",
    )
    _add_selection_args(parser)
    _add_filter_args(parser)
    _add_age_args(parser)
    _add_notify_args(parser)
    _add_resource_args(parser)
    return vars(parser.parse_args())
