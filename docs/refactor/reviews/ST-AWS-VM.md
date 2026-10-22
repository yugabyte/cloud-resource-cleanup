## Coding summary

Rung ST-AWS-VM: status DONE (implemented, `make check` green, pushed to `refactor/lane-aws-vm`; round 1 reviewed: APPROVE; round 2 reviewed: APPROVE after a README citation fix). Pure move, no behaviour change.

- **Move (`git mv`, history follows).** `crc/aws/vm.py` -> `crc/aws/vm/adapter.py` (`VM`, `VmRuntime`), `crc/aws/vm_cleaner.py` -> `crc/aws/vm/cleaner.py`, `crc/aws/vm_api.py` -> `crc/aws/vm/client.py`, `crc/aws/vm_scan.py` -> `crc/aws/vm/scan.py`. New `crc/aws/vm/__init__.py` re-exports every public name of the four old modules (`__all__`), so `from crc.aws.vm import VM` (`crc.py`, `crc/cli/vm_ops.py`) keeps working unchanged. Only import lines and the module docstrings that named the old paths changed inside the moved files.
- **Import sites updated.** `tests/test_aws_vm.py` and `tests/test_aws_vm_parity.py`: `crc.aws.vm_cleaner` -> `crc.aws.vm.cleaner` (import lines only, no assertion). No other file in `crc/`, `crc.py` or `tests/` imported the old sibling paths; none of the five hash-locked tests or `tests/legacy/**` does, so no shim module was needed.
- **monkeypatch targets.** None needed retargeting: the tests patch `boto3.client`, `boto3.session.Session` and `crc.service.init_logging`, which are looked up outside the moved modules.
- **Docs.** New `crc/aws/vm/README.md` with the four ARCH-002 sections (Public API synced with `__all__` and public defs); the VM entries of `crc/aws/README.md` trimmed to a pointer. `docs/refactor/bugs/pending/ST-AWS-VM.md`: no new bugs.
- **Not touched.** `pyproject.toml` (the `crc/aws/vm.py` ratchet entry was already removed in AWS-3), `tests/archtest/legacy_baseline.txt` (it still lists `crc/aws/vm.py`, which no longer exists; baseline entries may only be removed by its owner), `BUGS.md`, `STATUS.md`, `FILTER_VARIANTS.md`, `crc/README.md`, `tests/README.md`, `tests/legacy/**`.
- **Gates.** `make check` (ruff, mypy, import-linter, pytest incl. archtests) green. `pytest -q` before: 42213 passed; after: 42213 passed (same count).

## Round 1 (Argon)

Scope reviewed: `git diff -M d109ee5..HEAD` (`crc/aws/vm/__init__.py`, `crc/aws/vm/adapter.py`, `crc/aws/vm/cleaner.py`, `crc/aws/vm/client.py`, `crc/aws/vm/scan.py`, `crc/aws/vm/README.md`, `crc/aws/README.md`, `tests/test_aws_vm.py`, `tests/test_aws_vm_parity.py`, `docs/refactor/bugs/pending/ST-AWS-VM.md`, `docs/refactor/reviews/ST-AWS-VM.md`) against `AGENTS.md` and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### Verification

1. **Pure move (`git diff -M d109ee5..HEAD`):**
   - `crc/aws/vm.py` -> `crc/aws/vm/adapter.py` (`R097`): only the module docstring reference (`crc.aws.vm_cleaner.VmCleaner` -> `crc.aws.vm.cleaner.VmCleaner`) and internal imports (`crc.aws.vm_scan` -> `crc.aws.vm.scan`, `crc.aws.vm_api` -> `crc.aws.vm.client`, `crc.aws.vm_cleaner` -> `crc.aws.vm.cleaner`) changed; zero logic changes.
   - `crc/aws/vm_cleaner.py` -> `crc/aws/vm/cleaner.py` (`R098`): only the module docstring note (`(`adapter.py`)`) and internal imports (`crc.aws.vm_scan` -> `crc.aws.vm.scan`, `crc.aws.vm_api` -> `crc.aws.vm.client`) changed; zero logic changes.
   - `crc/aws/vm_api.py` -> `crc/aws/vm/client.py` (`R100`): 100% byte-for-byte rename.
   - `crc/aws/vm_scan.py` -> `crc/aws/vm/scan.py` (`R098`): only the import line (`crc.aws.vm_api` -> `crc.aws.vm.client`) changed; zero logic changes.
   - `crc/aws/vm/__init__.py`: new subpackage entry point that re-exports all 24 public symbols of the four modules in `__all__`.
2. **Import resolution, tests, and safety locks:**
   - `from crc.aws.vm import VM` in `crc.py:16` and `crc/cli/vm_ops.py:9` resolves unchanged through `crc/aws/vm/__init__.py`.
   - `tests/test_aws_vm.py:12` and `tests/test_aws_vm_parity.py:22` updated only their `from crc.aws.vm_cleaner import ...` line to `from crc.aws.vm.cleaner import ...` (plus a trailing blank line removed in `tests/test_aws_vm_parity.py`); no test assertions or case tables were modified, and no monkeypatch retargeting was needed.
   - No other caller in `crc/`, `crc.py`, or `tests/` imported `crc.aws.vm_cleaner`, `crc.aws.vm_api`, or `crc.aws.vm_scan`.
   - All five hash-locked safety test files (`safety_lock.json`) and `tests/legacy/**` (including `tests/legacy/aws_vm.txt`) are untouched.
