## Coding summary

**Rung ST-OCI-VM: `crc/oci/vm.py` and `crc/oci/vm_api.py` are now the subpackage `crc/oci/vm/`. Status: done, `make check` green.**

- **What moved (`git mv`, no logic edits).** `crc/oci/vm_api.py` -> `crc/oci/vm/client.py` (typed Protocols, `OciComputeApi`, `compute_api_for`). `crc/oci/vm.py` -> `crc/oci/vm/cleaner.py` (`VmSettings`, `VmCleaner`, `build_vm_cleaner`, the summary logging) and `crc/oci/vm/adapter.py` (`VmRuntime`, `VM`); the code was cut at the `VM` class and the imports adjusted. `adapter.py` imports the private `_log_summary` from `cleaner.py` (a private helper, not renamed). `crc/oci/vm/__init__.py` re-exports every name callers import (`VM`, `VmRuntime`, `VmSettings`, `VmCleaner`, `build_vm_cleaner`, `DEFAULT_INSTANCE_STATES`, `ApiFactory`, `ComputeApi`, `OciComputeApi`, `SdkInstance`, `STOP_ACTION`, `compute_api_for`), so `from crc.oci.vm import VM` in `crc.py` and `crc/cli/vm_ops.py` is unchanged.
- **Importers.** `crc.py`, `crc/cli/**` and `tests/legacy/**` need no change. `tests/test_oci_vm.py`: one import line changed (`from crc.oci.vm_api import ...` -> `from crc.oci.vm.client import ...`); no assertion touched. `tests/test_oci_vm_parity.py` imports `crc.oci.vm` only, unchanged. No hash-locked safety test imports the old `vm_api` path, so no shim module was needed (`crc/oci/vm_api.py` is gone).
- **monkeypatch targets.** None retargeted: the tests patch `oci.*` SDK attributes, `crc.service.init_logging` and environment variables, none of which live in the moved modules.
- **README.** New `crc/oci/vm/README.md` with the four ARCH-002 sections (Safety Invariants cite SAF-006 to SAF-013). The OCI section of `crc/oci/README.md` is trimmed to a pointer (module list, VM symbol list and VM safety bullet).
- **pyproject.toml.** Not touched; neither moved file was on a legacy list.
- **Gates.** `make check`: ruff, mypy, lint-imports and pytest green. Test count unchanged (see the commit summary: 42213 collected before and after).
- **Open issues.** The move adds none. Round 2 recorded one pre-existing quirk in `docs/refactor/bugs/pending/ST-OCI-VM.md` (`terminate_instance` keeps launch-time data volumes, `crc/oci/vm.py:156` on main). BUG-158 to BUG-171 still apply.
- **Review status.** Round 1 (Argon): APPROVE. Round 2 (Opus): APPROVE, with one stale README pointer fixed (see `## Round 2 (Opus)`).
- **Needed changes outside this lane (not made).** `docs/refactor/STATUS.md` (record the rung), `crc/README.md` and `tests/README.md` if they name `crc/oci/vm.py` or `vm_api.py`, and `docs/refactor/bugs/pending/README.md` (list `ST-OCI-VM.md`).

## Round 1 (Argon)

Scope reviewed: `git diff -M d109ee5..HEAD` (`crc/oci/vm/__init__.py`, `crc/oci/vm/cleaner.py`, `crc/oci/vm/adapter.py`, `crc/oci/vm/client.py`, `crc/oci/vm/README.md`, `crc/oci/README.md`, `tests/test_oci_vm.py`, `docs/refactor/bugs/pending/ST-OCI-VM.md`, `docs/refactor/reviews/ST-OCI-VM.md`) against `AGENTS.md` and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### Checklist verification

1. **Pure move (`git diff -M d109ee5..HEAD`)**:
   - `crc/oci/vm_api.py -> crc/oci/vm/client.py` is a 100% rename (`similarity index 100%`, 0 lines changed).
   - `crc/oci/vm.py` is split across `crc/oci/vm/cleaner.py` (`DEFAULT_INSTANCE_STATES`, `TagSpec`, `Clock`, `VmSettings`, `_log_summary`, `_log_for`, `VmCleaner`, `build_vm_cleaner`) and `crc/oci/vm/adapter.py` (`VmRuntime`, `VM`). Diffing `d109ee5:crc/oci/vm.py` against `cleaner.py + adapter.py` confirms zero logic changes—only module docstrings and import headers changed.
2. **Import paths, safety locks, and test edits**:
   - `crc/oci/vm/__init__.py` re-exports all 12 public symbols (`DEFAULT_INSTANCE_STATES`, `STOP_ACTION`, `VM`, `ApiFactory`, `ComputeApi`, `OciComputeApi`, `SdkInstance`, `VmCleaner`, `VmRuntime`, `VmSettings`, `build_vm_cleaner`, `compute_api_for`), so `from crc.oci.vm import VM` in `crc.py` and `crc/cli/vm_ops.py` and the imports in `tests/test_oci_vm.py` and `tests/test_oci_vm_parity.py` resolve unchanged.
   - `tests/test_oci_vm.py` has only a 1-line import update (`from crc.oci.vm_api import ...` -> `from crc.oci.vm.client import ...`); no assertions, fixtures, or monkeypatch targets changed.
   - All five hash-locked safety test files (`tests/test_aws_disk.py`, `tests/test_aws_spot_instance_requests.py`, `tests/test_azu_spot_vm.py`, `tests/test_gcp_spot_vm.py`, `tests/test_oci_spot_vm.py`) and `tests/legacy/**` are untouched.
