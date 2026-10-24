"""The age, notification and resource-specific flags of crc.py, in the order of `main`."""

import argparse
import ast


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
