# Copyright (c) Yugabyte, Inc.

import os

from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient


class Base:
    """
    This module contains functions to authenticate and connect to Azure, and clients to manage Azure resources
    """

    def __init__(self, resource_group: str) -> None:
        """
        Initialize the class with environment variables for Azure credentials.
        """
        self.compute_client = (
            {}
        )  # Dictionary to store the ComputeManagementClient object, used to implement the singleton pattern
        self.network_client = (
            {}
        )  # Dictionary to store the NetworkManagementClient object, used to implement the singleton pattern

        # Environment variables for Azure credentials
        self.subscription_id = os.environ[
            "AZURE_CREDENTIALS_SUBSCRIPTION_ID"
        ]  # The subscription ID for the Azure subscription you want to manage resources in.
        self.tenant_id = os.environ[
            "AZURE_CREDENTIALS_TENANT_ID"
        ]  # The tenant ID for the Azure Active Directory associated with your subscription.
        self.client_id = os.environ[
            "AZURE_CREDENTIALS_CLIENT_ID"
        ]  # The client ID for the Azure application that will authenticate to Azure.
        self.secret = os.environ[
            "AZURE_CREDENTIALS_CLIENT_SECRET"
        ]  # The client secret for the Azure application that will authenticate to Azure.
        self.resource_group = (
            resource_group if resource_group else os.environ["AZURE_RESOURCE_GROUP"]
        )  # The name of the resource group you want to manage resources in.

        self.credential = ClientSecretCredential(
            tenant_id=self.tenant_id,  # The tenant ID associated with the Azure subscription
            client_id=self.client_id,  # The client ID for the Azure application that will authenticate to Azure
            client_secret=self.secret,  # The client secret for the Azure application that will authenticate to Azure
        )

    def get_compute_client(self):
        """
        Return the ComputeManagementClient object for managing Azure compute resources.
        This method uses the singleton pattern to ensure that only one instance of the client is created,
        and that the same instance is returned every time this method is called.
        """
        if self.compute_client:
            return self.compute_client
        self.compute_client = ComputeManagementClient(
            credential=self.credential,
            subscription_id=self.subscription_id,
        )
        return self.compute_client

    def get_network_client(self):
        """
        Return the NetworkManagementClient object for managing Azure network resources.
        This method uses the singleton pattern to ensure that only one instance of the client is created,
        and that the same instance is returned every time this method is called.
        """
        if self.network_client:
            return self.network_client
        self.network_client = NetworkManagementClient(
            self.credential, self.subscription_id
        )
        return self.network_client
