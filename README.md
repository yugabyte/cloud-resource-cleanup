# cloud-resource-cleanup

## Introduction
This repository contains a Python tool called `cloud-resource-cleanup` (`crc` for short) that allows for cleaning up resources from AWS, Azure, and GCP. It allows for deleting resources based on tags or resource name regex, and also allows for exception tags or name regex. This tool can clean up Elastic IPs, keypairs, and VMs from AWS, disks, VMs, NICs and public IPs from Azure, and IPs, VMs and disks from GCP. It also has a good logging mechanism for tracking the resources that have been deleted.

## Installation
To install crc, you will need to have the following packages installed:

```
boto3
msrestazure
azure-mgmt-compute
azure-identity
azure-mgmt-network
google-cloud-compute
google-api-python-client
```
You can install these packages using pip:
```
pip install boto3 msrestazure azure-mgmt-compute azure-identity azure-mgmt-network google-cloud-compute google-api-python-client
```
## Usage
To use `crc`, you will need to provide your AWS, Azure, and/or GCP credentials. You can do this by setting the appropriate environment variables or by providing them as arguments to the script.

To run the script, use the following command:
```
python crc.py --tags tag1,tag2 --except_tags tag3,tag4 --name_regex regex1 --except_name_regex regex2
The script accepts the following arguments:

--tags: a comma-separated list of tags to match when deleting resources (e.g. tag1,tag2)
--except_tags: a comma-separated list of tags to exclude when deleting resources (e.g. tag3,tag4)
--name_regex: a regular expression to match against resource names when deleting resources (e.g. regex1)
--except_name_regex: a regular expression to exclude when deleting resources (e.g. regex2)
```
The script alows cleaning up of Elastic IPs, keypairs, and VMs from AWS, disks, VMs, NICs and public IPs from Azure, and IPs, VMs, and disks from GCP.

## Logging
The script will log all deleted resources to a file called crc.log in the same directory as the script. The log file will contain the resource type, name, and the date and time it was deleted.

## Note
Please make sure to test this script in a non-production environment before using it in a production environment. This script will delete resources permanently and cannot be undone. Also note that this tool is only available as a Python client and not as a command-line interface.
