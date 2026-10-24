## Coding summary

Rung AWS-3: `crc/aws/vm.py` refactored into a registered `Cleaner` (`@register("aws", "vm")`), no behaviour change.

- **Summary.** The module is split by responsibility (the single file would have been about 490 lines): `vm_cleaner.py` holds `VmCleaner` (`plan()` lists with `describe_instances` / `describe_network_interfaces` and decides, no mutating call; `apply()` calls `terminate_instances` / `stop_instances`), `VmSettings`, the closing-summary helpers and the registry factory `build_vm_cleaner`; `vm_api.py` holds the typed `VmClient` Protocol (and the tag-scan Protocols); `vm_scan.py` holds the read-only `find_instances_with_tag`; `vm.py` keeps the `VM` adapter (`crc.py` unchanged: constructor `(dry_run, filter_tags, exception_tags, age, custom_age_tag_key, notags)`, `get_deleted`, `get_stopped`, `delete_count`, `stopped_count`, `delete()`, `stop()`, `find_instances_with_tag()`, `instance_names_to_delete/stop`, plus the helper methods) and the `VmRuntime` (client factory, region lister, UTC clock, scan factories) that tests replace through the public `vm.runtime` attribute (the constructor is already at the 6-argument ruff limit, so no 7th parameter). The adapter drives the cleaner region by region in the exact call and log order of `main`. Filtering: exception tags through `policy.exception_tags_any` one tag at a time (first-match, malformed tags still raise `KeyError`), notags through `policy.notags_all`, age through `policy.effective_age` + `core.age.is_old` / `get_retention_age`; `now` comes from the injected clock (`context.now` in the Cleaner path), never read inside the cleaner. The closing summary lines are logged by `plan()` in a dry run and by `apply()` in a real run. Both ratchet lines for `crc/aws/vm.py` / `crc.aws.vm` are removed from `pyproject.toml`; nothing was added.
- **Tests.** Before: 4317 passed. After: 4692 passed (+375): `tests/test_aws_vm_parity.py` (365: 153 cases through the legacy adapter, the same 153 through the registered Cleaner path with split call/log streams, 35 quirk checks, 4 tag-scan cases, 6 helper checks, 8 `_get_filtered_instances` helper checks, 4 repeated-operation checks, 2 factory/coverage checks) and `tests/test_aws_vm.py` (10: plan read-only, apply order and error isolation, stop, dry run, adapter with injected runtime). The oracle `tests/legacy/aws_vm.txt` is byte-identical to `git show main:crc/aws/vm.py`. A deliberate one-character change to a log message fails 113 cases. `make check` equivalent (ruff, mypy strict, import-linter, pytest incl. archtests) is green.
- **Open issues.**
  - Cleaner path vs `main`, abnormal runs and several regions only: `plan()` lists and decides every region before `apply()` mutates, so (a) a listing error in a later region is raised before earlier regions were terminated, whereas `main` had already acted on them; (b) the `Error occured while ...` / `Instance: X deleted.` lines come after all listing lines of later regions. SDK calls, results, errors and every individual log stream (listing, mutating, summary) are identical; with one region (or a dry run) the whole log is identical. The adapter path (`VM.delete()` / `VM.stop()`) is identical including interleaving.
  - The Cleaner path in a dry run logs the closing summary from `plan()` (the engine never calls `apply()` then); `apply()` with a dry-run context makes no call and logs nothing.
  - The factory reads `filter_tags`, `exception_tags`, `notags`, `age`, `custom_age_tag_key`, `instance_state` (default `["running"]`) from `RunContext.options`; `crc.py` does not use the registry yet. `Result.succeeded` holds instance names (what `get_deleted` returns), `Result.failed` the failed instance ids.
  - No `age` validation exists on main (`age={}` matches everything), kept as is; `find_instances_with_tag` stays on `VM` (read-only, used by `crc.py`).
  - 14 quirks are in `docs/refactor/bugs/pending/AWS-3.md` (3 added in round 1; main one: no age gate validation; stop dry run records ids). BUG-004, BUG-020, BUG-021 are kept as is.
