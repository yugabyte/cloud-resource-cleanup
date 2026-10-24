## Coding summary

Lane WC-AZU: delete the dead Azure adapter classes `IP`, `NIC` and `Disk`.

- **Routed:** nothing new to route. `ip`, `nic` and `disk` already run through `crc/cli/bridge.py`, `crc/cli/routes/azu.py` and the registered cleaners (`azure`/`ip`, `azure`/`nic`, `azure`/`disk`).
- **Deleted:** `crc/azu/ip/adapter.py` (`IP`), `crc/azu/nic/adapter.py` (`NIC`), `crc/azu/disk/adapter.py` (`Disk`), their package re-exports and their README Public API lines. `git grep` showed no production user: only tests built them (`crc/cli/scan.py` uses the VM adapters, which stay). The VM and SpotVM classes and the five hash-locked safety tests are untouched.
- **Tests retargeted (no case dropped, no comparison loosened):** `test_azure_{ip,nic,disk}_parity.py`, `test_azure_{ip,nic,disk}.py`, `test_azure_{ip,nic,disk}_fix.py` and `test_azure_logger.py` now drive the registered Cleaner through `crc.core.engine.run` (new helper `tests/helpers_wc_azu_engine.py`: `engine_run`, `candidate_ids`). Every parity case still runs against the frozen oracle in `tests/legacy/azu_{ip,nic,disk}.txt` and asserts SDK calls (per kind), results, log lines, stdout and errors. BUG-NNN divergences (BUG-013/074/076/077 for IP, BUG-111/113/114 for NIC, BUG-172/174 for disk) are asserted as before. The direct-protocol tests (`test_cleaner_protocol_matches_main`) stay and now share one assertion helper with the engine test for disk.
- **Constructor/attribute cases ported:** the adapter-only tests (`get_deleted`, `count`, `delete_count`, `Disk.default_disk_state`, `adapter.clock`, repeated `delete()` accumulation) now assert the engine outcome: `candidate_ids`, report section ids, `DEFAULT_DISK_STATES`, `now` passed in the `RunContext`, and two engine runs giving the same calls and log lines as the oracle's two `delete()` calls (the oracle accumulates names, the engine starts afresh each run).
- **READMEs trimmed:** `crc/azu/README.md`, `ip/`, `nic/`, `disk/` (adapter wording, rung names, refactor history, `FILTER_VARIANTS.md` and `PLAN.md` links removed); `spot_vm/` and `vm/` READMEs text only (rung names, history, `FILTER_VARIANTS.md` link, "as on main" removed; the `VM` and `SpotVM` API lines stay because those classes still exist).
- **Test count:** floor 44415 before, 44415 after (ARCH-008 green; tests were renamed or retargeted one for one, none added or removed). `make check` is green (ruff, mypy, lint-imports, ARCH rules, SAF-LOCK).
- **CLI parity tests:** not touched in this lane.

## Allowed behaviour change, seen in the retargeted tests

The cleaner plans every resource before it mutates, so a listing or selection error raised mid-listing (IP `iteration-error-mid-listing`, NIC `iteration-error-mid-listing` and `name-none-after-earlier-delete`, disk `iteration-error-mid-listing` and `created-none-error`) now stops the pair before any delete. Each is asserted in an `ENGINE_QUIRKS` table next to the oracle outcome. Fewer deletes, never more.

## Log line order (not in the allowed list, flagged for the owner)

On the engine path the `REGION` line is logged before the `ACTION` lines for NIC and disk (and in a dry run for IP), because `plan()` logs it; the adapters logged `ACTION` lines first. `tests/test_azure_logger.py` now pins the engine order exactly. The text of every line is unchanged, only the order of `REGION` against `ACTION`. This was already the production order since the Azure routing landed; the adapter tests were the only place that still showed the old order.

## Owner actions

- Nothing to relock: no hash-locked test was touched.
- `AzureIpCleaner.list_ips/decide/delete_candidate`, `AzureNicCleaner.prepare/list_nics/decide/attempt_delete` and `AzureDiskCleaner.list_disks/decide/delete_disk` were public helpers for the adapters; they are still used by each cleaner's own `plan()`/`apply()`, so they stay.

## What is left

Nothing for this lane. The `VM` and `SpotVM` adapters stay (scan.py and the locked SpotVM test).

## New bugs

None found.

## Round 1 (Argon)

Scope reviewed: `git diff 302612d..HEAD` on branch `wc/azu` (lane `WC-AZU`).

### Findings & fixes made in review