3. **READMEs and ARCH-002**:
   - `crc/oci/vm/README.md` has all four required sections (`## Purpose`, `## Public API`, `## Safety Invariants` citing `SAF-006` to `SAF-013`, `## Extension Rules`) and its `## Public API` symbol list matches `__all__` and public top-level definitions (`ARCH-002` passes).
   - `crc/oci/README.md` updates only the OCI VM lines and removes the moved VM symbols from `crc/oci`'s symbol list without touching other modules' sections.
4. **Lane boundaries and forbidden files**:
   - `docs/refactor/BUGS.md`, `docs/refactor/STATUS.md`, `docs/refactor/FILTER_VARIANTS.md`, `crc/README.md`, `tests/README.md`, `tests/legacy/**`, `crc.py`, `crc/cli/**`, `crc/core/**`, `pyproject.toml`, `.github/CODEOWNERS` guardrail files, and all other cleaner modules are untouched.

### Findings

- **BLOCKING (fixed)**: None.
- **NIT (left)**: None.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 198 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken (130 files, 709 dependencies) |
| `pytest -q` | 42213 passed in 162.41s |

Test count: 42213 passed vs 42213 on base `d109ee5` (+0, unchanged).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff -M d109ee5..HEAD` (the rung commit `da2f982` and round 1's `c86c290`).

### Round 1 fixes assessment

Round 1 made no code or test change; it only added its review section. There was nothing to check
for changed behaviour or weakened tests. Its claims hold: `crc/oci/vm_api.py -> crc/oci/vm/client.py`
is a 100% rename, and a line multiset comparison of `d109ee5:crc/oci/vm.py` with
`cleaner.py` + `adapter.py`, ignoring import and comment lines, differs only in the module docstrings.

### Independent review

- **Parity with main.** The moved `VM._perform_operation` matches main's loop: regions, then a
  per-region `CONNECTIVITY_ERRORS` skip, then the state, exception, filter and age checks, then
  terminate or `STOP` per instance with logged, swallowed errors and the summary. Compare
  `git show main:crc/oci/vm.py` lines 123-170 with `crc/oci/vm/adapter.py` (`_perform_operation`)
  and `crc/oci/vm/cleaner.py`. No logic moved between modules except by the cut.
- **Hidden breakage from the move.** No `monkeypatch.setattr("crc.oci.vm....")` or `mock.patch`
  string targets the old modules, so no patch silently became vacuous. No logger depends on
  `__name__` (only root `logging.*` calls), so log records are unchanged. Registration
  (`@register("oci", "vm")` in `cleaner.py`) still runs on `import crc.oci.vm` through
  `__init__.py`. `pyproject.toml`, `legacy_baseline.txt` and `safety_lock.json` do not name the moved files.
- **CLEANUP_SAFETY.md.** No safety test touched; SAF-LOCK and ARCH-001..005 pass. `plan()` is still read-only.
- **Tests.** The only test edit is the import line in `tests/test_oci_vm.py`. No assertion changed.
- **Docs.** I checked the `BUG-158`..`BUG-171` citations used by the new READMEs against main
  (`:21`, `:43-46`, `:79`, `:101-103`, `:133-170`); they match.

### Findings

- **BLOCKING (fixed):** `crc/oci/README.md`, "Known bugs" bullet of Safety Invariants. It still pointed
  VM quirks to `docs/refactor/bugs/pending/OCI-2.md`, a file that does not exist (it was already
  missing at `d109ee5`). That contradicted the rewritten OCI VM bullet two lines below. I changed only
  the VM clause, so it now points to `pending/ST-OCI-VM.md`. The disk clause (`OCI-1.md`, also
  missing) belongs to another module's section and is left as is.
- **NIT (left):** `crc/oci/vm/adapter.py` imports the private `_log_summary` from `cleaner.py`. This
  is acceptable within one subpackage, though a public name would be cleaner in a later rung.
- **NIT (left):** Extension Rule 2 of `crc/oci/README.md` says "SDK calls behind a Protocol in a
  `*_api.py` module", but the VM Protocols now live in `vm/client.py`. That section is shared, so I
  left it for the docs owner.
- **New quirk (recorded, not fixed):** `terminate_instance(instance.id)` keeps data volumes created
  at launch, because the SDK default `preserve_data_volumes_created_at_launch` is true (main
  `crc/oci/vm.py:156`). It is recorded in `docs/refactor/bugs/pending/ST-OCI-VM.md`.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 198 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken (130 files, 709 dependencies) |
| `pytest -q` | 42213 passed in 165.85s |

Test count: 42213, the same as round 1 and the base. After the README edit, `pytest -q tests/archtest` passes: 92 passed.

ROUND 2 VERDICT: APPROVE
