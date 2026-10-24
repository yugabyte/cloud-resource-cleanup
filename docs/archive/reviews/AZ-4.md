# AZ-4 review

## Coding summary

Rung AZ-4: `crc/azu/vm.py` is now the subpackage `crc/azu/vm/` with a registered `Cleaner`
(`@register("azure", "vm")`) and the legacy `VM` kept as a thin adapter. Status: complete; ruff,
mypy strict, lint-imports and pytest are green (`make check` equivalent).

- Split by responsibility (the old module plus Protocols would pass the ARCH-004 400-line limit):
  `client.py` (SDK Protocols), `cleaner.py` (`VmSettings`, `Tally`, `log_summary`, `VmCleaner`,
  `build_vm_cleaner`), `adapter.py` (`VM`), each documented in `crc/azu/vm/README.md`. The old
  import path `from crc.azu.vm import VM` works through the package `__init__.py` (`__all__`).
- Cleaner: `plan()` lists the subscription (`list_all`), filters through `crc.core.policy`
  (`skip_untagged(SKIP)`, `should_skip_by_tags`, `filter_tags_all_keys`; FILTER_VARIANTS.md section 3,
  `azu/vm.py`), ages through `crc.core.age` (`get_retention_age`, `effective_age`, `is_old`) and
  reads `instance_view` for the VMs that pass (`policy.status_contains_any_allowed`); it makes no
  mutating call and logs the dry-run summary. `apply()` deletes (`begin_delete(...).result(timeout=3600)`
  then the unchanged `delete_vm_primary_nic` helper) or `begin_power_off`s, in plan order, with main's
  per-VM swallow-and-log and its summary lines. SDK behind the `VmComputeClient` / `VmOperations` /
  `VmNetworkClient` / `NicOperations` Protocols; client factories are injected through the constructor.
  `crc/azu/vm_delete_helpers.py` is unchanged (still legacy, shared with `SpotVM`).
- Adapter: `VM(resource_group, dry_run, filter_tags, exception_tags, age, custom_age_tag_key, notags)`
  keeps `delete()`, `stop()`, `get_deleted`, `delete_count`, `get_deleted_nic`, `nic_delete_count`,
  `get_stopped`, `stopped_count`, the name lists, `base` and the constructor attributes, plus one
  optional trailing `clock` argument (default `datetime.datetime.now`) so tests can fix the time. It
  iterates the lazy listing and decides, reads status and acts VM by VM, so the call order, mid-run
  error behaviour and cumulative lists are those of main. The private helpers of the old class
  (`_perform_operation` internals, `_should_*`, `_get_vm_status`, `_delete_vm`, `_stop_vm`) are gone;
  nothing used them. `crc.py` is unchanged.
- Cleaner path: `plan()` decides on every VM and reads every status before `apply()` mutates, so
  paging, status reads and mutations are no longer interleaved, and an age or listing error now
  happens before any delete instead of after earlier ones. A dry run builds no network client (main
  builds it without calling it). The summary log lines are the same: dry run logs from `plan()`,
  a real run from `apply()`. The factory maps `instance_state=None` to the default `["running"]`
  (an empty list means nothing, as for a direct `delete([])`; `crc.py` already maps `[]` to the default).
- Tests: baseline `pytest -q` 4681 passed; now 4969 passed (+288): `tests/test_azure_vm_parity.py`
  (103 cases through the adapter, the same 103 through the registered Cleaner, 58 quirk checks,
  plus subscription-wide/resource-group, power-off, repeated-delete, stop-then-delete and factory
  tests, 270 in all), `tests/test_azure_vm.py` (18 focused `plan()` / `apply()` tests). All existing
  tests unchanged.
  Oracle: `tests/legacy/azu_vm.txt` is byte-identical to `git show main:crc/azu/vm.py`. The module
  lists no regions, so there is no region fallback or region auth-error case; list errors,
  mid-listing errors, status errors, delete errors, NIC retry/404 paths, `MAX_AGE`, custom age tags
  and age edges are covered. Memory addresses in log text are masked in the comparison.
- Ratchet: `crc/azu/vm.py` removed from ruff `extend-exclude` and `crc.azu.vm` from the mypy override
  (the subpackage modules are checked). No per-SDK mypy override was needed.
- Open issues: new bugs and quirks in `docs/refactor/bugs/pending/AZ-4.md` (14 rows, ID `NEW`).
  BUG-002, BUG-003, BUG-004, BUG-010 and BUG-028 are preserved, not fixed.
