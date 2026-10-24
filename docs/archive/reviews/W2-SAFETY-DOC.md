# W2-SAFETY-DOC review

## Coding summary

Lane W2-SAFETY-DOC (docs only): `CLEANUP_SAFETY.md` and the root `README.md` now describe the code of branch `w2/safety-doc`. No code, test, lock or other doc file was touched. `make check` is green (archtest `SAF-LOCK` passes: no SAF ID was added, removed or renumbered).

### CLEANUP_SAFETY.md, SAF items changed (claim, proof)

| ID | Change | Proof in code |
|----|--------|---------------|
| SAF-002 | `sys.exit(0)` now lives in `crc/cli/scan.py`; scan forces `--dry_run` | `crc/cli/scan.py:149`, `:152`; dry_run forced at `:138-140`; the only `sys.exit` in `crc/` |
| SAF-007 | AWS disk age validation is in `crc/aws/disk/settings.py` (package, not `disk.py`) | `crc/aws/disk/settings.py:36` (`normalize_age`), `:86` (age gate required) |
| SAF-009 | Rewritten: `get_retention_age` users fail closed with `KEEP_AGE`, `is_old` never treats it as old, `MAX_AGE` included, `bad_age_tag` counted | `crc/core/age.py:23` (`KEEP_AGE`), `:216-218` (`is_old` returns False before `MAX_AGE`), `:249-280` (`get_retention_age`, `skip("bad_age_tag")` at `:279`); callers: `crc/aws/disk/volumes.py:175`, `crc/aws/kms.py:247`, `crc/aws/vm/cleaner.py:342`, `crc/azu/disk.py:230`, `crc/azu/vm/cleaner.py:299`, `crc/gcp/disk.py:274`, `crc/gcp/vm.py:258`, `crc/oci/disk/cleaner.py:323`, `crc/oci/vm/cleaner.py:256` |
| SAF-014 | Breaker is in `dispatch.py`; `--cloud all` with `disk`/`all` raises in `validate_targets` before any cleaner | `crc/cli/dispatch.py:26-28`, `:227-228` |
| SAF-017 | Path `crc/aws/disk.py` became `crc/aws/disk/` | `crc/aws/disk/settings.py:86-98` |
| SAF-025 | AWS floor: `--age` omitted: max(tag, detach_age); `--age` only: tag or age; both: (tag or age) + `detach_age`; `MAX_AGE` never shortens | `crc/core/policy.py:341-349` (`aws_disk_creation_floor`), `crc/aws/disk/volumes.py:189` and `:197` (`_detach_floor`), `:207-214` (adds `detach_age` only when both are set; BUG-283); CloudWatch window uses only `detach_age`: `crc/aws/disk/cleaner.py:231-240`; `older_than_floor` ignores `MAX_AGE`: `crc/aws/disk/volumes.py:69-75` |
| SAF-031 | Spot yb_task rule named precisely (exact keys `yb_task` / `yb-task`), no longer "same as disk" because disk folds key names | `crc/core/policy.py:24`, `:277-279` (spot) vs `:264-268` and `crc/aws/disk/volumes.py:58-66` (disk, BUG-282) |
| SAF-043 | GCP Spot keeps an unlabelled VM when `--filter_tags` is empty (BUG-228); OCI relies on age only | `crc/gcp/spot_vm/instances.py:94-95`; OCI `crc/oci/spot_vm/decide.py:85-100` has no such rule |
| SAF-044 | Extended: GCP leftover disk custom age label (BUG-242); OCI volume owned only if created at/after the instance and no other attachment except `DETACHED`; unknown keeps | `crc/gcp/spot_vm/disks.py:86-104`, `:107-135`; `crc/oci/spot_vm/leftovers.py:24` (`_NOT_ATTACHED`), `:120-150` (`volume_owned`) |
| SAF-045 | Extended to the OCI VM cleaner (skip reason `managed`) | `crc/oci/vm/cleaner.py:40`, `:235-240`; `crc/oci/spot_vm/decide.py:63-65`; GCP `crc/gcp/spot_vm/instances.py:49-53`, `:108-109` |
| other | Paths and wording only: SAF-001 (`crc/aws/connectivity.py:26`), SAF-003/004 (`crc/aws/disk/cleaner.py:120-139`, `crc/aws/_base.py:14-38`, `crc/aws/disk/__init__.py:51-52,170`), SAF-005 (`crc/azu/spot_vm/cleaner.py:162-173`), SAF-021..024 (`crc/gcp/disk.py:269-281`), SAF-046 (`crc/oci/spot_vm/cleaner.py:186-191`), SAF-037 (`crc/cli/dispatch.py:111-119`). "On main" headings and the "(on main)" wording are gone |

