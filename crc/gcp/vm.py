import logging
from datetime import datetime
from typing import Dict, List

from google.cloud import compute_v1
from googleapiclient import discovery

from crc.service import Service


class VM(Service):
    """
    This class provides an implementation of the Service class for managing virtual machines (VMs) on Google Cloud.
    """

    # Initialize class variables
    service_name = "compute"
    """
    The service name used for the Google Cloud API.
    """

    service_version = "v1"
    """
    The version of the service used for the Google Cloud API.
    """

    default_instance_state = ["RUNNING"]
    """
    The default state of instances that will be deleted.
    """

    time_format = "%Y-%m-%dT%H:%M:%S.%f%z"
    """
    The format of timestamps used for comparison.
    """

    def __init__(
        self,
        project_id: str,
        filter_labels: Dict[str, List[str]],
        exception_labels: Dict[str, List[str]],
        age: int,
    ) -> None:
        """
        Initialize an instance of the VM class.

        :param project_id: ID of the Google Cloud project.
        :type project_id: str
        :param filter_labels: Dictionary of labels and their values used to filter VMs for deletion.
        :type filter_labels: Dict[str, List[str]]
        :param exception_labels: Dictionary of labels and their values used to exclude VMs from deletion.
        :type exception_labels: Dict[str, List[str]]
        :param age: Age in days of VMs that will be deleted.
        :type age: int
        """
        super().__init__()
        self.instance_names_to_delete = []
        self.instance_names_to_stop = []
        self.project_id = project_id
        self.filter_labels = filter_labels
        self.exception_labels = exception_labels
        self.age = age

    @property
    def delete_count(self) -> int:
        """
        Returns the count of items in the instance_names_to_delete list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        count = len(self.instance_names_to_delete)
        logging.info(f"count of items in instance_names_to_delete: {count}")
        return count

    @property
    def stopped_count(self) -> int:
        """
        Returns the count of items in the instance_names_to_stop list.
        It's a read-only property, which means it can be accessed like a variable, but cannot be set like a variable.
        """
        count = len(self.instance_names_to_stop)
        logging.info(f"count of items in instance_names_to_stop: {count}")
        return count

    def _perform_operation(
        self, operation_type: str, instance_state: List[str] = default_instance_state
    ) -> None:
        """
        Perform the specified operation (delete or stop) on VMs that match the specified filter labels and are older than the specified age.

        :param operation_type: The type of operation to perform (delete or stop)
        :type operation_type: str
        :param instance_state: List of valid statuses of instances to perform the operation on.
        :type instance_state: List[str]
        """
        # Build the service for making API calls
        with discovery.build(self.service_name, self.service_version) as service:
            request = service.zones().list(project=self.project_id)
            zones = request.execute()["items"]
            instance_client = compute_v1.InstancesClient()

        # Iterate over all zones and instances
        for zone_obj in zones:
            zone = zone_obj["name"]
            for instance in instance_client.list(project=self.project_id, zone=zone):
                if instance.status not in instance_state:
                    continue
                # Check if the instance has any labels that are in the exception list
                if any(
                    key in instance.labels and instance.labels[key] in value
                    for key, value in self.exception_labels.items()
                ):
                    continue
                # Check if the instance has any labels that match the filter
                if not self.filter_labels and any(
                    key in instance.labels and instance.labels[key] in value
                    for key, value in self.filter_labels.items()
                ):
                    timestamp = instance.creation_timestamp
                    timestamp = timestamp[0:-3] + timestamp[-2:]
                    created_timestamp = datetime.strptime(timestamp, self.time_format)
                    dt = datetime.now().replace(tzinfo=created_timestamp.tzinfo)
                    # Check if the instance is older than the specified age
                    if self.is_old(self.age, dt, created_timestamp):
                        try:
                            if operation_type == "delete":
                                # perform the delete operation
                                service.instances().delete(
                                    project=self.project_id,
                                    zone=zone,
                                    instance=instance.name,
                                ).execute()
                                # log the instance that is being deleted
                                logging.info(f"Deleting instance {instance.name}")
                                # add the instance to the list of instances that have been deleted
                                self.instance_names_to_delete.append(instance.name)
                            elif operation_type == "stop":
                                # perform the stop operation
                                service.instances().stop(
                                    project=self.project_id,
                                    zone=zone,
                                    instance=instance.name,
                                ).execute()
                                # log the instance that is being stopped
                                logging.info(f"Stopping instance {instance.name}")
                                # add the instance to the list of instances that have been stopped
                                self.instance_names_to_stop.append(instance.name)
                        except Exception as e:
                            # log the error message if an exception occurs
                            logging.error(
                                f"Error occurred while {operation_type} instance {instance.name}: {e}"
                            )

    def delete(self, instance_state: List[str] = default_instance_state) -> None:
        """
        Deletes instances that match the filter and age threshold, and also checks for exception labels.

        :param instance_state: List of valid statuses of instances to delete.
        :type instance_state: List[str]
        """
        self._perform_operation("delete", instance_state)

    def stop(self) -> None:
        """
        Stop VMs that match the specified filter labels and are older than the specified age.
        """
        self._perform_operation("stop", self.default_instance_state)
