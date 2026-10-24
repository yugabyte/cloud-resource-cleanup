## Coding summary

**Rung OCI-2: `crc/oci/vm.py` is a registered `Cleaner` (`@register("oci", "vm")`). Status: done, `make check` green.**

- **What changed.** `VmCleaner` (`plan()` lists regions and instances and decides, no mutating call; `apply()` calls `terminate_instance` for `Operation.DELETE` or `stop_instance` for `Operation.STOP`; `supports()` accepts both). `VM` keeps its name, constructor (plus an optional trailing `runtime: VmRuntime | None`), attributes (`instance_names_to_delete`, `instance_names_to_stop`, `dry_run`, `filter_tags`, `exception_tags`, `age`, `custom_age_tag_key`, `notags`, `default_instance_state`), `get_deleted`, `delete_count`, `get_stopped`, `stopped_count`, `delete(instance_state=...)` and `stop()`, and still subclasses `Service` and `Base`. `crc.py` is unchanged. The OCI SDK sits behind the `ComputeApi` Protocol in the new `crc/oci/vm_api.py` (`list_instances`, `terminate_instance`, `stop_instance`; `OciComputeApi` pages through `oci.pagination.list_call_get_all_results` and maps stop to `instance_action(id, "STOP")`); `Base` still builds the clients and caches them per region, so SDK call order is the same as on main. `vm.py` is 367 lines, so no subpackage was needed. Filtering uses `policy.state_in`, `policy.should_skip_by_tags` (exception any, notags all) and `policy.filter_tags_all_keys`; age uses `core_age.get_retention_age`, `policy.effective_age` and `core_age.is_old`. The region lister is reused from `crc/oci/disk_api.py` (`regions_of`).
- **Parity.** `tests/legacy/oci_vm.txt` is the exact `git show main:crc/oci/vm.py`. `tests/test_oci_vm_parity.py` runs 75 cases, each for `delete()` and `stop()`, twice (the `VM` adapter and the registered Cleaner via `plan()` then `apply()`; dry run is `plan()` only) plus a quirk table and a delete, stop, delete sequence test. Cases cover dry run, state, filters (all keys, empty value list, string spec), exception tags, notags, untagged, age edges, `MAX_AGE`, custom age tag variants, region and client errors (connectivity, auth, request, unexpected), terminate or stop errors, display name quirks, and region-listing errors (connectivity and auth fall back to `us-sanjose-1`, unexpected errors propagate). The adapter path matches main on SDK calls in order, results, counts, log records, stdout and errors. The Cleaner path matches on results, stdout, error type and message, the non-mutating calls in order, the mutating calls in order, and the log records in the order plan logs, apply logs, summary logs (same summary lines as main). Differences by design, Cleaner path only: all reads precede all terminate or stop calls, so per-instance log lines are not interleaved with "Deleting instance ..." lines; on an error raised mid-run `plan()` fails before any mutation while main may already have acted on earlier instances (pending bug 5); `delete_count` / `stopped_count` logging exists only on the adapter; in a dry run `apply()` returns the names without logging (the summary was logged by `plan()`).
- **Tests.** Before: 3549 passed. After: 3913 passed (+364): 334 in `tests/test_oci_vm_parity.py`, 30 in `tests/test_oci_vm.py` (plan read-only, apply, stop, registry and factory options, adapter with injected fakes, real pagination, stop mapping). Every existing test passes unchanged. `ruff check .`, `mypy` (strict) and `lint-imports` are clean. A mutation check (filter all-keys changed to any-key) fails 5 parity tests.
- **pyproject.toml.** Removed `crc/oci/vm.py` from the ruff `extend-exclude` and `crc.oci.vm` from the mypy override list. Nothing added; the existing per-SDK `oci` override from OCI-1 is reused. `vm_api.py` is a new, checked module.
- **Open issues.** 13 new bugs and quirks in `docs/refactor/bugs/pending/OCI-2.md` (one high: `VM` has no OKE or instance pool guard, unlike `SpotVM`). BUG-004 (fail-open custom age tag) applies to this module and is unchanged.
- **Needed changes outside this lane (not made).**
  - `crc/README.md` and `crc/core/README.md`: say that `oci` / `vm` is now registered (they list only the `aws` keypair, ip and kms cleaners and `oci` / `disk`).
  - `docs/refactor/FILTER_VARIANTS.md`, `crc/oci/vm.py` row in section 5: since OCI-2 the module filters through `policy.should_skip_by_tags`, `policy.filter_tags_all_keys`, `policy.state_in` and `policy.effective_age` (line numbers still cite `main`, frozen in `tests/legacy/oci_vm.txt`); no variant changed, so no new policy function was needed and `crc/core/**` is untouched.
  - `tests/README.md` and `tests/legacy/README.md`: list `test_oci_vm.py`, `test_oci_vm_parity.py` and the `oci_vm.txt` oracle.
  - `docs/refactor/STATUS.md`: record rung OCI-2; `docs/refactor/bugs/pending/README.md`: list `OCI-2.md`.