- Needed changes outside this lane (not made): `crc/README.md`, `tests/README.md` and
  `tests/legacy/README.md` should list `crc/azu/vm/`, `tests/legacy/azu_vm.txt`,
  `tests/test_azure_vm_parity.py`, `tests/test_azure_vm.py` and `tests/helpers_azure_vm.py`.
  `FILTER_VARIANTS.md` section 3, `azu/vm.py` row: the location is now `crc/azu/vm/cleaner.py`
  (`policy.skip_untagged`, `should_skip_by_tags`, `filter_tags_all_keys`,
  `status_contains_any_allowed`, `get_retention_age` + `is_old`); the semantics are unchanged.
  No core change needed.

## Round 1 (Argon)

Scope reviewed: `git diff e7926e2..HEAD` (`crc/azu/README.md`, `crc/azu/vm/README.md`,
`crc/azu/vm/__init__.py`, `crc/azu/vm/adapter.py`, `crc/azu/vm/cleaner.py`,
`crc/azu/vm/client.py`, `docs/refactor/bugs/pending/AZ-4.md`, `docs/refactor/reviews/AZ-4.md`,
`pyproject.toml`, `tests/helpers_azure_vm.py`, `tests/legacy/azu_vm.txt`, `tests/test_azure_vm.py`,
`tests/test_azure_vm_parity.py`) against `git show main:crc/azu/vm.py`, `AGENTS.md`,
`docs/refactor/BUGS.md`, `docs/refactor/FILTER_VARIANTS.md`, and
`.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity with `git show main:crc/azu/vm.py`

- **SDK calls (order and arguments)** (`crc/azu/vm/cleaner.py:178-226`, `crc/azu/vm/adapter.py:111-132`, `crc/azu/vm_delete_helpers.py:22-90` vs `main:crc/azu/vm.py:134-157`, `:246-297`):
  - `VM.delete()` and `VM.stop()` call `self.base.get_compute_client().virtual_machines.list_all()`, iterate lazily, and for each VM that passes tag and age checks call `self.base.get_compute_client().virtual_machines.instance_view(self.base.resource_group, vm.name)` inside the per-VM `try` block.
  - For `delete()`, when `not self.dry_run`, `begin_delete_vm_and_wait(self.base.get_compute_client(), self.base.resource_group, vm_name)` calls `begin_delete(resource_group, vm_name).result(timeout=3600)`, appends `vm_name` to `instance_names_to_delete`, and calls `delete_vm_primary_nic(self.base.get_network_client(), self.base.resource_group, vm_name, self.dry_run, self.nics_names_to_delete)` (evaluating `get_network_client()` even in a dry run, as on `main`).
  - For `stop()`, when `not self.dry_run`, `self.base.get_compute_client().virtual_machines.begin_power_off(self.base.resource_group, vm_name)` is called before appending `vm_name` to `instance_names_to_stop`.
- **Filter and age decisions** (`crc/azu/vm/cleaner.py:182-196`, `:236-260` vs `main:crc/azu/vm.py:137-149`, `:198-260`):
  - Untagged VMs are skipped first via `policy.skip_untagged(tags, policy.UntaggedMode.SKIP)` (`SAF-042`).
  - Tag exclusions and filters use `policy.should_skip_by_tags(tags, settings.exception_tags, settings.notags)` (`exception_tags` any key, `notags` all keys) and `policy.filter_tags_all_keys(tags, settings.filter_tags)` (all keys, empty passes), matching `FILTER_VARIANTS.md` section 3 (`azu/vm.py`).
  - Age checks evaluate `clock().astimezone(created.tzinfo)` first, log `logging.info(vm.tags)`, call `core_age.get_retention_age` (fail-open per `BUG-004`, `SAF-009`), log `Updating age for vm: {vm.name}` when truthy, and call `core_age.is_old(policy.effective_age(retention_age, self._settings.age), now, created)` (`SAF-006`, `SAF-010`).
  - Status check reads `view.statuses[1].display_status` (`BUG-010`) and matches via `policy.status_contains_any_allowed(view.statuses[1].display_status, states)`.
- **Log lines, printed messages, errors, dry-run path, and `BUGS.md` quirks** (`crc/azu/vm/cleaner.py:63-110`, `:150-177`, `:197-235`, `crc/azu/vm/adapter.py:66-100` vs `main:crc/azu/vm.py:65-118`, `:140-196`, `:262-297`):
  - Every `logging.debug`, `logging.info`, `logging.warning`, and `logging.error` line matches `main` verbatim, including count-property logging and shared `deleted`/`stopped` check in `log_summary`.
  - `BUG-002` (subscription-wide `list_all()` vs configured `resource_group`), `BUG-003` (`begin_power_off` without deallocate), `BUG-004` (fail-open custom age tag), and `BUG-010` (`statuses[1]` without bounds check, caught by per-VM `except Exception`) are preserved.

### 2. Oracle and parity test verification (`tests/legacy/azu_vm.txt`, `tests/test_azure_vm_parity.py`)

- Diffed `tests/legacy/azu_vm.txt` against `git show main:crc/azu/vm.py`: byte-identical (0 lines of diff).
- Verified `tests/test_azure_vm_parity.py` runs both the oracle (`tests/legacy/azu_vm.txt`) and the refactored code (`VM` and registered `VmCleaner`) against the same `FakeAzure` fakes and compares ordered SDK `calls` (including lazy `yield` interleaving), `deleted`, `nics`, `stopped`, `counts`, `logs`, `stdout`, and `error`.
- Tested a deliberate one-line behaviour change in `crc/azu/vm/cleaner.py` (`policy.filter_tags_any_key` instead of `policy.filter_tags_all_keys` in `_skip_reason`), confirmed `python3 -m pytest -q tests/test_azure_vm_parity.py` failed (3 failed), and reverted it.

### 3. Architecture, scope, and lane rules

- `VmCleaner` implements `Cleaner` (`cloud = "azure"`, `kind = "vm"`, `supports`, read-only `plan`, mutating `apply`) and is registered with `@register("azure", "vm")` on `build_vm_cleaner`.
- `plan()` never mutates (`ARCH-005` passes on `crc/azu/vm/`).
- Filtering uses the exact `crc.core.policy` functions named in `docs/refactor/FILTER_VARIANTS.md`.
- SDK is behind typed `Protocol`s in `crc/azu/vm/client.py` (`SdkVm`, `InstanceStatus`, `InstanceView`, `LongRunningOperation`, `VmOperations`, `VmComputeClient`, `NicOperations`, `VmNetworkClient`).
- `crc/azu/vm.py` (`crc.azu.vm`) left both legacy ratchet lists in `pyproject.toml`; nothing was added (`ARCH-001`), and no new mypy override was needed.
- `crc.py`, `crc/core/**`, other cleaners, other clouds' packages, and all files forbidden by the lane rules are untouched; `crc/azu/README.md` and `crc/azu/vm/README.md` are accurate and pass `ARCH-002` symbol sync.

### BLOCKING (fixed)

1. **Line range off-by-ones and missing `main` quirks in `docs/refactor/bugs/pending/AZ-4.md`** (`docs/refactor/bugs/pending/AZ-4.md:11-22`, `tests/test_azure_vm_parity.py:106`, `:583`, `:741-764`):
   - Verified every row of `docs/refactor/bugs/pending/AZ-4.md` against `git show main:crc/azu/vm.py`, `git show main:crc/azu/vm_delete_helpers.py`, `git show main:crc.py`, and `git show main:crc/service.py`. Tightened line ranges on rows 3 (`:271-283`), 4 (`:277-283`), 7 (`:134-136`, `:160-196`), 8 (`:277-283`), and 10 (`:54-56`, `:73-81`, `:91-99`, `:109-117`, `:160-196`).
   - Added 4 missing `main` quirks to `docs/refactor/bugs/pending/AZ-4.md` (sorted by risk: empty/`None` age matching all VMs and `MAX_AGE` shortening age (`BUG-102`/`BUG-163` shape, medium); `logging.info(vm.tags)` logging full tag dicts without VM name (`BUG-170` shape, low); mutable `default_instance_state` default argument and `stop()` ignoring per-call state (`BUG-171` shape, low); string tag spec acting as a substring test (`BUG-082`/`BUG-108`/`BUG-166` shape, low)), plus parity cases `filter-string-spec-is-substring-test` and `test_stop_then_empty_delete_shares_name_lists_like_main` in `tests/test_azure_vm_parity.py`.

### NIT (left)

- `VmCleaner.decide` records `Skipped(id=getattr(vm, "name", ""), reason=reason)` (`crc/azu/vm/cleaner.py:188`), so a skipped VM whose `name` attribute is `None` yields a `Skipped` with `id=None` instead of `""`.
- `build_vm_cleaner` maps `instance_state=None` to `DEFAULT_INSTANCE_STATES` while preserving `instance_state=[]` as "match nothing" (`crc/azu/vm/cleaner.py:270`, `:277`), matching `VM.delete([])`; when `crc.py` is later wired to `engine.run`, the CLI layer should map an empty `--resource_states` list to `None` before building `RunContext.options` (just as `crc.py:108-111` does today).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 110 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 4969 passed in 39.73s |

Test count: 4681 before AZ-4 (`e7926e2`), 4965 before review, 4969 after review additions (+4).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff e7926e2..HEAD` (commits `26d5846`, `5f492e1`), read against
`git show main:crc/azu/vm.py`, `main:crc/azu/vm_delete_helpers.py`, `main:crc/azu/_base.py`,
`main:crc/service.py`, `CLEANUP_SAFETY.md`, `AGENTS.md`, `ARCHITECTURE.md`, `docs/refactor/BUGS.md`
and `REVIEW_CHECKLIST.md`.

### Round 1 fixes assessment

- Line-range changes in `docs/refactor/bugs/pending/AZ-4.md`: re-checked every row against
  `git show main:crc/azu/vm.py` (byte-identical to `tests/legacy/azu_vm.txt`); all cited lines are
  correct. The four rows round 1 added are real `main` behaviour and correctly scoped.
- Parity cases round 1 added (`filter-string-spec-is-substring-test`,
  `test_stop_then_empty_delete_shares_name_lists_like_main`) assert outcomes against the oracle and
  pass; no test was removed, loosened or skipped, and no production code was changed in round 1.
- Round 1's two NITs stand (`Skipped.id` can be `None`; the future CLI wiring must map an empty
  `--resource_states` list to `None`).

### Independent review

- Parity: the adapter keeps main's order (lazy `list_all`, tag checks, clock, `logging.info(vm.tags)`,
  `get_retention_age` on the raw tags, `is_old(retention_age or age)` outside the `try`, then status,
  action and NIC inside the per-VM `try`), the same number of client factory calls and the same log
  lines. `policy.normalize_tags` only differs from the raw dict for `None` tag values, which the
  Azure SDK does not return. `RunContext.options` freezing (tuples, read-only mappings) works with
  `crc.core.policy` and `crc.core.age`.
- Safety: `plan()` makes no mutating call (ARCH-005 green); dry runs call nothing mutating on
  either path; a status-read failure keeps the VM (fail closed); list and age errors still propagate;
  BUG-002, BUG-003, BUG-004, BUG-010, BUG-028 are preserved, not fixed.
- Tests: mutation probes, each reverted, were all caught by `tests/test_azure_vm_parity.py` /
  `tests/test_azure_vm.py`: `statuses[1]` to `statuses[-1]`; NIC delete before recording the VM;
  `UntaggedMode.SKIP` to `INCLUDE`; adapter clock replaced by `datetime.datetime.now`.
- Architecture and typing: registration by decorator, factories injected, SDK behind the Protocols
  in `crc/azu/vm/client.py`, ratchet lines only removed, no `# type: ignore`, no new mypy override.
  Lane-restricted files (`crc.py`, `crc/core/**`, `BUGS.md`, `STATUS.md`, `FILTER_VARIANTS.md`,
  `crc/README.md`, `tests/README.md`, `tests/legacy/README.md`) are untouched in the rung diff.

### BLOCKING (fixed)

1. `crc/azu/vm/README.md`, Safety Invariants: the authoritative list cited SAF-005 (Spot VM list
   re-raise), SAF-041 and SAF-044 (Spot modules), which do not apply to this module, and said "SAF-011
   holds only when `age` is non-empty: an empty age selects every old-enough VM", which is
   self-contradictory (SAF-011 is an `__init__` age gate that this module, as on main, does not
   have). Now cites SAF-006, SAF-009, SAF-010, SAF-042, states SAF-011 to SAF-013 are not met, and
   says an empty age selects every VM that passes the tag and status checks.

### NIT (left)

- `VmCleaner.apply` (`crc/azu/vm/cleaner.py:163-168`, `:214-219`): if `self._network()` raises after
  the VM delete finished, the VM is already in `tally.deleted` and is also added to `failed`, so the
  `Result` lists it in both `succeeded` and `failed`. The adapter path matches main; only the new
  `Result` shape is ambiguous.
- `crc/azu/README.md`: the rung edits shared lines (the "Mostly legacy" sentence, the legacy list in
  "Public API" and Extension rule 3) rather than only a module subsection; the edits are accurate and
  needed for ARCH-002, but other Azure lanes editing the same sentences will conflict on merge.
- `## Coding summary` gives 4969 tests as the coding result while round 1 reports 4965 before its
  additions; the summary was updated with round 1's count.

### New bugs

- One row added to `docs/refactor/bugs/pending/AZ-4.md`: `display_status=None` on the second
  instance view status raises `TypeError` inside the per-VM `try` and the VM is never cleaned
  (`main:crc/azu/vm.py:147-149`, `:255-260`, `:154`; variant of BUG-010; low; AZ-4 round 2).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 110 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 4969 passed |

Test count: 4681 before AZ-4 (`e7926e2`), 4969 now (round 2 added no tests).

ROUND 2 VERDICT: APPROVE
