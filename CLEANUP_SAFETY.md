# CLEANUP_SAFETY.md — CRC destructive-cleanup behaviour

Maintainer notes on how this Jenkins cleanup tool deletes cloud resources. The
document describes the code in this repository. Sections marked **Gap** are known
limits. Update this file in the same PR when those facts change.

Each invariant carries a stable `[SAF-NNN]` tag. IDs are never renumbered or
reused. `tests/archtest/safety_lock.json` maps IDs to the tests that lock them.

---

## Purpose

- CRC deletes or stops leftover **test / itest** cloud resources so accounts do
  not accumulate cost.
- Callers usually pass explicit `--cloud`, `--resource`, `--filter_tags` /
  `--notags`, and an age gate. `--cloud all --resource all` is not a working
  mode: `validate_targets` raises before any cleaner runs, because the AWS EBS
  opt-in breaker would raise on the aws+disk pair.

---

## CLI validation

- Before any cleaner runs, `validate_targets` (`crc/cli/dispatch.py`) checks every
  (cloud, resource) pair. `main()` calls it (when `--scan_tag` is not set) before
  `set_max_age` and dispatch, and `run_dispatch` calls it again.
- `--cloud all --resource disk|all` raises.
- An unsupported pair of an explicit cloud raises `ValueError`: `keypair` and
  `kms` off AWS, `nic` off Azure, `ip` on OCI, `snapshot` off AWS. `--resource vpc` was removed (BUG-083): it is an
  argparse invalid choice (exit 2) and no cleaner exists.
- Under `--cloud all` an unsupported pair is skipped with a warning only when no
  supported pair follows it; otherwise it raises. `snapshot` runs in the
  AWS pass only.
- An empty or blank `--scan_tag` raises `ValueError` before any cleaner runs.

---

## Failure handling

- [SAF-001] Unreachable AWS regions are logged and skipped
  (`log_skipped_region` in `crc/aws/connectivity.py`).
- [SAF-002] The only explicit `sys.exit(0)` in this repository is the read-only
  `--scan_tag` path in `crc/cli/scan.py`. It forces `--dry_run` and never deletes.
- [SAF-003] Inside the per-region loop, the AWS disk cleaner catches per-volume and
  per-region errors, sets `had_errors`, and finishes with an error log rather
  than raising. An uncaught exception would exit non-zero and fail the Jenkins
  build; Slack / Influx still see whatever landed in `disks_to_delete`.
- [SAF-004] That “log rather than raise” path does not cover region listing:
  `get_all_regions` runs outside the per-region guard and only catches
  `CONNECTIVITY_ERRORS`. Auth / credential failures on `describe_regions`
  still raise out of `DiskCleaner.plan()` (`crc/aws/disk/cleaner.py`) before any volume is
  deleted, and `engine.run` propagates them (the disk route reports first, then re-raises).
- [SAF-005] The Azure Spot VM cleaner (`AzureSpotVmCleaner.list_vms`, called by `plan()`)
  logs and re-raises when the **list** call fails. Per-VM delete failures are logged and skipped.
- Engine path (BUG-388, BUG-391, BUG-408): a cleaner's `plan()` lists every region before `apply()` mutates,
  so a listing error in a later region stops the pair before any mutation (fewer deletes,
  never more); a loop that deleted region by region would instead delete the earlier regions. Every AWS kind runs this way, and so
  does every Azure kind (BUG-392), where an error while paging the listing now stops the pair before
  any delete, the GCP kinds ip, disk and vm (BUG-400), the OCI kinds disk and vm (BUG-408), and GCP and OCI spot_instances (BUG-409).

---

## Age gates

- [SAF-006] `Service.is_old({})` returns **True**. An empty / missing age with no other
  gate means every otherwise-matching resource is treated as old enough.
- [SAF-007] Strict rejection of unknown keys, zero durations, and typos exists in
  the AWS disk age validation (`crc/aws/disk/settings.py`) and in spot modules via
  `Service.require_positive_age` for `--age`.
