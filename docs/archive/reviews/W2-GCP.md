# W2-GCP review

## Coding summary

Wave W2 lane W2-GCP: structure-only split of `crc/gcp/disk.py`, `ip.py` and `vm.py`. No behaviour change.

**Files split (lines before -> after, `wc -l`).** Each old module is now a short facade that re-exports
every name it exported before (same import paths for tests, `crc.py` and `crc/cli`).

| Old module | Before | After | New modules (lines) |
|------------|-------:|------:|---------------------|
| `crc/gcp/disk.py` | 397 | 20 | `disk_cleaner.py` (235: `DiskCleaner`, `build_disk_cleaner`), `disk_adapter.py` (108: `Disk`), `disk_settings.py` (76: `DiskSettings`, record/summary helpers) |
| `crc/gcp/ip.py` | 298 | 19 | `ip_cleaner.py` (174: `IpCleaner`, `build_ip_cleaner`), `ip_adapter.py` (70: `IP`), `ip_client.py` (70: protocols, `ComputeIpClient`) |
| `crc/gcp/vm.py` | 388 | 19 | `vm_cleaner.py` (225: `VmCleaner`, `build_vm_cleaner`), `vm_adapter.py` (118: `VM`), `vm_settings.py` (46: `VmSettings`, summary log) |

The three adapters (`Disk`, `IP`, `VM`) each sit in their own module so they can be deleted in one move later.
Every file I own is at most 250 lines (largest: `disk_cleaner.py`, 235).

**Helpers extracted.** `crc/gcp/_common.py` (45): `is_old_enough`, `TIME_FORMAT`, `Clock`. The age gate
(parse timestamp, `clock()` converted to its tz, debug-log labels, custom age label, `Updating age for
<kind>: <name>` info log, `effective_age`, `is_old`) was written twice, in `DiskCleaner._older_than_detach_age`
and `VmCleaner._is_old`; call order and log text are unchanged. `_TIME_FORMAT` and `Clock` were duplicated in
`disk.py` and `vm.py`; now one definition.

**Patch targets.** No test patches a name in `crc.gcp.disk`, `ip` or `vm` by string; the string targets
(`google.cloud.compute_v1.*`, `googleapiclient.discovery.build`, `crc.gcp.spot_vm.operations.time*`,
`crc.service.init_logging`) still hit the same code. Registry registration (`@register("gcp", ...)`) runs when
`crc.gcp.<x>` is imported, as before. No test file and no hash-locked test was edited.

**Tests.** `pytest -q`: 43010 passed before (base `cea0a8a`), 43010 after. `make check` runtime: about
2m56s before and 2m45s after (ruff, mypy, lint-imports, pytest). README `crc/gcp/README.md` updated (module
list, `is_old_enough` in Public API).

## Owner action

- Cross-cloud helper candidates (not done, outside my lane): the "fail open custom age label, then
  `effective_age`, then `is_old`" sequence is repeated in the same shape in `crc/oci`, `crc/azu` and
  `crc/gcp/spot_vm/instances.py`/`disks.py`; a shared helper in `crc/core/age.py` or `policy.py` could replace
  `crc/gcp/_common.py:is_old_enough`. The `ZoneScan`/`log_action` style helpers could also be shared across clouds.
- `crc/gcp/disk_client.py` and `crc/gcp/vm_client.py` both define `_SERVICE_NAME` and `_SERVICE_VERSION`
  (same values); I did not touch them (not my files).

## New bugs

None found.

## Round 1 (Argon)

### Findings

1. **No behaviour change**: Verified hunk by hunk against `cea0a8a` (`git diff --color-moved=zebra cea0a8a..HEAD -- crc/gcp/`). All conditions, defaults, SDK call ordering, exception handling, and log messages in `disk`, `ip`, and `vm` are preserved verbatim. The shared helper `is_old_enough` in `crc/gcp/_common.py` preserves the exact evaluation order (timestamp parse, `clock().astimezone(...)`, `logging.debug(item.labels)`, `core_age.get_retention_age`, `Updating age for <kind>: <name>` info log only when `retention_age` is truthy, `policy.effective_age`, `core_age.is_old`).
2. **Import paths & patch targets**: `crc.gcp.disk`, `crc.gcp.ip`, and `crc.gcp.vm` re-export all previously exported symbols (`__all__`) and trigger `@register("gcp", ...)` on import as before. All `monkeypatch` / `mock.patch` string targets in `tests/` (`googleapiclient.discovery.build`, `google.cloud.compute_v1.*`, `crc.gcp.spot_vm.operations.time.*`, `crc.service.init_logging`) still resolve to the live code under test.
3. **Tests & safety lock**: Zero test files, hash-locked tests (`tests/archtest/safety_lock.json`), or legacy oracle files (`tests/legacy/*.txt`) were modified.
4. **File sizes & module seams**: Every owned file in `crc/gcp/` is well under the 250-line target (`wc -l` max is `crc/gcp/disk_cleaner.py` at 235 lines, `crc/gcp/vm_cleaner.py` at 225 lines, `crc/gcp/ip_cleaner.py` at 174 lines). Splits follow clean seams (`*_cleaner.py`, `*_adapter.py`, `*_settings.py`, `ip_client.py`, and shared `_common.py`), isolating legacy adapters (`Disk`, `IP`, `VM`) in their own modules for one-step removal later.
5. **README**: `crc/gcp/README.md` (82 lines) is concise and its `## Public API` symbol list is in sync with ARCH-002.

