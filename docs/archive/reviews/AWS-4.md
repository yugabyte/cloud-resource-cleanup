## Coding summary

Rung AWS-4: `crc/aws/disk.py` refactored into the subpackage `crc/aws/disk/` with a registered `Cleaner` (`@register("aws", "disk")`), no behaviour change. The old import path `crc.aws.disk` and `from crc.aws.disk import Disk` (used by `crc.py`) keep working; `crc.py` is unchanged.

- **Summary.** The module was 651 lines, so it is split by responsibility (each file under 400 lines, README in `crc/aws/disk/README.md`): `cleaner.py` (`DiskCleaner`: `plan()` lists and filters, `apply()` deletes; per-region `plan_region`, `note_dry_run`, `apply_region`, `region_guard`, `log_summary`; the registry factory `build_disk_cleaner`), `client.py` (typed Protocols `Ec2Client`, `VolumePaginator`, `CloudWatchClient` of the calls used, boto3 factories, bounded-timeout `Config`), `settings.py` (`DiskSettings`, age normalisation and constructor validation in the order of main), `volumes.py` (`VolumeFilter`: state, tag, name, creation-age decisions, labels, server side filters) and `detach.py` (`DetachGuard`: CloudWatch detach-age guard). Filtering goes through `crc.core.policy` (`filter_tags_glob_all_keys`, `skip_untagged(SKIP_UNLESS_NOTAGS)`, `exception_tags_any` one tag at a time so the log names the first matching tag, `notags_all`, `name_regex_search_matches`, `exception_regex_search_matches`, `compile_patterns`, `aws_disk_yb_task` + `is_sensitive_yb_task`, `aws_disk_skips_kubernetes`, `aws_disk_creation_floor`, `is_older_than_floor`, `age_to_timedelta`); the custom age tag through `crc.core.age.get_retention_age`. `Disk` (in the package `__init__.py`) keeps the constructor `(dry_run, filter_tags, exception_tags, age, custom_age_tag_key, notags, name_regex, exception_regex, detach_age)` plus optional `runtime` and `clock`, the attributes `disks_to_delete`, `age`, `detach_age`, `get_deleted`, `count`, `delete()` and the private helpers `tests/test_aws_disk.py` uses (`_should_skip`, `_matches_filter_tags`, `_is_candidate`, `_drop_recently_attached`, `_age_to_timedelta`, `_had_errors`), and drives the cleaner region by region (client, listing, CloudWatch, deletes) in the exact call and log order of main. `now` comes from `RunContext.now` (Cleaner) or the injected clock (adapter, UTC); the cleaner never reads the clock. Both ratchet lines (`crc/aws/disk.py` in ruff `extend-exclude`, `crc.aws.disk` in the mypy override) are removed from `pyproject.toml`; nothing was added (`tests.test_aws_disk` stays listed: that existing test is unchanged).
- **Tests.** Before: 4681 passed. After: 5212 passed (+531). New: `tests/test_aws_disk_parity.py` (522: a 149-case table through the legacy adapter, the same table through the registered Cleaner path with the split call/log comparison, a non-vacuous call-ordering / no-delete-on-error check across all 149 cases, 61 quirk checks that each assert the oracle shows the quirk, factory checks, a repeated-`delete()` / `count`-logging parity check, and a direct adapter-helper parity check) and `tests/test_aws_disk_cleaner.py` (9: plan read-only, server side filters, dry-run plan, apply order, per-volume isolation, CloudWatch window and drop, connectivity skip, `supports`, adapter surface). The oracle `tests/legacy/aws_disk.txt` is byte-identical to `git show main:crc/aws/disk.py`. The table covers dry run, filters (globs, key only, two keys), exception tags, notags, untagged, regexes (invalid ones raise at init), age gate and normalisation errors, CreateTime edges (exact floor, future, naive, string, missing), custom age tag (valid, zero, bad key, unparseable fail-open, overflow), sensitive `yb_task`, Kubernetes, detach age (busy, partial, forbidden, missing result, paged results, batches of 50, CloudWatch client and connectivity errors), API errors (every skipped auth code, other `ClientError`, connectivity, unexpected, error after the first page), delete errors, duplicate ids, many volumes, and `describe_regions` connectivity fallback and auth error. All existing tests, including `tests/test_aws_disk.py`, pass unchanged. `make check` is green (ruff, mypy strict, import-linter, pytest, archtests).
- **Open issues.**
  - Cleaner path vs main, real multi-region runs only: `plan()` lists and filters every region before `apply()` deletes, so the `delete_volume` calls and the `Deleted volume` / `Failed to delete volume` lines of an earlier region come after the listing lines of later regions, and a listing error in a later region is raised before earlier regions are deleted (main had already deleted them). A dry run (`plan()` alone, which also logs the `Dry run: would delete` lines and the dry-run summary, in the same per-region order as main), a single region, results, errors and every individual stream are identical; the adapter path (`Disk.delete()`) is identical including the interleaving. The parity test splits the streams accordingly.
  - `Candidate.id` is the label main reports (`region/volume-id`, plus ` yb_task=...` or ` Name=...`), so `Plan` / `Result` ids equal `get_deleted`; the volume id is `extra["VolumeId"]`. `Plan.skipped` stays empty (skip reasons are only logged, as on main). `apply()` in a dry run makes no call and logs nothing; `apply()` logs the real-run summary lines of main (`number of AWS EBS volumes deleted`, `List of AWS EBS volumes deleted`, and the "did not complete" error line when an error was flagged) after the last region. `Result.failed` carries the failed deletes (main only logs them; the adapter ignores them).
  - The registry factory reads `filter_tags`, `exception_tags`, `notags`, `name_regex`, `exception_regex`, `custom_age_tag_key`, `age` and `detach_age` from `RunContext.options` and validates like the constructor (no default age); `crc.py` does not use the registry yet.
  - 14 new quirks are in `docs/refactor/bugs/pending/AWS-4.md` (main ones: only the first folded `yb_task` key is judged; `detach_age` is blind to stopped-instance attachments; no error ever fails the run). BUG-004, BUG-018 and BUG-022 are kept as is.
  - This rung ran past the 20 minute limit.