- [SAF-008] Spot modules use `Service.validated_retention_age` for custom age tags
  (absent → `--age`; present but empty/unparseable/invalid/zero → raise
  `ValueError` so callers **skip**, including TypeError / `inf`
  OverflowError).
- [SAF-009] Modules that use `get_retention_age` (AWS disk/kms/vm, Azure disk/vm,
  GCP disk/vm, OCI disk/vm) fail **closed** too: a custom age tag that is present
  but empty, unparseable, not finite, zero or negative makes `get_retention_age`
  return `KEEP_AGE`, which `is_old` never treats as old (`MAX_AGE` included), and
  the run log counts `bad_age_tag`. An absent tag still uses `--age` /
  `--detach_age`. Shared `Service.is_old` still treats an empty age as match-all
  for callers that do not use these helpers.
- [SAF-010] `--max_age` / `MAX_AGE` can still shorten a floor inside `is_old`
  (including to zero); do not set `MAX_AGE` to a zero duration in Jenkins.

**When adding a new destructive module**

- [SAF-011] Require a real age (or equivalent) gate. A real run (not `--dry_run`) without a
  positive `--age` raises `ValueError` naming the flag before any cloud call for AWS VM, GCP VM,
  GCP IP, Azure disk, Azure IP and Azure NIC; GCP disk requires a positive `--detach_age`
  (SAF-023). Dry runs need none. A custom age tag still replaces `--age` per resource where the
  kind supports it, never the requirement itself. Azure IP and NIC read the creation time from
  `azure-mgmt-resource` (`createdTime`, one listing per run); a resource without one, or every
  resource when the lookup fails, is kept.
- [SAF-012] Reject or skip unknown keys, zero durations, and bad custom age tags.
- [SAF-013] Avoid having `--max_age` / `MAX_AGE` silently shorten a floor another flag
  (for example AWS `--detach_age`) just established, unless that change is
  documented in the PR.

---

## Opt-in and circuit breakers

- [SAF-014] AWS EBS (`crc/aws/disk/`) requires `--cloud aws` and `--resource disk`.
  Hitting aws+disk without that opt-in raises; it does not `continue` into
  other AWS resources. `validate_targets` raises first for `--cloud all`
  with `disk` or `all`, so no cleaner of any cloud has run by then.

---

## Uncertain API signals

- [SAF-015] Incomplete CloudWatch answers (non-`Complete`, missing pages, pagination
  truncation, auth failures on metric lookup) keep the volume rather than
  delete it.
- [SAF-016] Helpers that return “candidates to delete” should not log as if they were
  “keeping” those candidates (and the reverse).

---

## Disk `--detach_age`

**AWS**

- [SAF-017] Inferred from AWS/EBS CloudWatch metrics published while attached to a
  **running** instance. Not a last-detach timestamp; blind to attachments on
  stopped instances. Documented that way in the `crc/aws/disk/` error strings,
  README, and CLI `--help`. BUG-283 is wontfix (test environments): a volume detached from a
  stopped instance may be deleted early.
- [SAF-018] The AWS disk cleaner (`DiskCleaner`, settings in `crc/aws/disk/settings.py`) requires `--age` and/or `--detach_age`. When `--age` is omitted,
  `detach_age` is also the CreateTime floor (`test_detach_age_alone_is_enough`).
- [SAF-019] Empty Complete CloudWatch series is not proof of long detach; CreateTime
  floor still applies.
- [SAF-020] Multi-page `GetMetricData` merges by concatenating `Values` and using the
  **last-page** `StatusCode` (non-final pages are often `PartialData`).

**GCP**

- [SAF-021] The GCP disk cleaner (`DiskCleaner` in `crc/gcp/disk_cleaner.py`) uses `disk.last_detach_timestamp` via
  `is_old(retention_age or self.detach_age, ...)`.
- [SAF-022] `--age` never reaches the GCP disk cleaner's age gate; only `detach_age` is read.
- [SAF-023] A real (not `--dry_run`) GCP disk run requires a positive `--detach_age` (SAF-011,
  BUG-124 fixed): the registered factory raises `ValueError` naming `detach_age` before any client
  is built or any cloud call, for an omitted, empty, zero or `None` value; `--age` is no
  substitute. A dry run without it still lists every unattached disk with a `last_detach_timestamp`
  (the Jenkins `-d` untagged reports).