## Round 1 (Argon)

Scope reviewed: `git diff 7d7b4ae..HEAD` (`crc/oci/vm.py`, `crc/oci/vm_api.py`, `crc/oci/README.md`, `pyproject.toml`, `docs/refactor/bugs/pending/OCI-2.md`, `docs/refactor/reviews/OCI-2.md`, `tests/legacy/oci_vm.txt`, `tests/test_oci_vm.py`, `tests/test_oci_vm_parity.py`) against `git show main:crc/oci/vm.py`, `AGENTS.md`, `docs/refactor/BUGS.md`, `docs/refactor/FILTER_VARIANTS.md`, and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity with `git show main:crc/oci/vm.py`

- **SDK calls (order and arguments)** (`crc/oci/vm.py:131-165`, `:182-205`, `:236-252`, `:330-367`, `crc/oci/vm_api.py:68-94`, `crc/oci/disk_api.py:173-180` vs `main:crc/oci/vm.py:133-167`, `:198-213`):
  - `get_all_regions()` runs first via `regions_of(base)` (creating cached `IdentityClient` and calling `list_region_subscriptions(self.tenancy_id)`, falling back to `["us-sanjose-1"]` on `CONNECTIVITY_ERRORS` and propagating non-connectivity errors).
  - For each region, `compute_api_for(base)(region)` calls `base.get_compute_client(region)` inside the region `try` block, then `list_instances()` calls `oci.pagination.list_call_get_all_results(self._compute.list_instances, compartment_id=self._compartment_id).data`.
  - In `VM.delete()` / `VM.stop()`, `terminate_instance(instance.id)` or `instance_action(instance.id, "STOP")` runs per matching instance before moving to the next instance or region.
- **Filter and age decisions** (`crc/oci/vm.py:36`, `:207-233` vs `main:crc/oci/vm.py:21`, `:74-121`, `:144-152`):
  - State check uses `policy.state_in` (`lifecycle_state in instance_states`, default `("RUNNING",)`).
  - Tag checks use `policy.normalize_tags`, `policy.should_skip_by_tags` (`exception_tags` any key, `notags` all keys), and `policy.filter_tags_all_keys` (`filter_tags` all keys, empty passes), matching `FILTER_VARIANTS.md` section 5 (`crc/oci/vm.py`).
  - Age uses `core_age.get_retention_age` (fail-open per `BUG-004`, `SAF-009`), `policy.effective_age`, and `core_age.is_old` (`SAF-006`, `SAF-010`), reading the clock only inside `_is_old` and evaluating `now.astimezone(created_time.tzinfo)` before `logging.info(tags)` as on `main`.
- **Log lines, printed messages, errors, and dry-run path** (`crc/oci/vm.py:64-103`, `:135-137`, `:144-145`, `:182-205`, `:220-233`, `:304-320`, `:355-367` vs `main:crc/oci/vm.py:59-72`, `:117-120`, `:140-142`, `:153-196`):
  - Every `logging.debug`, `logging.info`, `logging.warning`, and `logging.error` message matches `main`, including shared `instance_names_to_delete` / `instance_names_to_stop` handling in `_log_summary`.
  - `CONNECTIVITY_ERRORS` skip the region with `log_skipped_region(region, f"list_instances vm {operation}", e)`; per-instance `terminate_instance` / `instance_action` errors catch `Exception`, log `Error occurred while {operation} instance {name}: {e}`, and omit the instance from the succeeded list.
  - In dry run, no terminate or stop call is made, matching `display_name`s are appended to `instance_names_to_delete` / `instance_names_to_stop` (or `Plan.candidates`), and the dry-run summary warning is logged.

### 2. Oracle and parity test verification (`tests/legacy/oci_vm.txt`, `tests/test_oci_vm_parity.py`)

- Diffed `tests/legacy/oci_vm.txt` against `git show main:crc/oci/vm.py`: byte-identical (SHA-256 `98a6e59903e68ac900800fc2363c9a87b68feece71c877af68a1b3d1a72991b5`, 0 lines of diff).
- Verified `tests/test_oci_vm_parity.py` runs both the oracle and the refactored code (`VM` and `build_vm_cleaner`) on the same `FakeOci` fakes across 75 cases (each for `delete` and `stop`), a `delete -> stop -> delete` sequence test, and 32 quirk assertions, comparing calls (including `list_call_get_all_results`), results, counts, logs, stdout, and errors.
- Tested a deliberate one-line behaviour change in `crc/oci/vm.py` (`policy.filter_tags_any_key` instead of `policy.filter_tags_all_keys` in `_skip_reason`), confirmed `pytest -q tests/test_oci_vm_parity.py` failed (5 failed), and reverted it.