3. **READMEs and ARCH-002:**
   - `crc/aws/vm/README.md` has all four `ARCH-002` sections (`## Purpose`, `## Public API`, `## Safety Invariants`, `## Extension Rules`) and its 24 backticked names under `## Public API` match `crc/aws/vm/__init__.py` `__all__` and the public top-level definitions across `crc/aws/vm/*.py`.
   - `crc/aws/README.md` edits only the AWS VM lines in `## Purpose`, `## Public API`, and `## Safety Invariants` to point to `crc/aws/vm/README.md`, leaving all other module sections untouched.
4. **Lane rules and forbidden files:**
   - `docs/refactor/BUGS.md`, `docs/refactor/STATUS.md`, `docs/refactor/FILTER_VARIANTS.md`, `crc/README.md`, `tests/README.md`, `tests/legacy/**`, `crc.py`, `crc/cli/**`, `crc/core/**`, `pyproject.toml`, `.github/CODEOWNERS` guardrail files, and all other cleaner modules are untouched.

### Findings

- **BLOCKING:** none.
- **NIT (left):** `Clock` (`crc/aws/vm/adapter.py:42`) and `Decision` (`crc/aws/vm/cleaner.py:44`) are module-level type aliases not re-exported in `crc/aws/vm/__init__.py` (matching pre-move `crc/aws/README.md`, which did not list them under `## Public API`).
- **NIT (left):** `tests/archtest/legacy_baseline.txt` still lists `crc/aws/vm.py` (already removed from `pyproject.toml` in AWS-3; `legacy_baseline.txt` is an owner-only guardrail file).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed! |
| `mypy` (`crc`, `tests`) | Success: no issues found in 197 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 42213 passed |

Test count: 42213 passed on base (`d109ee5`), 42213 passed on `HEAD` (+0, unchanged).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff -M d109ee5..HEAD` (the move commit `fa4f16d` and round 1's `d669302`).

### Round 1 fixes assessment

Round 1 made no code or test change (`d669302` touches only this review file), so nothing it did can have changed behaviour or weakened a test. Its verification is correct: `client.py` is `R100`; `adapter.py`, `cleaner.py` and `scan.py` differ only in import lines and docstring path names; `tests/test_aws_vm.py:12` and `tests/test_aws_vm_parity.py:22` change only the import line (plus a trailing blank line). Its two NITs stand.

### Independent review

- **Parity / behaviour.** No logic line changed. `from crc.aws.vm import scan as vm_scan` inside `adapter.py` / `cleaner.py` imports the submodule while `crc/aws/vm/__init__.py` is still executing, which is safe (submodule import, not a name from the half-built package). Registration: `@register("aws", "vm")` in `cleaner.py` still runs on `import crc.aws.vm` (via `__init__.py`), `tests/test_aws_vm_parity.py:774` asserts it. No test monkeypatches a moved module attribute (only `boto3.client`, `boto3.session.Session`, `crc.service.init_logging`), so no patch silently became a no-op. `scan.py:31-32` keeps main's name fallback and `.upper()` (`main:crc/aws/vm.py:138`, `:140`).
- **Safety (CLEANUP_SAFETY.md).** No safety test touched; `safety_lock.json` and `tests/legacy/aws_vm.txt` untouched; `plan()` still read-only (ARCH-005 green).
- **Architecture.** import-linter 5 kept; archtests green; every file under 400 lines (largest `cleaner.py`, 330). The stale `crc/aws/vm.py` entry in `tests/archtest/legacy_baseline.txt` is harmless: legacy status comes from the ruff `extend-exclude` list (`tests/archtest/_common.py` `load_legacy`), the baseline only bounds ratchet growth.
- **Tests.** No assertion changed; no vacuous test added.

### Findings

- **BLOCKING (fixed):** `crc/aws/vm/README.md` `## Safety Invariants` cited `SAF-006, SAF-007` for the dry-run / read-only invariant, but those IDs are the empty-age and strict-validation items (`CLEANUP_SAFETY.md:46-50`), and the age-gate bullet listed `SAF-009` twice. Dropped the wrong citation and replaced the duplicate with `BUG-004, BUG-213` (the pre-move `crc/aws/README.md` text cited `SAF-006 to SAF-010, BUG-004`). Docs only.
- **NIT (left):** the new `crc/aws/vm/README.md` `## Safety Invariants` is shorter than the AWS-3 text it replaced in `crc/aws/README.md`: it no longer states that a dry run of `stop` records ids (BUG-217), that the closing summary is logged by `plan()` in a dry run and by `apply()` otherwise, or that the server-side filters are only state and `tag:key` (BUG-021). The facts remain in `BUGS.md` and `dry_run_names()` under `## Public API`.
- **NIT (left):** round 1's two NITs (`Clock` / `Decision` aliases not re-exported; stale `legacy_baseline.txt` entry, owner-only file).
- **New quirk recorded:** `docs/refactor/bugs/pending/ST-AWS-VM.md` (NEW): `find_instances_with_tag` return annotation, upper-cased `state`, and the duplicated-`Name` fallback (`main:crc/aws/vm.py:110`, `:136-141`, `:290-292`, verified with `git show main:crc/aws/vm.py`).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed! |
| `mypy` | Success: no issues found in 197 source files |
| import-linter | Contracts: 5 kept, 0 broken |
| `pytest -q` | 42213 passed |

Test count: 42213 passed (same as base `d109ee5` and round 1). After the README fix, ruff, mypy, import-linter and `tests/archtest` + `tests/test_aws_vm*.py` (467 passed) were re-run green.

ROUND 2 VERDICT: APPROVE
