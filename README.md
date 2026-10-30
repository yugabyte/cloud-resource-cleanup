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
  * Delete unattached EBS volumes (opt-in: only with `--cloud aws --resource disk`)
  * Delete VMs (including attached resources such as Disks and NICs)
  * Stop VMs
  * Delete KMS
  * Delete EBS snapshots older than 90 days
  * Delete Spot Instance Requests & associated Instances (one-time **and** persistent). Cancels the request **before** terminating the instance; deletes `DeleteOnTermination=false` EBS that CRC sees while the instance is still running/shutting-down (tagged for retry). Tags are matched on the **spot request** (instances may be untagged).
* Azure
  * Delete Orphan disks
  * Delete VMs (and attached NICs)
  * Delete Orphan public IPs
  * Delete Orphan NICs
  * Delete Spot VMs (stopped/deallocated) and leftover disks with `delete_option=Detach`
  * Stop VMs
* GCP
  * Delete Orphan Disks
  * Delete Orphan IPs
  * Delete VMs (including attached resources such as Disks and NICs)
  * Delete Spot / preemptible VMs and leftover disks with `autoDelete=false`
  * Stop VMs
* OCI
  * Delete VMs
  * Delete preemptible (spot-like) instances and leftover attached block volumes
  * Delete orphan disks / boot volumes

In addition to these features, `cloud-resource-cleanup` also includes the following features:

* `Dry Run mode`: Preview resources that match your criteria without actually performing any operations on them
* `Slack notifications`: Get notifications on your Slack channel.
* `InfluxDB support`: Receive data on the execution of the script by specifying a connection to InfluxDB
* `Age-based filtering`: Filter resources based on their `age`, considering the timezone.
* `Tag-based filtering`: Choose to include resources with specific tags. (`--filter_tags` option)
* `Delete untagged resources`: Option to delete resources that don't have specified tags. (`--notags` option)
* `Keep tagged resources`: Option to keep resources that have specific tags. (`--exception_tags` option)
* `Custom Age Override`: Specify a custom age for a resource using the `--custom_age_tag_key` option. A custom age tag that is present but empty, unparseable, zero or negative keeps the resource (it is never deleted for that tag).
* `Tag scan`: List VMs that carry a tag with `--scan_tag KEY=VALUE` (read-only, see [Usage](#usage)).

Get started with `cloud-resource-cleanup` today and see the difference it can make for your cloud infrastructure management.

# Get Started
## Prerequisites
* Python 3.12
* Required python packages
  * boto3 (for AWS)
  * msrestazure (for Azure)
  * azure-mgmt-compute (for Azure)
  * azure-identity (for Azure)
  * azure-mgmt-network (for Azure)
  * google-cloud-compute (for GCP)
  * google-api-python-client (for GCP)
  * oci (for OCI)
  * slack_sdk (for Slack notifications) and influxdb-client (for InfluxDB)

`requirements.txt` lists all of them.

## Installation
```
git clone https://github.com/yugabyte/cloud-resource-cleanup.git
cd cloud-resource-cleanup
pip install -r requirements.txt -c constraints.txt
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
* `AZURE_RESOURCE_GROUP`: The name of the resource group to use in Azure. If you don't want to set this as an environment variable or want to use a different resource group than the one set in the environment variable, you can specify it using the `--resource_group` option. Note that the `--resource_group` option will take priority over the environment variable.

### Oracle Cloud Infrastructure
* `OCI_PRIVATE_KEY_CONTENT` or `OCI_PRIVATE_KEY_FILE`: The API signing key, as content or as a path to the key file. One of the two must be set.
### Slack (Optional)
* `SLACK_BOT_TOKEN`: The API token for the Slack bot you want to use to receive notifications.
### InfluxDB (Optional)
* `INFLUXDB_TOKEN`: The API token for the InfluxDB you want to use to receive data.

It's important to note that you only need to set the environment variables for the cloud providers you are interacting with. For example, if you are only using the script to delete resources on AWS, you would only need to set the `AWS_SECRET_ACCESS_KEY` and `AWS_ACCESS_KEY_ID` environment variables.

You can set the environment variables in your shell by using the `export` command. For example, to set the `GOOGLE_APPLICATION_CREDENTIALS` environment variable, you would use the following command:
```
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/credentials.json"
```

You can also add these commands to your shell profile file, such as `~/.bash_profile` or `~/.bashrc`, to ensure that these environment variables are set every time you start a new shell session.

Make sure to replace the placeholders with the appropriate values for your environment.

## Logging
The script writes its log to `logs/crc.log`, relative to the directory you run it from (the `logs` directory is created if missing). The file is overwritten on every run. It holds the same messages as the console plus debug output, with the time, source file and line, and level of each message; the resources a run deleted (or would delete, in dry run) are logged in it.

# Usage
To run the script, use the following command:
```
python crc.py --cloud <cloud_name> --operation_type <operation_type> --resource <resource_name> --filter_tags <tags> --exception_tags <tags> --notags <tags> --age <age> --slack_channel <slack_channel> --influxdb <influxdb_connection_details>
```
* `cloud` (`-c`): Specify the cloud name (aws, azure, gcp, oci or all). Required.
* `project_id` (`-p`): Required for gcp, including `--cloud all`, which runs GCP too
* `resource` (`-r`): Indicate the type of resource you want to target: `disk`, `ip`, `keypair`, `vm`, `kms`, `nic`, `snapshot`, `spot_instance_requests` (AWS only: cancel the request, then terminate), `spot_instances` (Azure, GCP and OCI Spot or preemptible VMs) or `all`. Default: 'all'. `keypair`, `kms` and `snapshot` are AWS only, `nic` is Azure only and `ip` is not available on OCI. A pair that is not supported raises before anything is deleted; under `--cloud all` it is skipped only when no supported pair follows it. `--resource all` needs `--cloud all`, and `--cloud all` cannot be combined with `--resource all` or `--resource disk` (see below). AWS EBS volume deletion is opt-in and needs both `--cloud aws` and `--resource disk`. Any other combination that would hit the aws+disk path raises (it does not skip and continue), so `--cloud all` / `--resource all` cannot unlock other destructive AWS cleanups as a side effect. GCP and OCI `spot_instances` need an explicit `--cloud gcp` / `--cloud oci`; under `--cloud all` only Azure `spot_instances` runs.
* `resource_group`: Use this option to specify the resource group for Azure. If this flag is provided, the script will only operate on resources within the specified resource group. This allows for more precise targeting of resources in your Azure environment.
* `operation_type` (`-o`): Specify the type of operation to perform on the resource (delete or stop). Default: 'delete'. `stop` is supported only with `--resource vm`.
* `dry_run`: Enabling this option will only list resources that match the specified criteria without performing any operations on them. Use the `-d` or `--dry_run` flag to enable this feature. If this option is not specified, the script will perform the operation specified by the `operation_type` argument.
* `resource_states` (`-s`): Specify the state of the resource you want to delete. Only applicable for virtual machines (VMs) and can be either 'RUNNING' or 'STOPPED'. Default: ['RUNNING']. This means that by default, only running VMs will be considered for deletion.
* `filter_tags` (`-f`): Use this option to filter resources based on their tags. Leave value of Key empty to indicate `any` value. If not specified all available resources will be picked. **This option does not apply to AWS keypairs, AWS snapshots, GCP IPs and Azure NICs**. Resources will be included if `any` (or `all`, depending on the cleaner) of the key with `any` value pair matches. (e.g. {'test_task': ['test', 'stress-test']}).
* `exception_tags` (`-e`): Use this option to exclude resources based on their tags. Leave the value of Key empty to indicate `any` value. Resources will be excluded if `any` of the key with `any` value pair matches. **This option does not apply to AWS keypairs, GCP IPs and Azure NICs** (e.g. {'test_task': ['test-keep-resources', 'stress-test-keep-resources']}).
* `name_regex` (`-n`): Use this option to filter resources based on regular expressions applied to their names. If not specified, all available resources will be picked (except Azure NICs, where an empty `name_regex` selects nothing). **This option applies to AWS keypairs, AWS disks, GCP IPs, GCP disks and Azure NICs**. Resources will be included if `any` of the specified regular expressions match their names. (e.g. ['perftest_', 'feature_']).
* `exception_regex` (`-x`): Use this option to exclude resources based on regular expressions applied to their names. Resources will be excluded if `any` of the specified regular expressions match their names. **This option applies to AWS keypairs, AWS disks, GCP IPs, GCP disks and Azure NICs** (e.g. ['perftest_keep_resources', 'feature_keep_resources'])
* `age` (`-a`): Use this option to specify an age threshold for resources when deleting resources other than `IPs` (e.g. {'days': 3, 'hours': 12}). A real (non dry-run) `--resource vm` run on AWS requires a positive `age` (BUG-213). It is not used for GCP disks (use `--detach_age`), NICs, and `snapshot` always uses a fixed 90-day threshold. `--resource spot_instance_requests` and `--resource spot_instances` require a positive `age`.
* `custom_age_tag_key`: Define a custom tag key for the age threshold of resources on your cloud resource. This tag is ignored for `IPs` and `Keypairs` (e.g. value of the tag {'days': 3, 'hours': 12}; an integer value is read as days). A tag that is present but empty, unparseable, zero or negative keeps the resource instead of falling back to `age`.
* `max_age`: Specify the maximum age threshold for resources. This value will override the value of the tag specified by `custom_age_tag_key` (e.g. "{'days': 14, 'hours': 12}"). It is exported as the `MAX_AGE` environment variable for the run; never pass a zero duration.
* `notags` (`-t`): Use this option to filter resources based on tags that are not present. Leave the value of Key empty to indicate `any` value. Resources will be excluded if `all` of the key-value pair match. This option can be used independently of the `filter_tags` option. **This option does not apply to AWS keypairs, AWS KMS, AWS snapshots, GCP IPs and Azure NICs**. Format: -t or --notags {'test_task': ['test'], 'test_owner': []}
* `slack_channel` (`-m`): Use this option to specify a Slack channel to receive notifications about the execution of the script. Only works if specified, and needs `SLACK_BOT_TOKEN`.
* `detach_age`: Use this option to specify the detached age for filtering disks. This option only works for GCP and AWS disk deletions. **For AWS only**, `age` and `detach_age` accept only `days` and/or `hours`, and the duration must be greater than zero (that validation lives in `crc/aws/disk/settings.py`; GCP does not apply the same check). For AWS, `detach_age` cannot exceed CloudWatch's 455-day metric retention; it is inferred from AWS/EBS CloudWatch metrics (`VolumeIdleTime`, `VolumeReadOps`, `VolumeWriteOps`), so the caller needs `cloudwatch:GetMetricData`. Those metrics are published only while a volume is attached to a *running* instance — the gate is not a last-detach timestamp and is blind to attachments on stopped instances. An AWS disk deletion requires `age` (measured from volume creation), `detach_age`, or both. CreateTime is always applied: with only `detach_age`, the larger of `detach_age` and the custom age tag is the floor; with only `age`, the floor is the custom age tag if present, else `age`; with both, the floor is that age **plus** `detach_age`, because the CloudWatch check cannot see attachments to stopped instances. A custom age tag is validated the same way. Untagged volumes are skipped unless `--notags` is set. On GCP, `--age` is ignored, `detach_age` (or the custom age label, which replaces it) is the only age gate, and leaving both out deletes every matching detached disk.
* `slack_notify_users`: Use this option to tag the user in the Slack notification. It is mandatory to pass `--slack_user_label` with this flag. This option only works for GCP disks deletions.
* `slack_user_label`: Use this option to specify the label to look up in the GCP disks. This option only works for GCP disks deletions.
* `influxdb` (`-i`): Use this option to specify InfluxDB connection details. The argument takes a dictionary value, with keys 'url', 'org', 'bucket', and an optional key 'resource_suffix'. Example usage: -i or --influxdb {'url': 'http://localhost:8086', 'org': 'Test', 'bucket': 'CRC', 'resource_suffix': 'test'}. Only works if specified, and needs `INFLUXDB_TOKEN`.
* `kms_pending_window`: Use this option to specify the number of days before the key actually gets deleted. The number of days
must be between 7 to 30 inclusive. Default: 7.
* `kms_key_description`: Use this option to match specific string in kms key description. Required with `--resource kms` and `--resource all`.
* `kms_user`: Use this option to specify AWS ARN of user for which keys will be deleted. Required with `--resource kms` and `--resource all`.
* `scan_tag`: Read-only scan: list VMs that carry the tag `KEY=VALUE` (for example `--scan_tag auto_clean=false`) in the selected clouds and exit without deleting anything. The run is forced into dry run. Only AWS VMs can be scanned; the other clouds report that the scan is not implemented. Combine it with `--resource vm` (the default `--resource all` is rejected for a single cloud). An empty value raises an error.


# Examples
1. Delete all running AWS VMs that are older than `3 days and 12 hours` and have the tag `test_task` with the value `stress-test`. Additionally, consider the custom age specified in the `retention_age` tag, ensuring the value does not exceed the `max_age` of `14 days`.
```
python crc.py --cloud aws --resource vm --filter_tags "{'test_task': ['stress-test']}" --age "{'days': 3, 'hours': 12}" --custom_age_tag_key retention_age --max_age "{'days': 14}"
```

2. To stop all Azure VMs in `test-rg` resource group that are older than 2 days and have the tag `test_task` with the value `stress-test`:
```
python crc.py --cloud azure --resource_group test-rg --resource vm --filter_tags "{'test_task': ['stress-test']}" --age "{'days': 2}" --operation_type stop
```

3. To delete all GCP disks that have been detached for at least 14 days and have the tag `test_task` with the value `stress-test` and project_id as 'test_project':
```
python crc.py --cloud gcp --project_id test_project --resource disk --filter_tags "{'test_task': ['stress-test']}" --detach_age "{'days': 14}"
```

4. To stop all VMs across all clouds that have the tag `test_task` with the value `stress-test` and `perf-test` and do not have the tag `test_owner`:
```
python crc.py --cloud all --project_id <project> --resource vm --filter_tags "{'test_task': ['stress-test', 'perf-test']}" --notags "{'test_owner': []}" --operation_type stop
```

5. To perform a dry run of the script and list all VMs across all clouds that are older than 2 days and do not have the tag `test_task`:
```
python crc.py --cloud all --project_id <project> --resource vm --age "{'days': 2}" --notags "{'test_task': []}" --dry_run
```

6. To Delete AWS KMS that are older than 3 days
```
python3 crc.py --cloud aws --resource kms --kms_key_description <key-description> --kms_user <AWS-ARN> --age "{'days': 3}"
```

7. To delete all AWS Spot Instance Requests (including Type=persistent) & associated Instances that are older than 3 days and 12 hours and have the tag `test_task` with the value `stress-test`. Tags are matched on the spot request. Order is cancel request → terminate instance → delete leftover volumes with DeleteOnTermination=false:
```
python crc.py --cloud aws --resource spot_instance_requests --filter_tags "{'test_task': ['stress-test']}" --age "{'days': 3, 'hours': 12}"
```

8. Azure / GCP / OCI Spot (or preemptible) VMs — use `--resource spot_instances`:
```
python crc.py --cloud azure --resource_group <rg> --resource spot_instances --filter_tags "{'yb_task': ['itest']}" --age "{'days': 3}"
python crc.py --cloud gcp --project_id <project> --resource spot_instances --filter_tags "{'yb_task': ['itest']}" --age "{'days': 3}"
python crc.py --cloud oci --resource spot_instances --filter_tags "{'yb_task': ['itest']}" --age "{'days': 3}"
```

9. To delete unattached AWS EBS volumes with `--age` 3 days and `--detach_age` 7 days (with both set, CreateTime must be at least 3 + 7 = 10 days old and CloudWatch must show no running-instance attachment activity in the last 7 days; opt-in: needs `--cloud aws --resource disk`):
```
python crc.py --cloud aws --resource disk --filter_tags "{'test_task': ['stress-test']}" --age "{'days': 3}" --detach_age "{'days': 7}"
```

10. To delete Azure NICs in `test-rg` whose name matches a pattern:
```
python crc.py --cloud azure --resource_group test-rg --resource nic --name_regex "['perftest_']"
```

11. To delete AWS EBS snapshots older than 90 days (the threshold is fixed; `--age` is not used), previewing first:
```
python crc.py --cloud aws --resource snapshot --dry_run
```

12. To list (read-only) the AWS VMs that carry the tag `auto_clean=false`:
```
python crc.py --cloud aws --resource vm --scan_tag auto_clean=false
```

# Notes
* Please make sure to test this script in a non-production environment before using it in a production environment. This script will delete resources permanently and cannot be undone.
* Try using the dry run mode feature to avoid unfortunate circumstances.
* If filters are not specified, the tool will consider every resource for cleanup.
* Use the `filter_tags`, `exception_tags` and `notags` options in JSON format (`Dict[str, List[str]]`)
* Use the `resource_states`, `name_regex`, and `exception_regex` options in list format (`List[str]`)
* When giving a value to the `resource_states` parameter, be aware that different cloud libraries have different formats. (For eg. `running` state for AWS, AZU but `RUNNING` for GCP)
* Use the `age` and `influxdb` option in JSON format. Example: `{"days": 60}` (`Dict[str, int]`)
* `stop` works only with `--resource vm`.

# Need Help?

* You can ask questions, find answers, and help others on our Community [Slack](https://communityinviter.com/apps/yugabyte-db/register), [Forum](https://forum.yugabyte.com), [Stack Overflow](https://stackoverflow.com/questions/tagged/yugabyte-db), as well as Twitter [@Yugabyte](https://twitter.com/yugabyte)

* Please use [GitHub issues](https://github.com/yugabyte/cloud-resource-cleanup/issues) to report issues or request new features.

# Contribute

As an an open-source project with a strong focus on the user community, we welcome contributions as GitHub pull requests. See our [Contributor Guides](https://docs.yugabyte.com/preview/contribute/) to get going. Discussions and RFCs for features happen on the design discussions section of our [Forum](https://forum.yugabyte.com).

Notes on how destructive cleanup behaves (and known gaps) live in
[CLEANUP_SAFETY.md](CLEANUP_SAFETY.md). Update that file when a PR changes those facts.

# License

Source code in this repository is licensed under the Apache License 2.0. A copy of license can be found in the [LICENSE.md](LICENSE.md) file.

# Read More

* To see our updates, go to [The Distributed SQL Blog](https://blog.yugabyte.com/).
* For an in-depth design and the YugabyteDB architecture, see our [design specs](https://github.com/yugabyte/yugabyte-db/tree/master/architecture/design).
* Tech Talks and [Videos](https://www.youtube.com/c/YugaByte).
* See how YugabyteDB [compares with other databases](https://docs.yugabyte.com/preview/faq/comparisons/).
