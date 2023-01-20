# cloud-resource-cleanup

Introducing `cloud-resource-cleanup` (`crc` for short), a powerful tool that allows you to easily **delete** and **stop** resources across different clouds. Whether you're working with **AWS**, **Azure**, or **GCP**, this tool has got you covered. With `cloud-resource-cleanup`, you can:

* Delete Orphan Elastic IPs, Orphan keypairs, and VMs (including attached resources such as disks and NICs) from AWS
* Delete Orphan disks, VMs, NICs and Orphan public IPs from Azure
* Delete Orphan IPs, VMs (including attached resources such as disks and NICs) from GCP
* Stop VMs from AWS, Azure, and GCP

In addition to these features, `cloud-resource-cleanup` also includes a Monitor only mode which allows you to view resources that match certain criteria without performing any operations on them. This is useful for identifying resources that you may want to delete or stop later. The tool also includes a feature that allows you to filter resources based on the age of the resources. This makes it easy for you to identify and delete resources that are no longer needed, saving you time and money. Get started with `cloud-resource-cleanup` today and see the difference it can make for your cloud infrastructure management.

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

## Usage
To use `crc`, you will need to provide your AWS, Azure, and/or GCP credentials. You can do this by setting the appropriate environment variables.

To run the script, use the following command:
```
python crc.py --cloud <cloud_name> --project_id <project_id> --resource <resource_name> --filter_tags <tags> --exception_tags <tags>
```
* `cloud`: Specify the cloud name (aws, azu, gcp or all). Required.
* `project_id`: Required for gcp
* `resource`: Specify the resource name (vm, disk, ip, keypair or all). Default : 'all'
* `operation_type`: Type of operation to perform on resource (delete or stop). Default: 'delete'
* `monitor`: Enable monitor-only mode. This will only list resources that match the criteria, but will not perform any operations on them. Use -m or --monitor. If not specified, the script will perform the operation specified by `operation_type`
* `resource_states`: Resource State to consider for Delete. It is applicable only for VMs (['RUNNING', 'STOPPED']). Default: ['RUNNING']
* `filter_tags`: Specify the tags to filter the resources. Doesn't apply to AWS keypairs and GCP IPs (e.g. {'test_task': ['test', 'stress-test']})
* `exception_tags`: Specify the tags to exclude the resources. Doesn't apply if `filter_tags` is empty. (e.g. {'test_task': ['test-keep-resources', 'stress-test-keep-resources']})
* `name_regex`: Name Regex used to filter resources. Only applies to AWS keypairs and GCP IPs (e.g. ['perftest_', 'feature_'])
* `exception_regex`: Exception Regex to exclude resources. Doesn't apply if `name_regex` is empty (e.g. ['perftest_keep_resources', 'feature_keep_resources'])
* `age`: Age Threshold for resources is mandatory argument while deleting resources other than IPs (e.g. {'days': 3, 'hours': 12})


## Examples
Delete all the VMs in AWS which are tagged with 'task:qa', exclude 'type:prod' and are older than 10 days
```
python crc.py --cloud aws --resource vm --filter_tags '{"task":["qa"]}' --exception_tags '{"type":["prod"]}' --age '{"days":10}'
```

Delete all the ips in GCP which have regex ["test_", "qa_"] and don't have ["prod", "dont_delete"] in the name and are older than 2 days and 6 hours
```
python crc.py --cloud gcp --project_id <project_id> --resource ip --name_regex '["test_", "qa_"]' --exception_regex '["prod", "dont_delete"]' --age '{"days":2, "hours":6}'
```

## Logging
The script will log all deleted resources to a file called crc.log in the same directory as the script. The log file will contain the resource type, name, and the date and time it was deleted.

## Note
* Please make sure to test this script in a non-production environment before using it in a production environment. This script will delete resources permanently and cannot be undone.
* Try using Monitor mode feature to avoid unfortunate circumstances
* If filters are not specified the tool will consider every Resource for cleanup
* Use filter_tags and exception_tags in json format (Dict[str, List[str]])
* Use resource_states, name_regex and exception_regex in list format (List[str])
* Use age in json format. Example : {"days":60} (Dict[str, int])

## Contributing
Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.