### CLEANUP_SAFETY.md, other text

- New "CLI validation" section (untagged): `validate_targets` runs (when `--scan_tag` is not set) before `set_max_age` and the dispatch (`crc/cli/main.py:51-53`, `crc/cli/dispatch.py:217-236`, `:255`); unsupported pairs (`crc/cli/dispatch.py:187-200`); `--cloud all --resource disk|all` raises (`:227-228`); blank `--scan_tag` raises (`crc/cli/inputs.py:148-158`, called at `crc/cli/main.py:51`).
- New untagged "Per-resource keep rules": AWS disk `yb_task` key folding (`crc/aws/disk/volumes.py:58-66`, BUG-282), GCP disk errors keep the disk (`crc/gcp/disk.py:171-173,187-190,220-246`), OCI disk unknown attachment state keeps the volume (`crc/oci/disk/cleaner.py:142-147`, `:198-199`, `:220-230`: listing errors skip the region and nothing is deleted from it), OCI VM evaluation error keeps the instance (`crc/oci/vm/cleaner.py:175-191`). They have no SAF ID because `tests/archtest/safety_lock.json` is owner-only and an unlisted ID fails `SAF-LOCK`.
- Module map: lists `crc/cli/` and describes `crc.py` as a re-export shim (`crc.py:3-5` comment, `:104` the only call; everything else is imports and `__all__`); `crc/service.py` row notes `KEEP_AGE`; `crc/core/`, `crc/oci/disk/`, `crc/oci/vm/` rows added; `*.py` paths that are now packages (`crc/aws/disk/`, `crc/aws/spot_instance_requests/`, `crc/azu/spot_vm/`, `crc/gcp/spot_vm/`, `crc/oci/spot_vm/`) corrected; the `crc/gcp/disk.py` row is the FIX-GCP-DISK text.
- Owner-action sections with "None" (AWS-EIP, AWS-KMS, AWS-SNAP, AWS-VPC, AZU-*, CLI-ALL, GCP-IP, GCP-VM, OCI-SPOT optional text folded into SAF-044) needed nothing more.

### README.md fixes

