# Copyright (c) Yugabyte, Inc.

import os

from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient

"""
This module contains the credentials for connecting to Azure and clients for managing Azure resources
"""

# Environment variables for Azure credentials
Subscription_Id = os.environ["AZURE_CREDENTIALS_SUBSCRIPTION_ID"]
Tenant_Id = os.environ["AZURE_CREDENTIALS_TENANT_ID"]
Client_Id = os.environ["AZURE_CREDENTIALS_CLIENT_ID"]
Secret = os.environ["AZURE_CREDENTIALS_CLIENT_SECRET"]
resourceGroup = os.environ["AZURE_RESOURCE_GROUP"]


# Property to return the credential object
@property
def credential():
    return ClientSecretCredential(
        tenant_id=Tenant_Id, client_id=Client_Id, client_secret=Secret
    )


# Property to return the ComputeManagementClient object
@property
def compute_client():
    return ComputeManagementClient(
        credential=credential, subscription_id=Subscription_Id
    )


# Property to return the NetworkManagementClient object
@property
def network_client():
    return NetworkManagementClient(credential, Subscription_Id)
