## Coding summary

Lane TD-1 (no production behaviour change): tracked TODOs for open high-risk bugs, the
ARCH-012 check, the todo-to-issue workflow, the oracle note and the adapter-wording cleanup.

- **Routed kinds:** none changed. No cleaner, route table or flag was touched; only comments,
  docs, one workflow and one archtest check were added.
- **Adapters deleted or shimmed:** none in this lane. The wording about adapter classes was
  removed from comments and READMEs in `crc/`: `crc/README.md`, `crc/cli/README.md`,
  `crc/cli/bridge.py`, `runner.py`, `scan.py`, `routes/azu.py`, `routes/gcp.py`,
  `crc/azu/ip/cleaner.py`, `crc/aws/keypairs/__init__.py`. `git grep -niw adapter crc` still
  finds two hits in `crc/cli/scan.py` (`_NO_SCAN_CLASSES`): they are the class paths printed in
  the `NotImplementedError` message of `--scan_tag` for Azure, GCP and OCI, which is output the
  parity tests compare, so changing them is a behaviour change (left for the owner).
- **CLI parity tests:** unchanged; they still reach the cleaners through `crc.cli.bridge`
  (`build_registry` / `build_notifiers` patched, `FakeCleaners`).
- **Test count:** 44,572 before, 44,597 after (+25, the ARCH-012 self-tests); floor raised
  with `make raise-test-floor`. `make check` is green (44,596 passed, 1 skipped).

### What was done

1. **TODOs.** Critical/high rows still open or partly fixed in `docs/refactor/BUGS.md`
   (recomputed from the table; every one was re-read against this branch and is still true):
   BUG-062, 075, 083 (partly fixed), 112, 124, 142, 173, 183, 213, 283 (partly fixed). Each has
   one `TODO(BUG-NNN)` with a `labels: bug, risk-high, crc` line above the function that holds
   the behaviour:

   | Bug | Where |
   |-----|-------|
   | BUG-062 | `crc/gcp/ip_cleaner.py` `IpCleaner.plan_region` |
   | BUG-075 | `crc/azu/ip/cleaner.py` `AzureIpCleaner.decide` |
   | BUG-083 | `crc/aws/vpc/cleaner.py` `VpcCleaner.plan_region` |
   | BUG-112 | `crc/azu/nic/cleaner.py` `AzureNicCleaner.decide` |
   | BUG-124 | `crc/gcp/disk_cleaner.py` `DiskCleaner._older_than_detach_age` |
   | BUG-142 | `crc/aws/snapshot/cleaner.py` `SnapshotCleaner._decide` |
   | BUG-173 | `crc/azu/disk/cleaner.py` `AzureDiskCleaner._is_old_enough` |
   | BUG-183 | `crc/gcp/vm_cleaner.py` `VmCleaner._is_old` |
   | BUG-213 | `crc/aws/vm/judge.py` `_judge` |
   | BUG-283 | `crc/aws/disk/detach.py` `volume_recently_attached` (see also `volumes.py` `_detach_floor`) |

   BUG-355: `tests/test_jenkins_contract.py` passes (142 cases) and the Azure Spot fixture line in
   `tests/fixtures/jenkins_commands.txt` carries `--age '{"days": 1}'`; the row is marked
   "fixed in cloud-resource-cleanup-triggers #29" and has no TODO.
2. **Workflow** `.github/workflows/todo-to-issue.yml`: push to `main` and `workflow_dispatch`
   only, guarded by `github.repository == 'yugabyte/cloud-resource-cleanup'`, `contents: read`
   and `issues: write`, action pinned to `37bb7b56...` (v5.1.15), `CLOSE_ISSUES: "true"`,
   `AUTO_ASSIGN: "false"`, `IDENTIFIERS` TODO only, `IGNORE` `tests/legacy/.*,docs/archive/.*`.
3. **ARCH-012** (`tests/archtest/todo_bugs.py`, `test_arch012_todo_bugs.py`, README row, AGENTS.md,
   `fix-bug.md`, BUGS.md rule 5): a TODO(BUG-NNN) must name an open or partly fixed row; each
   open or partly fixed critical/high row has exactly one; a TODO without a BUG id fails; a
   duplicate fails. The self-tests build the keyword from two string pieces so the action does
   not open issues for test strings.
4. **Oracle note** in `tests/legacy/README.md` ("Retiring an oracle") and one line in `AGENTS.md`.
5. **Adapter wording** removed as described above.

### Owner actions

- Enable Settings > Actions > General > Workflow permissions "Read and write" in
  `yugabyte/cloud-resource-cleanup`, or the workflow cannot create or close issues.
  `INSERT_ISSUE_URLS` stays off.
- Decide whether `_NO_SCAN_CLASSES` in `crc/cli/scan.py` may stop naming `adapter` classes in the
  `--scan_tag` error message (behaviour change, parity-tested).
