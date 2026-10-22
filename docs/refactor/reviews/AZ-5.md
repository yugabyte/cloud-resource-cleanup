# AZ-5 review

## Coding summary

Rung AZ-5: the Azure Spot VM cleaner `crc/azu/spot_vm.py` is now the subpackage `crc/azu/spot_vm/`, a registered `Cleaner` (`@register("azure", "spot_instances")`) with `SpotVM` kept as a thin adapter. Status: complete; ruff, mypy strict, lint-imports and pytest are green (`make check` equivalent). `crc.py` is unchanged.

- Split by responsibility (the old file was 424 lines): `cleaner.py` (`AzureSpotVmCleaner`: `plan()`, `apply()`, `decide()`, factory), `client.py` (SDK Protocols and client factories), `disks.py` (leftover disk detection), `removal.py` (`Remover`: the mutating steps and the read-only disk tag check), `__init__.py` (the `SpotVM` adapter, import path unchanged), each documented in the subpackage README (ARCH-002).
- `plan()` lists VMs, filters (spot priority, `policy.should_skip_by_tags`, `policy.filter_tags_all_keys`, power state via `policy.state_in`, `core_age.validated_retention_age` + `core_age.is_old`, `policy.normalize_tags` only for the Candidate) with read calls only (`list`, `instance_view`). In a dry run it also previews leftover disks (`disks.get`) and the NIC and logs the summary, so the engine (which skips `apply()` in a dry run) reports the same results as main. `apply()` deletes VM, then each unprotected leftover disk, then the NIC, per VM, swallowing a per-VM error as main does (also in `Result.failed`).
- Adapter: `SpotVM(resource_group, dry_run, filter_tags, exception_tags, age, custom_age_tag_key, notags, clock=datetime.datetime.now)` keeps `delete()`, `get_deleted`, `delete_count`, `get_deleted_nic`, `nic_delete_count`, `get_deleted_disks`, `instance_names_to_delete`, `nics_names_to_delete`, `disks_names_to_delete`, `base`, and the private seams the old tests patch. `delete()` runs list, decide and delete VM by VM, so SDK call order, logs and mid-run behaviour are main's. The one new optional `clock` argument exists because ARCH-003 bans reading the wall clock in a cleaner; its default is `datetime.datetime.now`, as on main.
- Cleaner path vs main: only ordering differs in a real run (all VMs are listed and checked before the first delete; a `TypeError` from a naive `time_created` is raised in `plan()` before any delete instead of after earlier deletes). Dry-run calls and logs are identical.
- Tests: baseline `pytest -q` 4681 passed on the lane base, now 4859 passed (+178); new tests: `tests/test_azure_spot_instances_parity.py` (83 cases, 167 tests: 83 through the adapter against the frozen oracle, 83 through the registered Cleaner, plus repeated `delete()` accumulation), `tests/test_azure_spot_instances.py` (11 focused `plan()`/`apply()` tests), fake SDK in `tests/helpers_azure_spot.py`. `tests/test_azu_spot_vm.py` and all other existing tests are unchanged. Oracle `tests/legacy/azu_spot_vm.txt` is byte-identical to `git show main:crc/azu/spot_vm.py`.
- The module lists no regions, so there is no region-listing fallback or auth-error case; list errors are covered (`list-error`, `list-error-dry-run`).
- Ratchet: `crc/azu/spot_vm.py` removed from ruff `extend-exclude` and `crc.azu.spot_vm` from the mypy override. No per-SDK mypy override was needed. `tests.test_azu_spot_vm` stays in the test ratchet (that test file is unchanged).
- Open issues: 11 new rows in `docs/refactor/bugs/pending/AZ-5.md` (ID `NEW`; 7 from coding, 1 from round 1, 3 from round 2). No existing BUGS.md row is specific to this module.
- Needed changes outside this lane (not made): `crc/README.md`, `tests/README.md` and `tests/legacy/README.md` should list `tests/legacy/azu_spot_vm.txt`, `tests/test_azure_spot_instances_parity.py`, `tests/test_azure_spot_instances.py` and `tests/helpers_azure_spot.py`; `FILTER_VARIANTS.md` section 3 `azu/spot_vm.py` row now lives in `crc/azu/spot_vm/cleaner.py` (`decide`, `tags_skip`, `tags_include`) and `removal.py` (`_disk_tags_protect`), line numbers refer to main; `crc/core/README.md` could list `azure`/`spot_instances` as registered. `crc.py` needs no change.

## Round 1 (Argon)

