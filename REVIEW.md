# REVIEW.md — guidance for automated and human review of CRC

This repository is a **destructive multi-cloud cleanup tool** used from Jenkins.
Reviewers (including AgentK) should treat every change as potentially able to
delete account-wide resources. Prefer **fail-closed** over clever cleanup.

When this file conflicts with a local comment that would widen deletion, follow
this file unless the PR explicitly changes the contract and updates this file.

Label claims carefully:

- **Current** — true of code on `main` today; do not regress.
- **Required for new work** — must hold for any PR that adds or widens a
  destructive path, even if older modules are not there yet.
- **Open gap / intended** — documented target behaviour that is **not**
  implemented yet; do not treat as an existing safety guarantee.

---

## Product intent

- CRC deletes or stops leftover **test / itest** cloud resources so accounts do
  not accumulate cost.
- Callers usually pass explicit `--cloud`, `--resource`, `--filter_tags` /
  `--notags`, and an age gate. Broad `--cloud all --resource all` is dangerous
  and must not silently unlock new destructive paths.
- **Current:** unreachable AWS regions are logged and skipped via
  `crc/aws/connectivity.py` so one bad region does not abort the whole cloud
  pass. The only `sys.exit(0)` in this repo is on the read-only `--scan_tag`
  path in `crc.py`. Destructive helpers such as `CRC.delete_disks` and
  `SpotVM.delete` still re-raise on hard failures. If Jenkins itself maps some
  failures to exit 0, that lives **outside** this repository — do not cite
  in-repo `sys.exit(0)` as the cleanup-loop contract. Do not reintroduce “one
  bad volume fails the entire job” for region connectivity unless the PR
  documents why.

---

## Hard safety rules (do not regress)

### Age gates

- **Current:** `Service.is_old({})` returns **True**. An empty / missing age
  with no other gate means “delete everything that otherwise matches.”
- **Current:** strict rejection of unknown keys, zero durations, and typos
  exists today mainly in `crc/aws/disk.py` (`_normalize_age`). Shared
  `Service.is_old` still treats `{'days': 0}` as “older than 0” and returns
  True. AWS spot (`crc/aws/spot_instance_requests.py`), Azure spot
  (`crc/azu/spot_vm.py`), and other `is_old` callers do **not** yet reject a
  `{'days': 0}` custom tag or `--age`. README already notes the stricter check
  is AWS-disk-specific.
- **Required for new work:** new destructive modules must require a real age
  (or equivalent) gate in `__init__`, and must **raise** or skip on unknown
  keys / zero durations / bad custom age tags — never treat those as “no gate.”
- `--max_age` / `MAX_AGE` must not silently shorten a floor that another flag
  (e.g. AWS `--detach_age`) just established, unless the PR documents that.

### Opt-in and circuit breakers

- **AWS EBS** (`crc/aws/disk.py`): requires `--cloud aws` **and**
  `--resource disk`. Hitting aws+disk without that opt-in must **raise**, not
  `continue` into other AWS resources. Skipping and continuing unlocked
  vm/ip/kms/keypair deletes under `-r all` in the past — do not repeat that.
- Do not remove a hard abort that was protecting other clouds unless the PR
  replaces it with an equally explicit opt-in.

### Fail-closed on uncertain signals

- Incomplete API answers (CloudWatch non-`Complete`, missing metrics pages,
  pagination truncation, auth failures on metric lookup) mean **keep** the
  resource, not delete it.
- Name return values carefully. A helper that returns “candidates to delete”
  must not log “keeping” when it returns that list (and vice versa).

### Disk detach age (`--detach_age`)

- **Current (AWS):** inferred from AWS/EBS CloudWatch metrics published only
  while attached to a **running** instance. It is **not** a last-detach
  timestamp and is blind to attachments on stopped instances. `crc/aws/disk.py`
  error strings, README, and CLI `--help` must keep that distinction — do not
  call it “last detached” for AWS.
- **Current (AWS CreateTime floor):** `AWS_Disk` requires `--age` and/or
  `--detach_age`. When `--age` is omitted, `detach_age` itself is used as the
  CreateTime floor (`test_detach_age_alone_is_enough`). Do not document
  `--age` as required alongside `detach_age`.
- **Current (GCP):** `CRC.delete_disks` passes `detach_age` to `GCP_Disk`, which
  uses `disk.last_detach_timestamp` — a real last-detach timestamp. CLI help
  and docs must not say “AWS EBS only.”