- Example 6 ("To Delete AWS VPCs") used `--resource vm`; now `--resource vpc` (`crc/cli/args.py:30`).
- Log path: code writes `logs/crc.log` (`crc/service.py:23-34`: `logs_dir = "logs"`, `logs_file = "crc.log"`), truncated each run (`crc/utils.py:30`, `mode="w"`), debug level (`crc/utils.py:13-48`). The old text said "in the same directory as the script" and "resource type, name and date deleted".
- Clouds: `oci` added to `--cloud` (`crc/cli/args.py:12`). Resources: full list `disk ip keypair vm vpc kms nic spot_instance_requests spot_instances snapshot all` (`crc/cli/args.py:25-37`) with the AWS-only / Azure-only / not-on-OCI rules (`crc/cli/dispatch.py:187-200`), the `--cloud all` rules, and the GCP/OCI `spot_instances` opt-in.
- Flags: short options (`-c -p -r -o -s -f -e -n -x -a -t -m -i`) added (`crc/cli/args.py`); `--scan_tag` documented (`crc/cli/args.py:265-272`, `crc/cli/scan.py`; AWS VMs only, `crc/cli/vm_ops.py:93-97`; needs `--resource vm`, `crc/cli/inputs.py:115-119`, `--resource all` with a single cloud raises); `--kms_pending_window` default 7 and `--kms_key_description` / `--kms_user` required for kms/all (`crc/cli/inputs.py:124-128`); `stop` only with `vm` (`crc/cli/inputs.py:112-113`); `--age` not used for GCP disks, VPC, NIC; snapshot fixed at 90 days (`crc/cli/dispatch.py:127-129`); spot resources need a positive age (`crc/core/age.py:40-49`, callers in spot adapters); `--max_age` exports `MAX_AGE` (`crc/cli/clients.py:33-37`); `--detach_age` bullet rewritten for the SAF-025 floor and GCP behaviour; Slack/Influx need their tokens (`crc/cli/inputs.py:101-107`).
- Features list: AWS disks (opt-in), snapshots, VPCs, Azure NICs; custom age tag fail-closed note; scan tag.
- Prerequisites: Python 3.11 (AGENTS.md), `oci`, `slack_sdk`, `influxdb-client` (`requirements.txt`); new OCI environment variables (`crc/oci/_base.py:45-48`).
- Examples: 1 had an unquoted `--max_age {'days': 14}`; 5 said "created in the last 2 days" (it selects resources older than 2 days); 6 fixed; added 10 (AWS disk with `--detach_age`), 11 (Azure NIC), 12 (snapshot), 13 (`--scan_tag`).
- Typos: "esources" fixed, "specfic" fixed; "on main" wording removed.

### Owner actions left

1. `tests/archtest/safety_lock.json` is owner-only: assign SAF IDs and locked tests (or `gaps` entries) to the untagged rules in "CLI validation" and "Per-resource keep rules", then tag them in `CLEANUP_SAFETY.md`.
2. Per-package READMEs still say the custom age tag is fail open: `crc/azu/vm/README.md:39`, `crc/aws/vm/README.md:40`, `crc/gcp/README.md:59` (other lanes own them).
3. `crc/cli/args.py:156-176` `--detach_age` help is stale (see New bugs).

## New bugs

- `crc/cli/args.py:170-173`: the `--detach_age` help says the AWS tag floor is `max(tag, detach_age)` only in detach_age-only mode and never mentions that with both `--age` and `--detach_age` the CreateTime floor is the effective age plus `detach_age` (`crc/aws/disk/volumes.py:207-214`, BUG-283).
- `crc/cli/args.py:39-44`: `--resource` `help=` string omits `'vpc'` even though `'vpc'` is in `choices` (`crc/cli/args.py:30`).
- `crc/cli/args.py:105`: `--name_regex` `help=` string says `"Only applies to AWS keypairs and GCP IPs"`, omitting AWS disks, GCP disks and Azure NICs (`crc/cli/dispatch.py:35,56`, `crc/cli/resource_ops.py:163,195`).
- `crc/core/policy.py:24`, `:277-279` vs `:264-268`: the spot-volume `yb_task` protection matches only the exact keys `yb_task` / `yb-task` (case sensitive) while the AWS disk cleaner folds case and `-`/`_` (BUG-282). A leftover spot volume with key `YB_TASK=prod` is not protected by its own tag.
- `crc/oci/_base.py:18-22`: tenancy OCID, user OCID, fingerprint, home region and compartment are hard-coded constants, so `--cloud oci` can only ever address one tenancy; README can only document the key variables.
- `crc/cli/scan.py:149-152`, `crc/cli/vm_ops.py:93-97`: only AWS VMs implement `find_instances_with_tag`. For Azure, GCP and OCI the scan prints "scan not implemented" and the run ends with "No matching instances found. Nothing to clean." and exit 0, which reads as a clean result.
- `crc/cli/constants.py:5`: `RESOURCES` has no `vpc`, `spot_instance_requests` or `spot_instances`, so `--cloud all --resource all` never runs them (and `--cloud all --resource all` raises anyway at `crc/cli/dispatch.py:227-228`). The help text of `--resource all` does not say so.
- `crc/core/selection.py:55-111`: a second implementation of the flag and pair validation (`ValueError` messages of `main()`) next to `crc/cli/inputs.py` and `crc/cli/dispatch.py`; the two can drift apart.
- `crc/core/age.py:216`: `is_old` recognises the bad-tag marker by equality with `KEEP_AGE`, so a real `{'days': 1000000}` tag or `--age` is also read as "keep". Harmless (fail closed) but undocumented.
- `crc/cli/inputs.py:118`, `:128`: error text says `--resources` and "reuired"; the flag is `--resource`.