- `tests/README.md` (outside this lane's edit list) still says "adapter" in four lines.

### New bugs

None. Note: `crc/aws/disk/README.md` line 50 says BUG-283 is "(fixed)" while the register row
says "partly fixed"; the row wins and keeps its TODO (the lane could not edit that README's
status wording beyond comments; the owner may align it).

### Left for later

Nothing from the task list. `make check` is the gate and passed.


## Round 1 (Argon)

Reviewed `git diff c16a3ec..HEAD` against the Lane TD-1 specification and owner-approved contract.

### Findings

None blocking or requiring changes.

### Checklist Verification

1. **Open critical/high bugs (`docs/refactor/BUGS.md` and `crc/`):**
   - Recomputed all 403 rows in `docs/refactor/BUGS.md`. All 5 critical rows (`BUG-001`, `002`, `074`, `111`, `172`) are already fixed.
   - `BUG-355` (high): verified that line 27 of `tests/fixtures/jenkins_commands.txt` (from `yugabyte/cloud-resource-cleanup-triggers` `8ceee47`) passes `--age '{"days": 1}'` for Azure `spot_instances` and all 142 cases in `tests/test_jenkins_contract.py` pass; the row status is updated to `fixed in cloud-resource-cleanup-triggers #29 (8ceee47 passes \`--age\`; \`tests/test_jenkins_contract.py\` runs the production command)` with no `TODO`.
   - The remaining 10 open or partly fixed high-risk rows (`BUG-062`, `075`, `083`, `112`, `124`, `142`, `173`, `183`, `213`, `283`) are each still present on `td/1` and each has exactly one `# TODO(BUG-NNN): <title>` (all titles 31–70 chars, well under 100) followed by `#  labels: bug, risk-high, crc` directly above the function implementing the behaviour (`IpCleaner.plan_region`, `AzureIpCleaner.decide`, `VpcCleaner.plan_region`, `AzureNicCleaner.decide`, `DiskCleaner._older_than_detach_age`, `SnapshotCleaner._decide`, `AzureDiskCleaner._is_old_enough`, `VmCleaner._is_old`, `InstanceJudge._judge`, and `volume_recently_attached` with a `#  see also:` line pointing to `crc/aws/disk/volumes.py` `_detach_floor`).
2. **Workflow (`.github/workflows/todo-to-issue.yml`):**
   - Triggers only on `push` to `branches: [main]` and `workflow_dispatch` (no `pull_request`).
   - Guarded by `if: github.repository == 'yugabyte/cloud-resource-cleanup'`.
   - Minimal permissions: `contents: read`, `issues: write`.
   - Pinned to `alstr/todo-to-issue-action@37bb7b56e58569ef273b60678048030a7f0c261a  # v5.1.15` with `CLOSE_ISSUES: "true"`, `AUTO_ASSIGN: "false"`, `IDENTIFIERS: '[{"name": "TODO", "labels": []}]'`, `IGNORE: "tests/legacy/.*,docs/archive/.*"`, and header comment noting the repo admin workflow permission setting and that `INSERT_ISSUE_URLS` stays off.
3. **ARCH-012 (`tests/archtest/todo_bugs.py`, `tests/archtest/test_arch012_todo_bugs.py`):**
   - Uses `RULE = "ARCH-012"` and `_common.violation(...)` with actionable `REMEDIATION:` text for each failure mode.
   - Enforces that every `TODO(BUG-NNN)` in `crc/` (`.py`, `.md`) names an `open` or `partly fixed` row in `docs/refactor/BUGS.md`, every `open` or `partly fixed` `critical`/`high` row has at least one `TODO(BUG-NNN)`, duplicate `TODO(BUG-NNN)` comments fail, and any `TODO` in `crc/` without a `BUG-NNN` ID fails (while `medium`/`low` rows are not required to have a `TODO`).
   - Self-tests in `test_arch012_todo_bugs.py` (25 tests total) verify firing on missing `TODO` (`critical`/`high`, `open`/`partly fixed`), `TODO` on closed (`fixed`, `not a bug`, `wontfix`) or unknown (`BUG-999`) rows, `TODO` without a bug ID (including short IDs like `BUG-1`), and duplicate `TODO(BUG-NNN)`, and pass on the repository.
   - Documented in `tests/archtest/README.md`, `AGENTS.md`, `.cursor/skills/crc-maintain/fix-bug.md`, and `docs/refactor/BUGS.md` rule 5.
   - Frozen-oracle retirement note added in `tests/legacy/README.md` ("Retiring an oracle") and `AGENTS.md` rule 3.
4. **No production behaviour change in `crc/` & contract compliance:**
   - AST comparison against `c16a3ec` across all 15 modified `crc/**/*.py` files confirms zero executable AST changes (comments and docstrings only).
   - Guardrail files (`tests/legacy/*.txt`, `tests/archtest/safety_lock.json`, `tests/archtest/frozen_inputs.json`, `requirements.txt`) are untouched.
5. **No adapter wording left in `crc/` comments or READMEs:**
   - `git grep -ni adapter crc` matches only the 3 parity-tested class-name strings in `_NO_SCAN_CLASSES` (`crc/cli/scan.py:21-23`).

### Gates

Ran `pip install -r requirements.txt -r requirements-dev.txt && make check`:
- `ruff check .`: All checks passed!
- `ruff format --check .`: 371 files already formatted
- `mypy`: Success: no issues found in 370 source files
- `lint-imports`: Contracts: 5 kept, 0 broken
- `pytest -q -n auto`: **44,596 passed, 1 skipped in 56.49s** (44,597 collected, up from 44,572 at `c16a3ec`, +25)
- `python3 -m tests.archtest.test_count check`: `ARCH-008: 44597 tests, floor 44597`
- Total `make check` runtime: **1m16.0s** wall clock

VERDICT: APPROVE


## Round 2 (Opus)

Re-checked Round 1 (no findings, APPROVE), then checked `git diff c16a3ec..HEAD` independently.

### Findings

1. Fixed (wording): `.cursor/skills/crc-maintain/fix-bug.md` step 7 said the new comment goes
   "two lines above the function", which reads as a two-line gap. It now says "a two-line comment
   directly above the function". Docs only.
2. Not blocking, for the owner: `crc/aws/disk/README.md` lines 50 and 53 call BUG-283 "fixed",
   but the register row says "partly fixed" and the code keeps `TODO(BUG-283)`. This lane may
   edit crc READMEs only to remove adapter wording, so the line is left as it is (the coder noted
   it as well).
3. Not blocking: `actions/checkout@v4` in `todo-to-issue.yml` is pinned by tag, not by SHA. That
   matches `ci.yml`, and the task required a SHA pin only for the todo action.
4. Confirmed correct to leave: `git grep -niw adapter crc` finds only two `_NO_SCAN_CLASSES`
   strings in `crc/cli/scan.py` (azure and oci; Round 1 said three, which is the count for the
   case-insensitive grep without `-w`). They are the `--scan_tag` NotImplementedError output, so
   they are behaviour and not comment wording. No comment or README in `crc/` still mentions
   adapter classes.

### Checks

1. Bugs: recomputed from the 403 rows. No critical row is open. Ten high rows are open or partly
   fixed (BUG-062, 075, 083, 112, 124, 142, 173, 183, 213, 283), and each has exactly one
   `# TODO(BUG-NNN): <title>` plus `#  labels: bug, risk-high, crc` directly above the function
   that holds the behaviour. All titles are under 100 characters, and BUG-283 has a `see also`
   line for `volumes.py` `_detach_floor`. Spot-checked that the bugs are still true: there is no
   `require_positive_age` or age gate in the AWS VM, GCP VM, GCP disk or Azure disk paths, and no
   age check at all in Azure NIC, Azure IP, GCP IP, AWS VPC or AWS snapshot (age only). BUG-355:
   `tests/fixtures/jenkins_commands.txt:27` passes `--age '{"days": 1}'` for Azure
   `spot_instances`, and `tests/test_jenkins_contract.py` passes (142). The row is marked fixed
   in triggers #29 and has no TODO.
2. Workflow: runs on `push` to `main` and `workflow_dispatch` only, `if:
   github.repository == 'yugabyte/cloud-resource-cleanup'`, permissions `contents: read` and
   `issues: write`. `gh api .../git/ref/tags/v5.1.15` resolves to
   `37bb7b56e58569ef273b60678048030a7f0c261a`, the pinned SHA. It sets `CLOSE_ISSUES` "true",
   `AUTO_ASSIGN` "false", `IDENTIFIERS` TODO only and the `IGNORE` list, and the header comment
   names the admin setting and `INSERT_ISSUE_URLS` off. Outside `crc/` and the ignored paths, no
   comment starts with TODO (the self-tests build the keyword from two pieces), so the action
   opens only the ten intended issues.
3. ARCH-012: the self-tests cover a missing TODO (critical and high, open and partly fixed), a
   TODO for a fixed, not-a-bug, wontfix or unknown row, a TODO without an id, and a duplicate.
   The check passes on the repository. Two mutations on the real repository: deleting
   `TODO(BUG-183)` fires "has no TODO(BUG-183)", and marking BUG-062 fixed fires "is not an open
   or partly fixed row". Both were reverted.
4. No behaviour change: in the 16 changed `crc/**/*.py` files, the AST with docstrings blanked is
   identical to `c16a3ec`. `tests/legacy/*.txt`, `safety_lock.json`, `frozen_inputs.json` and
   `requirements.txt` are untouched. AGENTS.md is 116 lines (budget 120).
5. Docs: the oracle note is in `tests/legacy/README.md` ("Retiring an oracle") and in AGENTS.md
   rule 3. The ARCH-012 row is in `tests/archtest/README.md`, and the fix-a-bug steps in AGENTS.md
   and `fix-bug.md` say to remove the TODO and mark the row fixed.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt && make check`: ruff clean, 371 files
formatted, mypy clean (370 files), import-linter 5 contracts kept, **44,596 passed, 1 skipped
(44,597 collected) in 58.95 s** pytest, ARCH-008 floor 44,597. `make check` wall clock **1m21s**.

ROUND 2 VERDICT: APPROVE