- [SAF-024] A custom age label replaces `detach_age` on GCP.
- [SAF-025] On AWS a custom tag never changes the CloudWatch `detach_age` window
  (`_drop_recently_attached` uses only the `detach_age` setting). The CreateTime
  floor is: with `--age` omitted, `max(retention_tag, detach_age)`; with `--age`
  and no `--detach_age`, `retention_age or age`; with both set, the effective age
  (`retention_age or age`) **plus** `detach_age`, because the CloudWatch guard
  cannot see attachments to stopped instances. `MAX_AGE` never shortens the floor.

---

## AWS Spot Instance Requests

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
  `describe_volumes` (per-request and a gated region sweep).
- [SAF-031] Before delete, each volume's **own** tags are checked for
  `--exception_tags` / `--notags` and sensitive `yb_task` values (exact keys
  `yb_task` and `yb-task`, `policy.aws_spot_has_sensitive_yb_task`; the AWS disk
  cleaner folds key case, see Per-resource keep rules). The sweep skips in-use volumes and
  re-checks filter/exception/notags/untagged/age (live request tags + CreateTime
  when present; otherwise scope copies + volume CreateTime) plus volume-owned
  protection tags.
- [SAF-032] If cancel/terminate fails after tagging, the leftover marker **and** every
  `crc_leftover_tag_*` key are cleared (DeleteTags / per-key / overwrite to
  `terminate-failed` on ClientError/BotoCoreError). Cancel failures catch
  ClientError and BotoCoreError.
- [SAF-033] Ambiguous terminate transport errors re-check instance state and keep the
  marker only when confirmed `shutting-down`/`terminated`/gone; otherwise
  invalidate.
- [SAF-034] Sweep isolates per-volume errors.
- [SAF-035] Already-cancelled requests whose instance is gone and that have no tagged
  leftovers are skipped (not re-reported). Closed/failed requests may still be
  cancelled again and counted until AWS drops them.
- [SAF-036] Influx: requests+instances under VMS; leftover volumes under DISKS.
- [SAF-037] GCP/OCI `--resource spot_instances` require explicit `--cloud gcp` /
  `--cloud oci`. Under `--cloud all`, only Azure spot_instances runs.

---

## Leftover disks on other clouds

| Cloud | “Won’t auto-delete” signal | Cleanup |
|-------|----------------------------|---------|
| Azure Spot [SAF-038] | `delete_option` is Detach or unset (Azure default) | Delete managed disk after `begin_delete_vm_and_wait` |
| GCP Spot / preemptible [SAF-039] | `autoDelete=false` | Wait for instance-delete zone op, then delete disk (zonal or regional) |
| OCI preemptible [SAF-040] | Attached block volume | Wait for instance `TERMINATED` (best-effort), then delete volume |

[SAF-041] All three require a positive `--age`. Custom age tags: absent → `--age`;
present but empty/unparseable/invalid/zero → skip. [SAF-042] Azure skips untagged Spot
VMs (same as `crc.azu.vm.VmCleaner`). [SAF-043] GCP/OCI match their regular VM label/tag
helpers; GCP Spot also keeps an unlabelled Spot VM when `--filter_tags` is empty,
while OCI with an empty filter relies on the age gate alone. GCP/OCI are opt-in via
explicit `--cloud` (not `--cloud all`). [SAF-044] Leftover disks/volumes are skipped
when their own exception/notags match, and for Azure disks outside
`--resource_group`. A GCP leftover disk is also kept when its own custom age label is
not yet reached, invalid, or has an unreadable `creationTimestamp`. An OCI leftover
volume is deleted only when it was created at or after the instance's `time_created`
and no other instance in the run's compartment has it attached (any state but
`DETACHED`); a missing time or a failed attachment read keeps it.
[SAF-045] GCP skips GKE/MIG members; OCI skips OKE / instance-pool members
(tags/metadata) in both the OCI Spot VM and the OCI VM cleaner (`crc/oci/vm`, skip
reason `managed`).