Scope reviewed: `git diff e7926e2..HEAD` (`crc/azu/README.md`, `crc/azu/spot_vm/README.md`, `crc/azu/spot_vm/__init__.py`, `crc/azu/spot_vm/cleaner.py`, `crc/azu/spot_vm/client.py`, `crc/azu/spot_vm/disks.py`, `crc/azu/spot_vm/removal.py`, `docs/refactor/bugs/pending/AZ-5.md`, `docs/refactor/reviews/AZ-5.md`, `pyproject.toml`, `tests/helpers_azure_spot.py`, `tests/legacy/azu_spot_vm.txt`, `tests/test_azure_spot_instances.py`, `tests/test_azure_spot_instances_parity.py`) against `git show main:crc/azu/spot_vm.py`, `AGENTS.md`, `docs/refactor/BUGS.md`, `docs/refactor/FILTER_VARIANTS.md` and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity with `git show main:crc/azu/spot_vm.py`

- **SDK calls (order and arguments)** (`crc/azu/spot_vm/__init__.py:190-228`, `crc/azu/spot_vm/cleaner.py:99-177`, `crc/azu/spot_vm/removal.py:69-181` vs `main:crc/azu/spot_vm.py:79-89`, `:203-355`, `:398-401`):
  - `SpotVM.delete()` lists VMs via `compute.virtual_machines.list(self.base.resource_group)` (logging and re-raising on failure per `SAF-005`), then processes each VM in order: `instance_view(resource_group, vm_name)`, VM deletion via `begin_delete_vm_and_wait`, per-leftover-disk tag check via `disks.get(resource_group, disk_name)` and deletion via `disks.begin_delete(resource_group, disk_name).result(timeout=600)`, and primary NIC deletion via `delete_vm_primary_nic`.
- **Filter and age decisions** (`crc/azu/spot_vm/cleaner.py:179-251`, `crc/azu/spot_vm/removal.py:105-119` vs `main:crc/azu/spot_vm.py:73-121`, `:244-269`, `:358-396`):
  - Spot priority (`str(priority).lower() == "spot"`), VM skip tags (`policy.should_skip_by_tags`), VM filter tags (`policy.filter_tags_all_keys` with untagged VMs skipped per `SAF-042`), power state (`policy.state_in` against `("VM stopped", "VM deallocated")`), fail-closed custom age tag (`core_age.validated_retention_age` per `SAF-008`, `SAF-041`), `policy.effective_age` + `core_age.is_old`, and leftover disk protection (`policy.exception_tags_any` / `policy.notags_all` and resource-group casefold check per `SAF-038`, `SAF-044`) match `main` in order and semantics.
- **Log lines, printed messages, errors and dry-run path** (`crc/azu/spot_vm/cleaner.py:51-65`, `:140-177`, `crc/azu/spot_vm/disks.py:93-99`, `crc/azu/spot_vm/removal.py:75-181` vs `main:crc/azu/spot_vm.py:88`, `:171-196`, `:212-324`, `:350-424`):
  - Every `logging.debug`, `logging.info`, `logging.warning` and `logging.error` message, swallowed per-VM/per-disk error, raised list/age error, and dry-run preview path match `main` verbatim.

### 2. Oracle and parity test verification (`tests/legacy/azu_spot_vm.txt`, `tests/test_azure_spot_instances_parity.py`)

- Diffed `tests/legacy/azu_spot_vm.txt` against `git show main:crc/azu/spot_vm.py`: byte-identical (0 lines of diff).
- Verified `tests/test_azure_spot_instances_parity.py` runs both the oracle and the refactored code (`SpotVM` and registered `AzureSpotVmCleaner`) against the same `FakeAzure` fake and compares ordered SDK `calls`, `deleted`, `nics`, `disks`, `logs`, `stdout` and `error`.
- Tested a deliberate one-line behaviour change in `crc/azu/spot_vm/cleaner.py` (`policy.filter_tags_any_key` instead of `policy.filter_tags_all_keys` in `tags_include`), confirmed `python3 -m pytest -q tests/test_azure_spot_instances_parity.py` failed (`test_matches_main[filter-all-keys]` and `test_cleaner_protocol_matches_main[filter-all-keys]`), and reverted it.

### 3. Architecture and lane rules