- **Core changes needed.** None.
- **`FILTER_VARIANTS.md` change needed (not edited).** In the `aws/disk.py` row: the module is now the package `crc/aws/disk/` (cite `main` lines, frozen in `tests/legacy/aws_disk.txt`); since AWS-4 the filter is `policy.filter_tags_glob_all_keys` (client side), the untagged rule is `policy.skip_untagged(..., UntaggedMode.SKIP_UNLESS_NOTAGS)` on the raw `Tags` list, skips use `policy.exception_tags_any` (one tag at a time) and `policy.notags_all`, regexes use `policy.name_regex_search_matches` / `policy.exception_regex_search_matches` with `policy.compile_patterns` in the constructor, yb_task and Kubernetes use `policy.aws_disk_yb_task` + `policy.is_sensitive_yb_task` and `policy.aws_disk_skips_kubernetes`, the age floor is `policy.aws_disk_creation_floor` + `policy.is_older_than_floor`, and the custom age tag is `crc.core.age.get_retention_age` (fail open, BUG-004).
- **`crc/README.md` / `tests/README.md` / `tests/legacy/README.md` (not edited).** `crc/README.md` should list the `crc/aws/disk/` subpackage (its own README, `DiskCleaner` registered as `aws` / `disk`); `tests/legacy/README.md` should list `aws_disk.txt` (exact `git show main:crc/aws/disk.py`, run by `tests/test_aws_disk_parity.py`); `tests/README.md` should list `test_aws_disk_parity.py` and `test_aws_disk_cleaner.py`. `crc/core/README.md` could name `aws` / `disk` among the registered cleaners.

## Round 1 (Argon)

