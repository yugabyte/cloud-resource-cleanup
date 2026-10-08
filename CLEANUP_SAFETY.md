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
- Strict rejection of unknown keys, zero durations, and typos exists mainly in
  `crc/aws/disk.py` (`_normalize_age`). Shared `Service.is_old` still treats
  `{'days': 0}` as “older than 0” and returns True. AWS spot
  (`crc/aws/spot_instance_requests.py`), Azure spot (`crc/azu/spot_vm.py`), and
  other `is_old` callers accept a `{'days': 0}` custom tag or `--age`. README
  notes the stricter check is AWS-disk-specific.

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

**On main**

- Tags for YBA fleets often live on the spot request; filtering is on the
  request.
- `delete()` already runs cancel before terminate. A failed cancel is only
  dropped from a “finalized” list; the terminate loop still walks every
  instance in `instance_id_to_operate`, so cancel-fail does not skip terminate.
- The module does not read request `Type`, does not inspect
  `DeleteOnTermination`, and does not delete leftover EBS volumes. For
  `Type=persistent`, terminate-without-successful-cancel can leave the request
  open so AWS launches a replacement.

**Not implemented**

- Skip terminate when cancel fails.
- After terminate, delete EBS volumes with `DeleteOnTermination=false`.

---

## Leftover disks on other clouds

**Not implemented.** Azure `SpotVM._delete_vm` deletes the VM and primary NIC
only; it never reads `delete_option`. There is no GCP/OCI spot leftover-disk
cleanup under `crc/`.

| Cloud | “Won’t auto-delete” signal | Intended cleanup |
|-------|----------------------------|------------------|
| Azure Spot | `delete_option` is Detach (not Delete) | Delete the managed disk |
| GCP Spot / preemptible | `autoDelete=false` | Delete the disk |
| OCI preemptible | Attached block volume not removed with terminate | Delete the volume |

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
| `crc/service.py` | Shared `is_old` / retention tag parsing — empty age is True; zero-age not rejected |
| `crc/aws/disk.py` | AWS EBS; `_normalize_age`; detach_age via CloudWatch; per-region errors logged (region-list auth failures still raise) |
| `crc/gcp/disk.py` | GCP disks; `detach_age` via `last_detach_timestamp`; `--age` ignored; custom label replaces detach_age |
| `crc/aws/spot_instance_requests.py` | On main: cancel then terminate (cancel-fail still terminates). Gap: persistent `Type`; leftover DoT=false EBS |
| `crc/aws/connectivity.py` | Skip unreachable regions |
| `crc/azu/spot_vm.py` | On main: Spot VM + primary NIC. Gap: leftover Detach disks |
| `crc/gcp/` / `crc/oci/` | Labels/freeform tags; spot leftover-disk cleanup not present |
