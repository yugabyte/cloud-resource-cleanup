# CLEANUP_SAFETY.md — CRC destructive-cleanup behaviour

Maintainer notes on how this Jenkins cleanup tool deletes cloud resources.
Sections marked **On main** describe code as it exists today. Sections marked
**Not implemented** are known gaps. Update this file in the same PR when those
facts change.

Each invariant carries a stable `[SAF-NNN]` tag. IDs are never renumbered or
reused. `tests/archtest/safety_lock.json` maps IDs to the tests that lock them.

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

- [SAF-001] Unreachable AWS regions are logged and skipped in `crc/aws/connectivity.py`.
- [SAF-002] The only explicit `sys.exit(0)` in this repository is the read-only
  `--scan_tag` path in `crc.py`.
- [SAF-003] Inside the per-region loop, `AWS_Disk.delete` catches per-volume errors, sets
  `_had_errors`, and finishes with an error log rather than raising. An
  uncaught exception would exit non-zero and fail the Jenkins build; Slack /
  Influx still see whatever landed in `disks_to_delete`.
- [SAF-004] That “log rather than raise” path does not cover region listing:
  `get_all_regions` runs outside the per-region `try` and only catches
  `CONNECTIVITY_ERRORS`. Auth / credential failures on `describe_regions`
  still raise out of `AWS_Disk.delete`, and `CRC.delete_disks` re-raises them.
- [SAF-005] Azure `SpotVM.delete` re-raises when the **list** call fails. Per-VM delete
  failures are logged and skipped (`_delete_vm` may raise; the loop catches it).

---

## Age gates

**On main**

- [SAF-006] `Service.is_old({})` returns **True**. An empty / missing age with no other
  gate means every otherwise-matching resource is treated as old enough.
- [SAF-007] Strict rejection of unknown keys, zero durations, and typos exists in
  `crc/aws/disk.py` (`_normalize_age`) and in spot modules via
  `Service.require_positive_age` for `--age`, plus
  [SAF-008] Spot modules use `Service.validated_retention_age` for custom age tags
  (absent → `--age`; present but empty/unparseable/invalid/zero → raise
  `ValueError` so callers **skip**, including TypeError / `inf`
  OverflowError). [SAF-009] Other modules that still call `get_retention_age` alone
  **fail open**: parse errors become `None` and the resource is judged by
  `--age` / detach_age. Shared `Service.is_old` still treats empty age as
  match-all for callers that do not use those helpers. [SAF-010] `--max_age` /
  `MAX_AGE` can still shorten a floor inside `is_old` (including to zero);
  do not set `MAX_AGE` to a zero duration in Jenkins.

**When adding a new destructive module**

- [SAF-011] Require a real age (or equivalent) gate in `__init__`.
- [SAF-012] Reject or skip unknown keys, zero durations, and bad custom age tags.
- [SAF-013] Avoid having `--max_age` / `MAX_AGE` silently shorten a floor another flag
  (for example AWS `--detach_age`) just established, unless that change is
  documented in the PR.

---

## Opt-in and circuit breakers (on main)

- [SAF-014] AWS EBS (`crc/aws/disk.py`) requires `--cloud aws` and `--resource disk`.
  Hitting aws+disk without that opt-in raises; it does not `continue` into
  other AWS resources. An earlier bug skipped EBS and still deleted
  vm/ip/kms/keypair under `-r all`.

---

## Uncertain API signals (on main)

- [SAF-015] Incomplete CloudWatch answers (non-`Complete`, missing pages, pagination
  truncation, auth failures on metric lookup) keep the volume rather than
  delete it.
- [SAF-016] Helpers that return “candidates to delete” should not log as if they were
  “keeping” those candidates (and the reverse).

---

## Disk `--detach_age`

**On main (AWS)**

- [SAF-017] Inferred from AWS/EBS CloudWatch metrics published while attached to a
  **running** instance. Not a last-detach timestamp; blind to attachments on
  stopped instances. Documented that way in `crc/aws/disk.py` error strings,
  README, and CLI `--help`.
- [SAF-018] `AWS_Disk` requires `--age` and/or `--detach_age`. When `--age` is omitted,
  `detach_age` is also the CreateTime floor (`test_detach_age_alone_is_enough`).
- [SAF-019] Empty Complete CloudWatch series is not proof of long detach; CreateTime
  floor still applies.
- [SAF-020] Multi-page `GetMetricData` merges by concatenating `Values` and using the
  **last-page** `StatusCode` (non-final pages are often `PartialData`).

**On main (GCP)**

- [SAF-021] `GCP_Disk` uses `disk.last_detach_timestamp` via
  `is_old(retention_age or self.detach_age, ...)`.
- [SAF-022] `--age` is passed into `GCP_Disk` but never read.
- [SAF-023] Omitting `--detach_age` with no custom age label leaves the age argument
  empty/`None`, so `Service.is_old` returns True and every matching unattached
  disk with a `last_detach_timestamp` is deleted.