Scope reviewed: `git diff e7926e2..HEAD` (`crc/aws/disk/__init__.py`, `crc/aws/disk/cleaner.py`, `crc/aws/disk/client.py`, `crc/aws/disk/detach.py`, `crc/aws/disk/settings.py`, `crc/aws/disk/volumes.py`, `crc/aws/disk/README.md`, `crc/aws/README.md`, `pyproject.toml`, `docs/refactor/bugs/pending/AWS-4.md`, `docs/refactor/reviews/AWS-4.md`, `tests/legacy/aws_disk.txt`, `tests/test_aws_disk_cleaner.py`, `tests/test_aws_disk_parity.py`) against `git show main:crc/aws/disk.py`, `AGENTS.md`, `docs/refactor/BUGS.md`, `docs/refactor/FILTER_VARIANTS.md`, and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity (`git show main:crc/aws/disk.py`)

- **SDK calls (order and arguments)** (`crc/aws/disk/__init__.py:164-177`, `crc/aws/disk/cleaner.py:134-172`, `:195-210`, `:242-252`, `crc/aws/disk/detach.py:127-184` vs `main:crc/aws/disk.py:282-343`, `:529-651`):
  - Constructor validates `age`, `detach_age`, `name_regex`, `exception_regex`, the age-gate requirement, and the 455-day `CW_MAX_LOOKBACK` limit in the exact order and with the exact `ValueError` / `OverflowError` exceptions of `main` (`crc/aws/disk/settings.py:36-99` vs `main:crc/aws/disk.py:99-168`).
  - Region list comes from `get_all_regions("ec2", "us-west-2")` (connectivity fallback to static region list, auth errors propagate, SAF-001 / SAF-004).
  - Per region in `Disk.delete()`: creates `boto3.client("ec2", region_name=region, config=BOTO_CFG)`, paginates `describe_volumes` with `[{"Name": "status", "Values": ["available"]}, ...]` (`tag:<key>` when values are non-empty, `tag-key` when empty), evaluates `_is_candidate` per volume, then if `detach_age` and candidates are non-empty creates `boto3.client("cloudwatch", region_name=region, config=BOTO_CFG)` and queries `get_metric_data` in batches of 50 across `("VolumeIdleTime", "VolumeReadOps", "VolumeWriteOps")` with `ScanBy="TimestampDescending"` (paginating `NextToken`), and finally in non-dry runs calls `delete_volume(VolumeId=volume_id)` in order before moving to the next region.
- **Filter, skip, and age decisions** (`crc/aws/disk/volumes.py:18-160`, `crc/aws/disk/detach.py:23-118` vs `main:crc/aws/disk.py:170-271`, `:367-527`):
  - State (`State == "available"`), attachments (`Attachments` empty), Multi-Attach (`MultiAttachEnabled` falsy), and untagged (`skip_untagged` with `SKIP_UNLESS_NOTAGS` on raw `Tags`) match `main`.
  - Tag map `{t["Key"]: t.get("Value", "") for t in tags or [] if t.get("Key")}` preserves `main`'s exact handling of `None` values, non-dict entries, and duplicate keys.
  - Client-side `filter_tags` uses `policy.filter_tags_glob_all_keys`; `name_regex` and `exception_regex` use `policy.name_regex_search_matches` and `policy.exception_regex_search_matches` on `Name` tag or `VolumeId`; `yb_task` and Kubernetes CSI protections use `policy.aws_disk_yb_task` + `policy.is_sensitive_yb_task` and `policy.aws_disk_skips_kubernetes`; `exception_tags` uses `policy.exception_tags_any` one tag at a time in `tag_map` order; `notags` uses `policy.notags_all`.
  - Creation age uses `core_age.get_retention_age` (fail-open per `BUG-004`), `normalize_age`, `policy.aws_disk_creation_floor`, and `policy.is_older_than_floor` (ignoring `MAX_AGE`, SAF-010).
  - CloudWatch detach-age guard merges pages per query ID (concatenating `Values`, taking last page `StatusCode`) and fails closed on missing results, non-`Complete` status, non-empty `Values`, or `_CLOUDWATCH_ERRORS`.
