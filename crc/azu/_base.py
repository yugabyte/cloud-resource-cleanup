# Copyright (c) Yugabyte, Inc.

import os

from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient

"""
This module contains functions to authenticate and connect to Azure, and clients to manage Azure resources
"""

# Environment variables for Azure credentials
Subscription_Id = os.environ.get("AZURE_CREDENTIALS_SUBSCRIPTION_ID")
Tenant_Id = os.environ.get("AZURE_CREDENTIALS_TENANT_ID")
Client_Id = os.environ.get("AZURE_CREDENTIALS_CLIENT_ID")
Secret = os.environ.get("AZURE_CREDENTIALS_CLIENT_SECRET")
resourceGroup = os.environ.get("AZURE_RESOURCE_GROUP")


# Property to return the credential object
@property
def credential():
    """
    Return the credential object for connecting to Azure
    """
    return ClientSecretCredential(
        tenant_id=Tenant_Id,
        client_id=Client_Id,
        client_secret=Secret,
    )


# Property to return the ComputeManagementClient object
@property
def compute_client():
    """
    Return the ComputeManagementClient object for managing Azure compute resources
    """
    return ComputeManagementClient(
        credential=credential,
        subscription_id=Subscription_Id,
    )


# Property to return the NetworkManagementClient object
@property
def network_client():
    """
    Return the NetworkManagementClient object for managing Azure network resources
    """
    return NetworkManagementClient(credential, Subscription_Id)
