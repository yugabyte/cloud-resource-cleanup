## Coding summary

Wave W2, lane W2-AZU: structure-only split of the five Azure files above 250 lines. No behaviour change. `make check` green (ruff, mypy strict, lint-imports 5 kept, pytest, ARCH-001..005, SAF-LOCK). Runtime of `make check`: 2m49s (pytest 163 s).

Tests: `pytest -q` 43010 passed at cea0a8a, 43010 passed after. No test, `tests/legacy/*.txt` or hash-locked file was edited; no test import line needed changing (every old import path still works).

### Files split (before -> after, `wc -l`)

| Old file | Before | After |
|----------|-------:|-------|
| `crc/azu/ip.py` | 265 | package `crc/azu/ip/`: `client.py` 32, `cleaner.py` 144, `adapter.py` (`IP`) 95, `__init__.py` 15 |
| `crc/azu/nic.py` | 342 | package `crc/azu/nic/`: `client.py` 40, `cleaner.py` 216, `adapter.py` (`NIC`) 97, `__init__.py` 17 |
| `crc/azu/disk.py` | 344 | package `crc/azu/disk/`: `client.py` 67, `cleaner.py` 182, `adapter.py` (`Disk`) 109, `__init__.py` 29 |
| `crc/azu/spot_vm/cleaner.py` | 278 | `cleaner.py` 248 + new `config.py` 61 (`SpotVmConfig`, `STOPPED_POWER_STATES`, `TagSpec`, `log_summary`, `old_enough`) |
| `crc/azu/vm/cleaner.py` | 327 | `cleaner.py` 234 + new `settings.py` 75 (`VmSettings`, `Tally`, `log_summary`, `Clock`, `TagSpec`), `helpers.py` 39 (`tally_of`, `power_state`, `log_processing_error`, constants), `selection.py` 37 (`skip_reason`, `is_old`) |

Import paths: `crc.azu.ip|nic|disk` are packages that re-export every old public name (`IP`, `NIC`, `Disk`, cleaners, factories, Protocols, `DiskSettings`, `DELETE_ATTEMPTS`, ...). `crc.azu.spot_vm.cleaner` and `crc.azu.vm.cleaner` re-export the moved names through `__all__`, so `crc.azu.spot_vm.__init__`, `crc.azu.vm.adapter` and the tests import unchanged. Existing mock targets (`crc.azu._base.*`, `crc.azu.vm_delete_helpers.time.sleep`, `crc.azu.spot_vm.Base|begin_delete_vm_and_wait|delete_vm_primary_nic`) are unmoved, so the patches still apply. The ip/nic/disk adapters (`IP`, `NIC`, `Disk`) are each in their own `adapter.py`, deletable in one move.

### Helpers extracted (`crc/azu/_common.py`, 35 lines)

- `kept(reason)`: the `_kept` static method that was copied in `AzureIpCleaner` and `AzureNicCleaner`.
- `log_would_delete(resource_group, candidates)`: the dry-run ACTION loop copied in the three `plan()` methods.
- `log_delete_summary(noun, deleted, dry_run=, number_word=)`: the three `_log_summary` functions (ip, nic, disk), same text per kind (the NIC one keeps its capital "Number").

READMEs updated: `crc/azu/README.md`, `crc/azu/vm/README.md`, `crc/azu/spot_vm/README.md`; new short READMEs for `crc/azu/ip`, `nic`, `disk`.

### Owner action

- Cross-cloud candidates (not touched, other clouds and `crc/core` are out of lane): the `TagSpec` and `Clock` aliases and the `DeletePoller` Protocol are re-declared in many cloud modules; the "plan loop + dry-run ACTION lines + closing delete summary" pattern repeats in every cloud's ip/nic/disk cleaners; a `kept(reason)` helper (count a skip and return `(None, reason)`) could live in `crc/core` next to the logger.
- After the wave merges, the `crc/azu/*/adapter.py` classes (`IP`, `NIC`, `Disk`, `VM`, `SpotVM`) are the single-move deletion targets once `crc/cli` stops using them.
- `crc/azu/spot_vm/__init__.py` (239 lines, not in this lane) still holds the `SpotVM` adapter; it is under 250 but should move to `adapter.py` for consistency.