## Round 1 (Argon)

Scope reviewed: `git diff cea0a8a..HEAD` (`CLEANUP_SAFETY.md`, `README.md`, `docs/refactor/reviews/W2-SAFETY-DOC.md`).

### Findings (fixed in review)

1. **`CLEANUP_SAFETY.md:25-27`**: said "`main()` calls it [i.e. `validate_targets`] before the read-only scan and the dispatch". In `crc/cli/main.py:52-53`, `validate_targets(targets)` is guarded by `if options.scan_tag is None:` so read-only `--scan_tag` runs skip pair validation before `set_max_age` and `run_scan`. Clarified to "`main()` calls it (when `--scan_tag` is not set) before `set_max_age` and dispatch, and `run_dispatch` calls it again."
2. **`README.md:140-143,147,149,207`**:
   - `--name_regex` and `--exception_regex` bullets (`:142-143`) still said "**This option only applies to AWS keypairs and GCP IPs**" even though `--name_regex` / `--exception_regex` are also wired and used for AWS disks (`crc/cli/resource_ops.py:163-164`, `crc/aws/disk/volumes.py:105,148`), GCP disks (`crc/cli/resource_ops.py:195-196`, `crc/gcp/disk.py:198,251`), and Azure NICs (`crc/cli/dispatch.py:56`, `crc/azu/nic.py:240-244`, Example 11). Updated both bullets to list all five resources (and noted that an empty `name_regex` on Azure NICs selects nothing).
   - Updated `--filter_tags`, `--exception_tags`, and `--notags` bullets (`:140-141,147`) to also mention Azure NICs, AWS snapshots (which ignore `filter_tags` and `notags`), AWS KMS (which has no `notags`), and `--resource vpc` (which requires a non-empty `filter_tags`), and removed the stale claim that `exception_tags` does not apply when `filter_tags` is unset.
   - Updated `crc/aws/disk.py` to `crc/aws/disk/settings.py` in `--detach_age` (`:149`), and clarified Example 10's description (`:207`) to note the `3 + 7 = 10` days `CreateTime` floor when both `--age` and `--detach_age` are set.
3. **`docs/refactor/reviews/W2-SAFETY-DOC.md` line citations**: corrected pre-split line numbers (`crc/cli/scan.py:138-140`, `crc/service.py:23-34`, `crc/utils.py:13-48`, `crc/cli/args.py:12,25-37,156-176,265-272`) and added `crc/cli/args.py:39-44` and `:105` to `## New bugs`.

### Checklist verification

1. **Every changed (and unchanged) `SAF-NNN` statement is true for the code on this branch; nothing weakened; no SAF ID removed or renumbered**:
   - Verified `SAF-001` through `SAF-046` against the code on `w2/safety-doc` (`SAF-002` in `crc/cli/scan.py:138-152`; `SAF-007` in `crc/aws/disk/settings.py:36-58,83-99`; `SAF-009` in `crc/core/age.py:23,216-218,249-280` and all 9 callers; `SAF-014` in `crc/cli/dispatch.py:25-28,227-228`; `SAF-017` in `crc/aws/disk/settings.py:86-98`; `SAF-025` in `crc/core/policy.py:341-349`, `crc/aws/disk/volumes.py:69-75,189-214`, `crc/aws/disk/cleaner.py:231-240`; `SAF-031` in `crc/core/policy.py:24,277-279`, `crc/aws/spot_instance_requests/rules.py:46-50`, `crc/aws/disk/volumes.py:58-66`; `SAF-043` in `crc/gcp/spot_vm/instances.py:94-95`, `crc/oci/spot_vm/decide.py:72-75`; `SAF-044` in `crc/gcp/spot_vm/disks.py:86-135`, `crc/oci/spot_vm/leftovers.py:24,120-152`, `crc/azu/spot_vm/removal.py:70-122`; `SAF-045` in `crc/oci/vm/cleaner.py:40,235-240`, `crc/oci/spot_vm/decide.py:63-69`, `crc/gcp/spot_vm/instances.py:49-53,108-109`).
   - `SAF-LOCK` (`tests/archtest/test_safety_lock.py`) passes: all 46 `SAF-NNN` IDs match `tests/archtest/safety_lock.json` (`locks` + `gaps`).
   - Concise, no refactor history, no `BUG-NNN` or rung names in `CLEANUP_SAFETY.md` or `README.md`.