- **Core changes needed.** None.
- **`FILTER_VARIANTS.md` change needed (not edited).** In the `aws/vm.py` row, add that since AWS-3 exception tags go through `policy.exception_tags_any` applied one tag at a time (first match wins, a tag without `Key`/`Value` raises `KeyError`, main `:261-272`), notags through `policy.notags_all` on the full tag dict, the custom age through `policy.effective_age(get_retention_age(...), age)` + `core.age.is_old` (main `:218-235`), and that the oracle is `tests/legacy/aws_vm.txt`.
- **`crc/README.md` / `tests/README.md` / `tests/legacy/README.md` (not edited).** `tests/legacy/README.md` should list `aws_vm.txt` (exact `git show main:crc/aws/vm.py`, run by `tests/test_aws_vm_parity.py`); `tests/README.md` should list `test_aws_vm_parity.py` and `test_aws_vm.py`; `crc/README.md` should mention the new `aws` / `vm` registration and the `vm_cleaner.py`, `vm_api.py`, `vm_scan.py` modules.

## Round 1 (Argon)

Scope reviewed: `git diff d3e97b0..HEAD` (`crc/aws/vm.py`, `crc/aws/vm_cleaner.py`, `crc/aws/vm_api.py`, `crc/aws/vm_scan.py`, `crc/aws/README.md`, `pyproject.toml`, `docs/refactor/bugs/pending/AWS-3.md`, `docs/refactor/reviews/AWS-3.md`, `tests/legacy/aws_vm.txt`, `tests/test_aws_vm.py`, `tests/test_aws_vm_parity.py`) against `git show main:crc/aws/vm.py`, `git show main:crc.py`, `AGENTS.md`, `docs/refactor/BUGS.md`, and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity (`git show main:crc/aws/vm.py`)

- **SDK calls (order and arguments)** (`crc/aws/vm.py:127-209`, `crc/aws/vm_cleaner.py:110-252`, `crc/aws/vm_scan.py:45-94` vs `main:crc/aws/vm.py:110-163`, `:165-245`, `:295-403`):
  - Builds the server-side filter list `[{"Name": "instance-state-name", "Values": instance_state}]` plus `{"Name": f"tag:{key}", "Values": value}` for each `filter_tags` entry and logs `f"Filters created: {filters}"` before listing regions (`build_filters` at `crc/aws/vm_cleaner.py:140-148`).
  - Per region in `get_all_regions("ec2", "us-west-2")`: creates `boto3.client("ec2", region_name=region)`, calls `describe_instances(Filters=filters)`, iterates `Reservations` and `Instances` in order, calls `describe_network_interfaces(NetworkInterfaceIds=[nic_id])` on `NetworkInterfaces[0]["NetworkInterfaceId"]` for each instance that passes tag screening, and in non-dry-run mode calls `terminate_instances(InstanceIds=[id])` (`delete`) or `stop_instances(InstanceIds=[id])` (`stop`) one instance at a time.
  - `find_instances_with_tag` (`crc/aws/vm_scan.py:64-94`) queries `boto3.session.Session().get_available_regions("ec2")` with fallback to `get_all_regions("ec2", "us-west-2")`, creates `boto3.client("ec2", region_name=region)`, and paginates `describe_instances(Filters=[{"Name": f"tag:{tag_key}", "Values": [tag_value]}])` in the exact order of `main`.
- **Filter, protection and age decisions** (`crc/aws/vm_cleaner.py:229-309` vs `main:crc/aws/vm.py:200-280`):
  - Skips instances where `"Tags" not in inst` (`BUG-020`: empty list `[]` is not skipped at this step).
  - `should_skip` logs `"Tags and notags not present"` at WARNING when both `exception_tags` and `notags` are falsy; otherwise iterates `tags` in list order, indexing `tag["Key"]` and `tag["Value"]` (raising `KeyError` on malformed tags), checking `policy.exception_tags_any({key: value}, settings.exception_tags)` at each tag so the first matching tag returns `True` immediately, and accumulating `all_tags[key] = value` (last duplicate key wins) for `policy.notags_all(all_tags, settings.notags or {})`.
  - Requires a truthy `Name` tag (first `"Name"` entry via `vm_scan.instance_name`), else logs `f"{name} instance doesn't have Name Tag. Skipping it"` at ERROR and skips.
  - Evaluates `core_age.get_retention_age(tags, custom_age_tag_key)` (`BUG-004` fail-open), logs `f"Updating age for instance_id: {instance_id}"` when truthy, and checks `core_age.is_old(policy.effective_age(retention_age, settings.age), now.astimezone(attached.tzinfo), attached)`.
