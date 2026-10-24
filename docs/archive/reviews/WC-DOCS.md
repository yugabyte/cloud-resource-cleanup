# WC-DOCS

## Coding summary

- Lane: docs only (docstrings, comments and READMEs); no logic, no test logic, no BUGS.md or
  CLEANUP_SAFETY.md edits. Nothing was routed, shimmed or deleted: the CLI already runs every
  (cloud, kind) pair through `crc.cli.bridge` -> route tables -> `crc.core.engine.run` -> registered
  Cleaners -> `crc.notify`, so the docs now say so. The adapter classes in `crc/aws`, `crc/azu`,
  `crc/gcp` and `crc/oci` belong to the other lanes and are untouched; no parity-test retargeting
  was needed here.
- `crc/core`: `engine.py` ("nothing is wired yet", the `vpc` label "placeholder for the wiring
  rung"), `selection.py` ("the wiring rung only calls `build_selection()`"; now says the bridge builds
  a one-pair `Selection` and `validate_targets` checks pairs), `report.py`, `logger.py`, `policy.py`
  docstrings and comments; `crc/core/README.md` (deferred-wiring paragraph, rung names R3 / FIX-CORE-AGE /
  LOG-0, `crc.py` strings, moved-doc links: `docs/archive/FILTER_VARIANTS.md`, `docs/logging.md`).
- `crc/README.md` (`crc.py` re-exports only `main`, `get_argparser` and the validators; the adapter
  classes are not the CLI entry point), `crc/notify/README.md` and `crc/notify/*.py` comments (no more
  "as `crc.py` does today"), `tests/README.md`, `docs/refactor/bugs/README.md` and
  `docs/refactor/bugs/pending/README.md` (no lanes or rungs).
- `tests/*.py`: 64 module docstrings and comments lost their rung, lane and review names (`FIX-*`,
  `LOG-*`, `CL2-*`, `WA-*`, `ENG-*`, `pre-CL2`, `BEHAVIOUR_FIX.md`, `LOGGING.md`). BUG-NNN and SAF-NNN
  IDs stay. Test and file names are unchanged (for example `test_bug_356_lane_rule_...`,
  `helpers_cl2_*.py`, `helpers_wa1_notify.py`).
- Test count: 44415 before and after (no test added or removed; ARCH-008 unchanged, no floor
  raise). `make check` is green: ruff, mypy, lint-imports, pytest, ARCH checks, SAF-LOCK.
- Owner actions: none. Optional follow-ups below.

## Left over (files this lane may not edit)

- `crc/aws/**`, `crc/azu/**`, `crc/gcp/**`, `crc/oci/**` READMEs still cite `docs/refactor/PLAN.md`,
  `FILTER_VARIANTS.md`, `LOGGING.md` and `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`, and
  rung names (R4a, AWS-2, ENG-0, ...); `crc/aws/keypairs/__init__.py` names `ENG-0`.
- `crc/cli/README.md` and `crc/cli/routes/azu.py:1` ("Empty until its lane migrates them").
- `tests/legacy/README.md:65` (`BEHAVIOUR_FIX.md`) and the archtest remediation texts
  (`tests/archtest/bug_refs.py:15`, `frozen_inputs.py:13-14`).
- Test file names that carry lane names (`helpers_cl2_*.py`, `helpers_wa1_notify.py`,
  `helpers_wa2_scan.py`) were left because renaming is not a docstring edit.

## New bugs

None found. Doc statements that were wrong and are now fixed: `crc/README.md` said `crc.py`
re-exports the adapter names, the constants and `CRC`; `crc/notify/README.md` said `crc.py`
delegates `notify_*`, `ping_on_slack`, `get_msg` and `write_influxdb` to the notifiers (`crc.py` is
a 30-line entry point; the `CRC` run context in `crc/cli/runner.py` has only `get_msg`).

## Round 1 (Argon)

### Findings and fixes

- The coder's WC-DOCS commit (`8611c31`) stayed strictly within the allowed files and doc-only
  scope (`crc/core/**` docstrings/comments, `crc/core/README.md`, `crc/README.md`,
  `crc/notify/README.md`, `crc/notify/*.py` docstrings/comments, `tests/README.md`, `tests/*.py`
  docstrings/comments, `docs/refactor/bugs/README.md` and `docs/refactor/bugs/pending/README.md`),
  fixing all items from the WC-DOCS task and the owned files in `docs/archive/reviews/DOCS-1.md`.