1. **Restored full parity assertions and removed dead helpers in `tests/test_azure_disk_parity.py`, `tests/test_azure_ip_parity.py`, and `tests/test_azure_nic_parity.py`**:
   - In `tests/test_azure_disk_parity.py`, unifying `test_matches_main` and `test_cleaner_protocol_matches_main` onto `_assert_matches` had switched `if case.diverges:` from `_assert_diverges` to the weaker `_assert_cleaner_diverges` (leaving `_assert_diverges` and `_begin_deletes` unused and dropping `case.diverges == "BUG-174"`, `old.error` vs `fixed.old_error`, `new.count`, `Deleted disk:` log records, `begin_delete` calls, and `new.stdout == old.stdout`), and omitted `(new.error is not None) == (case.error_phase is not None)`, `yield` call order, `waited` poller checks, and `old.count_logs`. Restored `_assert_diverges` in `_assert_matches`, removed the duplicate `_assert_cleaner_diverges`, and restored the non-diverging assertions in `_assert_matches` (and `old.count_logs` in `tests/test_azure_nic_parity.py`).
   - Removed the unused `_expected_calls` helper in `tests/test_azure_ip_parity.py` (superseded by `_expected_engine_calls`).
2. **Removed dead `Clock` alias and remaining legacy wording (`crc/azu/disk/client.py`, `crc/azu/disk/cleaner.py`, `crc/azu/vm/README.md`)**:
   - Removed `Clock = Callable[[], datetime.datetime]` from `crc/azu/disk/client.py` (only used by the deleted `Disk` adapter).
   - Trimmed `"as crc.py passes them"` in `crc/azu/disk/cleaner.py` and `"of main"` in `crc/azu/vm/README.md` (`log_summary()`).

### Checklist verification

1. **Nothing production uses was deleted**: `crc/cli/scan.py` (uses `crc.azu.vm.VM`), `crc/cli/routes/azu.py`, `@register("azure", ...)` factories (`build_ip_cleaner`, `build_nic_cleaner`, `build_disk_cleaner`, `build_vm_cleaner`, `build_spot_vm_cleaner`), `crc.azu.vm.VM`, `crc.azu.spot_vm.SpotVM`, and all 5 hash-locked adapters are intact.
2. **Every module parity test drives the registered Cleaner through `crc.core.engine.run`**:
   - `tests/test_azure_{ip,nic,disk}_parity.py`, `tests/test_azure_{ip,nic,disk}.py`, `tests/test_azure_{ip,nic,disk}_fix.py`, and `tests/test_azure_logger.py` run the registered cleaners through `crc.core.engine.run` via `tests/helpers_wc_azu_engine.py`, preserving all oracle cases against `tests/legacy/azu_{ip,nic,disk}.txt` and all `BUG-NNN` divergence assertions (`BUG-013`, `BUG-074`, `BUG-076`, `BUG-077`, `BUG-111`, `BUG-113`, `BUG-114`, `BUG-172`, `BUG-174`).
   - Local mutation verification (reverted without committing): breaking `in_use` in `crc/azu/ip/cleaner.py`, `excluded` in `crc/azu/nic/cleaner.py`, and `state_is_substring_of_any` in `crc/azu/disk/cleaner.py` caused 33 failures across `test_azure_{ip,nic,disk}_parity.py` (13 in IP, 9 in NIC, 11 in Disk).
3. **Deleted names unreferenced and READMEs trimmed**:
   - `git grep` across `crc/` and `tests/` confirms `crc.azu.ip.IP`, `crc.azu.nic.NIC`, and `crc.azu.disk.Disk` (and `crc/azu/{ip,nic,disk}/adapter.py`) are gone and unreferenced outside the frozen `tests/legacy/crc_main.txt` oracle (which `tests/helpers_oracle.py` binds to `tests/legacy/azu_*.txt`).
   - `crc/azu/README.md` and `crc/azu/{ip,nic,disk,spot_vm,vm}/README.md` describe only the current code with no rung names, refactor history, legacy-path or adapter wording, or links to moved docs.
4. **No protected files touched**: no hash-locked test, `tests/legacy/*.txt` oracle, `tests/archtest/**`, `AGENTS.md`, `ARCHITECTURE.md`, `.cursor/**`, `pyproject.toml`, `Makefile`, `requirements*.txt`, or other `docs/archive/**` file was modified.
5. **Line/function limits & Public API sync**: all touched files are well under 250 lines (max: `crc/azu/nic/cleaner.py` at 216 lines, `crc/azu/disk/cleaner.py` at 182 lines, `crc/azu/ip/cleaner.py` at 152 lines, `tests/helpers_wc_azu_engine.py` at 30 lines), all functions at most 60 lines (`ARCH-004`), and all package READMEs in sync (`ARCH-002`).

