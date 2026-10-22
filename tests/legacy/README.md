# tests/legacy

## Purpose

Frozen copies of `main` code, used as parity oracles. They are `.txt` files so they are never
linted, imported by the product or collected by pytest. Each oracle is the exact output of
`git show main:<path>` for the module named below (the `crc_*` ones are copies of `crc.py`,
`crc/service.py` and `crc/utils.py`). Every refactored module is pinned by a parity test that
loads its oracle under another module name and runs it and the refactored code on the same fake
SDK clients, comparing calls, logs, output and errors. The oracles are kept after the refactor:
behaviour-fix rungs mark each diverging parity case with its bug ID instead of editing them.

| Oracle | Frozen from `main` | Parity test(s) |
|--------|--------------------|----------------|
| `aws_base.txt` | `crc/aws/_base.py` | `tests/test_aws_base_parity.py` |
| `aws_connectivity.txt` | `crc/aws/connectivity.py` | `tests/test_aws_connectivity_parity.py`, `tests/test_aws_base_parity.py` |
| `aws_disk.txt` | `crc/aws/disk.py` | `tests/test_aws_disk_parity.py` |
| `aws_elastic_ips.txt` | `crc/aws/elastic_ips.py` | `tests/test_aws_ip_parity.py` |
| `aws_keypairs.txt` | `crc/aws/keypairs.py` | `tests/test_aws_keypair_parity.py` |
| `aws_kms.txt` | `crc/aws/kms.py` | `tests/test_aws_kms_parity.py` |
| `aws_snapshot.txt` | `crc/aws/snapshot.py` | `tests/test_aws_snapshot_parity.py` |
| `aws_spot_instance_requests.txt` | `crc/aws/spot_instance_requests.py` | `tests/test_aws_spot_instance_requests_parity.py` |
| `aws_vm.txt` | `crc/aws/vm.py` | `tests/test_aws_vm_parity.py` |
| `aws_vpc.txt` | `crc/aws/vpc.py` | `tests/test_aws_vpc_parity.py` |
| `azu_base.txt` | `crc/azu/_base.py` | `tests/test_azure_base_parity.py` |
| `azu_disk.txt` | `crc/azu/disk.py` | `tests/test_azure_disk_parity.py` |
| `azu_ip.txt` | `crc/azu/ip.py` | `tests/test_azure_ip_parity.py` |
| `azu_nic.txt` | `crc/azu/nic.py` | `tests/test_azure_nic_parity.py` |
| `azu_spot_vm.txt` | `crc/azu/spot_vm.py` | `tests/test_azure_spot_instances_parity.py` |
| `azu_vm.txt` | `crc/azu/vm.py` | `tests/test_azure_vm_parity.py` |
| `azu_vm_delete_helpers.txt` | `crc/azu/vm_delete_helpers.py` | `tests/test_azure_vm_delete_helpers_parity.py` |
| `crc_main.txt` | `crc.py` (whole file: argument parser, `main()`, `CRC`) | `tests/test_cli_args_parity.py`, `tests/test_cli_crc_parity.py`, `tests/test_cli_main_parity.py` |
| `crc_service.txt` | `crc/service.py` | `tests/test_crc_service_parity.py`, `tests/test_crc_service_is_old_parity.py` (both through `tests/helpers_crc_service.py`) |
| `crc_utils.txt` | `crc/utils.py` | `tests/test_crc_utils_parity.py` |
| `gcp_base.txt` | `crc/gcp/_base.py` | `tests/test_gcp_base_parity.py` |
| `gcp_disk.txt` | `crc/gcp/disk.py` | `tests/test_gcp_disk_parity.py` |
| `gcp_ip.txt` | `crc/gcp/ip.py` | `tests/test_gcp_ip_parity.py` |
| `gcp_spot_vm.txt` | `crc/gcp/spot_vm.py` | `tests/test_gcp_spot_instances_parity.py` |
| `gcp_vm.txt` | `crc/gcp/vm.py` | `tests/test_gcp_vm_parity.py` |
| `oci_base.txt` | `crc/oci/_base.py` | `tests/test_oci_base_parity.py` |
| `oci_connectivity.txt` | `crc/oci/connectivity.py` | `tests/test_oci_connectivity_parity.py` |
| `oci_disk.txt` | `crc/oci/disk.py` | `tests/test_oci_disk_parity.py` |
| `oci_spot_vm.txt` | `crc/oci/spot_vm.py` | `tests/test_oci_spot_instances_parity.py` |
| `oci_vm.txt` | `crc/oci/vm.py` | `tests/test_oci_vm_parity.py` |

The Slack and InfluxDB oracle is not in this directory: `tests/legacy_crc_notify_reference.txt`
(a frozen copy of the old `crc.py` notify methods) is run by `tests/test_notify_parity.py`.

## Public API

This directory exposes no library, only reference text files (listed in the table above).

## Safety Invariants

- Never edit an oracle file: it must stay byte-identical to `main` at the time it was frozen.
- Oracles run only against fake SDK clients; no network, no credentials.
- Every oracle has a parity test; a new oracle without one is not a safety net.

## Extension Rules

1. A rung that refactors a module freezes `git show main:<path>` here as `<package>_<module>.txt`,
   adds a parity test next to the other tests and a row to the table above.
2. Never delete or edit an oracle (`AGENTS.md` rule 5).
3. A behaviour-fix rung keeps the oracle and marks each diverging parity case with its bug ID
   (`.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`).