- Four minor leftover stale phrases / doc inaccuracies in the lane's files fixed in Round 1:
  1. `crc/notify/README.md:32-33` and `tests/README.md:10-13,45-46` still said `test_notify_parity.py`
     runs inputs through `tests/legacy_crc_notify_reference.txt`. Since `CL2-NOTIFY`, both
     `test_notify_parity.py` and `test_notify_crc_parity.py` compare against `tests/legacy/crc_main.txt`
     via `load_legacy_crc()`, while `legacy_crc_notify_reference.txt` is an unused frozen reference copy.
     Updated both READMEs (and listed `test_cli_runner_parity.py`, `test_cli_scan_parity.py`,
     `test_cli_bridge_<cloud>.py` and the `test_<name>_engine.py` companions in `tests/README.md`).
  2. `tests/test_azure_logger.py:4` said "Every case runs the adapter `crc.py` uses"; updated to
     "Every case runs the Azure adapter or cleaner".
  3. `tests/test_crc_service_parity.py:1` and `tests/test_crc_utils_parity.py:1` still said
     "against the refactored `crc.service.Service`" / "against the refactored `crc.utils`"; dropped
     "the refactored".

### Gates

1. **Nothing production uses deleted:** No production code was removed in this docs-only lane.
   Verified `crc/cli/scan.py`, `crc/cli/routes/{aws,azu,gcp,oci}.py`, all 20 registered cleaners in
   `crc.core.registry`, and all 5 adapters built by the hash-locked safety tests (`crc.aws.disk.Disk`,
   `crc.aws.spot_instance_requests.SpotInstanceRequests`, `crc.azu.spot_vm.SpotVM`,
   `crc.gcp.spot_vm.SpotVM`, `crc.oci.spot_vm.SpotVM`) remain importable and intact.
2. **Parity tests non-vacuous:** Only docstrings and comments changed in `tests/*.py` (zero test
   logic edits). Locally broke `KeyPairCleaner.plan` in `crc/aws/keypairs/cleaner.py` to return an
   empty `Plan()`, confirmed `tests/test_aws_keypair_parity.py::test_cleaner_protocol_matches_main`
   failed immediately, and restored the file.
3. **No rung, refactor, deferred-wiring or moved-doc references in owned files:** Grepped across
   `crc/core/**`, `crc/README.md`, `crc/notify/**`, `tests/README.md`, `tests/*.py` and
   `docs/refactor/bugs/**`: no rung/lane/wave names (`R0a`..`R4`, `AWS-*`, `ENG-*`, `FIX-*`,
   `LOG-*`, `CL2-*`, `WA-*`), no "refactored" or deferred-wiring claims, and all moved-doc links
   updated (`docs/archive/FILTER_VARIANTS.md`, `docs/logging.md`, `.cursor/skills/crc-maintain/*`).
   `BUG-NNN` and `SAF-NNN` IDs are preserved.
4. **No hash-locked test, oracle, archtest or out-of-lane file touched:** `git diff 302612d..HEAD`
   touches zero files in `tests/archtest/`, `tests/legacy/`, the 5 hash-locked test files,
   `crc/{aws,azu,gcp,oci,cli}/`, `docs/refactor/BUGS.md` or `CLEANUP_SAFETY.md`.
5. **Size limits and Public API sync:** Every touched file is at most 250 lines and every function
   at most 60 lines (`ARCH-004` green); README `## Public API` sections are in sync (`ARCH-002`
   green) and all relative doc links resolve (`ARCH-011` green).
6. **Full `make check`:** After `pip install -r requirements.txt -r requirements-dev.txt`,
   `make check` passes (`ruff`, `mypy`, `lint-imports`, `pytest`, `ARCH-001`..`011`, `SAF-LOCK`):
   **44415 passed in 55.82s** (total `make check` wall time **62.7s**; `ARCH-008: 44415 tests, floor 44415`).

VERDICT: APPROVE


## Round 2 (Opus)

### Round 1 check

- Round 1's four fixes are correct: `crc/notify/README.md` and `tests/README.md` now name
  `tests/legacy/crc_main.txt` via `load_legacy_crc()` as the notify oracle, every file they list
  exists (including the five `test_<name>_engine.py` companions), and the `test_azure_logger.py`,
  `test_crc_service_parity.py` and `test_crc_utils_parity.py` docstrings are accurate.