## New bugs

No new behavioural bugs. Quirks seen, recorded as-is and not fixed:

- `crc/azu/nic/cleaner.py:146`: `list_nics` issues the list call twice and discards the first (documented BUG-111 behaviour kept on purpose).
- `crc/azu/disk/adapter.py:30`: `Disk.default_disk_state` duplicates `DEFAULT_DISK_STATES` (`crc/azu/disk/cleaner.py`); two sources for one default.
- `crc/azu/spot_vm/config.py:35`: `log_summary` logs "No Azure Spot VMs ... to delete." and then still logs the three empty "List of ..." lines.

## Round 1 (Argon)

Scope reviewed: `git diff cea0a8a..HEAD` (`crc/azu/_common.py`, `crc/azu/ip/**`, `crc/azu/nic/**`, `crc/azu/disk/**`, `crc/azu/spot_vm/{cleaner,config,README}`, `crc/azu/vm/{cleaner,settings,helpers,selection,README}`, `crc/azu/README.md`, `docs/refactor/reviews/W2-AZU.md`).

### Verification

1. **No behaviour change (`git diff --color-moved=zebra cea0a8a..HEAD`)**:
   - `crc/azu/ip.py` (265) -> `crc/azu/ip/{__init__.py (15), client.py (32), cleaner.py (144), adapter.py (95)}`: `PublicIpOperations`, `IpNetworkClient`, `ClientFactory`, `TagSpec` moved verbatim to `client.py`; `_OPTIONAL_IN_USE_FIELDS`, `_in_use`, `AzureIpCleaner`, `build_ip_cleaner` moved to `cleaner.py`; `IP(Service)` moved verbatim to `adapter.py`.
   - `crc/azu/nic.py` (342) -> `crc/azu/nic/{__init__.py (17), client.py (40), cleaner.py (216), adapter.py (97)}`: `DeletePoller`, `NicOperations`, `NicNetworkClient`, `ClientFactory` moved verbatim to `client.py`; `DELETE_ATTEMPTS`, `_compile`, `log_none_to_delete`, `AzureNicCleaner`, `build_nic_cleaner` moved to `cleaner.py`; `NIC(Service)` moved verbatim to `adapter.py`.
   - `crc/azu/disk.py` (344) -> `crc/azu/disk/{__init__.py (29), client.py (67), cleaner.py (182), adapter.py (109)}`: `TagSpec`, `AgeSpec`, `SdkDisk`, `DeletePoller`, `DiskOperations`, `DiskComputeClient`, `ClientFactory`, `Clock` moved verbatim to `client.py`; `DEFAULT_DISK_STATES`, `DiskSettings`, `AzureDiskCleaner`, `build_disk_cleaner` moved to `cleaner.py`; `Disk(Service)` moved verbatim to `adapter.py`.
   - `crc/azu/_common.py` (35): deduplicates `kept(reason)` (formerly `_kept` in `AzureIpCleaner` and `AzureNicCleaner`), `log_would_delete(resource_group, candidates)` (the dry-run `_logger.acted("would_delete", ...)` loop in `ip`, `nic`, and `disk` `plan()`), and `log_delete_summary(noun, deleted, *, dry_run, number_word="number")` (produces the exact closing `logging.warning` lines of `ip`, `disk`, and `nic` with `number_word="Number"`).
   - `crc/azu/spot_vm/cleaner.py` (278 -> 248) + `crc/azu/spot_vm/config.py` (61): `STOPPED_POWER_STATES`, `TagSpec`, `SpotVmConfig`, `log_summary`, and `old_enough` (formerly `AzureSpotVmCleaner._old_enough`) moved verbatim to `config.py` and re-exported in `cleaner.py`.
   - `crc/azu/vm/cleaner.py` (327 -> 234) + `settings.py` (75) + `helpers.py` (39) + `selection.py` (37): `DEFAULT_INSTANCE_STATES`, `TagSpec`, `Clock`, `VmSettings`, `Tally`, `_log_deleted`, `_log_stopped`, `log_summary` moved verbatim to `settings.py`; constants, `tally_of`, `power_state`, `log_processing_error` moved verbatim to `helpers.py`; `skip_reason` and `is_old` moved verbatim to `selection.py`; public symbols re-exported via `crc/azu/vm/cleaner.py` `__all__`.
   - Every condition, default, SDK call order, exception handling path, and log message is unchanged.