- **Logs, errors and dry-run path** (`crc/aws/vm_cleaner.py:76-107`, `:211-228`, `:254-266` vs `main:crc/aws/vm.py:241-244`, `:323-402`):
  - Every `logging.info`, `logging.warning`, and `logging.error` message (including the `"Error occured while delete instance"` spelling vs `"Error occurred while stop instance"`, and `"Instance: {} deleted."` vs `"Instance {} stopped."`) matches `main`.
  - Per-instance exceptions in `_decide` are caught, logged as `f"Error occurred while processing {name} instance: {e}"`, and skip the instance (fail closed). Per-instance `terminate_instances` / `stop_instances` exceptions are caught, logged, and omit the instance from the succeeded list. Region-level `CONNECTIVITY_ERRORS` call `log_skipped_region(region, f"ec2 vm {operation.value}", e)`, while non-connectivity errors from `describe_instances` or client creation propagate.
  - Dry run makes no `terminate_instances` or `stop_instances` call and records instance names for `delete` (`instance_names_to_operate`) but instance IDs for `stop` (`instances_to_operate`, `dry_run_names`).

### 2. Oracle and parity test verification

- `diff -u <(git show main:crc/aws/vm.py) tests/legacy/aws_vm.txt` is empty (SHA-256 `5bf6e2d6e35758bc4e6e7f53b6edac20204e30c54a5bcef6e154b778695190b4`).
- `tests/test_aws_vm_parity.py` executes both the oracle (`tests/legacy/aws_vm.txt`) and the refactored `VM` / registered `VmCleaner` against `FakeAws` and asserts ordered SDK calls, deleted/stopped lists, counts, log records, stdout, and exceptions.
- Tested a deliberate one-line behaviour change in `crc/aws/vm_cleaner.py` (`DEFAULT_REGION = "us-east-1"`), confirmed `pytest -q tests/test_aws_vm_parity.py` failed immediately (304 failed), and reverted it.

### 3. Architecture, scope and lane compliance

- `VmCleaner` implements `Cleaner`, is registered with `@register("aws", "vm")`, has a strictly read-only `plan()` (`ARCH-005` passes), uses `crc.core.policy.exception_tags_any`, `crc.core.policy.notags_all`, `crc.core.policy.effective_age`, `crc.core.policy.normalize_tags` and `crc.core.age.get_retention_age` / `crc.core.age.is_old` as named in `docs/refactor/FILTER_VARIANTS.md`, puts `boto3` behind the typed `VmClient`, `VmPaginator`, `VmScanClient`, `VmSession` `Protocol`s in `crc/aws/vm_api.py`, and removed `crc/aws/vm.py` / `crc.aws.vm` from both legacy ratchet lists in `pyproject.toml` with nothing added.
- `crc.py`, `crc/core/**`, other clouds, and shared docs forbidden by lane rules are untouched; `crc/aws/README.md` passes `ARCH-002` symbol sync.

### BLOCKING (fixed)

1. **Legacy adapter helper `VM._get_filtered_instances` and repeated-operation accumulation had no direct parity tests, and `test_cleaner_protocol_matches_main` omitted log assertions on error cases** (`crc/aws/vm.py:152-156`, `tests/test_aws_vm_parity.py:189-207`, `:655-674`, `:915-994`):
   - `VM._perform_operation` calls `cleaner.filter_instances(...)` directly, so `VM._get_filtered_instances` was never called during the test suite. Added `test_get_filtered_instances_matches_main` (8 cases) comparing return values, SDK calls, log records, and exceptions directly against `_load_oracle().VM._get_filtered_instances`.
   - Added `test_repeated_operations_match_main` (4 cases: `delete` then `delete`, `delete` then empty `delete`, `delete` then empty `stop`, `stop` then empty `delete`) verifying cumulative lists and the `if not deleted and not stopped` warning quirk against the oracle, plus `notags-last-duplicate-wins` in `CASES` and `QUIRKS`.
   - Strengthened `test_cleaner_protocol_matches_main` so error cases also assert `new.logs` matches the oracle's listing log stream and non-error cases assert the full ordered log equality `new.logs == [*listing, *mutating, *summary]`.
