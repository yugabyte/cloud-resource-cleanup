# CLEANUP_SAFETY.md — CRC destructive-cleanup behaviour

Maintainer notes on how this Jenkins cleanup tool deletes cloud resources.
Sections marked **On main** describe code as it exists today. Sections marked
**Not implemented** are known gaps. Update this file in the same PR when those
facts change.

---

## Purpose

- CRC deletes or stops leftover **test / itest** cloud resources so accounts do
  not accumulate cost.
- Callers usually pass explicit `--cloud`, `--resource`, `--filter_tags` /
  `--notags`, and an age gate. `--cloud all --resource all` is not a working
  mode on main: the loop hits aws+disk first and the opt-in circuit breaker
  raises before any delete runs.

---

## Failure handling (on main)

- Unreachable AWS regions are logged and skipped in `crc/aws/connectivity.py`.
- The only explicit `sys.exit(0)` in this repository is the read-only
  `--scan_tag` path in `crc.py`.
- Inside the per-region loop, `AWS_Disk.delete` catches per-volume errors, sets
  `_had_errors`, and finishes with an error log rather than raising. An
  uncaught exception would exit non-zero and fail the Jenkins build; Slack /
  Influx still see whatever landed in `disks_to_delete`.
- That “log rather than raise” path does not cover region listing:
  `get_all_regions` runs outside the per-region `try` and only catches
  `CONNECTIVITY_ERRORS`. Auth / credential failures on `describe_regions`
  still raise out of `AWS_Disk.delete`, and `CRC.delete_disks` re-raises them.
- Azure `SpotVM.delete` re-raises when the **list** call fails. Per-VM delete
  failures are logged and skipped (`_delete_vm` may raise; the loop catches it).

---

## Age gates

**On main**

- `Service.is_old({})` returns **True**. An empty / missing age with no other
  gate means every otherwise-matching resource is treated as old enough.
- Strict rejection of unknown keys, zero durations, and typos exists in
  `crc/aws/disk.py` (`_normalize_age`) and in spot modules via
  `Service.require_positive_age` (AWS/Azure/GCP/OCI spot). Shared
  `Service.is_old` still treats `{'days': 0}` / empty age as match-all for
  callers that do not use `require_positive_age`.

**When adding a new destructive module**

- Require a real age (or equivalent) gate in `__init__`.
- Reject or skip unknown keys, zero durations, and bad custom age tags.
- Avoid having `--max_age` / `MAX_AGE` silently shorten a floor another flag
  (for example AWS `--detach_age`) just established, unless that change is
  documented in the PR.

---

## Opt-in and circuit breakers (on main)

- AWS EBS (`crc/aws/disk.py`) requires `--cloud aws` and `--resource disk`.
  Hitting aws+disk without that opt-in raises; it does not `continue` into
  other AWS resources. An earlier bug skipped EBS and still deleted
  vm/ip/kms/keypair under `-r all`.

---

## Uncertain API signals (on main)

- Incomplete CloudWatch answers (non-`Complete`, missing pages, pagination
  truncation, auth failures on metric lookup) keep the volume rather than
  delete it.
- Helpers that return “candidates to delete” should not log as if they were
  “keeping” those candidates (and the reverse).

---

## Disk `--detach_age`

**On main (AWS)**

- Inferred from AWS/EBS CloudWatch metrics published while attached to a
  **running** instance. Not a last-detach timestamp; blind to attachments on
  stopped instances. Documented that way in `crc/aws/disk.py` error strings,
  README, and CLI `--help`.
- `AWS_Disk` requires `--age` and/or `--detach_age`. When `--age` is omitted,
  `detach_age` is also the CreateTime floor (`test_detach_age_alone_is_enough`).
- Empty Complete CloudWatch series is not proof of long detach; CreateTime
  floor still applies.
- Multi-page `GetMetricData` merges by concatenating `Values` and using the
  **last-page** `StatusCode` (non-final pages are often `PartialData`).

**On main (GCP)**

- `GCP_Disk` uses `disk.last_detach_timestamp` via
  `is_old(retention_age or self.detach_age, ...)`.
- `--age` is passed into `GCP_Disk` but never read.
- Omitting `--detach_age` with no custom age label leaves the age argument
  empty/`None`, so `Service.is_old` returns True and every matching unattached
  disk with a `last_detach_timestamp` is deleted.
- A custom age label replaces `detach_age` on GCP.
- On AWS a custom tag never changes the CloudWatch `detach_age` window
  (`_drop_recently_attached` uses only `self.detach_age`). For CreateTime,
  `_creation_age_floor` takes `max(retention_tag, detach_age)` only in
  detach_age-only mode (`--age` omitted); when `--age` is set it returns
  `retention_age or self.age`.

---

## AWS Spot Instance Requests

**On main (this branch)**

- Tags for YBA fleets live on the spot request; filtering is on the request.
  Untagged requests are always skipped (fail-closed).
- `--age` is required and must be >0 (`Service.require_positive_age`).
- Order: cancel → terminate → wait for EBS `available` → delete volumes with
  `DeleteOnTermination=false`. A failed cancel does **not** terminate (avoids
  relaunch for `Type=persistent`).
- Cancelled requests that still have an `InstanceId` are retried so a prior
  run that cancelled but failed terminate / volume delete can finish.
- Influx: requests+instances under VMS; leftover volumes under DISKS.

---

## Leftover disks on other clouds

**On main (this branch)**

| Cloud | “Won’t auto-delete” signal | Cleanup |
|-------|----------------------------|---------|
| Azure Spot | `delete_option` is Detach (not Delete) | Delete managed disk after `begin_delete_vm_and_wait` |
| GCP Spot / preemptible | `autoDelete=false` | Wait for instance-delete zone op, then delete disk |
| OCI preemptible | Attached block volume | Wait for instance `TERMINATED` (best-effort), then delete volume |

All three require a positive `--age`. Azure skips untagged Spot VMs (same as
`crc.azu.vm.VM`). GCP/OCI match their regular VM label/tag helpers; empty
filter still needs the age gate.

---

## Testing

- Unit tests with mocks cover non-obvious safety logic (order of operations,
  leftover-disk detection, age validation, fail-closed branches).
- Live cloud credentials are not required in CI for every PR.
- New destructive modules should ship with tests for their safety behaviour.
- `tests/` needs to be importable (`tests/__init__.py` / `pytest.ini` as
  needed).

---

## Module map

| Path | Notes |
|------|--------|
| `crc.py` | CLI wiring, opt-in gates, cloud/resource loop |
| `crc/service.py` | Shared `is_old` / `require_positive_age` / retention tags — empty age is True unless a module rejects it |
| `crc/aws/disk.py` | AWS EBS; `_normalize_age`; detach_age via CloudWatch; per-region errors logged (region-list auth failures still raise) |
| `crc/gcp/disk.py` | GCP disks; `detach_age` via `last_detach_timestamp`; `--age` ignored; custom label replaces detach_age |
| `crc/aws/spot_instance_requests.py` | cancel→terminate→DoT=false EBS; cancel-fail skips terminate; age required; cancelled+InstanceId retried |
| `crc/aws/connectivity.py` | Skip unreachable regions |
| `crc/azu/spot_vm.py` | Spot VM + NIC + Detach disks; age required; untagged skipped |
| `crc/gcp/spot_vm.py` | Spot/preemptible + autoDelete=false disks after zone-op wait; age required |
| `crc/oci/spot_vm.py` | Preemptible + leftover block volumes after TERMINATED wait; age required |