2. **Import paths and mock/monkeypatch targets**:
   - Every import from `crc.azu.ip`, `crc.azu.nic`, `crc.azu.disk`, `crc.azu.spot_vm`, `crc.azu.spot_vm.cleaner`, `crc.azu.vm`, and `crc.azu.vm.cleaner` across `crc/` (`crc/cli/resource_ops.py`, `crc/cli/vm_ops.py`) and `tests/` resolves unchanged; importing `crc.azu.ip|nic|disk` imports `cleaner.py` so `@register("azure", ...)` executes at import time as before.
   - All string `monkeypatch.setattr` and `mock.patch` targets in `tests/` (`crc.azu._base.{ClientSecretCredential,ComputeManagementClient,NetworkManagementClient}`, `crc.azu.vm_delete_helpers.time.sleep`, `crc.azu.spot_vm.{Base,begin_delete_vm_and_wait,delete_vm_primary_nic}`) still hit the real code.
3. **Tests and safety locks**:
   - Zero test files edited; all five hash-locked safety tests (`tests/archtest/safety_lock.json`) and `tests/legacy/*.txt` are untouched.
4. **Line counts (`wc -l`) and split seams**:
   - Every owned `.py` file is at 250 lines or fewer (max: `crc/azu/spot_vm/cleaner.py` at 248 lines, `crc/azu/vm/cleaner.py` at 234 lines, `crc/azu/nic/cleaner.py` at 216 lines, `crc/azu/disk/cleaner.py` at 182 lines, `crc/azu/ip/cleaner.py` at 144 lines).
   - Splits follow clean architectural seams (`client.py` Protocols, `cleaner.py` Cleaner + `@register` factory, `adapter.py` legacy `Service` subclass for one-move deletion later, `settings.py`/`config.py`/`helpers.py`/`selection.py`), and `crc/azu/_common.py` holds helpers shared 2-3 times across `ip`, `nic`, and `disk`.
5. **READMEs and ARCH-002**:
   - `crc/azu/README.md`, `crc/azu/ip/README.md` (31 lines), `crc/azu/nic/README.md` (34 lines), `crc/azu/disk/README.md` (35 lines), `crc/azu/spot_vm/README.md` (37 lines), and `crc/azu/vm/README.md` (58 lines) are concise and pass `ARCH-002` Public API symbol sync.

### Findings

- **BLOCKING (fixed)**: None.
- **NIT (fixed)**: `crc/azu/README.md:7,53` still referred to `ip.py`, `nic.py`, `disk.py` in prose under `## Purpose` and `## Extension Rules`; updated to `ip/`, `nic/`, `disk/`.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed! |
| `mypy` (`crc`, `tests`) | Success: no issues found in 213 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` (`ARCH-001..005`, `SAF-LOCK`, full suite) | 43010 passed in 197.33s |

Runtime of `make check`: 3m38s (pytest 197.33s).
Test count: 43010 passed vs 43010 at base `cea0a8a` (+0, unchanged).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff cea0a8a..HEAD`. Round 1 (Argon) findings re-checked: its README nit (`ip.py`/`nic.py`/`disk.py` prose in `crc/azu/README.md`) is fixed; its import-path, patch-target and line-count claims hold.