2. **Wrong `Effect` column and incomplete line citations in `docs/refactor/bugs/pending/AWS-3.md`, plus 3 missing quirks on `main`** (`docs/refactor/bugs/pending/AWS-3.md:9-23`, `crc/aws/README.md:7`):
   - Row 8 (`crc/aws/vm.py:260-280`, duplicate `Key` entries in `Tags` where `notags` keeps only the last value via `all_tags[k] = v`) had `Effect: cosmetic` and cited only `:261-272`; when `notags={"env": ["prod"]}` is evaluated on `[env=prod, env=dev]`, the last value `"dev"` overwrites `"prod"` so the instance is not skipped and is deleted/stopped (`deletes what it should keep`, `BUG-146` shape). Updated the citation to `:260-280` and `Effect` to `deletes what it should keep`.
   - Updated Row 1 (`__init__` starts at `:38`, so `:38-73`), Row 2 (added `:229-235` where `network_interface_attached_time` is passed to `is_old`), and Row 6 (added `:65-66` where the result lists are initialized in `__init__`).
   - Added 3 `NEW` low-risk rows verified against `main`: non-atomic region loop where non-connectivity `describe_instances` errors abort after earlier regions were already terminated/stopped without summary logs (`main:crc/aws/vm.py:196-197`, `:311-372`, `:375-402`), `delete_count` / `stopped_count` side-effect INFO logging (`BUG-081` / `BUG-121` shape) and bare `Name` recording in `instance_names_to_delete` / `instance_names_to_stop` (`BUG-050` / `BUG-106` shape, `main:crc/aws/vm.py:82-90`, `:100-108`, `:331-333`, `:342-346`, `:352-354`, `:363`), and noisy `logging.info(tags)` before the age check (`BUG-073` / `BUG-139` shape) plus asynchronous `terminate_instances` / `stop_instances` logged as completed without waiting (`BUG-064` / `BUG-078` / `BUG-126` shape, `main:crc/aws/vm.py:222`, `:330`, `:339-341`, `:351`, `:360-362`).
   - Fixed a duplicate `and` in `crc/aws/README.md:7`.

### NIT (left)

- `VmSettings` fields (`crc/aws/vm_cleaner.py:57-66`) are typed as `Any` outside the SDK edge so malformed legacy inputs (`None`, non-list tag values) preserve `main`'s exact runtime behaviour until a typed-options rung narrows them.
- `VmCleaner.plan()` caches per-region clients in `self._clients` (`crc/aws/vm_cleaner.py:135`, `:166`) for reuse by `apply()`.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 106 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 4692 passed |

Test count: 4317 before AWS-3 (`d3e97b0`), 4675 on `9411b7c`, 4692 after review additions (+375).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff d3e97b0..HEAD` (both rung commits `9411b7c` and `8e1152a`), read against `git show main:crc/aws/vm.py`, `git show main:crc.py`, `AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `docs/refactor/BUGS.md` and `REVIEW_CHECKLIST.md`.

### Round 1 fixes assessment