- `AzureSpotVmCleaner` implements `Cleaner` (`cloud = "azure"`, `kind = "spot_instances"`, `supports`, read-only `plan`, mutating `apply`) and is registered with `@register("azure", "spot_instances")` on `build_spot_vm_cleaner`.
- `plan()` never mutates (`ARCH-005` passes on all modules in `crc/azu/spot_vm/`).
- Filtering uses `crc.core.policy` (`should_skip_by_tags`, `filter_tags_all_keys`, `state_in`, `exception_tags_any`, `notags_all`, `effective_age`, `normalize_tags`) as named in `docs/refactor/FILTER_VARIANTS.md`.
- SDK sits behind typed `Protocol`s in `crc/azu/spot_vm/client.py` (`Poller`, `VirtualMachineOperations`, `DiskOperations`, `SpotComputeClient`, `SpotNetworkClient`).
- `crc/azu/spot_vm.py` (`crc.azu.spot_vm`) left both legacy ratchet lists in `pyproject.toml`; nothing was added (`ARCH-001` passes), and no per-module mypy override was added.
- `crc.py`, `crc/core/**`, other clouds' packages, and all files forbidden by the lane rules are untouched; `crc/azu/README.md` and `crc/azu/spot_vm/README.md` are accurate and pass `ARCH-002` symbol sync.

### BLOCKING (fixed)

1. **`docs/refactor/bugs/pending/AZ-5.md` row 4 cited `crc/azu/spot_vm.py:385`, `:392-393` instead of `:385`, `:395` (`self.is_old(...)` is on line 395 of `main:crc/azu/spot_vm.py`) and classified an uncaught `TypeError` aborting `delete()` as `cosmetic` instead of `run fails`** (`docs/refactor/bugs/pending/AZ-5.md:10`):
   - Verified all rows against `git show main:crc/azu/spot_vm.py`; corrected row 4's line citation to `:385`, `:395` and effect to `run fails` (matching `BUG-104`, `BUG-152`, `BUG-162`), refined the line ranges on rows 1, 2, 3, 5, 6 and 7, and added row 8 for repeated `SpotVM.delete()` accumulation (`main:crc/azu/spot_vm.py:42-44`, `:276`, `:284`, `:288`, `:299`, `:326`, `:338`, `:406-424`).
2. **Missing parity coverage for `rg_from_id=False` on 404 / `ResourceNotFoundError` in leftover disk deletion (`main:crc/azu/spot_vm.py:289-294`, `:300-305`, `crc/azu/spot_vm/removal.py:154-158`) and missing log assertion on `fails_in_plan` cases in `test_cleaner_protocol_matches_main`** (`tests/test_azure_spot_instances_parity.py:70-74`, `:252-261`, `:492-532`):
   - Added `disk-delete-not-found-rg-fallback` and `disk-delete-404-rg-fallback` parity cases, asserted `new.logs == old.logs` on `fails_in_plan` cases in `test_cleaner_protocol_matches_main`, and added `test_repeated_delete_accumulates_lists_as_main`.
3. **`SpotVM.__init__` parameter annotations did not allow `None` for optional tag specs / `custom_age_tag_key` or `int` for `age`, unlike `SpotVmConfig`, `crc.py`, and `tests/test_azu_spot_vm.py`** (`crc/azu/spot_vm/__init__.py:94-108`):
   - Widened `SpotVM.__init__` type annotations (`TagSpec | None`, `core_age.AgeSpec | int`, `str | None`) to match `SpotVmConfig` and callers.

### NIT (left)

- Calling `AzureSpotVmCleaner.apply()` directly with `context.dry_run=True` returns `Result(succeeded=..., related=plan.related)` without re-logging, whereas `plan()` already logged the dry-run summary (`crc/azu/spot_vm/cleaner.py:114-122`); `crc.core.engine.run` skips `apply()` in a dry run (`crc/core/engine.py:100`).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 111 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 4859 passed |