### Findings and fixes

1. Fixed: `docs/refactor/bugs/README.md:16` and `docs/refactor/bugs/pending/README.md:13` changed the
   row location rule to "current file:line, verified in the working tree". The register they feed
   (`docs/refactor/BUGS.md` header and rules 1 and 5, its `Location on main` / `Exists on main`
   columns) still requires `main` file:line verified with `git show main:<path>`, and that register
   is out of this lane. Restored the `main` rule in both READMEs. For the same reason, restored
   "`main` file:line" in Extension Rules `crc/README.md:45` and `crc/notify/README.md:56`. That
   names the branch the register cites; it is not refactor history.
2. Accepted as accurate on this branch: `tests/README.md:14-16,29,44` and `crc/README.md:8` still
   name "adapter" classes. They still exist in `crc/aws`, `crc/azu`, `crc/gcp` and `crc/oci`, and the
   parity tests still run them. Once the adapter-deleting lanes merge, the docs lane that follows
   must drop those mentions.
3. Not changed: the BUGS.md header and rules still use rung wording (`R3`, "behaviour-fix rung",
   `docs/refactor/PLAN.md`, `STATUS.md`). This lane may not edit BUGS.md; leave it to the docs lane.

### Gates

1. **Nothing production uses deleted:** the diff `302612d..HEAD` deletes no file. `crc/cli/scan.py`,
   `crc/cli/routes/*.py`, `crc/cli/bridge.py` (`build_notifiers`, `engine.run` at line 140), the
   registry factories and the five adapters the hash-locked tests build are unchanged.
2. **Parity tests non-vacuous, no test logic edited:** I compared the AST of all 72 touched `.py` files
   before and after, with docstrings removed: none differs, so only docstrings and comments changed.
   In a throwaway worktree, I made `crc/gcp/ip_cleaner.py` `apply()` skip the last region.
   `tests/test_gcp_ip_parity.py::test_cleaner_protocol_matches_main[no-filter-all-reserved]` failed
   (1 failed, 60 passed); after the restore, all 158 pass. Not committed. Round 1 broke a different
   cleaner (`KeyPairCleaner.plan`) with the same result.
3. **Names and links:** grep over `crc/core/**`, `crc/README.md`, `crc/notify/**`, `tests/README.md`,
   `tests/*.py` and `docs/refactor/bugs/**` finds no rung names (`R0a`..`R4`, `AWS-n`, `ENG-*`,
   `FIX-*`, `LOG-*`, `CL2-*`, `WA-n`, `WB-n`), no "rung", "wiring" or "deferred" wording, and no link to
   `docs/refactor/PLAN.md`, `STATUS.md`, `docs/refactor/FILTER_VARIANTS.md`, `docs/refactor/reviews`,
   `LOGGING.md`, `crc-python-refactor` or `BEHAVIOUR_FIX.md`. Every file path cited in the six touched
   READMEs exists. The owned items of `DOCS-1.md` "## Links to fix" are all addressed. The
   docstrings' claims match the code: `crc.py` re-exports `main`, `get_argparser` and the validators;
   `bridge.py` builds a one-pair `Selection`; `validate_targets` is in `crc/cli/dispatch.py`;
   SAF-009 is in `CLEANUP_SAFETY.md:73`; Slack `fail_open` is False and Influx `fail_open` is True.
4. **Protected files:** no change to the five hash-locked test files, `tests/legacy/`,
   `tests/archtest/`, `crc/{aws,azu,gcp,oci,cli}/`, `docs/refactor/BUGS.md` or `CLEANUP_SAFETY.md`.
5. **Sizes and Public API:** `ARCH-002` and `ARCH-004` are green (files at most 250 lines,
   functions at most 60 lines, Public API sections in sync).
6. **`make check`** (after `pip install -r requirements.txt -r requirements-dev.txt`) is green on
   the final tree: ruff, mypy (372 files), lint-imports, pytest, ARCH checks and SAF-LOCK.
   **44415 passed in 53.47s**, with `make check` taking 60s in total; `ARCH-008: 44415 tests, floor
   44415`. The test count is unchanged.

### New bugs

None.

ROUND 2 VERDICT: APPROVE