- **Parity tests added in round 1 are correct and not vacuous.** `test_get_filtered_instances_matches_main` (`tests/test_aws_vm_parity.py:933-956`) compares the return value, SDK calls and logs of `VM._get_filtered_instances` with the oracle on 8 existing cases, including the error paths. `test_repeated_operations_match_main` (`:959-991`) compares two calls on the same object against the oracle.
- **The `test_cleaner_protocol_matches_main` change made the test stronger.** The removed per-stream equality plus "summary is last" check (`git diff 9411b7c..HEAD -- tests/test_aws_vm_parity.py`) is implied by the new `new.logs == [*listing, *mutating, *summary]` (`:670-673`). Error cases now also assert the listing log and that there was no mutating call (`:660-664`). No assertion was removed without a stronger one replacing it.
- **Pending-bug corrections are correct.** I re-checked every citation round 1 changed or added with `git show main:crc/aws/vm.py`: `:38-73`, `:196-197`, `:229-235`, `:260-280`, `:311-372`, `:375-402`, `:82-90`, `:100-108`, `:222`, `:330-363`. All of them point at the code described. Moving the duplicate-key `notags` row to `deletes what it should keep` is right.
- **No round 1 change touched production code** except the `and` typo fix in `crc/aws/README.md`, so behaviour did not change.

### Independent review

- **Parity holds.**
  - The oracle is byte-identical to `git show main:crc/aws/vm.py` (`diff` is empty).
  - `should_skip` (`crc/aws/vm_cleaner.py:229-245`) matches main `:255-280`. `exception_tags_any({key: value}, spec)` is equivalent to main's `k in spec and (not spec[k] or v in spec[k])` through `_key_matches`. `notags_all` is `bool(spec) and all(...)`, which matches main's `if self.notags:` guard.
  - `effective_age` is `retention_age or age`.
  - The error and success texts render the same strings as main `:336`, `:340`, `:357`, `:361`.
  - Dry-run `stop` still records ids (main `:365`).
  - `vm_scan._scan_region` uses `results.extend(<generator>)`. On an exception partway through, CPython keeps the items already appended, so partial-region results match main's per-item `append` (checked directly).
- **Mutation checks: the tests catch both changes.**
  - Replacing `policy.notags_all` with the vacuous `policy.matches_all_keys` fails the parity suite immediately.
  - Turning the empty age into `{'days': 0}` fails 4 cases.
  - Both changes were reverted.
- **Safety (`CLEANUP_SAFETY.md`) is unchanged.**
  - `plan()` makes no mutating call. `terminate_instances` and `stop_instances` run only from `apply()` / `apply_region()`, never in a dry run.
  - No age gate was weakened. The missing age validation (SAF-006 / SAF-011) is main behaviour, already recorded as the first AWS-3 pending row, and the factory follows the other AWS Cleaner factories.
  - Only connectivity errors are caught at the region level, as on main.
- **Architecture and typing are fine.**
  - `@register("aws", "vm")` is used and there is no import-time cloud call.
  - boto3 sits behind the `vm_api.py` Protocols.
  - Both ratchet lines were removed and nothing was added.
  - `crc/aws/README.md` changes only the AWS-3 parts (module list, symbols, the VMs bullet, extension rule 3), and ARCH-002 passes.
  - No file outside the lane was touched (`crc.py`, `crc/core/**`, shared docs).

### BLOCKING (fixed)

None.

### NIT (left)

- **`now` is read once per region instead of once per instance.** `VM._perform_operation` reads the clock once per region (`crc/aws/vm.py:192-194`), while main calls `datetime.datetime.now()` once per instance (main `:231`). During a long region listing the cutoff is slightly earlier than on main. That is the fail-closed direction (an instance can only look younger) and is not visible in tests that use a frozen clock. The coding summary does not mention it.
- **No test for a malformed tag partway through a region in the tag scan.** `find_instances_with_tag` is not tested with a malformed tag (missing `Key`) in the middle of a region's pages, which is the partial-result path above. The behaviour matches main by inspection.
- **`VmCleaner._clients` keeps state between runs** (`crc/aws/vm_cleaner.py:135`, `:166`). Reusing one cleaner instance keeps the previous run's clients. This is harmless, because the registry builds a new cleaner per context.

### New bugs

- One `NEW` row was added to `docs/refactor/bugs/pending/AWS-3.md`. `find_instances_with_tag` takes its regions from the static `get_available_regions` list, swallows every per-region error as a warning, and a malformed tag cuts a region's results short (main `crc/aws/vm.py:117-123`, `:124-129`, `:136-140`, `:156-161`; cosmetic, low).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 106 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 4692 passed |

Test count: 4692 (4317 before the rung, +375; round 2 added no tests).

ROUND 2 VERDICT: APPROVE
