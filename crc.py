import argparse
import ast
from typing import Dict, List

# Import classes for interacting with different resources across different clouds
from crc.aws.elastic_ips import ElasticIPs
from crc.aws.keypairs import KeyPairs
from crc.aws.vm import VM as AWS_VM
from crc.azu.disk import Disk as Disk
from crc.azu.ip import IP as AZU_IP
from crc.azu.vm import VM as AZU_VM
from crc.gcp.ip import IP as GCP_IP
from crc.gcp.vm import VM as GCP_VM

# List of supported clouds and resources
CLOUDS = ["aws", "azu", "gcp"]
RESOURCES = ["disk", "ip", "keypair", "vm"]


class CRC:
    """
    Class for cleaning up resources across different clouds.
    """

    def __init__(self, cloud: str, project_id=None) -> None:
        """
        Initialize the class with the cloud and project_id (if applicable).

        :param cloud: The cloud to interact with (e.g. "aws", "azu", "gcp").
        :param project_id: The project_id for GCP (mandatory for GCP).
        """
        self.cloud = cloud
        if cloud == "gcp" and not project_id:
            raise TypeError("project_id is mandatory Parameter for GCP")
        self.project_id = project_id

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
            return AWS_VM(filter_tags, exception_tags, age)
        elif self.cloud == "azu":
            return AZU_VM(filter_tags, exception_tags, age)
        elif self.cloud == "gcp":
            return GCP_VM(self.project_id, filter_tags, exception_tags, age)
        raise TypeError("Incorrect Cloud Provided")

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
            return ElasticIPs(filter_tags, exception_tags)
        elif self.cloud == "azu":
            return AZU_IP(filter_tags, exception_tags)
        elif self.cloud == "gcp":
            return GCP_IP(self.project_id, name_regex, exception_regex)
        raise TypeError("Incorrect Cloud Provided")

    def delete_vm(
        self,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
        instance_state: List[str],
    ):
        """
        Delete the specified VM.

        :param filter_tags: Dictionary of tags to filter the VM.
        :param exception_tags: Dictionary of tags to exclude the VM.
        :param age: Dictionary of age conditions to filter the VM.
        :param instance_state: List of instance states that should be deleted.
        """
        vm = self._get_vm(filter_tags, exception_tags, age)
        self._delete_vm(vm, instance_state)

    def stop_vm(
        self,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
    ):
        """
        Stop the specified VM.

        :param filter_tags: Dictionary of tags to filter the VM.
        :param exception_tags: Dictionary of tags to exclude the VM.
        :param age: Dictionary of age conditions to filter the VM.
        """
        vm = self._get_vm(filter_tags, exception_tags, age)
        vm.stop()

    def delete_ip(
        self,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        name_regex: List[str],
        exception_regex: List[str],
    ):
        """
        Delete the specified IP.

        :param filter_tags: Dictionary of tags to filter the IP.
        :param exception_tags: Dictionary of tags to exclude the IP.
        :param name_regex: List of regex patterns to filter the IP.
        :param exception_regex: List of regex patterns to exclude the IP.
        """
        ip = self._get_ip(
            filter_tags, exception_tags, name_regex, exception_regex
        )
        ip.delete()

    def delete_keypairs(
        self,
        name_regex: List[str],
        exception_regex: List[str],
        age: Dict[str, int],
    ):
        """
        Delete the specified keypairs. This method is only supported on AWS.

        :param name_regex: List of regex patterns to filter the keypairs.
        :param exception_regex: List of regex patterns to exclude the keypairs.
        :param age: Dictionary of age conditions to filter the keypairs.
        """
        if self.cloud != "aws":
            raise TypeError(
                "Incorrect Cloud Provided. Keypairs operation is supported only on AWS"
            )
        KeyPairs(name_regex, exception_regex, age).delete()

    def delete_disks(
        self,
        filter_tags: Dict[str, List[str]],
        exception_tags: Dict[str, List[str]],
        age: Dict[str, int],
    ):
        """
        Delete the specified disks. This method is only supported on AZU.

        :param filter_tags: Dictionary of tags to filter the disks.
        :param exception_tags: Dictionary of tags to exclude the disks.
        :param age: Dictionary of age conditions to filter the disks.
        """
        if self.cloud != "azu":
            raise TypeError(
                "Incorrect Cloud Provided. Disks operation is supported only on AZU. AWS, GCP clean the NICs, Disks along with VM"
            )
        Disk(filter_tags, exception_tags, age).delete()