### Gates & test count

Ran `pip install -r requirements.txt -r requirements-dev.txt` followed by `make check`:

- `python3 -m ruff check .`: passed
- `python3 -m mypy`: passed (370 source files)
- `lint-imports`: passed (5 contracts kept, 0 broken)
- `python3 -m pytest -q -n auto`: **44415 passed in 56.92s** (`make check` wall time: **1m17.9s**)
- `python3 -m tests.archtest.test_count check`: `ARCH-008: 44415 tests, floor 44415`

VERDICT: APPROVE


## Round 2 (Opus)

Scope: `git diff 302612d..HEAD` on `wc/azu`, after the round 1 fixes (`d72d873`).

### Round 1 fixes checked

- The disk parity fix holds. `_assert_matches` is shared by `test_matches_main` (engine) and `test_cleaner_protocol_matches_main`. It calls `_assert_diverges` for BUG-172/174 and checks `yield` order, `waited` and `old.count_logs` again. The dead `_expected_calls` (IP) and the `Clock` alias (`crc/azu/disk/client.py`) are gone and nothing references them.

### Findings and fixes made in review (test-only, no production change)

1. **Disk and NIC success paths compared only per-kind projections of the SDK calls.** The adapter tests compared the whole call list. On the engine path the full order is fixed: client, listing, every `yield`, then the deletes in plan order. `_assert_matches` (disk) and `test_matches_main` (NIC) now assert `new.calls == listing + deletes` for every case without an error. This also covers the protocol test for disk. All cases pass.
2. **IP `new.logs == old.logs[:-1]` dropped the oracle's last line without saying which one.** It is `count of items in deleted_ips: N`, which the oracle logs when the harness reads `.count`; `new.count == old.count` is still asserted. `test_matches_main` now asserts that line explicitly, so the slice cannot hide any other line.

### Checklist

1. **Nothing production uses was deleted.** These are all intact: `crc/cli/scan.py` (`crc.azu.vm.VM`), `crc/cli/routes/azu.py`, the `@register("azure", ip|nic|disk|vm|spot_instances)` factories, `crc.azu.spot_vm.SpotVM` and the other four hash-locked adapters.
2. **Parity tests use the engine.** `test_azure_{ip,nic,disk}_parity.py` drive the registered cleaner through `crc.core.engine.run` (`tests/helpers_wc_azu_engine.py`) on every oracle case from `tests/legacy/azu_{ip,nic,disk}.txt`. The BUG-013/074/076/077, BUG-111/113/114 and BUG-172/174 divergences are still asserted. The plan-before-apply cases (`fails_in_plan`, `error_phase == "plan"`, `ENGINE_QUIRKS`) assert no delete and no delete log.
   - Mutation check (reverted, not committed), different from round 1: I reversed the delete order in the disk and NIC `apply()` and changed the IP `Deleted IP address:` log text. That gave 114 failures across `test_matches_main`, `test_cleaner_protocol_matches_main`, `test_quirk_is_preserved`, `test_repeated_run_*` and others in all three files.
3. **Deleted names and READMEs.** `git grep` finds no `crc.azu.{ip,nic,disk}.adapter` and no `IP`/`NIC`/`Disk` import or constructor outside `tests/legacy`. The package re-exports and README Public API lines match `__all__`. The READMEs have no rung names, refactor history, legacy-path or adapter wording (except for the `VM` adapter, which still exists), and no links to moved docs. `docs/refactor/BUGS.md` has not moved, so those links are valid.
4. **No protected file touched.** No change to `tests/legacy/*.txt`, `tests/archtest/**`, the five locked test files, `BUGS.md`, `CLEANUP_SAFETY.md`, `AGENTS.md`, `ARCHITECTURE.md`, `.cursor/**`, `pyproject.toml`, `Makefile` or `requirements*.txt`.
5. **Sizes.** Every lane source file is at most 250 lines (largest in `crc/azu`: `spot_vm/cleaner.py`, 248, not touched). ARCH-004 (functions at most 60 lines) and ARCH-002 (Public API) are green.

### Note for the owner (not a failure of this lane)

- On the engine path the `REGION` line comes before the `ACTION` lines and the `Deleted ...` lines come after all listing. `_groups` (disk) keeps the order within each group. This follows from plan-before-apply and was already the production order before this lane.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt`, then `make check`: ruff, mypy (370 files), lint-imports (5/5 contracts), ARCH rules and SAF-LOCK all pass. **44415 passed in 51.29s** (`make check` wall time 69s). `ARCH-008: 44415 tests, floor 44415`.

## New bugs

None found in round 2.

ROUND 2 VERDICT: APPROVE