- **Logs, errors, and dry-run path** (`crc/aws/disk/cleaner.py:115-193`, `:200-252` vs `main:crc/aws/disk.py:296-365`, `:639-651`):
  - Every `logging.info`, `logging.warning`, and `logging.error` message matches `main`.
  - Per-volume exceptions (except `CONNECTIVITY_ERRORS`, which re-raise to the region guard) are logged and set `had_errors = True`; region `CONNECTIVITY_ERRORS` call `log_skipped_region(region, "EBS disk cleanup", e)`; region `ClientError`s with `AuthFailure`, `UnauthorizedOperation`, `OptInRequired`, or `InvalidClientTokenId` log a warning without setting `had_errors`; all other region exceptions log an error and set `had_errors = True`.

### 2. Oracle and parity test verification

- `diff -u <(git show main:crc/aws/disk.py) tests/legacy/aws_disk.txt` is empty (0 lines of diff).
- `tests/test_aws_disk_parity.py` runs both the oracle (`tests/legacy/aws_disk.txt`) and the refactored `Disk` / registered `DiskCleaner` against `FakeAws` across 149 cases and 61 quirk checks, asserting ordered SDK calls, deleted labels, counts, log records, stdout, and exceptions.
- Tested a deliberate one-line behaviour change in `crc/aws/disk/volumes.py` (`"Skipping volume %s: sensitive yb_task=%s!"`), confirmed `python3 -m pytest -q tests/test_aws_disk_parity.py` failed immediately, and reverted it.

### 3. Architecture, scope, and lane compliance

- `DiskCleaner` implements `Cleaner`, is registered with `@register("aws", "disk")`, has a strictly read-only `plan()` (`ARCH-005` passes), uses the `crc.core.policy` functions named in `docs/refactor/FILTER_VARIANTS.md` and `crc.core.age.get_retention_age`, puts `boto3` EC2 and CloudWatch clients behind typed `Protocol`s (`Ec2Client`, `VolumePaginator`, `CloudWatchClient`), and was removed from both legacy ratchet lists in `pyproject.toml` with nothing added (`ARCH-001`).
- `crc.py`, `crc/core/**`, other clouds, and shared docs forbidden by lane rules are untouched; `crc/aws/README.md` only edits the `disk` bullet and adds `### disk` subsections; `crc/aws/disk/README.md` passes `ARCH-002` symbol sync.

### BLOCKING (fixed)

1. **Invalid `Effect` column values, inaccurate `main` line citations, and 2 missing quirks in `docs/refactor/bugs/pending/AWS-4.md`** (`docs/refactor/bugs/pending/AWS-4.md:10-25`):
   - Rows 3-6 and 8-12 used non-standard `Effect` values (`error swallowed`, `wrong error type`, `invalid call`, `dead code`, `surprising filter`) instead of the four allowed values in `docs/refactor/BUGS.md` rule 5 (`deletes what it should keep`, `keeps what it should delete`, `run fails`, `cosmetic`). Fixed all `Effect` values to conform to `BUGS.md`.
   - Corrected `main` line citations on row 2 (`:545-624` -> `:529-625`), row 3 (added `:588-597`, `:607-614` for CloudWatch error flags and exact `:302-309`, `:316-323`, `:338-342`, `:356-365`, `:648-651`), row 8 (added `:454-459` where `datetime.timedelta` raises `OverflowError` on large ints and `:302-309` where per-volume handler catches it), row 10 (noted `KeyError` at `:575`, `:584` when `detach_age` is set), and row 11 (added `:417-420`, `:431-432`, `:539-548`).
   - Added 2 `NEW` low-risk rows verified against `main`: duplicate `Key` entries in `Tags` collapsing in `_tag_map` (`main:crc/aws/disk.py:170-171`, `:400`; `BUG-146` shape, `deletes what it should keep`) and `disks_to_delete` accumulating across `delete()` calls plus `count` logging at INFO on every access (`main:crc/aws/disk.py:89`, `:130-133`, `:282`, `:641`, `:647`; `BUG-080` / `BUG-081` shape, `cosmetic`).