### 3. Architecture, scope, and lane rules

- `VmCleaner` implements `Cleaner` (`cloud = "oci"`, `kind = "vm"`, `supports`, read-only `plan`, mutating `apply`) and is registered with `@register("oci", "vm")` on `build_vm_cleaner`.
- `plan()` never mutates (`ARCH-005` passes on `crc/oci/vm.py` and `crc/oci/vm_api.py`).
- SDK sits behind typed `ComputeApi` and `SdkInstance` `Protocol`s in `crc/oci/vm_api.py`.
- `crc/oci/vm.py` (`crc.oci.vm`) was removed from both legacy ratchet lists in `pyproject.toml`; nothing was added to the ratchet (`ARCH-001`), and the existing `module = ["oci", "oci.*"]` mypy override is per-SDK.
- `crc.py`, `crc/core/**`, other clouds' packages, and all files forbidden by the lane rules are untouched; `crc/oci/README.md` is accurate and passes `ARCH-002` symbol sync.

### BLOCKING (fixed)

1. **`build_vm_cleaner` honored `options["instance_state"]` during `Operation.STOP`, and `tests/test_oci_vm_parity.py` masked it by omitting `instance_state` for `stop`** (`crc/oci/vm.py:242`, `tests/test_oci_vm_parity.py:599-600`, `tests/test_oci_vm.py:276-280`):
   - On `main`, `stop_vm` / `VM.stop()` always uses `["RUNNING"]` and never accepts `instance_state` (`main:crc/oci/vm.py:208-213`, `main:crc.py:747-748`), whereas `build_vm_cleaner` read `options.get("instance_state")` regardless of `context.operation` and `_run_cleaner` in `tests/test_oci_vm_parity.py` had `if case.states is not None and operation == "delete":` to avoid failing `custom-states-stop` and `custom-states-empty-stop`.
   - Restricted `states` in `build_vm_cleaner` to `Operation.DELETE` (`options.get("instance_state") if context.operation is Operation.DELETE else None`), removed `and operation == "delete"` from `_run_cleaner` in `tests/test_oci_vm_parity.py`, and added a unit assertion in `tests/test_oci_vm.py`.
2. **Inaccurate `main` citations and claim in `docs/refactor/bugs/pending/OCI-2.md` rows 7, 8, 11, and 13** (`docs/refactor/bugs/pending/OCI-2.md:17-18`, `:21`, `:23`):
   - Row 7 claimed "`crc.py` never passes another state" and cited `crc.py:159-166`, `:748` (`stop_vm`); on `main`, `crc.py` does pass `--resource_states` to `vm.delete(instance_state)` when `--resource_states` is given (`main:crc.py:108-111`, `:558-559`, `:1576-1583`) and only uses the `["RUNNING"]` default when `--resource_states` is omitted.
   - Row 8 cited `crc/oci/vm.py:51-52`, `:172-203` instead of `main:crc/oci/vm.py:45-46` (`instance_names_to_delete` / `instance_names_to_stop`) and `:172-196` (summary block).
   - Row 11 cited `crc/oci/vm.py:53-54` instead of `main:crc/oci/vm.py:43-44` (`Service.__init__` / `Base.__init__`).
   - Row 13 omitted `:200` (`def delete(..., instance_state: List[str] = default_instance_state)`).
   - Corrected all four rows against `git show main:crc/oci/vm.py` and `git show main:crc.py`.

### NIT (left)

- `VmCleaner.act` returns `Failure(id=candidate.name, ...)` (`crc/oci/vm.py:204`) while `Candidate.id` and `Skipped.id` are OCIDs; matches `DiskCleaner` in `crc/oci/disk.py:230`.
- `VmRuntime.clock` defaults to `datetime.datetime.now` (as a callable reference, not a call, so `ARCH-003` passes) to keep `VM()` backwards-compatible until `crc.py` is wired to `engine.run`.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 97 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 3913 passed |

