# REVIEW.md — guidance for automated and human review of CRC

This repository is a **destructive multi-cloud cleanup tool** used from Jenkins.
Reviewers (including AgentK) should treat every change as potentially able to
delete account-wide resources. Prefer **fail-closed** over clever cleanup.

When this file conflicts with a local comment that would widen deletion, follow
this file unless the PR explicitly changes the contract and updates this file.

---

## Product intent

- CRC deletes or stops leftover **test / itest** cloud resources so accounts do
  not accumulate cost.
- Callers usually pass explicit `--cloud`, `--resource`, `--filter_tags` /
  `--notags`, and an age gate. Broad `--cloud all --resource all` is dangerous
  and must not silently unlock new destructive paths.
- Jobs are expected to **log and continue** on unreachable regions / partial
  failures (`crc/aws/connectivity.py`, `sys.exit(0)` in the pipeline). Do not
  reintroduce “raise and fail the whole Jenkins job” for a single bad volume
  or region unless the PR documents why.

---

## Hard safety rules (do not regress)

### Age gates

- `Service.is_old({})` returns **True**. An empty / missing age with no other
  gate means “delete everything that otherwise matches.” New destructive
  modules must require a real age (or equivalent) gate in `__init__`.
- Age unit keys are `days` and/or `hours`. Unknown keys, zero durations, or
  typos must **raise** or skip — never treat as “no gate.”
- Custom age tags must be validated the same way. A tag of `{'days': 0}` must
  not open the delete path.
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

### AWS EBS detach age (`--detach_age`)

- Inferred from AWS/EBS CloudWatch metrics published only while attached to a
  **running** instance. It is **not** a last-detach timestamp and is blind to
  attachments on stopped instances. Docs and error strings must say that.
- Empty Complete series ≠ proof of long detach; CreateTime floor is still
  required.
- Merge multi-page `GetMetricData` by concatenating `Values` and taking
  **last-page** `StatusCode` (non-final pages are often `PartialData`).

### AWS Spot Instance Requests

- Tags for YBA fleets often live on the **spot request**, not the instance.
  Filter on the request.
- Order is mandatory: **cancel request → then terminate instance**.
  `Type=persistent`: terminate-first leaves the request open and AWS launches
  a replacement.
- If cancel fails, do **not** terminate that instance.
- After terminate, delete EBS volumes whose mapping had
  `DeleteOnTermination=false`. When the flag is true, the cloud deletes them
  with the instance — still log/check the flag on future matches.

### Leftover disks on other clouds (same idea)

| Cloud | “Won’t auto-delete” signal | Action after VM/instance gone |
|-------|----------------------------|-------------------------------|
| Azure Spot | `delete_option` is Detach (not Delete) | Delete the managed disk |
| GCP Spot / preemptible | `autoDelete=false` | Delete the disk |
| OCI preemptible | Attached block volume not removed with terminate | Delete the volume |

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
4. Are docs/error strings honest about what a signal measures?
5. For spot: is cancel-before-terminate preserved? Are leftover volumes handled?
6. Do Slack/Influx still see partial successes if we log errors instead of
   aborting the job?
7. Are generated artifacts (`__pycache__`, `logs/`) gitignored and not committed?

---

## What not to bike-shed

- Style-only renames in unrelated modules.
- Requiring full multi-cloud e2e tests without a CI credential story.
- Reverting region-skip / exit-0 pipeline behavior without an incident-driven
  reason.
- Asking for CloudTrail (or equivalent) on every detach-age design unless the
  PR claims a true last-detach timestamp.

---

## File ownership hints

| Path | Notes |
|------|--------|
| `crc.py` | CLI wiring, opt-in gates, cloud/resource loop |
| `crc/service.py` | Shared `is_old` / retention tag parsing — empty age is True |
| `crc/aws/disk.py` | AWS EBS; detach_age via CloudWatch; sensitive tag skips |
| `crc/aws/spot_instance_requests.py` | Cancel then terminate; persistent Type; leftover EBS |
| `crc/aws/connectivity.py` | Skip unreachable regions; do not fail the whole job |
| `crc/azu/spot_vm.py` | Azure Spot; leftover Detach disks |
| `crc/gcp/` / `crc/oci/` | Prefer labels/freeform tags; mirror leftover-disk idea for spot |

PRs that change safety contracts above should update this file in the same PR.