2. **Vacuous parametrizations in `test_cleaner_error_path_never_deletes_after_listing_error`, conditional skip of error-case call/log checks in `test_cleaner_protocol_matches_main`, and missing `Disk.SENSITIVE_YB_TASK` / direct helper parity checks** (`crc/aws/disk/__init__.py:75`, `tests/test_aws_disk_parity.py:989-993`, `:1010-1025`, `:1235-1315`):
   - `test_cleaner_error_path_never_deletes_after_listing_error` made no assertion on the 131 cases where `new.error is None`; updated it so non-error dry-run cases assert no `delete_volume` call and non-error live cases assert all read/listing/CloudWatch calls precede all `delete_volume` calls (`new.calls == reads + deletes`).
   - Removed the unnecessary `if old.error is None or not case.regions:` guard in `test_cleaner_protocol_matches_main` so all 18 error cases also assert `new.calls == old.calls` and `new.logs == old.logs`.
   - Restored the `SENSITIVE_YB_TASK` class attribute on `Disk` (`crc/aws/disk/__init__.py:75`), added duplicate-`Key` coverage to `yb-task-key-variants`, and added `test_second_delete_accumulates_and_count_logs_like_main` and `test_adapter_private_helpers_match_main` in `tests/test_aws_disk_parity.py`.

### NIT (left)

- `DiskCleaner` caches per-region EC2 clients in `self._clients` during `plan_region` (`crc/aws/disk/cleaner.py:76`, `:139`, `:159`) so `apply_region` reuses the client created in `plan_region` without a second `boto3.client` call.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 111 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 5212 passed |