### Gates & Verification

- `ruff check .`: passed
- `mypy`: passed (261 source files)
- `lint-imports`: passed (5 contracts kept, 0 broken)
- `ARCH-001..005` & `SAF-LOCK`: passed
- `pytest -q`: **43010 passed** (matches base `cea0a8a` count of 43010)
- `make check` runtime: **2m56.5s** (`pytest` 158.94s)

VERDICT: APPROVE


## Round 2 (Opus)

Round 1 made no fixes (it approved as is). I re-did every check independently against `cea0a8a`.

### Findings

1. **No behaviour change.** I concatenated the new modules of each old file (`ip_client` + `ip_cleaner` + `ip_adapter`,
   `vm_settings` + `vm_cleaner` + `vm_adapter`, `disk_settings` + `disk_cleaner` + `disk_adapter`) and ran
   `diff -w` against the base file. IP is a pure move: only imports and docstrings differ. VM and disk differ only in
   the age gate (`VmCleaner._is_old`, `DiskCleaner._older_than_detach_age`), which now calls
   `_common.is_old_enough` with the same sequence: `strptime(TIME_FORMAT)`, then `clock().astimezone(tz)`,
   `logging.debug(labels)`, `get_retention_age`, the `Updating age for instance|disk: <name>` info log (only when set),
   `effective_age` and `is_old`. `time_format = TIME_FORMAT` has the same value. `_SERVICE_NAME`/`_SERVICE_VERSION`
   in `Disk` now come from `disk_client.py` (`"compute"`/`"v1"`, same values as the old local copies).
2. **Import paths and patch targets.** `crc.py`, `crc/cli/resource_ops.py`, `crc/cli/vm_ops.py` and all tests
   import only names that the facades re-export. Base private names such as `_TIME_FORMAT`, `_record` and
   `_log_summary` are no longer on the facades, but nothing imports or patches them (I checked string and attribute
   targets). The `setattr(module, "datetime", FrozenDatetime)` calls in `tests/test_gcp_{vm,disk}_parity.py` target the
   exec'd legacy oracle module, not ours; our code takes its clock by injection, as before. The `time` patches hit
   `crc.gcp.spot_vm.operations` and `crc.gcp._scan`, which are unchanged. `@register("gcp", ...)` still runs on
   `import crc.gcp.<x>`.
3. **Tests untouched.** `git diff --stat cea0a8a..HEAD -- tests` is empty, so no hash-locked test, oracle or archtest
   was edited. No other protected files changed.
4. **Sizes and seams.** All owned files are at most 250 lines (largest: `disk_cleaner.py` at 235, `vm_cleaner.py` at
   225). Each split is cleaner / adapter / settings or client, with adapters isolated, and `_common.py` is used by both
   the disk and VM code.
5. **README.** `crc/gcp/README.md` (82 lines) lists the new modules, and `is_old_enough` is in the Public API (ARCH-002
   green).
6. **Note (not blocking).** `LOG_FORMATTER` (`crc/utils.py:8`) prints `%(filename)s:%(lineno)d`, so moved log lines
   now report the new file and line (for example `_common.py` for the `Updating age` line). Any file split has this
   effect, and the message text is unchanged.

No fixes were needed in round 2.

### Gates

- `pip install -r requirements.txt -r requirements-dev.txt`, then `make check`: ruff all passed; mypy no issues (261
  files); lint-imports 5 kept, 0 broken; ARCH-001..005 and SAF-LOCK green (in pytest).
- Tests: **43010 passed**, which equals the base `cea0a8a` count (43010 collected, re-checked in a worktree).
- `make check` runtime: **2m57s** wall time (pytest 160.12s).

ROUND 2 VERDICT: APPROVE
