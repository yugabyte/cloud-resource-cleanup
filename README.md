<img src="https://www.yugabyte.com/wp-content/themes/yugabyte/assets/images/yugabyteDB-site-logo-new-blue.svg" align="center" alt="YugabyteDB" width="50%"/>

---------------------------------------

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Documentation Status](https://readthedocs.org/projects/ansicolortags/badge/?version=latest)](https://docs.yugabyte.com/)
[![Ask in forum](https://img.shields.io/badge/ask%20us-forum-orange.svg)](https://forum.yugabyte.com/)
[![Slack chat](https://img.shields.io/badge/Slack:-%23yugabyte_db-blueviolet.svg?logo=slack)](https://communityinviter.com/apps/yugabyte-db/register)
[![Analytics](https://yugabyte.appspot.com/UA-104956980-4/home?pixel&useReferer)](https://github.com/yugabyte/ga-beacon)

# cloud-resource-cleanup

Introducing `cloud-resource-cleanup` (`crc` for short), a powerful tool that allows you to easily **delete** and **stop** resources across different clouds.

* [Core Features](#core-features)
* [Get Started](#get-started)
* [Usage](#usage)
* [Examples](#examples)
* [Notes](#notes)
* [Need Help?](#need-help)
* [Contribute](#contribute)
* [License](#license)
* [Read More](#read-more)

# Core Features
We support below Cloud Providers:
* AWS
  * Delete Orphan Elastic IPs
  * Delete Orphan keypairs
  * Delete VMs (including attached resources such as Disks and NICs)
  * Stop VMs
* Azure
  * Delete Orphan disks
  * Delete VMs (and attached NICs)
  * Delete Orphan public IPs
  * Stop VMs
* GCP
  * Delete Orphan IPs
  * Delete VMs (including attached resources such as Disks and NICs)
  * Stop VMs

In addition to these features, `cloud-resource-cleanup` also includes the following features:

* `Dry Run` mode to view resources that match certain criteria without performing any operations on them.
* Filter resources based on the `age` of the resources.
* Filter resources based on tags and tag values, so that the resources with specific tags can be excluded or included while cleaning up (`--filter_tags` option)
* Delete resources that do not have certain tags and tag values (`--notags` option)
* Keep resources that have certain tags and tag values (`--exception_tags` option)

Get started with `cloud-resource-cleanup` today and see the difference it can make for your cloud infrastructure management.

# Get Started
## Prerequisites
* Python 3.x
* Required python packages
  * boto3 (for AWS)
  * msrestazure (for Azure)
  * azure-mgmt-compute (for Azure)
  * azure-identity (for Azure)
  * azure-mgmt-network (for Azure)
  * google-cloud-compute (for GCP)
  * google-api-python-client (for GCP)

## Installation
```
git clone https://github.com/yugabyte/cloud-resource-cleanup.git
cd cloud-resource-cleanup
pip install -r requirements.txt
```

## Environment Variables
The script requires certain environment variables to be set in order to interact with different cloud providers. The following environment variables must be set before running the script:

### Google Cloud Platform
* `GOOGLE_APPLICATION_CREDENTIALS`: The path to the JSON file containing your GCP service account credentials.
### Amazon Web Services
* `AWS_SECRET_ACCESS_KEY`: The secret access key for your AWS account.
* `AWS_ACCESS_KEY_ID`: The access key ID for your AWS account.
### Azure
* `AZURE_CREDENTIALS_TENANT_ID`: The tenant ID for your Azure subscription.
* `AZURE_CREDENTIALS_SUBSCRIPTION_ID`: The subscription ID for your Azure subscription.
* `AZURE_CREDENTIALS_CLIENT_SECRET`: The client secret for your Azure application.
* `AZURE_CREDENTIALS_CLIENT_ID`: The client ID for your Azure application.
* `AZURE_RESOURCE_GROUP`: The name of the resource group in Azure to use.

It's important to note that you only need to set the environment variables for the cloud providers you are interacting with. For example, if you are only using the script to delete resources on AWS, you would only need to set the `AWS_SECRET_ACCESS_KEY` and `AWS_ACCESS_KEY_ID` environment variables.

You can set the environment variables in your shell by using the `export` command. For example, to set the `GOOGLE_APPLICATION_CREDENTIALS` environment variable, you would use the following command:
```
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/credentials.json"
```
Similarly, you can set the other environment variables as well.
```
export AWS_SECRET_ACCESS_KEY="your_secret_key"
export AWS_ACCESS_KEY_ID="your_access_key"
export AZURE_CREDENTIALS_TENANT_ID="your_tenant_id"
export AZURE_CREDENTIALS_SUBSCRIPTION_ID="your_subscription_id"
export AZURE_CREDENTIALS_CLIENT_SECRET="your_client_secret"
export AZURE_CREDENTIALS_CLIENT_ID="your_client_id"
export AZURE_RESOURCE_GROUP="your_resource_group"
```
You can also add these commands to your shell profile file, such as `~/.bash_profile` or `~/.bashrc`, to ensure that these environment variables are set every time you start a new shell session.

Make sure to replace the placeholders with the appropriate values for your environment.

## Logging
The script will log all deleted resources to a file called `crc.log` in the same directory as the script. The log file will contain the resource type, name, and the date and time it was deleted.

# Usage
To run the script, use the following command:
```
python crc.py --cloud <cloud_name> --operation_type <operation_type> --resource <resource_name> --filter_tags <tags> --exception_tags <tags> --notags <tags> --age <age>
```
* `cloud`: Specify the cloud name (aws, azu, gcp or all). Required.
* `project_id`: Required for gcp
* `resource`: Indicate the type of resource you want to target (e.g. vm, disk, ip, keypair) or specify "all" to target all types of resources. Default: 'all'
* `operation_type`: Specify the type of operation to perform on the resource (delete or stop). Default: 'delete'
* `dry_run`: Enabling this option will only list resources that match the specified criteria without performing any operations on them. Use the -d or --dry_run flag to enable this feature. If this option is not specified, the script will perform the operation specified by the `operation_type` argument.
* `resource_states`: Specify the state of the resource you want to delete. Only applicable for virtual machines (VMs) and can be either 'RUNNING' or 'STOPPED'. Default: ['RUNNING']. This means that by default, only running VMs will be considered for deletion.
* `filter_tags`: Use this option to filter resources based on their tags. Leave value of Key empty to indicate `any` value. If not specified all available resources will be picked. **This option does not apply to AWS keypairs and GCP IPs**. esources will be included if `any` of the key with `any` value pair matches. (e.g. {'test_task': ['test', 'stress-test']}).
* `exception_tags`: Use this option to exclude resources based on their tags. **Does not apply if `filter_tags` is not set**. Leave the value of Key empty to indicate `any` value. Resources will be excluded if `any` of the key with `any` value pair matches. **This option does not apply to AWS keypairs and GCP IPs** (e.g. {'test_task': ['test-keep-resources', 'stress-test-keep-resources']}).
* `name_regex`: Use this option to filter resources based on regular expressions applied to their names. If not specified, all available resources will be picked. **This option only applies to AWS keypairs and GCP IPs**. Resources will be included if `any` of the specified regular expressions match their names. (e.g. ['perftest_', 'feature_']).
* `exception_regex`: Use this option to exclude resources based on regular expressions applied to their names. **This option does not apply if `name_regex` is not set**. Resources will be excluded if `any` of the specified regular expressions match their names. **This option only applies to AWS keypairs and GCP IPs** (e.g. ['perftest_keep_resources', 'feature_keep_resources'])
* `age`: Use this option to specify an age threshold for resources when deleting resources other than `IPs` (e.g. {'days': 3, 'hours': 12}). 
* `notags`: Use this option to filter resources based on tags that are not present. Leave the value of Key empty to indicate `any` value. Resources will be excluded if `all` of the key-value pair match. This option can be used independently of the `filter_tags` option. **This option does not apply to AWS keypairs and GCP IPs**. Format: -t or --notags {'test_task': ['test'], 'test_owner': []}

# Examples
1. To delete all running AWS VMs that are older than 3 days and 12 hours and have the tag `test_task` with the value `stress-test`:
```
python crc.py --cloud aws --resource vm --filter_tags {'test_task': ['stress-test']} --age {'days': 3, 'hours': 12}
```

2. To stop all Azure VMs that are older than 2 days and have the tag `test_task` with the value `stress-test`:
```
python crc.py --cloud azu --resource vm --filter_tags {'test_task': ['stress-test']} --age {'days': 2} --operation_type stop
```

3. To delete all GCP disks that are older than 2 days and have the tag `test_task` with the value `stress-test` and project_id as 'test_project':
```
python crc.py --cloud gcp --project_id test_project --resource disk --filter_tags {'test_task': ['stress-test']} --age {'days': 2}
```

4. To stop all VMs across all clouds that have the tag `test_task` with the value `stress-test` and `perf-test` and do not have the tag `test_owner`:
```
python crc.py --cloud all --resource vm --filter_tags {'test_task': ['stress-test', 'perf-test']} --notags {'test_owner': []} --operation_type stop
```

5. To perform a dry run of the script and list all VMs across all clouds that have been created in the last 2 days and do not have the tag `test_task`:
```
python crc.py --cloud all --resource vm --age {'days': 2} --notags {'test_task': []} --dry_run
```
# Notes
* Please make sure to test this script in a non-production environment before using it in a production environment. This script will delete resources permanently and cannot be undone.
* Try using the dry run mode feature to avoid unfortunate circumstances.
* If filters are not specified, the tool will consider every resource for cleanup.
* Use the `filter_tags`, `exception_tags` and `notags` options in JSON format (`Dict[str, List[str]]`)
* Use the `resource_states`, `name_regex`, and `exception_regex` options in list format (`List[str]`)
* When giving a value to the `resource_states` parameter, be aware that different cloud libraries have different formats. (For eg. `running` state for AWS, AZU but `RUNNING` for GCP)
* Use the `age` option in JSON format. Example: `{"days": 60}` (`Dict[str, int]`)

# Need Help?

* You can ask questions, find answers, and help others on our Community [Slack](https://communityinviter.com/apps/yugabyte-db/register), [Forum](https://forum.yugabyte.com), [Stack Overflow](https://stackoverflow.com/questions/tagged/yugabyte-db), as well as Twitter [@Yugabyte](https://twitter.com/yugabyte)

* Please use [GitHub issues](https://github.com/yugabyte/cloud-resource-cleanup/issues) to report issues or request new features.

# Contribute

As an an open-source project with a strong focus on the user community, we welcome contributions as GitHub pull requests. See our [Contributor Guides](https://docs.yugabyte.com/preview/contribute/) to get going. Discussions and RFCs for features happen on the design discussions section of our [Forum](https://forum.yugabyte.com).

# License

Source code in this repository is licensed under the Apache License 2.0. A copy of license can be found in the [LICENSE.md](LICENSE.md) file.

# Read More

* To see our updates, go to [The Distributed SQL Blog](https://blog.yugabyte.com/).
* For an in-depth design and the YugabyteDB architecture, see our [design specs](https://github.com/yugabyte/yugabyte-db/tree/master/architecture/design).
* Tech Talks and [Videos](https://www.youtube.com/c/YugaByte).
* See how YugabyteDB [compares with other databases](https://docs.yugabyte.com/preview/faq/comparisons/).
