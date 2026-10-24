## Coding summary

**Rung ST-OCI-DISK: `crc/oci/disk.py` and `crc/oci/disk_api.py` are now the subpackage `crc/oci/disk/`. Status: done, `make check` green (ruff, mypy, lint-imports, pytest). Pure move, no behaviour change.**

- **Layout (`git mv`, history follows).** `crc/oci/disk_api.py` -> `crc/oci/disk/api.py` (only the docstring wording changed). `crc/oci/disk.py` -> `crc/oci/disk/cleaner.py` (`DiskCleaner`, `DiskSettings`, `DiskRuntime`, `build_disk_cleaner`, constants, summary log); the `Disk` class moved verbatim into `crc/oci/disk/adapter.py` (imports adjusted; it uses the private `_LIST_CONTEXT` and `_log_summary` of `cleaner.py`). `crc/oci/disk/__init__.py` re-exports `Disk`, `DiskCleaner`, `DiskRuntime`, `DiskSettings`, `build_disk_cleaner` (`__all__`), so `from crc.oci.disk import X` works as before (`crc.py`, `crc/cli/resource_ops.py` unchanged).
- **Import sites updated.** `crc/oci/vm.py` (`crc.oci.disk_api` -> `crc.oci.disk.api`) and `tests/test_oci_disk.py` line 16 (same). `tests/test_oci_disk_parity.py` imports only `crc.oci.disk`, unchanged. No assertion changed. No shim was needed: none of the five hash-locked files nor `tests/legacy/**` import `crc.oci.disk_api`.
- **monkeypatch targets.** None needed retargeting: the tests patch `oci.core`, `oci.identity`, `oci.pagination` attributes and `crc.service.init_logging`, all looked up where they were before.
- **README.** New `crc/oci/disk/README.md` with the four ARCH-002 sections (Public API lists every public name of `api.py`, `cleaner.py`, `adapter.py` and the `__all__`). The `crc/oci/README.md` disk lines (module list, Public API names, SAF bullet, extension rules) now point at it; other sections untouched.
- **Bugs.** `docs/refactor/bugs/pending/ST-OCI-DISK.md`: the coding pass found no new bugs (module already covered by `BUG-004`, `BUG-096` to `BUG-110`); round 2 added two `NEW` rows (INFO log of every candidate's tags, inverted `notags` docstring on main), both kept as on main.
- **Tests.** Before: 42213 passed. After: 42213 passed (same; no test added or removed).
- **Not changed (lane rules).** `pyproject.toml` and `tests/archtest/legacy_baseline.txt` (which still lists `crc/oci/disk.py`; the entry is now a nonexistent path and may be dropped by the integrator), `docs/refactor/STATUS.md`, `FILTER_VARIANTS.md` (the `oci/disk.py` row now means `crc/oci/disk/cleaner.py`), `crc/README.md`, `tests/README.md`.

## Round 1 (Argon)

Scope reviewed: `git diff -M d109ee5..HEAD` (`crc/oci/README.md`, `crc/oci/disk/README.md`, `crc/oci/disk/__init__.py`, `crc/oci/disk/adapter.py`, `crc/oci/disk/api.py`, `crc/oci/disk/cleaner.py`, `crc/oci/vm.py`, `docs/refactor/bugs/pending/ST-OCI-DISK.md`, `docs/refactor/reviews/ST-OCI-DISK.md`, `tests/test_oci_disk.py`) against `AGENTS.md` and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### Verification

1. **Pure move (`git diff -M d109ee5..HEAD`)**:
   - `crc/oci/disk_api.py` -> `crc/oci/disk/api.py` (`R098`, 180 lines): byte-identical except for line 3 in the module docstring. Zero logic changes.
   - `crc/oci/disk.py` -> `crc/oci/disk/cleaner.py` (`R072`, 294 lines) and `crc/oci/disk/adapter.py` (122 lines): diffing `d109ee5:crc/oci/disk.py[:class Disk]` against `crc/oci/disk/cleaner.py` shows only the module docstring update, `from crc.oci.disk_api import (` -> `from crc.oci.disk.api import (`, and removal of `from crc.service import Service` (moved to `adapter.py`); diffing `d109ee5:crc/oci/disk.py[class Disk:]` against `crc/oci/disk/adapter.py[class Disk:]` has 0 lines of diff. Zero logic changes.
   - `crc/oci/disk/__init__.py` (8 lines): re-exports `Disk`, `DiskCleaner`, `DiskRuntime`, `DiskSettings`, `build_disk_cleaner` in `__all__`; importing `crc.oci.disk` imports `crc.oci.disk.cleaner` so `@register("oci", "disk")` runs at import time as before.
2. **Import paths, safety locks, and test edits**:
   - Every `crc.oci.disk` import site (`crc.py:52`, `crc/cli/resource_ops.py:21`, `tests/legacy/crc_main.txt:30`, `tests/test_oci_disk.py:15`, `tests/test_oci_disk_parity.py:22`) resolves unchanged via `crc/oci/disk/__init__.py`.
   - Every `crc.oci.disk_api` import site (`crc/oci/disk/cleaner.py:32`, `crc/oci/vm.py:32`, `tests/test_oci_disk.py:16`) was updated to `crc.oci.disk.api`.
   - All five hash-locked safety test files (`tests/test_aws_disk.py`, `tests/test_aws_spot_instance_requests.py`, `tests/test_azu_spot_vm.py`, `tests/test_gcp_spot_vm.py`, `tests/test_oci_spot_vm.py`) and `tests/legacy/**` are untouched.
   - `tests/test_oci_disk.py` has only the 1-line import change at line 16; `tests/test_oci_disk_parity.py` is untouched; no assertions or monkeypatch targets changed.
3. **Subpackage README and ARCH-002**:
   - `crc/oci/disk/README.md` has all four `ARCH-002` sections (`## Purpose`, `## Public API`, `## Safety Invariants` citing `SAF-006` to `SAF-013`, `## Extension Rules`) and its `## Public API` symbol list matches `__all__` and all 11 public top-level definitions across `api.py`, `cleaner.py`, `adapter.py`, and `__init__.py` (`ARCH-002` passes).
   - `crc/oci/README.md` updates only the OCI disk lines and moves the 11 disk symbols out of `crc/oci`'s `## Public API` without rewriting other modules' sections.
4. **Forbidden files and lane rules**:
   - `docs/refactor/BUGS.md`, `docs/refactor/STATUS.md`, `docs/refactor/FILTER_VARIANTS.md`, `crc/README.md`, `tests/README.md`, `tests/legacy/**`, `crc.py`, `crc/cli/**`, `crc/core/**`, `pyproject.toml`, and all `.github/CODEOWNERS` guardrail files are untouched.

### Findings

- **BLOCKING (fixed)**: None.
- **NIT (left)**: `crc/oci/vm.py:32` had its 1-line import updated (`from crc.oci.disk_api import RegionLister, regions_of` -> `from crc.oci.disk.api import RegionLister, regions_of`) because `crc/oci/disk_api.py` moved to `crc/oci/disk/api.py`. When merging with parallel lane `refactor/lane-oci-vm` (which splits `crc/oci/vm.py` into `crc/oci/vm/cleaner.py` and `crc/oci/vm/adapter.py`), the integrator will need to apply that same `crc.oci.disk_api` -> `crc.oci.disk.api` import update in both `crc/oci/vm/cleaner.py` and `crc/oci/vm/adapter.py`.
- **NIT (left)**: `crc/oci/disk/__init__.py` re-exports the 5 `disk.py` symbols (`Disk`, `DiskCleaner`, `DiskRuntime`, `DiskSettings`, `build_disk_cleaner`) while the `api.py` symbols (`SdkVolume`, `SdkAttachment`, `VolumeApi`, `OciVolumeApi`, `ApiFactory`, `RegionLister`, `api_for`, `regions_of`) are imported from `crc.oci.disk.api`; `ARCH-002` and all callers are satisfied.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed! |
| `mypy` (`crc`, `tests`) | Success: no issues found in 198 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken (130 files, 705 dependencies) |
| `pytest -q` | 42213 passed in 164.23s |

Test count: 42213 passed vs 42213 on base `d109ee5` (+0, unchanged).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff -M d109ee5..HEAD` (the rung commit `3e54af5` and round 1's commit `b6e7cab`), re-checked against `main` for the legacy paths.

### Round 1 fixes assessment

Round 1 (`b6e7cab`) made no code or test change: it only appended its report. Nothing to undo; no test was weakened. Its verification claims hold: re-split `d109ee5:crc/oci/disk.py` at `class Disk(` and diffed both halves. `crc/oci/disk/adapter.py` from `class Disk(` on is byte-identical; `crc/oci/disk/cleaner.py` differs only in the module docstring, `crc.oci.disk_api` -> `crc.oci.disk.api` and the dropped `from crc.service import Service`; `crc/oci/disk/api.py` differs from `d109ee5:crc/oci/disk_api.py` only on docstring line 3. One correction to round 1's text: `crc/oci/README.md` and `crc/oci/disk/README.md` both pointed new disk quirks at `docs/refactor/bugs/pending/OCI-1.md`, which no longer exists (folded into `BUGS.md` at integration); round 1 missed it (fixed below).

### Independent review

- Behaviour parity: `crc.oci.disk` still exposes `Disk`, `DiskCleaner`, `DiskRuntime`, `DiskSettings`, `build_disk_cleaner` (`crc/oci/disk/__init__.py`); `crc.py:52`, `crc/cli/resource_ops.py:21` and `tests/test_oci_disk_parity.py:22` resolve unchanged; importing the package imports `cleaner.py`, so `@register("oci", "disk")` still runs at import. No other reference to `crc.oci.disk_api` or `crc/oci/disk.py` remains outside `tests/legacy/**`, `docs/refactor/**` and `tests/archtest/legacy_baseline.txt:33`.
- Safety (CLEANUP_SAFETY.md SAF-006 to SAF-013): no logic moved or changed; `plan()` still read-only, `apply()`/`remove()` the only callers of `delete_volume` / `delete_boot_volume`; attachment-error fail-open (`BUG-096`, `BUG-097`) and the age paths unchanged. The five hash-locked safety tests and `tests/legacy/**` untouched (`test_safety_lock.py` green).
- Tests: only `tests/test_oci_disk.py:16` changed (import path); no assertion or monkeypatch target changed; count unchanged.
- Architecture: import-linter 5 kept; ARCH-002 README sync green for `crc/oci/disk/README.md` and `crc/oci/README.md`; all files under 400 lines (`cleaner.py` 294). The stale baseline entry `crc/oci/disk.py` is harmless (ARCH-001 only rejects `extend-exclude` growth; `crc/oci/spot_vm.py` is stale the same way) and is outside this lane.
- Docs: `VolumeApi` "seven OCI calls" list in `crc/oci/disk/README.md` matches `crc/oci/disk/api.py:72-96` (round 1's old `crc/oci/README.md` text listed only six).

### Findings

- **BLOCKING (fixed)**: `crc/oci/disk/README.md` (Safety Invariants) and `crc/oci/README.md:39` (disk part only) sent new disk quirks to the nonexistent `docs/refactor/bugs/pending/OCI-1.md`; they now cite `BUGS.md` rows and `pending/ST-OCI-DISK.md`. Same README bullet also grouped `BUG-098` (no availability domains) under "connectivity error swallowed"; reworded to match `BUGS.md:84`.
- **NIT (left)**: `crc/oci/disk/adapter.py:17-26` imports the private `_LIST_CONTEXT` and `_log_summary` from `cleaner.py`; the `spot_vm/` precedent made the shared helpers public (`log_summary`). Renaming would add names to the README Public API; left for a later tidy-up.
- **NIT (left)**: `crc/oci/README.md` extension rule 2 still says "SDK calls behind a Protocol in a `*_api.py` module" while disk now uses `disk/api.py`, and the VM part of `crc/oci/README.md:39` cites the folded `pending/OCI-2.md`; both are shared or VM text outside this lane.
- **NIT (left)**: round 1's `crc/oci/vm.py:32` merge note stands: the integrator must apply `crc.oci.disk.api` in whatever the OCI VM lane produces.
- **New bugs** (`docs/refactor/bugs/pending/ST-OCI-DISK.md`, both `NEW`, kept as on main): INFO log of every candidate's freeform tags with no volume name (main `crc/oci/disk.py:144-145`; VM analogue `BUG-170`), and main's inverted `notags` docstring (main `crc/oci/disk.py:41` vs `:116-122`).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check` (before the doc edits; `tests/archtest` and the OCI disk tests re-run after them: 302 passed):

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed! |
| `mypy` | Success: no issues found in 198 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken (130 files, 705 dependencies) |
| `pytest -q` | 42213 passed in 168.14s |

Test count: 42213 passed (base `d109ee5`: 42213; unchanged).

ROUND 2 VERDICT: APPROVE
