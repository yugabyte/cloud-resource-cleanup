"""The `CRC` run context: the settings of one cloud that the engine bridge and the notifiers read.

`bridge.py` hands this object to the Slack and Influx notifiers (`slack_client`,
`slack_channel`, `influxdb_client`, `influxdb_bucket`, `resource_suffix`) and to the route
hooks (`get_msg` for the `snapshot` console line); `scan.py` reads `cloud`, `dry_run`, `notags`,
`project_id` and `resource_group` to prepare the `--scan_tag` scan.
"""

from typing import Any


class CRC:
    """Holds the run settings of one cloud; it runs nothing itself."""

    cloud: str
    dry_run: bool
    # Any: the unvalidated CLI values (None, a dict where an int is declared, BUG-197) are
    # wider than the types the cleaners declare, and the call must stay as on main.
    project_id: Any
    notags: Any
    slack_client: Any
    influxdb_client: Any
    slack_channel: Any
    resource_group: Any
    # Set only when `influxdb_conn` is given (BUG-030), so they are declared, not assigned.
    influxdb_bucket: str | None
    resource_suffix: str | None

    def __init__(  # noqa: PLR0913, PLR0917 - the nine parameters are the public signature
        self,
        cloud: str,
        dry_run: bool,
        notags: dict[str, Any] | None,
        slack_client: object,
        influxdb_client: object,
        project_id: str | None = None,
        resource_group: str | None = None,
        slack_channel: str | None = None,
        influxdb_conn: dict[str, Any] | None = None,
    ) -> None:
        """
        Initializes the object with required properties.

        Parameters:
        cloud (str): the name of the cloud platform ('aws', 'gcp', 'azure' or 'oci')
        dry_run (bool): flag to indicate whether the operation is a dry run or not
        notags (dict): a dictionary containing a list of resources that don't have any tags
        slack_client (object): the Slack client instance used to send messages
        influxdb_client (object): the InfluxDB client instance used to send data
        project_id (str, optional): the ID of the project (mandatory for GCP)
        resource_group (str, optional): the resource group of azure resources (mandatory for Azure)
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
        self.resource_group = resource_group

        if influxdb_conn:
            self.influxdb_bucket = influxdb_conn.get("bucket")
            self.resource_suffix = influxdb_conn.get("resource_suffix")

    def get_msg(self, resource: str, operation_type: str, operated_list: Any) -> str:
        """
        Returns a message to be sent to the Slack channel

        :param resource: Cloud resource type (e.g. "nics", "vms", "ips", "keypairs", "disks")
        :param operation_type: Operation type (e.g. "Deleted", "Stopped")
        :param operated_list: List of operated resources.
        :return: Message to be sent to the Slack channel
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
