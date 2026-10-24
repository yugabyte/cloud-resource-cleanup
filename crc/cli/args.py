"""The command line definition of crc.py: flags, defaults, choices and literal values."""

import argparse
import ast
from typing import Any

from crc.cli.args_run import _add_age_args, _add_notify_args, _add_resource_args
from crc.core.kinds import KIND_NAMES


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
        choices=[*KIND_NAMES, "all"],
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