2. **Every `## Owner action` from `docs/refactor/reviews/FIX-*.md` is applied or explained**:
   - `FIX-AWS-DISK.md` (`SAF-025` floor + `yb_task` key folding), `FIX-CORE-AGE.md` (`SAF-009` `KEEP_AGE` + `crc/service.py` module map), `FIX-CLI-MERGE.md` / `FIX-CLI-ROUTE.md` (`CLI validation` section), `FIX-GCP-DISK.md` (`crc/gcp/disk.py` module map + per-resource keep rule), `FIX-GCP-SPOT.md` (`SAF-043`, `SAF-044`), `FIX-OCI-DISK.md` (OCI disk keep rule + `crc/oci/disk/` module map), `FIX-OCI-SPOT.md` (`SAF-044`), `FIX-OCI-VM.md` (`SAF-045` + OCI VM keep rule + `crc/oci/vm/` module map) are all applied in `CLEANUP_SAFETY.md`. All other `FIX-*.md` files have `Owner action: None`.
3. **Root `README.md` matches `crc/cli/args.py` and `crc/cli/dispatch.py`**:
   - Every flag (all 24 flags and their short options), cloud (`aws`, `azure`, `gcp`, `oci`, `all`), resource (`disk`, `ip`, `keypair`, `vm`, `vpc`, `kms`, `nic`, `snapshot`, `spot_instance_requests`, `spot_instances`, `all`), log path (`logs/crc.log`), and all 13 examples match `crc/cli/args.py`, `crc/cli/inputs.py`, and `crc/cli/dispatch.py`.
4. **File scope**:
   - `git diff --name-only cea0a8a..HEAD` touches only `CLEANUP_SAFETY.md`, `README.md`, and `docs/refactor/reviews/W2-SAFETY-DOC.md`.

### Gates

Ran `pip install -r requirements.txt -r requirements-dev.txt && make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | **43010 passed in 165.36s** |

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff cea0a8a..HEAD`. I checked the Round 1 fixes first. The `validate_targets` wording ("when `--scan_tag` is not set", `crc/cli/main.py:51-53`), the `--name_regex` / `--exception_regex` / `--filter_tags` / `--notags` resource lists and the Example 10 floor of 3 + 7 days are correct. Then I did the checks again on my own.

### Checks