Test count: 4681 before AWS-4 (`e7926e2`), 5210 before review, 5212 after review additions (+531).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff e7926e2..HEAD` (4 commits, including round 1's `391cfb7`), compared with `git show main:crc/aws/disk.py`, `AGENTS.md`, `ARCHITECTURE.md`, `docs/refactor/BUGS.md`, `CLEANUP_SAFETY.md` and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### Round 1 fixes assessment

- `Disk.SENSITIVE_YB_TASK` restored (`crc/aws/disk/__init__.py:75`): correct. It is `policy.SENSITIVE_YB_TASK` (a `frozenset`) where main has a mutable `set` (`main:crc/aws/disk.py:67-74`). The two are equal, nothing reads or mutates it, and there is no behaviour change.
- `test_cleaner_protocol_matches_main` (`tests/test_aws_disk_parity.py:990-993`): the guard removal makes the test stronger. All error and single-region cases now assert `calls` and `logs` equality.
- `test_cleaner_error_path_never_deletes_after_listing_error` (`tests/test_aws_disk_parity.py:1020-1026`): this is no longer vacuous. Dry runs and error cases assert no `delete_volume` call. Live runs assert that every read precedes every delete (`new.calls == reads + deletes`).
- `yb-task-key-variants` case `f` and its quirk check: correct (duplicate `yb_task` keys collapse to the last value, as `_tag_map` does on main at `:170-171`).
- `test_second_delete_accumulates_and_count_logs_like_main` and `test_adapter_private_helpers_match_main`: both compare the oracle with the adapter and assert concrete outcomes.
- Pending rows: the `Effect` values now conform to `BUGS.md`. I spot-checked these rows against `git show main:crc/aws/disk.py`: yb_task first match at `:173-178` and `:236-243`, auth-code skip at `:326-337`, duplicate keys at `:170-171`, `:400` and `crc/service.py:302`, and `disks_to_delete` at `:89` and `:130-133`. All citations are accurate.
- No round 1 change altered production behaviour or weakened a test.

### Independent review

- **Parity, adapter path.** I traced every decision against main:
  - Constructor validation order and messages (`settings.py:61-99` vs `main:99-123`, `:135-168`).
  - Untagged, filter-tag, name-regex, `should_skip` order, exception tags one key at a time, notags, exception regex (`volumes.py:62-122` vs `main:235-271`, `:367-393`).
  - CreateTime floor and custom age tag (`volumes.py:136-159` vs `main:395-452`).
  - CloudWatch batching, query ids, paging, merge and fail-closed reasons (`detach.py` vs `main:461-625`).
  - Labels (`volumes.py:23-34` vs `main:627-637`).
  - Region error policy (`cleaner.py:115-132` vs `main:324-342`).
  - Summary lines (`cleaner.py:174-193` vs `main:344-365`).

  The policy helpers I checked (`filter_tags_glob_all_keys`, `exception_tags_any` / `_key_matches`, `notags_all`, `aws_disk_yb_task`, `is_sensitive_yb_task`, `aws_disk_skips_kubernetes`, `aws_disk_creation_floor`, `is_older_than_floor`, `compile_patterns`, `age_to_timedelta`) match main's inline code expression for expression.

  Labels are now computed in `plan_region` (after CloudWatch) instead of inside the delete loop. `volume_label` can only raise on inputs that already raised in `is_candidate` (non-dict tag entries, non-string keys or values), so the per-volume log order cannot diverge.
- **Oracle.** `diff <(git show main:crc/aws/disk.py) tests/legacy/aws_disk.txt` is empty. `tests/test_aws_disk.py`, `crc.py` and `crc/core/**` are unchanged since `e7926e2`.
- **Safety.**
  - `plan()` makes no mutating call (ARCH-005 archtest passes).
  - The age gate is required, and the floor ignores `MAX_AGE` (SAF-006, SAF-010).
  - Incomplete or failed CloudWatch answers keep the volume (`detach.py:104-118`, `:162-172`).
  - Non-connectivity `describe_regions` errors still propagate (SAF-004).
  - The `aws` + `disk` breaker lives in `crc.py`, which is untouched (SAF-014).
- **Tests are not vacuous.** I removed `self.had_errors = True` from `DiskCleaner._delete_volume` (`cleaner.py:250`). Nine tests across the parity, cleaner and legacy `tests/test_aws_disk.py` suites failed; I then restored the line.
- **Architecture, typing and lane.** `import-linter` keeps 5 contracts, and `mypy` strict and `ruff` are clean. Both ratchet lines were removed from `pyproject.toml` and nothing was added. Every file is under 400 lines, and the package README symbol list matches the code. `crc/aws/README.md` adds only `### disk` subsections plus the `disk` bullet.

### BLOCKING (fixed)

None.

### NIT (left)

1. **The coding summary's first open issue is inaccurate for this module.** It claims "a listing error in a later region is raised before earlier regions are deleted". In fact, `region_guard` (`crc/aws/disk/cleaner.py:115-132`) swallows every region error in `plan()`, and only `describe_regions` errors propagate, which happens before any region. The real difference is only the interleaving of delete calls and log lines. `crc/aws/disk/README.md` and the `### disk` safety subsection describe this correctly.
2. **`Result.failed` holds only `ClientError` delete failures.** A non-`ClientError` delete failure is logged as "failed considering volume" and flagged, but is not added to `Result.failed` (`crc/aws/disk/cleaner.py:165-167`). Main has no `Result`, and the adapter ignores `failed`, so there is no parity impact.
3. **The CloudWatch window end differs slightly in the Cleaner path.** It is `RunContext.now` (`cleaner.py:221-224`), whereas main reads the clock right before the CloudWatch call (`main:550`). The window therefore shifts by the listing time (seconds). The adapter calls the clock afresh at that point (`__init__.py:160-163`) and matches main.
4. **`DiskCleaner._clients` keeps per-region EC2 clients for the life of the instance** (`cleaner.py:76`, `:139`, `:159`). This is harmless for one run per instance.
5. **One test assertion is weak.** `assert len(deletes) >= len(new.deleted)` (`tests/test_aws_disk_parity.py:1026`) adds little, but the ordering assertion before it carries the check.
6. **Shared docs still name `crc/aws/disk.py`** (`CLEANUP_SAFETY.md:73`, `docs/refactor/FILTER_VARIANTS.md`). These are outside the lane; the coding summary lists the needed changes for the integrator.

### New bugs

One row added to `docs/refactor/bugs/pending/AWS-4.md`. Server-side EC2 tag filters support only `*` and `?`, but the client-side `fnmatchcase` re-check also treats `[...]` as a character class. As a result, a `filter_tags` value containing `[` never matches, and those volumes are kept (`main:crc/aws/disk.py:213-220`, `:273-280`, `:295`; keeps what it should delete, low).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 111 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 5212 passed |

Test count: 5212 (4681 before the rung; no change in round 2).

ROUND 2 VERDICT: APPROVE