- Empty Complete CloudWatch series ≠ proof of long detach; CreateTime floor is
  still applied on AWS.
- Merge multi-page `GetMetricData` by concatenating `Values` and taking
  **last-page** `StatusCode` (non-final pages are often `PartialData`).

### AWS Spot Instance Requests

**Current on `main`:**

- Tags for YBA fleets often live on the **spot request**, not the instance.
  Filter on the request.
- Cleanup attempts cancel then terminate, but a failed cancel is only dropped
  from a “finalized” list; the terminate loop still walks every instance in
  `instance_id_to_operate`. So cancel-fail does **not** reliably skip
  terminate today.
- The module does **not** read request `Type`, does **not** inspect
  `DeleteOnTermination`, and does **not** delete leftover EBS volumes.

**Open gap / intended** (enforce in the PR that implements them; then move
into Current):

- Order is mandatory: **cancel request → then terminate instance**.
  `Type=persistent`: terminate-first leaves the request open and AWS launches
  a replacement.
- If cancel fails, do **not** terminate that instance.
- After terminate, delete EBS volumes whose mapping had
  `DeleteOnTermination=false`. When the flag is true, the cloud deletes them
  with the instance — still log/check the flag on future matches.

### Leftover disks on other clouds (same idea)

**Open gap / intended** — not implemented on `main` today. Azure
`SpotVM._delete_vm` deletes the VM and primary NIC only; it never reads
`delete_option`. There is no GCP/OCI spot leftover-disk cleanup under `crc/`.

| Cloud | “Won’t auto-delete” signal | Intended action after VM/instance gone |
|-------|----------------------------|----------------------------------------|
| Azure Spot | `delete_option` is Detach (not Delete) | Delete the managed disk |
| GCP Spot / preemptible | `autoDelete=false` | Delete the disk |
| OCI preemptible | Attached block volume not removed with terminate | Delete the volume |

When a PR implements a row, update this table’s status and the ownership hints
in the same PR.

---

## Testing expectations

- Prefer **unit tests with mocks** for non-obvious safety logic (order of
  operations, leftover-disk detection, age validation, fail-closed branches).
- Do **not** require live cloud credentials in CI for every PR.
- New destructive AWS/Azure/GCP/OCI modules should ship with tests for their
  safety invariants, not only happy-path list/delete wrappers.
- `tests/` must be importable (`tests/__init__.py` / `pytest.ini` as needed).

---

## Review focus checklist

When reviewing a PR, check:

1. Can an empty age / bad age dict delete everything?
2. Does `-c all` / `-r all` newly enable a destructive path without opt-in?
3. On uncertainty (metrics, pagination, connectivity), do we keep or delete?
4. Are docs/error strings honest about what a signal measures **and** about
   what the code actually does today?
5. For spot: if the PR claims cancel-before-terminate or leftover volumes, is
   that actually in the diff (not only in docs)?
6. Do Slack/Influx still see partial successes if we log errors instead of
   aborting the job?
7. Are generated artifacts (`__pycache__`, `logs/`) gitignored and not committed?

---

## What not to bike-shed

- Style-only renames in unrelated modules.
- Requiring full multi-cloud e2e tests without a CI credential story.
- Reverting region-skip / connectivity continue-on-error without an
  incident-driven reason.
- Asking for CloudTrail (or equivalent) on every detach-age design unless the
  PR claims a true last-detach timestamp.

---

## File ownership hints

| Path | Notes |
|------|--------|
| `crc.py` | CLI wiring, opt-in gates, cloud/resource loop |
| `crc/service.py` | Shared `is_old` / retention tag parsing — empty age is True; zero-age not rejected |
| `crc/aws/disk.py` | AWS EBS; `_normalize_age`; detach_age via CloudWatch; age and/or detach_age required |
| `crc/gcp/disk.py` | GCP disks; `detach_age` via `last_detach_timestamp` |
| `crc/aws/spot_instance_requests.py` | **Current:** cancel then terminate (cancel-fail still terminates). **Gap:** persistent `Type`; leftover DoT=false EBS |
| `crc/aws/connectivity.py` | Skip unreachable regions; do not fail the whole AWS pass |
| `crc/azu/spot_vm.py` | **Current:** Spot VM + primary NIC. **Gap:** leftover Detach disks |
| `crc/gcp/` / `crc/oci/` | Prefer labels/freeform tags; spot leftover-disk cleanup is **intended**, not present |

PRs that change safety contracts above should update this file in the same PR.