**Gap:** Azure/GCP/OCI do not persist leftover IDs after the instance is gone
(no `crc_leftover_of`). If leftover disk/volume delete fails (or OCI TERMINATED
wait times out while still attached), a later `spot_instances` run will not
rediscover them; use `--resource disk` if tags match. [SAF-046] OCI skips terminate when
attachment listing fails, so that case is retried.

---

## Per-resource keep rules

No SAF ID is assigned to these yet.

- AWS disk: every tag whose key folds (case, `-` to `_`) to `yb_task` is judged, and
  one sensitive value keeps the volume.
- GCP disk delete is recorded only after the zone operation is DONE without
  error. An error evaluating or deleting one disk, or listing one zone, is recorded
  and the run continues; the disk is kept. GCP IP and GCP VM also meet SAF-011
  (BUG-062, BUG-183 fixed): a real run requires a positive `--age` (`ValueError`
  naming `age` before any cloud call when the age is missing or zero; a dry run
  needs none, and a custom age label still replaces `--age` per instance on GCP VM).
  The GCP IP cleaner (`crc/gcp/ip_cleaner.py`) gates each RESERVED address on its
  `creationTimestamp` (RFC 3339, the disk and VM parser): an address younger than
  `--age`, or with an unreadable timestamp (recorded as a failure), is kept.
- OCI disk: when the volume attachments, the availability domains or the
  boot volume attachments of a region cannot be listed (connectivity or auth error,
  or no availability domain), every block volume (respectively boot volume) of that
  region is kept and the region is recorded as skipped. Unknown attachment state is
  never read as “unattached”.
- OCI VM: an error while evaluating one instance keeps that instance, is
  recorded as a failure and does not stop the run.

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
| `crc.py` | Entry point and re-export shim: `main`, `get_argparser` and the input validators are re-exported from `crc/cli/`; holds no logic |
| `crc/cli/` | CLI: `crc/cli/args.py` (flags), `crc/cli/inputs.py` (checks, target resolution), `crc/cli/pairs.py` (supported pairs), `crc/cli/dispatch.py` (`validate_targets`, AWS EBS breaker, the cloud and resource loops), `crc/cli/bridge.py` and `routes/<cloud>.py` (every pair runs through `crc.core.engine`), `crc/cli/scan.py` (read-only `--scan_tag`), `crc/cli/main.py`, `crc/cli/runner.py` (`CRC`, the run settings) |
| `crc/service.py` | Shared `is_old` / `require_positive_age` / retention tags — empty age is True unless a module rejects it; `get_retention_age` returns `KEEP_AGE` for a bad tag |
| `crc/core/` | Age helpers (`crc/core/age.py`, `KEEP_AGE`), tag and age policy (`crc/core/policy.py`), run log |
| `crc/aws/disk/` | AWS EBS; age validation in `crc/aws/disk/settings.py`; detach_age via CloudWatch (`crc/aws/disk/detach.py`); per-region errors logged (region-list auth failures still raise) |
| `crc/gcp/disk_cleaner.py` | GCP disks; positive detach_age required on a real run, compared with `last_detach_timestamp`; `--age` ignored; custom label replaces detach_age; notags and name_regex applied; delete waits for the zone operation; per-disk and per-zone errors are recorded and skipped |
| `crc/oci/disk/` | OCI block and boot volumes; unknown attachment state keeps the volume |
| `crc/aws/spot_instance_requests/` | cancel→terminate→DoT=false EBS; cancel-fail skips terminate; age required; cancelled+InstanceId retried |
| `crc/aws/connectivity.py` | Skip unreachable regions |
| `crc/azu/spot_vm/` | Spot VM + NIC + Detach disks; age required; untagged skipped |
| `crc/gcp/spot_vm/` | Spot/preemptible + autoDelete=false disks after zone-op wait; age required |
| `crc/oci/spot_vm/` | Preemptible + leftover block volumes after TERMINATED wait; age required |
| `crc/oci/vm/` | OCI VMs; OKE / instance-pool members skipped |
