## Coding summary

Rung FINAL, README sweep. Docs only: no `.py`, `pyproject.toml`, `Makefile`, `.github/`, `tests/archtest/`, `tests/legacy/*.txt`, `AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `docs/refactor/PLAN.md` or `.cursor/` file changed. Each README keeps the four ARCH-002 sections and its synced Public API.

Files changed and what was stale:

- `crc/README.md`: the direct modules were called "legacy, not yet refactored". `service.py` and `utils.py` are refactored and strict-checked (parity: `tests/test_crc_service_parity.py`, `tests/test_crc_service_is_old_parity.py`, `tests/test_crc_utils_parity.py`); `aws/azu/gcp/oci` were listed as "legacy". The note "Not synced with ARCH-002" is still true but its reason was wrong: `package_dirs` in `tests/archtest/readmes.py` skips the modules directly in `crc/`, and `crc/__init__.py` has no `__all__` (not "the modules are legacy"). The note now says so. The `crc/cli/` text was already correct.
- `crc/aws/README.md`: "Legacy AWS cleaners", "Partly refactored", "every other module is legacy" (all modules are refactored); `spot_instance_requests.py` listed as a module (it is the package `spot_instance_requests/`); Public API intro listed only some modules; "legacy adapter class" and "not as another legacy class"; Extension Rule 4 told readers to remove a module from the ruff and mypy lists (the lists hold only five test files).
- `crc/azu/README.md`, `crc/gcp/README.md`, `crc/oci/README.md`: "Legacy Azure/GCP cleaners" headers, "Partly refactored" (GCP), "legacy adapter", "legacy Base", "not as another legacy class", and the extension rule about removing a module from the legacy lists.
- `crc/aws/{disk,vm,vpc,spot_instance_requests}/README.md`, `crc/azu/{vm,spot_vm}/README.md`, `crc/gcp/spot_vm/README.md`, `crc/oci/{vm,spot_vm}/README.md`: "legacy class / entry point / adapter / Base / tests" wording; the adapter classes remain the entry point `crc/cli` uses.
- `crc/core/README.md`: said only `aws` / `keypair`, `ip` and `kms` are registered and that `crc.py` sends notifications; every cleaner of the four clouds is registered (lists them), and `crc/cli` (not `crc.py`) sends them. States that wiring `crc/cli` through `engine.run` is deferred (`docs/refactor/PLAN.md`).
- `tests/README.md`: "tests of the legacy cloud modules", missing CLI, service, utils and all other parity tests, `test_azure_*` pattern; now lists every parity test file, the five hash-locked files and the helpers.
- `tests/legacy/README.md`: listed three oracles; now a table of all 30 oracles with their `main` source path and parity tests, plus the notify oracle that lives in `tests/legacy_crc_notify_reference.txt`.
- `docs/refactor/README.md`, `docs/refactor/reviews/README.md`: rung and review lists stopped at R2c; "legacy module" in the `FILTER_VARIANTS.md` row.
- Checked and left unchanged (still true): `crc/cli/README.md`, `crc/notify/README.md`, `crc/oci/{disk}/README.md`, `crc/aws/{...}` safety text, `docs/README.md`, `docs/refactor/bugs/**/README.md`, `tests/archtest/README.md`.

Every path written was checked to exist; the `main` source paths in the oracle table were checked with `git cat-file -e main:<path>`. No SAF or BUG ID was added.

## Out of scope: docs found wrong, not changed

- `.py` docstrings still say "legacy class": `crc/aws/disk/__init__.py:6`, `crc/aws/vpc/__init__.py:6`, `crc/oci/spot_vm/adapter.py:3` (and likely the other adapter module docstrings). Wording only; `.py` files are out of scope.
- `docs/refactor/STATUS.md` row "Final (docs)" still says "in progress" and "README wording sweep follows as a Cursor rung"; the owner or driver updates it.
- `docs/refactor/FILTER_VARIANTS.md` intro (rung R1b) says "Read from the code on `refactor/python-agentic`" and calls the modules "legacy modules" with `file:line` citations of the pre-refactor files. The citations are `main` file:line, not the current layout; not rewritten here.
- `AGENTS.md` line 10 says the branch is `refactor/python-agentic` (guardrail file) and `docs/refactor/PLAN.md` "Final" row mentions the README sweep (guardrail file); both untouched.
- `make check` in this environment: `mypy` reports 17 `import-untyped` errors for `google.cloud`, `azure.*` and `google.api_core` (installed SDKs without `py.typed`; one `unused-ignore` in `tests/test_gcp_base.py:15`). It is the same before and after this change (no `.py` or config file touched), so it comes from the VM's package versions, not from this rung. Not fixed (config out of scope).

## Round 1 (Argon)

Scope reviewed: `git show 4ecf41a` (local driver's Final docs commit: `AGENTS.md`, `ARCHITECTURE.md`, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`, `.cursor/rules/00-map.mdc`, `.cursor/skills/crc-python-refactor/{SKILL.md,REVIEW_CHECKLIST.md,README.md,BEHAVIOUR_FIX.md}`) and `git diff 4ecf41a..7bd3202` (coder's README sweep across `crc/**/README.md`, `tests/README.md`, `tests/legacy/README.md`, `docs/refactor/README.md`, `docs/refactor/reviews/README.md`, `docs/refactor/reviews/FINAL.md`).

### Verification against current tree

1. **Changed docs vs current tree**:
   - Ratchet contents in `pyproject.toml` (`[tool.ruff] extend-exclude` and `[[tool.mypy.overrides]]` with `ignore_errors = true`): verified both lists contain only the 5 hash-locked safety test files (`tests/test_aws_disk.py`, `tests/test_aws_spot_instance_requests.py`, `tests/test_azu_spot_vm.py`, `tests/test_gcp_spot_vm.py`, `tests/test_oci_spot_vm.py`). Every module under `crc/` is checked by ruff and mypy strict; `crc.py` is checked by ruff and excluded from mypy due to the module/package name clash.
   - `tests/legacy/README.md`: verified all 30 oracle `.txt` files under `tests/legacy/` are listed with accurate `main` source paths and parity test mappings, plus `tests/legacy_crc_notify_reference.txt` mapped to `tests/test_notify_parity.py`.
   - `tests/README.md`: verified all 28 `*_parity.py` files, the 5 hash-locked safety test files, and test helpers (`helpers_azure_*.py`, `helpers_crc_service.py`) are accurately described.
   - `crc/README.md`: accurately describes `crc/cli/`, `crc/service.py` and `crc/utils.py` as refactored and strict-checked, and explains why `crc/README.md` is not symbol-synced by `ARCH-002` (`package_dirs` in `tests/archtest/readmes.py` skips files whose parent is `crc/` directly, and `crc/__init__.py` has no `__all__`).
   - `ARCH-001`..`ARCH-005` and `SAF-LOCK` descriptions in `ARCHITECTURE.md` and `tests/archtest/README.md` match `tests/archtest/*.py`; `ARCH003_ALLOWLIST` in `tests/archtest/bans.py` holds exactly `crc/azu/_base.py` and `crc/oci/_base.py` (`frozenset({"env"})`).
   - Found and fixed stale `docs/refactor/bugs/pending/*.md` references across READMEs (all pending bug files were deleted when folded into `docs/refactor/BUGS.md`; see Findings below).
2. **No README calls refactored code legacy or partly refactored**:
   - Verified across all 30 `README.md` files in the repository. Only the legacy ratchet, `tests/legacy/` oracles, and the 5 hash-locked test files are referred to as legacy. Adapter classes (`SpotVM`, `VM`, `Disk`, ...) are called adapters / entry points used by `crc/cli`.
3. **`BEHAVIOUR_FIX.md` consistency and completeness**:
   - Consistent with `AGENTS.md` hard rules 1 (no weakened safety tests), 5 (no behaviour change outside named `BUG-NNN` rows in `PLAN.md`; never edit `tests/legacy/*.txt` oracles; per-case divergence markers), 11 (record newly noticed bugs in `BUGS.md` with `main` file:line, do not fix out of scope), and 12 (stop and hand locked safety test changes to the owner; never run `update_safety_lock.py`).
   - Consistent with `CLEANUP_SAFETY.md` and the `SAF-LOCK` workflow in `tests/archtest/README.md`.
   - Gives an agent a complete workflow to fix a `BUGS.md` row without weakening tests (locating code from `main` citations, marking diverging parity cases with `BUG-NNN` while asserting both inequality with the oracle and the exact new outcome, updating quirk checks only for in-scope bugs, adding unit tests, and updating `BUGS.md` status and READMEs).
   - Two minor gaps/inconsistencies with other CODEOWNERS files are flagged below as NITs (`BUGS.md` status string format and `tests/legacy/README.md` Extension Rule 2).
4. **Coder diff scope check (`git diff 4ecf41a..7bd3202`)**:
   - Verified: zero `.py`, `pyproject.toml`, `Makefile`, `tests/legacy/*.txt`, `tests/archtest/*`, or CODEOWNERS files were touched by `7bd3202`.

### Findings fixed in this review (READMEs and `STATUS.md`)

- **BLOCKING (fixed)** — Stale references to deleted `docs/refactor/bugs/pending/*.md` files across 12 READMEs (`crc/aws/README.md`, `crc/aws/disk/README.md`, `crc/aws/spot_instance_requests/README.md`, `crc/azu/README.md`, `crc/azu/vm/README.md`, `crc/azu/spot_vm/README.md`, `crc/cli/README.md`, `crc/gcp/README.md`, `crc/gcp/spot_vm/README.md`, `crc/oci/README.md`, `crc/oci/disk/README.md`, `crc/oci/spot_vm/README.md`, `crc/oci/vm/README.md`) and `docs/refactor/bugs/pending/README.md` (which still listed `GCP-1.md` under `## Public API` even though `docs/refactor/bugs/pending/` now contains only `.gitkeep` and `README.md`, and `docs/refactor/bugs/README.md` Extension Rule 2 claimed pending files are kept as history after integration). Replaced all nonexistent `docs/refactor/bugs/pending/*.md` references with their folded `BUG-NNN` ranges in `docs/refactor/BUGS.md`, updated `crc/oci/README.md` Extension Rule 2 (`api.py` or `client.py` instead of `*_api.py`), added missing `BUG-351` (`crc/aws/vm/README.md`), `BUG-327`..`BUG-332` and `BUG-043`..`BUG-044` (`crc/README.md`, `crc/notify/README.md`), and updated `docs/refactor/bugs/{,pending/}README.md`.
- **NIT (fixed)** — `tests/legacy/README.md:64`: Extension Rule 2 still said `Delete an oracle only in the rung that removes the behaviour it pins (a behaviour-fix rung)`, contradicting Extension Rule 3 and `BEHAVIOUR_FIX.md` / `AGENTS.md` rule 5 ("The oracles are kept after the refactor: behaviour-fix rungs mark each diverging parity case with its bug ID instead of editing them"). Updated Rule 2 to `Never delete or edit an oracle (AGENTS.md rule 5).`
- **NIT (fixed)** — `docs/refactor/STATUS.md:49`: Updated the `Final (docs)` row from `in progress` to `done, reviewed: Round 1 (Argon) APPROVE` with the summary of `4ecf41a`, `7bd3202`, and this review.

### Findings in CODEOWNERS / guardrail files (not edited; proposed changes for owner)

- **NIT** — `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md:70`:
  - Problem: `BEHAVIOUR_FIX.md:70` says `Set the Status of each fixed row in docs/refactor/BUGS.md to fixed (<rung>); keep the row.`, whereas `docs/refactor/BUGS.md:16` says `change its status (open, fixed in <rung>, not a bug, wontfix) and keep the row.` (`fixed (<rung>)` vs `fixed in <rung>`).
  - Proposed text for `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md:70`:
    `- Set the \`Status\` of each fixed row in \`docs/refactor/BUGS.md\` to \`fixed in <rung>\` (matching \`BUGS.md\` Rule 3); keep the row.`
- **NIT** — `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md:27-31`:
  - Problem: Step 2 tells the agent to open `tests/test_<cloud>_<kind>_parity.py` to find the code, but when a bug is in a cleaner that still has an adapter (`VM`, `Disk`, `SpotVM`, ...), both the `Cleaner` (`plan`/`apply`) and the adapter (or `crc/core/policy.py` / `crc/core/age.py`) may execute the path, and the parity test exercises both entry points.
  - Proposed text for `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md:27-31`:
    `2. Open the module's parity test (\`tests/test_<cloud>_<kind>_parity.py\`, \`tests/test_cli_*_parity.py\`, \`tests/test_<cloud>_base_parity.py\`, ...); its oracle is the \`tests/legacy/*.txt\` file it loads. Check both the registered \`Cleaner\` (\`plan\`/\`apply\`) and the adapter entry point (plus \`crc/core/policy.py\` or \`crc/core/age.py\` if shared) so both parity entry points stay aligned.`
- **NIT** — `ARCHITECTURE.md:19`, `ARCHITECTURE.md:30-32`, `ARCHITECTURE.md:73-74`, `ARCHITECTURE.md:102`:
  - Problem: `ARCHITECTURE.md` still has pre-refactor phrasing in a few spots: line 19 & 102 say `one module per resource` / `Create crc/<cloud>/<resource>.py` (several cleaners are now subpackages `crc/<cloud>/<resource>/`), lines 30-32 say `Today the independence contract for the four cloud packages is active. The core layer contract is a commented TODO until crc/core exists` (all 5 contracts in `pyproject.toml` are active), and lines 73-74 say `The engine selects cleaners from the registry using the parsed --cloud / --resource values` (`crc/core/selection.py` does the expansion and wiring `crc/cli` to `engine.run` is deferred per `PLAN.md`).
  - Proposed text for `ARCHITECTURE.md:30-32`:
    `- Enforced by import-linter (\`lint-imports\`, config in \`pyproject.toml\`): cloud package independence, \`crc.core\` isolation from cloud packages/services/notify/SDKs, and \`crc.notify\` isolation from cloud packages/services.`
- **NIT** — `docs/refactor/PLAN.md:19-26`:
  - Problem: Target architecture layout in `PLAN.md:19-26` omits `crc/core/selection.py` and `crc/cli/`.
  - Proposed text for `docs/refactor/PLAN.md:16-26`:
    Add `crc/cli/                  CLI arg parsing, validators, scan, dispatch and CRC class` and `crc/core/selection.py     --cloud / --resource expansion and opt-in gates`.

### Gates

Ran `pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | `All checks passed!` |
| `mypy` | `Success: no issues found in 202 source files` |
| `lint-imports` | `Contracts: 5 kept, 0 broken` (134 files, 725 dependencies) |
| `pytest -q` | `42213 passed in 164.32s` |

Test count: 42213 passed (unchanged from 42213 before this rung).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git show 4ecf41a` (driver's Final docs commit) and `git diff 4ecf41a..HEAD` (coder sweep `7bd3202` and Round 1 fixes `4d84933`).

### Round 1 fixes and findings re-checked

- The stale `docs/refactor/bugs/pending/*.md` paths are gone: `docs/refactor/bugs/pending/` holds only `README.md` and `.gitkeep`, and no README links a pending file. Confirmed.
- `tests/legacy/README.md` Extension Rule 2 (never delete or edit an oracle) now matches `AGENTS.md` rule 5 and `BEHAVIOUR_FIX.md` section 3. Confirmed.
- `STATUS.md` `Final (docs)` row: accurate (commits `4ecf41a`, `7bd3202`, pytest 42213).
- Round 1 NITs on `BEHAVIOUR_FIX.md:70` (`fixed (<rung>)` against `BUGS.md:16` `fixed in <rung>`) and on `ARCHITECTURE.md` / `PLAN.md` layout: agreed, still open (owner files).
- Round 1 said there are "28 `*_parity.py` files"; there are 34 (`ls tests/*parity*.py`). `tests/README.md` lists all 34 (CLI 3, service 2, utils 1, notify 1, AWS 10, Azure 7, GCP 5, OCI 5), so only the review text was wrong.

### Independent checks

1. **Statements against the current tree.**
   - The ratchet in `pyproject.toml`: ruff `extend-exclude` and the mypy `ignore_errors` override both list exactly the five hash-locked files. mypy has `files = ["crc", "tests"]` and `exclude = ['^crc\.py$']`. These are the same five files that `tests/archtest/safety_lock.json` locks. The README statements are true.
   - `ARCH003_ALLOWLIST` (`tests/archtest/bans.py:14`) holds `crc/azu/_base.py` and `crc/oci/_base.py` with `env`; the `crc/azu` and `crc/oci` READMEs match it.
   - The archtest rule names in the READMEs (ARCH-001 to ARCH-005, SAF-LOCK) match the files in `tests/archtest/`.
   - `crc/core/README.md`: its list of registered cleaners equals the `@register(...)` calls under `crc/` (8 AWS, 5 Azure, 4 GCP, 3 OCI).
   - `crc/README.md`: `crc/__init__.py` is one copyright line; `crc.py` (104 lines) imports `main`, `CRC`, the constants and the adapter names from `crc/cli`; `notifications.py` exists. The "Not synced with ARCH-002" reason is true.
   - Oracles: all 30 `tests/legacy/*.txt` files are byte-identical to `git show origin/main:<path>` for the paths in the table (checked with `cmp`). `crc_main.txt` is the whole `crc.py`.
   - **Fixed:** the `tests/legacy/README.md` `azu_vm.txt` row listed `tests/test_azure_vm_delete_helpers_parity.py`, but that test loads only `azu_vm_delete_helpers.txt`; I removed it. I also changed "the `crc_*` ones are the pieces of" to "copies of", because each `crc_*` oracle is a whole file.
2. **Legacy wording.** No README calls refactored code legacy or partly refactored. The word "legacy" now appears only for the ratchet, the `tests/legacy/` oracles and the archtest internals.
   - **Fixed:** `crc/{aws,azu,gcp,oci}/README.md:7` still said "Any change must follow ... the refactor rung for this package in `docs/refactor/PLAN.md` (Rung R4+)". Those rungs are done, so the line now sends a behaviour change to a behaviour-fix rung and `BEHAVIOUR_FIX.md`.
3. **`BEHAVIOUR_FIX.md`.** It is consistent with `AGENTS.md` rules 1, 5 and 12, with `CLEANUP_SAFETY.md` (fail closed; a SAF change goes to the owner) and with the SAF-LOCK workflow in `tests/archtest/README.md` (the owner runs `update_safety_lock.py`; a new `[SAF-NNN]` tag goes to the owner). It is enough to fix one BUGS.md row without weakening a test. Gaps:
   - **NIT** `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md:11-13` and `:70-71`, together with `AGENTS.md:70` (rule 11). Section 1 says a newly found bug is recorded in `BUGS.md`, and section 5 has the rung edit `BUGS.md` status directly. `docs/refactor/bugs/README.md` says parallel lanes never edit `BUGS.md`; they write `docs/refactor/bugs/pending/<rung>.md` and the integrator folds the rows in. Proposed text for line 13: "`BUGS.md` (in a parallel lane: `docs/refactor/bugs/pending/<rung>.md`, see `docs/refactor/bugs/README.md`), not fixed." Proposed text for line 70: "Set the `Status` of each fixed row in `docs/refactor/BUGS.md` to `fixed in <rung>` (in a parallel lane, the integrator does it); keep the row."
   - **NIT** `BEHAVIOUR_FIX.md:26-28`: add "or the `Found in` column of the `BUGS.md` row" after "the rung that found it" (the column exists, `BUGS.md:30`).
   - **NIT** `BEHAVIOUR_FIX.md:63-67`: also say that a locked test that asserts the buggy behaviour keeps passing until the owner relocks it. The rung must not mark it skip or xfail, because SAF-LOCK fails on that. The rung stays blocked, or the bug moves to a later rung.
4. **Coder diff scope.** `git diff --name-only 4ecf41a..HEAD` contains only `.md` files: no `.py`, `pyproject.toml`, `Makefile`, `tests/legacy/*.txt` or `tests/archtest/*` change.
5. **Other guardrail-file NITs (not edited):**
   - **NIT** `AGENTS.md:10` and `:84`: the branch name `refactor/python-agentic` is right for the integration branch, but lanes push to `refactor/lane-*`. Proposed text: "...push to the branch your task names (lanes: `refactor/lane-<rung>`; integration: `refactor/python-agentic`)".
   - **NIT** `pyproject.toml:45-47` and `:51-52` (CODEOWNERS): comments still say "the CLI rung must resolve that" and "Each rung removes the modules it refactors". Proposed text: "crc.py is a thin entry point; its code lives in crc/cli/ and is checked" and "LEGACY RATCHET: may only shrink; it holds only the five hash-locked safety test files."

### Gates

`pip install -r requirements.txt -r requirements-dev.txt`, then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | `All checks passed!` |
| `mypy` | `Success: no issues found in 202 source files` |
| `lint-imports` | `Contracts: 5 kept, 0 broken.` |
| `pytest -q` | `42213 passed in 160.56s` |
| `pytest -q tests/archtest` (re-run after the README fixes) | `92 passed` |

Test count: 42213 passed (same as Round 1 and before this rung).

No finding is BLOCKING. The README errors are fixed in this round. The open items are NITs in owner files: `BEHAVIOUR_FIX.md`, `AGENTS.md` and `pyproject.toml` comments, plus the `ARCHITECTURE.md` and `PLAN.md` items from Round 1.

ROUND 2 VERDICT: APPROVE
