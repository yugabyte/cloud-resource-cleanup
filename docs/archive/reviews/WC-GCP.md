## Coding summary

Lane WC-GCP (branch `wc/gcp`).

- Routed: `(gcp, ip)` and `(gcp, disk)` already run through `crc/cli/bridge.py`, the GCP route table and the registered Cleaners (`IpCleaner`, `DiskCleaner`); nothing in `crc/`, `crc.py` or the CLI tests used the `IP` and `Disk` classes. Unrouted kinds: none in this lane.
- Deleted: adapter classes `crc.gcp.ip.IP` (`ip_adapter.py`) and `crc.gcp.disk.Disk` (`disk_adapter.py`), their package re-exports and their Public API lines. `ip.py` and `disk.py` stay as facades for the client and cleaner names. The `VM` class (used by `crc/cli/scan.py`), `SpotVM` and the hash-locked tests are untouched.
- Tests retargeted to `crc.core.engine.run` (Registry plus `build_*_cleaner`, or a helper `tests/helpers_wc_gcp_engine.py` that runs a cleaner built around a fake client): `test_gcp_ip_parity.py`, `test_gcp_disk_parity.py` (same oracle cases, same SDK-mutation, order, log, stdout and error assertions, BUG-001, 063, 064, 005, 006, 126, 127 divergences kept), `test_gcp_ip.py`, `test_gcp_ip_fix.py`, `test_gcp_disk.py`, `test_gcp_disk_fix.py`, `test_gcp_logger.py`. The old adapter-vs-oracle test (`test_matches_main`) became `test_engine_matches_main`; a parametrised `..._lists_every_region/zone_before_it_deletes` test over all cases replaces its case count. Ported cases: second `delete()` accumulation (each engine run is independent; the oracle's accumulation is still asserted), `default_state` attribute (IpCleaner `state` argument through the engine), `disk_state` argument ignored, class attributes (client constants), constructor slack shapes (factory options).
- Allowed behaviour change (ENG): plan() lists everything before apply() deletes, also within a zone for disks; so a later listing error, or a nameless zone entry (`zone-without-name-after-first-zone`, `addresses-list-permission-denied`), stops before any delete. Asserted as `ENGINE_QUIRKS`.
- READMEs: `crc/gcp/README.md` trimmed to the current code (rung names, adapter wording, legacy-path wording and moved-doc links removed).
- Test count: floor 44415 before; 44415 collected after (adapter tests removed, ordering and ported tests added). Floor not raised.
- Owner actions: none required. `spot_vm/README.md` still has rung names and adapter wording (see Left).

## Left

- `crc/gcp/spot_vm/README.md` trim (text only) not done in the time limit; `VM`/`SpotVM` stay as classes.
- BUGS.md / CLEANUP_SAFETY.md ENG row and sentence belong to the CLI lane, not edited here.

## New bugs

None found.


## Round 1 (Argon)

### Findings

- **Check (1) Production paths**: Confirmed nothing used by production was deleted: `crc/cli/scan.py` still imports `crc.gcp.vm.VM`, `crc/cli/routes/gcp.py` imports `crc.gcp.ip_cleaner`, `crc.gcp.disk_cleaner`, `crc.gcp.vm_cleaner`, and `crc.gcp.spot_vm`, all four GCP registry factories (`build_ip_cleaner`, `build_disk_cleaner`, `build_vm_cleaner`, `build_spot_cleaner`) remain registered, and all five adapters used by the hash-locked safety tests (`crc.aws.disk.Disk`, `crc.aws.spot_instance_requests.SpotInstanceRequests`, `crc.azu.spot_vm.SpotVM`, `crc.gcp.spot_vm.SpotVM`, `crc.oci.spot_vm.SpotVM`) are untouched.
- **Check (2) Parity & unit tests on `crc.core.engine.run`**: Verified `tests/test_gcp_ip_parity.py` and `tests/test_gcp_disk_parity.py` (along with `test_gcp_ip.py`, `test_gcp_ip_fix.py`, `test_gcp_disk.py`, `test_gcp_disk_fix.py`, and `test_gcp_logger.py`) drive the registered Cleaners (`IpCleaner`, `DiskCleaner`) through `crc.core.engine.run` against the full oracle case sets in `tests/legacy/gcp_ip.txt` and `tests/legacy/gcp_disk.txt`, preserving all assertions on SDK mutations, call ordering, log lines, stdout, raised errors, and fixed-bug divergences (`BUG-001`, `BUG-005`, `BUG-006`, `BUG-063`, `BUG-064`, `BUG-126`, `BUG-127`, `BUG-358`), with only the allowed `plan()`-before-`apply()` listing-error difference recorded in `ENGINE_QUIRKS`. Locally broke `IpCleaner` (`_RESERVED = "BROKEN"`) and `DiskCleaner` (inverted `disk.users` check) and confirmed `pytest tests/test_gcp_ip_parity.py tests/test_gcp_disk_parity.py` failed with 243 failures before restoring.
- **Check (3) Deleted names & READMEs**: Confirmed `crc/gcp/ip_adapter.py`, `crc/gcp/disk_adapter.py`, `crc.gcp.ip.IP`, and `crc.gcp.disk.Disk` are deleted and unreferenced across `crc/`, `crc.py`, and `tests/`. In Round 1, also trimmed `crc/gcp/spot_vm/README.md` (text only, removing `GCP-4`/`FIX-GCP-SPOT` rung names, `docs/refactor/PLAN.md` and `docs/refactor/reviews/FIX-GCP-SPOT.md` links, and stale CLI-adapter wording), removed the remaining legacy-ratchet/`main` phrasing in `crc/gcp/README.md`, and cleaned stale `Disk` and `rung LOG-GCP` references in `crc/gcp/disk_settings.py` and `crc/gcp/_scan.py` docstrings.
- **Check (4) Locked files**: Confirmed no hash-locked safety test, `tests/legacy/*.txt` oracle, `tests/archtest/**`, `BUGS.md`, `CLEANUP_SAFETY.md`, `AGENTS.md`, `ARCHITECTURE.md`, or build/config file was touched.
- **Check (5) Size limits & Public API sync**: Every file in `crc/gcp/` is well within ARCH-004 limits (max 250 lines/file, max 60 lines/function) and Public API sections in `crc/gcp/README.md` and `crc/gcp/spot_vm/README.md` are in sync (ARCH-002 green).
- **Check (6) Gates & test count**: After `pip install -r requirements.txt -r requirements-dev.txt`, `make check` passed in full:
  - `ruff check .`: passed
  - `mypy`: passed (`Success: no issues found in 371 source files`)
  - `lint-imports`: 5 kept, 0 broken
  - `pytest -q -n auto`: `44415 passed in 51.10s`
  - `ARCH-008`: 44415 tests, floor 44415 (all ARCH-001..011 and SAF-LOCK checks green)

VERDICT: APPROVE

## Round 2 (Opus)

### Round 1 check

- Round 1's fixes (spot_vm README trim, last `main`/ratchet wording in `crc/gcp/README.md`, the stale `Disk` and `rung LOG-GCP` docstrings in `disk_settings.py` and `_scan.py`) are present and correct. Its findings were re-checked independently below; no disagreement.

### Findings

- **(1) Production untouched**: `crc/cli/scan.py` still imports `crc.gcp.vm.VM`; `crc/cli/routes/gcp.py` imports `ip_cleaner`, `disk_cleaner`, `vm_cleaner` and `spot_vm`; the four `@register("gcp", ...)` factories (`ip`, `disk`, `vm`, `spot_instances`) are in place. No locked-test adapter changed (`git diff 302612d..HEAD` touches no aws, azu, oci or `crc/gcp/spot_vm/*.py` code, and no `tests/test_*spot*` or `tests/test_aws_disk*` file).
- **(2) Parity tests on the engine**: `tests/test_gcp_ip_parity.py` and `tests/test_gcp_disk_parity.py` build the cleaner through `Registry` + `build_ip_cleaner` / `build_disk_cleaner` and call `crc.core.engine.run` for every oracle case (cases list unchanged against the base). Fixed-bug divergences carry their IDs (BUG-001, BUG-063, BUG-064 for IP; BUG-005, BUG-006, BUG-126, BUG-127 for disk; BUG-358 log level). The only test functions dropped are the adapter-vs-oracle `test_matches_main` and the adapter-specific `test_cleaner_protocol_matches_main`; the new `test_engine_matches_main` keeps the full cleaner-protocol comparison (mutations and their order, results, stdout, errors, log multiset, exact log and call order where the case has one region or zone), and the new `..._lists_every_region/zone_before_it_deletes` tests assert the plan-then-apply order for every case. The exact call order of multi-region cases is no longer compared against the oracle; that follows from the one allowed ENG change (all listing before any delete), and the order of deletes is still compared exactly. Port of constructor-only cases (`default_state`, class attributes, constructor slack shapes, second `delete()` accumulation) checked: each still asserts the oracle fact and the engine-side equivalent.
- **Mutation check (local, not committed, in a scratch copy)**: re-inverted the IP exception filter (`return not any(...)` in `IpCleaner`, the BUG-001 regression) and skipped the disk exception check in `DiskCleaner._reject`. `pytest tests/test_gcp_ip_parity.py tests/test_gcp_disk_parity.py`: 87 failed (IP: 31 `test_engine_matches_main`, 11 quirk, 5 fixed-quirk, plus default_state, BUG-358 and second-run tests; disk: 16 `test_engine_matches_main`, 8 quirk, 13 BUG-126). The parity tests reach the registered cleaners and are not vacuous.
- **(3) Deleted names**: `crc/gcp/ip_adapter.py`, `crc/gcp/disk_adapter.py`, `crc.gcp.ip.IP` and `crc.gcp.disk.Disk` are gone; grep over `crc/`, `crc.py` and `tests/` finds no reference (only log text and the unrelated `legacy_crc_notify_reference.txt` constants). READMEs: no rung names, refactor-history, legacy-path or adapter wording for deleted classes; no links to `docs/refactor/PLAN.md`, `STATUS.md`, `FILTER_VARIANTS.md`, `LOGGING.md`, reviews or `.cursor/skills/crc-python-refactor` (the remaining `docs/refactor/BUGS.md` links point to a file that still lives there; the review link is `docs/archive/reviews/FIX-GCP-SPOT.md`). The `SpotVM` and `VM` class descriptions describe classes that still exist (locked-test shim and the CLI scan class).
- **(4) Locked files**: no change to `tests/legacy/**`, `tests/archtest/**` (including `safety_lock.json`), the hash-locked safety tests, `BUGS.md`, `CLEANUP_SAFETY.md`, `AGENTS.md`, `ARCHITECTURE.md`, `.cursor/**`, `pyproject.toml`, `Makefile` or `requirements*.txt`.
- **(5) Sizes and API**: largest `crc/gcp` modules are `disk_cleaner.py` (240 lines) and `vm_cleaner.py` (225); ARCH-004 (files, functions) and ARCH-002 (Public API sync for `crc/gcp` and `crc/gcp/spot_vm`) pass in the suite.
- No fixes were needed in this round.

### Gates

After `pip install -r requirements.txt -r requirements-dev.txt`, `make check` (wall time 1m21s):

- `ruff check .`: all checks passed
- `mypy`: no issues found in 371 source files
- `lint-imports`: 5 kept, 0 broken
- `pytest -q -n auto`: 44415 passed in 61.11s (ARCH-001..011 and SAF-LOCK included)
- ARCH-008: 44415 tests, floor 44415 (count unchanged, floor not raised)

### New bugs

None found.

ROUND 2 VERDICT: APPROVE
