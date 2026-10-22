"""State of a `CRC` run: the constructor and the attributes every operation reads."""

from typing import Any


class CRCBase:
    """Holds the run settings. `crc.cli.runner.CRC` composes the operation mixins on top."""

    cloud: str
    dry_run: bool
    # Any: the adapters declare narrower types than the unvalidated CLI values (None, a dict
    # where an int is declared, BUG-197), and the call must stay as on main.
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