Test count: 4681 before AZ-5 (`e7926e2`), 4854 before review, 4859 after review additions (+5).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff e7926e2..HEAD` (including round 1 commit `34cb62d`), compared with `git show main:crc/azu/spot_vm.py`, `main:crc/azu/vm_delete_helpers.py`, `crc/core/policy.py`, `crc/core/age.py`, `CLEANUP_SAFETY.md` and `REVIEW_CHECKLIST.md`.

### Round 1 fixes

- Pending-bug citations: correct. I re-checked every row against `git show main:crc/azu/spot_vm.py` (for example row 4, where `:385` is the `now().astimezone(...)` line and `:395` the `is_old` call). The effect `run fails` on row 4 is right. Row 8 (accumulation on a repeated `delete()`) is real and cited correctly.
- New parity cases `disk-delete-not-found-rg-fallback` and `disk-delete-404-rg-fallback`: correct. They exercise the `rg_from_id=False` branch (`main:crc/azu/spot_vm.py:289-294`, `:300-305`) against the oracle. The new log assertion on `fails_in_plan` cases and `test_repeated_delete_accumulates_lists_as_main` only add assertions; no existing assertion was removed or loosened.
- Widened `SpotVM.__init__` annotations: type-only. Runtime behaviour is unchanged (`require_positive_age` still converts an `int` and rejects an empty age).

### Independent review

- Parity: the filter order (spot priority, then skip tags, include tags, power state, `time_created`, custom age tag, `is_old`) matches main `:357-396`. `policy.should_skip_by_tags`, `filter_tags_all_keys`, `exception_tags_any` and `notags_all` reduce to main's `key in tags and (not value or tags[key] in value)`, keeping the `in` semantics on raw tag values. An empty spec gives the same result as main's `self.x and ...` guards. `state_in` is exact membership, like main's `not in`. `effective_age` is `retention_age or age`.
- The dry-run preview in `plan()` calls `self._network_factory()` and records `<vm>-NIC`, matching `delete_vm_primary_nic`'s dry-run branch (`main:crc/azu/vm_delete_helpers.py:48-51`), including the client construction.
- The legacy path still uses `datetime.datetime.now` by default; `crc.py` passes no `clock`.
- Safety: SAF-005 (the list error is re-raised; `list-error` cases pass), the SAF-008/041 fail-closed custom age (`custom-age-*` cases), SAF-042 (untagged VMs skipped) and SAF-038/044 (`disks-*` cases) all hold. `plan()` makes no mutating call (ARCH-005; `test_plan_makes_no_mutating_call_in_a_real_run`).
- Tests are not vacuous. I ran three mutations, then reverted each one:
  - Dropping the resource-group check in `Remover.disk_protected` made 5 tests fail, including `tests/test_azu_spot_vm.py`.
  - Making an invalid custom age tag fall back to `--age` made 6 tests fail.
  - Removing the network-factory call in the dry-run preview made 5 tests fail.
- Architecture and typing: `lint-imports` keeps 5 contracts and ARCH-001..005 pass. The `pyproject.toml` diff removes only this module's two ratchet lines. Any `Any` is at the SDK edge only, and the one `# type: ignore[arg-type]` is in a test helper that builds the config from a dict.

### BLOCKING (fixed)

- None in code. Doc fix: the Safety Invariants section of `crc/azu/spot_vm/README.md` did not cite SAF-038, SAF-041 and SAF-044, although the module implements them (`CLEANUP_SAFETY.md:173`, `:177-182`). I added them.

### NIT (left)

- Lane rules: `crc/azu/README.md` edits lines in the shared Modules, Public API and Notes sections, not a dedicated `### spot_vm` subsection. These are the module's own bullets, and the removal of `SpotVM` from the "legacy" sentence is needed for accuracy, but that sentence is shared with other Azure lanes, so the merge may conflict.
- `tests/test_azure_spot_instances.py::test_apply_isolates_a_failing_vm` passes a `script` entry for `vm_begin` that is never used, because `RemovalOps.delete_vm` is replaced. This is harmless; the outcome is asserted through the replaced deleter.
- In the Cleaner dry run, `plan.candidates` (and so `apply()`'s `Result.succeeded`) includes a VM whose preview raised, while `related` stays as main would record it. This can only happen if `detached_disk_refs` raises before the VM is recorded, which needs a malformed SDK object. It only affects the engine path, which `crc.py` does not use yet.
- Round 1's NIT about `apply()` in a dry run still applies.

### New bugs and quirks (in `docs/refactor/bugs/pending/AZ-5.md`)

- `poller.result(timeout=...)` returns without raising after the timeout, so a disk or VM still being deleted is logged and counted as deleted (`main:crc/azu/spot_vm.py:279-284`, `:318-326`; `main:crc/azu/vm_delete_helpers.py:31-33`).
- A disk id without a `resourceGroups` segment falls back to a name-only guess in the VM's resource group (`main:crc/azu/spot_vm.py:136-148`, `:164-170`, `:183-188`).
- A NIC that returns not-found is recorded as deleted, and only `<vm>-NIC` is ever tried (`main:crc/azu/spot_vm.py:333-339`; `main:crc/azu/vm_delete_helpers.py:48`, `:62-70`).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt`, then `make check` (Python 3.12.3 locally; CI uses 3.11):

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 111 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 4859 passed |

Test count: 4859, the same as after round 1. Round 2 added no tests and removed none. The count before AZ-5 was 4681.

ROUND 2 VERDICT: APPROVE