1. **SAF statements match the code on this branch.**
   - SAF-002: the only `sys.exit` in non-test code is `crc/cli/scan.py:149,152`, and dry run is forced at `:138-140`.
   - SAF-009: `KEEP_AGE` is set at `crc/core/age.py:23`. `is_old` returns False before `_apply_max_age_override` (`:216-220`). `get_retention_age` counts `bad_age_tag` and returns `KEEP_AGE` (`:279-280`). The callers are exactly the 9 modules listed (grep: `crc/aws/{disk/volumes,kms,vm/cleaner}.py`, `crc/azu/{disk,vm/cleaner}.py`, `crc/gcp/{disk,vm}.py`, `crc/oci/{disk,vm}/cleaner.py`) plus the `crc/service.py:92` wrapper. AWS disk does not use `is_old`: `KEEP_AGE` becomes the CreateTime floor (`crc/core/policy.py:341-349`), which is never reached, so the volume is kept just the same.
   - SAF-010: `_apply_max_age_override` (`crc/core/age.py:154-163`) still accepts a zero `MAX_AGE`.
   - SAF-014: `crc/cli/dispatch.py:25-28`, `:227-228`.
   - SAF-025: `aws_disk_creation_floor` (`crc/core/policy.py:341-349`); `_detach_floor` adds `detach_age` only when both are set (`crc/aws/disk/volumes.py:207-214`); `older_than_floor` ignores `MAX_AGE` (`:69-75`).
   - SAF-043: `crc/gcp/spot_vm/instances.py:94-95`.
   - SAF-044 (OCI): `crc/oci/spot_vm/leftovers.py:24`, `:120-152`.
   - SAF-045: `crc/oci/vm/cleaner.py:40`, `:235-240`.
   - SAF-001: `log_skipped_region` is at `crc/aws/connectivity.py:26`.
   - CLI validation section: `crc/cli/dispatch.py:187-236`, `crc/cli/inputs.py:148-158`.
   - `crc.py` re-export shim: only imports, `__all__` and `main()` at `:103-104`.
   - All 46 IDs are present and in order, and `SAF-LOCK` passes. No true statement was weakened: SAF-003, SAF-005 and SAF-014 lost implementation detail or history, not safety meaning.
2. **Owner actions.** Every `FIX-*.md` "Owner action" section is applied or is "None": AWS-DISK (SAF-025 and the `yb_task` folding rule), CORE-AGE (SAF-009 and the `crc/service.py` row), CLI-MERGE and CLI-ROUTE (CLI validation), GCP-DISK (module row and keep rule), GCP-SPOT (SAF-043, SAF-044), OCI-DISK (keep rule), OCI-SPOT (SAF-044) and OCI-VM (SAF-045 and keep rule). The per-package READMEs that still say "fail open" (FIX-CORE-AGE) belong to other lanes. Round 1 already lists them under "Owner actions left".
3. **README.**
   - Every flag, short option, cloud and resource exists in `crc/cli/args.py`.
   - The `stop`, `--resource all`, kms and `--scan_tag` rules match `crc/cli/inputs.py:111-128,148-158` and `crc/cli/scan.py`.
   - The log path matches `crc/service.py` and `crc/utils.py`.
   - The OCI and Azure environment variables match `crc/oci/_base.py:45-48` and `crc/azu/_base.py:27-31`.
4. **File scope.** Only `CLEANUP_SAFETY.md`, `README.md` and this file changed.

### Findings (fixed in review)

1. **`README.md` Examples 4 and 5**: `--cloud all --resource vm` without `--project_id`. `--cloud all` runs GCP, and `GCP_VM` passes `project_id` unchecked to `list_zones` (`crc/cli/vm_ops.py:72-80`, `crc/gcp/vm.py:99`), so the GCP pass fails. I added `--project_id <project>` to both examples and noted on the `project_id` bullet that `--cloud all` needs it too.
2. **`README.md` Example 3**: it said "older than 14 days" but sets only `--detach_age`, and GCP ignores `--age` for disks. I changed it to "detached for at least 14 days".

### New bugs (not fixed)

- `crc/cli/vm_ops.py:72-80`: GCP VM (`delete_vm` / `stop_vm`) has no `project_id` check, unlike GCP Spot (`:170-171`). `--cloud all` without `--project_id` reaches the GCP SDK with `project=None`, after the AWS and Azure passes have already deleted or stopped resources.
- `crc/cli/inputs.py:115-119` with `crc/cli/dispatch.py:227-228`: the default `--resource all` can never run. A single cloud raises "needs `--cloud all`", and `--cloud all --resource all` raises at the EBS breaker. The `--resource` default and help still present it as a mode.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt`, then `make check` (wall time 179 s):

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 251 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | **43010 passed in 161.64s** |

ROUND 2 VERDICT: APPROVE
