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
| `aws_base.txt` | `main:crc/aws/_base.py` | `tests/test_aws_base_parity.py` |
| `aws_connectivity.txt` | `main:crc/aws/connectivity.py` | `tests/test_aws_connectivity_parity.py`, `tests/test_aws_base_parity.py` |
| `aws_disk.txt` | `main:crc/aws/disk.py` | `tests/test_aws_disk_parity.py` |
| `aws_elastic_ips.txt` | `main:crc/aws/elastic_ips.py` | `tests/test_aws_ip_parity.py` |
| `aws_keypairs.txt` | `main:crc/aws/keypairs.py` | `tests/test_aws_keypair_parity.py` |
| `aws_kms.txt` | `main:crc/aws/kms.py` | `tests/test_aws_kms_parity.py` |
| `aws_snapshot.txt` | `main:crc/aws/snapshot.py` | `tests/test_aws_snapshot_parity.py` |
| `aws_spot_instance_requests.txt` | `main:crc/aws/spot_instance_requests.py` | `tests/test_aws_spot_instance_requests_parity.py` |
| `aws_vm.txt` | `main:crc/aws/vm.py` | `tests/test_aws_vm_parity.py` |
| `aws_vpc.txt` | `main:crc/aws/vpc.py` | none (`--resource vpc` removed, BUG-083; only the `crc_main.txt` binding reads it; the owner retires it) |
| `azu_base.txt` | `main:crc/azu/_base.py` | `tests/test_azure_base_parity.py` |
| `azu_disk.txt` | `main:crc/azu/disk.py` | `tests/test_azure_disk_parity.py` |
| `azu_ip.txt` | `main:crc/azu/ip.py` | `tests/test_azure_ip_parity.py` |
| `azu_nic.txt` | `main:crc/azu/nic.py` | `tests/test_azure_nic_parity.py` |
| `azu_spot_vm.txt` | `main:crc/azu/spot_vm.py` | `tests/test_azure_spot_instances_parity.py` |
| `azu_vm.txt` | `main:crc/azu/vm.py` | `tests/test_azure_vm_parity.py` |
| `azu_vm_delete_helpers.txt` | `main:crc/azu/vm_delete_helpers.py` | `tests/test_azure_vm_delete_helpers_parity.py` |
| `crc_main.txt` | `crc.py` (whole file: argument parser, `main()`, `CRC`) | `tests/test_cli_args_parity.py`, `tests/test_cli_crc_parity.py`, `tests/test_cli_main_parity.py` |
| `crc_service.txt` | `main:crc/service.py` | `tests/test_crc_service_parity.py`, `tests/test_crc_service_is_old_parity.py` (both through `tests/helpers_crc_service.py`) |
| `crc_utils.txt` | `main:crc/utils.py` | `tests/test_crc_utils_parity.py` |
| `gcp_base.txt` | `main:crc/gcp/_base.py` | `tests/test_gcp_base_parity.py` |
| `gcp_disk.txt` | `main:crc/gcp/disk.py` | `tests/test_gcp_disk_parity.py` |
| `gcp_ip.txt` | `main:crc/gcp/ip.py` | `tests/test_gcp_ip_parity.py` |
| `gcp_spot_vm.txt` | `main:crc/gcp/spot_vm.py` | `tests/test_gcp_spot_instances_parity.py` |
| `gcp_vm.txt` | `main:crc/gcp/vm.py` | `tests/test_gcp_vm_parity.py` |
| `oci_base.txt` | `main:crc/oci/_base.py` | `tests/test_oci_base_parity.py` |
| `oci_connectivity.txt` | `main:crc/oci/connectivity.py` | `tests/test_oci_connectivity_parity.py` |
| `oci_disk.txt` | `main:crc/oci/disk.py` | `tests/test_oci_disk_parity.py` |
| `oci_spot_vm.txt` | `main:crc/oci/spot_vm.py` | `tests/test_oci_spot_instances_parity.py` |
| `oci_vm.txt` | `main:crc/oci/vm.py` | `tests/test_oci_vm_parity.py` |

The Slack and InfluxDB oracle is not in this directory: `tests/legacy_crc_notify_reference.txt`
(a frozen copy of the old `crc.py` notify methods) is run by `tests/test_notify_parity.py`.

## Retiring an oracle

The oracles are a migration tool: they proved the refactor kept the behaviour of `main`. They
are not a permanent requirement. A module may be moved off its oracle onto behaviour tests one
kind at a time, when every parity case of that module is re-expressed as an explicit expected
outcome (the calls, results, log lines and errors written out in the test, not compared with
the oracle). Do this in a PR that keeps the number of cases equal or higher and weakens none.
Once no test reads an oracle file, it is removed: the owner deletes the `.txt` file and runs
`tests/archtest/update_frozen_inputs.py`, and the row leaves the table above. Agents never delete
an oracle or run the updater.

## Public API

This directory exposes no library, only reference text files (listed in the table above).

## Safety Invariants

- Never edit an oracle file: it must stay byte-identical to `main` at the time it was frozen.
- Oracles run only against fake SDK clients; no network, no credentials.
- Every oracle has a parity test; a new oracle without one is not a safety net.

## Extension Rules

1. A rung that refactors a module freezes `git show main:<path>` here as `<package>_<module>.txt`,
   adds a parity test next to the other tests and a row to the table above.
2. Never edit an oracle (`AGENTS.md` rule 3). Only the owner removes one, and only when no test
   reads it (see Retiring an oracle).
3. A behaviour-fix rung keeps the oracle and marks each diverging parity case with its bug ID
   ([fix-bug.md](../../.cursor/skills/crc-maintain/fix-bug.md)).
