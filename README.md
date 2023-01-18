# cloud-resource-cleanup

Introducing `cloud-resource-cleanup` (`crc` for short), a powerful tool that allows you to easily **delete** and **stop** resources across different clouds. Whether you're working with **AWS**, **Azure**, or **GCP**, this tool has got you covered. With `cloud-resource-cleanup`, you can delete **Elastic IPs**, **keypairs**, and **VMs** (including attached resources such as **disks** and **NICs**) from **AWS**, **disks**, **VMs**, **NICs**, and **public IPs** from **Azure**, and **IPs**, **VMs** (including attached resources such as **disks** and **NICs**) from **GCP**. Additionally, you can also **stop** **VMs** from **AWS**, **Azure**, and **GCP**. The tool also includes a feature that allows you to filter resources based on the **age** of the resources. This makes it easy for you to identify and delete resources that are no longer needed, saving you time and money. Get started with `cloud-resource-cleanup` today and see the difference it can make for your cloud infrastructure management.

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
git clone https://github.com/<username>/cloud-resource-cleanup.git
cd cloud-resource-cleanup
pip install -r requirements.txt
```

## Usage
To use `crc`, you will need to provide your AWS, Azure, and/or GCP credentials. You can do this by setting the appropriate environment variables.

To run the script, use the following command:
```
python crc.py --cloud <cloud_name> --project_id <project_id> --resource <resource_name> --filter_tags <tags> --exception_tags <tags>
```
* `cloud`: Specify the cloud name (aws, azu, gcp or all)
* `project_id`: Project id is mandatory for gcp.
* `resource`: Specify the resource name (vm, disk, ip, keypair or all). Default : 'all'
* `operation_type`: Type of operation to perform on resource (delete or stop). Default: 'delete'
* `resource_states`: Resource State to consider for Delete. It is applicable only for VMs (['RUNNING', 'STOPPED']). Default: ['RUNNING']
* `filter_tags`: Specify the tags to filter the resources. Doesn't apply to AWS keypairs and GCP IPs (e.g. {'test_task': ['test', 'stress-test']})
* `exception_tags`: Specify the tags to exclude the resources. Doesn't apply if `filter_tags` is empty. (e.g. {'test_task': ['test-keep-resources', 'stress-test-keep-resources']})
* `name_regex`: Name Regex used to filter resources. Only applies to AWS keypairs and GCP IPs (e.g. perftest_)
* `exception_regex`: Exception Regex to exclude resources. Doesn't apply if `name_regex` is empty (e.g. perftest_keep_resources)



## Logging
The script will log all deleted resources to a file called crc.log in the same directory as the script. The log file will contain the resource type, name, and the date and time it was deleted.

## Note
* Please make sure to test this script in a non-production environment before using it in a production environment. This script will delete resources permanently and cannot be undone.
* Use filter_tags and exception_tags in json format (Dict[str, List[str]])
* Use name_regex and exception_regex in list format (List[str])
* Use age in json format. Example : {"days":60} (Dict[str, int])

## Contributing
Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.