Test count: 3549 before OCI-2 (`7d7b4ae`), 3913 after OCI-2 (+364).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 7d7b4ae..HEAD` (all three rung commits, including round 1's `a23540e`), checked against `git show main:crc/oci/vm.py`, `git show main:crc.py`, `git show main:crc/oci/_base.py`, `git show main:crc/oci/spot_vm.py`, `crc/core/policy.py`, `crc/core/age.py` and `REVIEW_CHECKLIST.md`.

### Round 1 fixes assessment

- **`instance_state` ignored for `Operation.STOP` in `build_vm_cleaner`** (`crc/oci/vm.py:242`): correct. On main, `VM.stop()` always passes `default_instance_state` (`main:crc/oci/vm.py:208-213`), and `crc.py` `stop_vm` calls `vm.stop()` with no state (`main:crc.py:745-748`). The test change makes the parity check stronger, not weaker: `_run_cleaner` now passes `instance_state` for stop too (`tests/test_oci_vm_parity.py:599-600`), so `custom-states-stop` and `custom-states-empty-stop` now exercise the Cleaner path. The new unit assertion (`tests/test_oci_vm.py:276-280`) pins it. Nothing else in the code changed.
- **Pending-bug citations** (rows 7, 8, 11, 13): I re-checked each against `git show main:...`. `crc.py:108-111` (`_delete_vm`), `:558-559`, `:1576-1583`, `crc/oci/vm.py:43-46`, `:169-196`, `:200` are all correct. I also spot-checked rows 1 to 3 (`spot_vm.py:74-90`, `:266`; `_base.py:14`, `:33-40`, `:65-84`; `connectivity.py:14-18`): all correct.

### Independent review

- **Parity.** `tests/legacy/oci_vm.txt` is byte-identical to `git show main:crc/oci/vm.py` (`cmp`). I compared the filter helpers line by line with main. `_key_matches` (`crc/core/policy.py:90-91`) is main's `key in tags and (not value or tags[key] in value)`, so string specs remain substring tests. `effective_age` is `retention_age or age`. `state_in` is `in tuple(allowed)`. `Operation` is a `StrEnum`, so the f-string log lines still say `delete` / `stop`. The adapter keeps main's call order: regions, client and list inside the region `try`, then terminate or stop per instance. `normalize_tags` turns a `None` or non-`str` tag value into a string, where main compares the raw value. OCI freeform tag values are always `str`, so this cannot change behaviour in practice (FILTER_VARIANTS.md line 25 already documents the normalisation).
- **Safety.** `plan()` makes no mutating call (`crc/oci/vm.py:127-146`). The fail-open custom age (BUG-004, SAF-009), the empty-age match (SAF-006) and the `MAX_AGE` shortening (SAF-010) are unchanged and pinned by parity cases. Auth errors from `list_region_subscriptions` are still swallowed exactly as on main (pending row 3). No safety test was removed, skipped or weakened.
- **Tests.** The unit tests assert candidates, skip reasons, exact SDK call lists, results and log records; none is vacuous. The parity suite compares calls, results, counts, logs, stdout and errors against the oracle.
- **Architecture and typing.** `lint-imports` shows 5 contracts kept. `pyproject.toml` only removes `crc/oci/vm.py` / `crc.oci.vm` from the ratchets. `Any` appears only at the SDK edge (`OciComputeApi.__init__`). Registration uses the decorator, and the factory builds `Base()` only when it is called. No file outside the lane was touched.

### BLOCKING (fixed)

None.

### Fixed in passing (docs accuracy)

- `crc/oci/README.md` (`build_vm_cleaner` entry) still said the factory reads `instance_state` without qualification. After round 1's fix it is read only for delete, and the entry now says so.

### NIT (left)

1. **`instance_state: []` in the factory options means "match nothing"** (`crc/oci/vm.py:242`, `:249`). On main, `crc.py` `_delete_vm` treats an empty `resource_states` as "use the default" and calls `vm.delete()` (`main:crc.py:108-111`). The factory matches main's `VM.delete([])` instead (parity case `custom-states-empty`). Nothing is affected today because `crc.py` still calls the `VM` adapter, `resource_states` defaults to `None` (`main:crc.py:1329`), and the mismatch keeps instances rather than deleting them. The rung that wires `crc.py` to `engine.run` must either map `[]` to `None` before building the options or change the factory to `options.get("instance_state") or None`.
2. `VmCleaner.apply()` calls `self._api_factory(region)` outside a `try` (`crc/oci/vm.py:159`). With `Base` the client is already cached by `plan()`, so it cannot raise a new connectivity error. With an injected factory that raises, `apply()` would propagate instead of skipping the region. This is acceptable because `plan()` has already built the same client.
3. Same as round 1: `Failure.id` is the display name (`crc/oci/vm.py:204`), consistent with `crc/oci/disk.py`.

### New bugs and quirks

One row was added to `docs/refactor/bugs/pending/OCI-2.md`: the age check logs the whole freeform tag dict at INFO with no instance name (`main:crc/oci/vm.py:116-117`, cosmetic, low).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 97 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 3913 passed |

Test count: 3913 (3549 before the rung, +364). Not lower than before.

ROUND 2 VERDICT: APPROVE