- [SAF-024] A custom age label replaces `detach_age` on GCP.
- [SAF-025] On AWS a custom tag never changes the CloudWatch `detach_age` window
  (`_drop_recently_attached` uses only `self.detach_age`). For CreateTime,
  `_creation_age_floor` takes `max(retention_tag, detach_age)` only in
  detach_age-only mode (`--age` omitted); when `--age` is set it returns
  `retention_age or self.age`.

---

## AWS Spot Instance Requests

**On main (this branch)**

- [SAF-026] Tags for YBA fleets live on the spot request; filtering is on the request.
  Untagged requests are always skipped (fail-closed).
- [SAF-027] `--age` is required and must be >0 (`Service.require_positive_age`).
- [SAF-028] Order: tag DoT=false volumes → cancel → terminate → wait for EBS
  `available` → delete volumes. Tagging runs before cancel so a CreateTags
  failure leaves the request untouched. A failed cancel does **not**
  terminate (avoids relaunch for `Type=persistent`).
- [SAF-029] Cancelled requests that still have an `InstanceId` (and match tags/age) are
  cleaned up: terminate the instance if still running, then delete
  `DeleteOnTermination=false` volumes. That finishes a prior CRC run that
  cancelled but failed later steps, **and** also cleans hand-cancelled
  requests whose instance AWS left running. Untagged cancelled requests stay
  skipped.
- [SAF-030] Before terminate (and when BDMs are still visible on a shutting-down
  instance), each `DeleteOnTermination=false` volume is tagged
  `crc_leftover_of=<spot-request-id>` plus filter/exception/notags copies under
  `crc_leftover_tag_*` (prefixed so volume-owned tags are never overwritten;
  prefixed keys are always overwritten on retag so sticky values cannot linger
  after a failed invalidate). Later runs rediscover available orphans via
  `describe_volumes` (per-request and a gated region sweep). [SAF-031] Before delete,
  each volume's **own** tags are checked for `--exception_tags` / `--notags`
  and the same sensitive `yb_task` values as `crc/aws/disk.py`. The sweep
  skips in-use volumes and re-checks filter/exception/notags/untagged/age
  (live request tags + CreateTime when present; otherwise scope copies +
  volume CreateTime) plus volume-owned protection tags. [SAF-032] If cancel/terminate
  fails after tagging, the leftover marker **and** every `crc_leftover_tag_*`
  key are cleared (DeleteTags / per-key / overwrite to `terminate-failed` on
  ClientError/BotoCoreError).
  Cancel failures catch ClientError and BotoCoreError. [SAF-033] Ambiguous terminate
  transport errors re-check instance state and keep the marker only when
  confirmed `shutting-down`/`terminated`/gone; otherwise invalidate. [SAF-034] Sweep
  isolates per-volume errors. [SAF-035] Already-cancelled requests whose instance is
  gone and that have no tagged leftovers are skipped (not re-reported).
  Closed/failed requests may still be cancelled again and counted until AWS
  drops them.
- [SAF-036] Influx: requests+instances under VMS; leftover volumes under DISKS.
- [SAF-037] GCP/OCI `--resource spot_instances` require explicit `--cloud gcp` /
  `--cloud oci`. Under `--cloud all`, only Azure spot_instances runs.

---

## Leftover disks on other clouds

**On main (this branch)**

| Cloud | “Won’t auto-delete” signal | Cleanup |
|-------|----------------------------|---------|
| Azure Spot [SAF-038] | `delete_option` is Detach or unset (Azure default) | Delete managed disk after `begin_delete_vm_and_wait` |
| GCP Spot / preemptible [SAF-039] | `autoDelete=false` | Wait for instance-delete zone op, then delete disk (zonal or regional) |
| OCI preemptible [SAF-040] | Attached block volume | Wait for instance `TERMINATED` (best-effort), then delete volume |

[SAF-041] All three require a positive `--age`. Custom age tags: absent → `--age`;
present but empty/unparseable/invalid/zero → skip. [SAF-042] Azure skips untagged Spot
VMs (same as `crc.azu.vm.VM`). [SAF-043] GCP/OCI match their regular VM label/tag
helpers; empty filter still needs the age gate. GCP/OCI are opt-in via
explicit `--cloud` (not `--cloud all`). [SAF-044] Leftover disks/volumes are skipped when
their own exception/notags match (and Azure disks outside `--resource_group`).
[SAF-045] GCP skips GKE/MIG members; OCI skips OKE / instance-pool members (tags/metadata).

**Gap:** Azure/GCP/OCI do not persist leftover IDs after the instance is gone
(no `crc_leftover_of`). If leftover disk/volume delete fails (or OCI TERMINATED
wait times out while still attached), a later `spot_instances` run will not
rediscover them; use `--resource disk` if tags match. [SAF-046] OCI skips terminate when
attachment listing fails, so that case is retried.

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