### Independent checks

1. **No behaviour change.** Concatenated the new `client.py + cleaner.py + adapter.py + __init__.py` of ip, nic and disk and diffed them against the base `ip.py`, `nic.py`, `disk.py`. The only non-import differences are the three `_common.py` substitutions: `_kept` -> `kept` (same `_logger.skip` + `(None, reason)`), the dry-run `acted("would_delete", ...)` loop -> `log_would_delete`, and `_log_summary` -> `log_delete_summary(noun, ..., number_word=)`. The log text is identical for IPs, Disks ("number ...") and NICs ("Number ..."); the `if not dry_run` / `if dry_run` branch swap in the IP variant gives the same output. `spot_vm/cleaner.py` -> `config.py` and `vm/cleaner.py` -> `settings.py` / `helpers.py` / `selection.py` are verbatim moves (`--color-moved`); `_old_enough`, `_skip_reason` and `_is_old` became module functions that take the settings as their first argument, with the same condition order, log text and exception handling. SDK call order is unchanged (including the double `network_interfaces.list` of BUG-111).
2. **Imports and patch targets.** `crc.py`, `crc/cli/{resource_ops,vm_ops}.py` and every `tests/` import from `crc.azu.{ip,nic,disk,spot_vm,vm}` resolve. String targets in tests: `crc.azu._base.*`, `crc.azu.vm_delete_helpers.time.sleep`, `crc.azu.spot_vm.{Base,begin_delete_vm_and_wait,delete_vm_primary_nic}`; all are in modules this lane did not change, so they still patch the code that runs. The three `datetime` overrides in `tests/test_azure_{disk,vm,spot_instances}_parity.py` write only into the exec'd legacy oracle module, so the split does not affect them. No test touches the private names that moved (`_old_enough`, `_kept`, `_power_state`, ...), and there are no star imports, so the new `__all__` lists in `spot_vm/cleaner.py` and `vm/cleaner.py` drop nothing that is used.
3. **Tests untouched.** `git diff --name-only cea0a8a..HEAD` outside `crc/azu/` is only this review file: no test, `tests/legacy/*.txt`, `tests/archtest/**` or other no-touch file changed.
4. **Size and seams.** Every owned file is 248 lines or fewer (largest: `spot_vm/cleaner.py`, 248). The split follows real seams: SDK Protocols in `client.py`, Cleaner in `cleaner.py`, legacy adapter in `adapter.py` (one-move deletion), and settings/selection/helpers for vm. The `_common.py` helpers are each used 3 to 6 times.
5. **READMEs.** ARCH-002 passes; the new subpackage READMEs are 31 to 35 lines.

### Findings

- BLOCKING: none.
- NIT (fixed): `crc/azu/spot_vm/config.py:3`: the module docstring said "Settings and summary logging" but the module also holds `old_enough`; it now says "Settings, age check and summary logging" (docstring only).
- NIT (not fixed, owner action): the type aliases `TagSpec` (`ip/client.py`, `disk/client.py`, `spot_vm/config.py`, `vm/settings.py`) and `Clock` (`disk/client.py`, `vm/settings.py`), and the `DeletePoller` Protocol (`nic/client.py`, `disk/client.py`, `vm_delete_helpers.py`), are still declared more than once inside `crc/azu`. They are declarations, not logic, so they are left where they are; they can move to `crc/azu/_common.py` (or `crc/core`) together with the cross-cloud candidates listed above.
- The "New bugs" line references (`nic/cleaner.py:146`, `disk/adapter.py:30`, `spot_vm/config.py:35`) are correct.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`: ruff All checks passed; mypy strict Success (265 source files); lint-imports 5 kept, 0 broken; pytest (ARCH-001..005, SAF-LOCK, full suite) 43010 passed in 162.10s.

Runtime of `make check`: 2m59s. Test count: 43010 collected at `cea0a8a` and 43010 at HEAD (+0).

ROUND 2 VERDICT: APPROVE