def get_argparser():
    """
    Method to parse and return command line arguments.

    :return: A dictionary containing all the command line arguments.
    """
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # Add Argument for Cloud
    parser.add_argument(
        "--cloud",
        choices=["aws", "azu", "gcp", "all"],
        required=True,
        help="The cloud to operate on. Valid options are: 'aws', 'azu', 'gcp', 'all'. Example: --cloud all",
    )

    # Add Argument for Project ID (GCP only)
    parser.add_argument(
        "--project_id",
        help="Project ID for GCP. Required only for GCP. Example: --project_id testing",
    )

    # Add Argument for Resource Type
    parser.add_argument(
        "--resource",
        default="all",
        choices=["disk", "ip", "keypair", "vm", "all"],
        help="Type of resource to operate on. Valid options are: 'disk', 'ip', 'keypair', 'vm', 'all'. Default: 'all'. Example: --resource vm",
    )

    # Add Argument for Operation Type
    parser.add_argument(
        "--operation_type",
        default="delete",
        choices=["delete", "stop"],
        help="Type of operation to perform on resource. Valid options are: 'delete', 'stop'. Default: 'delete'. Example: --operation_type stop",
    )

    # Add argument for resource states
    parser.add_argument(
        "--resource_states",
        type=ast.literal_eval,
        help="State of the resource to filter. Format: --resource_states ['RUNNING', 'STOPPED']",
    )

    # Add argument for filter tags
    parser.add_argument(
        "--filter_tags",
        type=ast.literal_eval,
        help="Tags to use for filtering resources. Format: --filter_tags {'test_task': ['test', 'stress-test']}",
    )

    # Add argument for exception tags
    parser.add_argument(
        "--exception_tags",
        type=ast.literal_eval,
        help="Exception tags to use for filtering resources. Format: --exception_tags {'test_task': ['test-keep-resources', 'stress-test-keep-resources']}",
    )

    # Add Argument for Name Regex
    parser.add_argument(
        "--name_regex",
        help="Name Regex used to filter resources. Only applies to AWS keypairs and GCP IPs. Format: --name_regex perftest_",
    )

    # Add Argument for Exception Regex
    parser.add_argument(
        "--exception_regex",
        help="Exception Regex to filter out resources. Format: --exception_regex perftest_keep_resources",
    )

    # Add Argument for Age Threshold
    parser.add_argument(
        "--age",
        type=ast.literal_eval,
        help="Age Threshold for resources. Format: --age {'days': 3, 'hours': 12}",
    )

    return vars(parser.parse_args())


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

    # Process Cloud
    if clouds == "all":
        clouds = CLOUDS
    else:
        clouds = [clouds]

    # Validate operation_type and resources
    if operation_type == "stop" and resources != "vm":
        raise TypeError("Stop is supported only for vm resource")

    if resources == "all":
        if clouds != CLOUDS:
            raise TypeError(
                "All Resources cleanup is supported only with all Clouds. Format: --cloud all --resources all"
            )
        resources = RESOURCES
    else:
        resources = [resources]

    # Perform operations
    for cloud in clouds:
        crc = CRC(cloud, project_id)
        for resource in resources:
            if resource == "disk":
                crc.delete_disks(filter_tags, exception_regex, age)
            elif resource == "ip":
                crc.delete_ip(
                    filter_tags, exception_tags, name_regex, exception_regex
                )
            elif resource == "keypair":
                crc.delete_keypairs(name_regex, exception_regex, age)
            elif resource == "vm":
                if operation_type == "delete":
                    crc.delete_vm(
                        filter_tags, exception_tags, age, resource_states
                    )
                elif operation_type == "stop":
                    crc.stop_vm(filter_tags, exception_tags, age)


if __name__ == "__main__":
    main()
