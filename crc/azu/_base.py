# Copyright (c) Yugabyte, Inc.

"""Azure credential and the compute/network clients of one resource group.

Reads the `AZURE_CREDENTIALS_*` environment variables (and `AZURE_RESOURCE_GROUP` when no
resource group is passed). Each client is built on first use and then reused.
"""

import os
from typing import Any

from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.resource.resources import ResourceManagementClient


class Base:
    """Authenticate to Azure and hand out clients to manage Azure resources."""

    def __init__(self, resource_group: str) -> None:
        """Read the Azure credentials from the environment and build the credential."""
        # Empty dicts mean "not built yet" (the singleton pattern of main). The clients are `Any`
        # because each caller narrows them with its own Protocol of the calls it uses.
        self.compute_client: Any = {}
        self.network_client: Any = {}
        self.resource_client: Any = {}

        self.subscription_id: str = os.environ["AZURE_CREDENTIALS_SUBSCRIPTION_ID"]
        self.tenant_id: str = os.environ["AZURE_CREDENTIALS_TENANT_ID"]
        self.client_id: str = os.environ["AZURE_CREDENTIALS_CLIENT_ID"]
        self.secret: str = os.environ["AZURE_CREDENTIALS_CLIENT_SECRET"]
        self.resource_group: str = resource_group or os.environ["AZURE_RESOURCE_GROUP"]

        self.credential = ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.secret,
        )

    def get_compute_client(self) -> Any:
        """Return the shared `ComputeManagementClient`, creating it on the first call."""
        if self.compute_client:
            return self.compute_client
        client = ComputeManagementClient(
            credential=self.credential,
            subscription_id=self.subscription_id,
        )
        self.compute_client = client
        return client

    def get_network_client(self) -> Any:
        """Return the shared `NetworkManagementClient`, creating it on the first call."""
        if self.network_client:
            return self.network_client
        client = NetworkManagementClient(self.credential, self.subscription_id)
        self.network_client = client
        return client

    def get_resource_client(self) -> Any:
        """Return the shared `ResourceManagementClient` (creation times), built on first use."""
        if self.resource_client:
            return self.resource_client
        client = ResourceManagementClient(self.credential, self.subscription_id)
        self.resource_client = client
        return client
