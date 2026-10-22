## Coding summary

Rung LOG-GCP (GCP lane of the LOG family, contract in `docs/refactor/LOGGING.md`). Log text and
levels only: no selection, SDK call, call order, delete/stop, Slack, Influx, CLI flag or exit code
changed.

Bug IDs handled: BUG-358 (GCP part: disk/VM label dump, IP address dump and the per-disk "Skipping
leftover Spot disk" line are DEBUG, text unchanged), BUG-361 (GCP part: one `REGION` line per zone
or region walked, for vm, spot_vm, disk and ip), BUG-362 (GCP part: swallowed per-resource errors
call `runlog.current().fail()`; the inline error lines stay). `ACTION` lines and skip reasons
(BUG-360 plumbing) are wired for all four cleaners. Rows in `BUGS.md` are not edited (other lanes
do; GCP part is done, the rows stay open until every lane is merged).

Files changed:

- `crc/gcp/_scan.py` (new): `ZoneScan` (scanned/selected counters, `finish()` logs REGION through
  `runlog.current()`; `time.monotonic` for the seconds) and `log_action()`.
- `crc/gcp/vm.py`, `crc/gcp/disk.py`: `plan_zone` counts scanned/selected and logs REGION when the
  lazy listing is exhausted; the filter chain moved into `_reject()` / `_exception_reason()` (same
  order, same short circuits) returning a skip reason that is counted (`state`, `exception_tag`,
  `exception_regex`, `filter_mismatch`, `attached`, `never_detached`, `too_young`); `apply_*` log
  ACTION after a successful call (`would_*` in a dry run) and call `fail()` on a swallowed error;
  a dry-run `plan()` logs the would-be ACTION lines; label dump `logging.debug`. Both files are at
  exactly 400 lines (ARCH-004), so a few docstrings were shortened.
- `crc/gcp/ip.py`: address dump DEBUG, REGION per region, skip reasons (`state`, `filter_mismatch`,
  `exception_regex`), ACTION in `apply_region` (and in a dry-run `plan()`), a delete error is
  recorded with `fail()` and re-raised unchanged (the IP cleaner never swallowed errors).
- `crc/gcp/spot_vm/cleaner.py`, `instances.py`, `disks.py`, `adapter.py`: new `SpotVMCleaner.scan_zone()`
  (used by `plan()` and the adapter, same call order) logs REGION; `list_zone` failure calls
  `region_skipped()` and logs no REGION; new `instances.reject_reason()` (`is_selected` is
  `reject_reason() is None`); skip reasons `state`, `not_spot`, `managed_or_gke`, `exception_tag`,
  `filter_mismatch`, `too_young`, `disk_exception_tag`; failures (evaluate, delete, leftover disk
  read or delete, incomplete operation) call `fail()`; "Skipping leftover Spot disk" is DEBUG.
- READMEs: `crc/gcp/README.md` (`ZoneScan`, `log_action`, logging invariant),
  `crc/gcp/spot_vm/README.md` (`scan_zone`, `reject_reason`).
- Tests: `tests/helpers_log_fix.py` (rule `BUG-358`), `tests/test_log_fix_helpers_gcp.py` (rule unit
  tests), `tests/test_gcp_runlog.py` (new, 17 exact-line tests: dry run, real run, failure, skipped
  region, no-run silence), the four parity tests `tests/test_gcp_{vm,disk,ip,spot_instances}_parity.py`
  (every log comparison goes through `expected_logs(old.logs, {"BUG-358"})`, plus one explicit
  `oracle != new` case per file). Oracles untouched; calls, results, stdout and errors still equal.

Test count: 42256 before (LOG-0 result at 7437ac5), 42286 after. `make check` (ruff, mypy, lint-imports, pytest) green.

Left / notes:

- `Updating age for ...` (per labelled resource with a custom age tag) stays INFO, like LOG-0 left
  `Overwriting resource age`; it is not in the contract list. Candidate for a later LOG rung.
- The spot_vm leftover disks are not ACTION lines (resource=spot_instances counts VMs only);
  their failures are in `FAILED` and their label skips under `disk_exception_tag`.
- `notags` and `exception_tags` share the reason `exception_tag` in `vm` (one policy call).
- `tests/helpers_log_fix.py` `BUG-358` matches INFO dict dumps (`{...}`), the `namespace(...)` test
  double of an address, and the Spot disk skip line; other lanes add their own shapes to the same
  rule when merging (this lane only needed these).
- No locked safety test asserts these log levels (`test_gcp_spot_vm.py` has no log assertion);
  none was touched. No `## Owner action` needed.
- The runlog API had everything needed; nothing was worked around. `crc/core` and `crc/cli` untouched.

## Round 1 (Argon)

Scope reviewed: `git diff 7437ac5..HEAD` (`crc/gcp/_scan.py`, `crc/gcp/disk.py`, `crc/gcp/ip.py`,
`crc/gcp/vm.py`, `crc/gcp/spot_vm/{adapter,cleaner,disks,instances,README.md}`, `crc/gcp/README.md`,
`docs/refactor/reviews/LOG-GCP.md`, `tests/helpers_log_fix.py`, `tests/test_log_fix_helpers_gcp.py`,
`tests/test_gcp_runlog.py`, `tests/test_gcp_{disk,ip,vm,spot_instances}_parity.py`) against
`docs/refactor/LOGGING.md`, `crc/core/runlog.py`, and `tests/legacy/gcp_{disk,ip,vm,spot_vm}.txt`.

### 1. Contract & Behaviour Verification

- **Selection, call order, and side effects unchanged**:
  - `crc/gcp/disk.py`, `crc/gcp/vm.py`, `crc/gcp/ip.py`, and `crc/gcp/spot_vm/*` preserve the exact filter order, short-circuit evaluation, SDK calls, SDK call order, result lists/groupings, Slack/Influx inputs, CLI flags, and exception propagation of the pre-LOG code and `main` oracles.
  - Per-resource label dumps (`crc/gcp/disk.py:280`, `crc/gcp/vm.py:269`), IP address dumps (`crc/gcp/ip.py:147`), and Spot leftover-disk label skip messages (`crc/gcp/spot_vm/disks.py:95`) are moved from `logging.info` to `logging.debug` with identical message text (`BUG-358`).
  - Existing summary lines (`List of GCP ...`, `number of GCP ...`, `No GCP ...`) and inline error logs remain untouched at their existing levels.
- **RunLog integration (`BUG-360`, `BUG-361`, `BUG-362`)**:
  - `ZoneScan` (`crc/gcp/_scan.py:12-25`) uses `time.monotonic()` and calls `runlog.current().region(region, scanned, selected, seconds)` once per zone/region after listing completes across all four cleaners (`disk`, `vm`, `ip`, `spot_instances`).
  - `SpotVMCleaner._list` (`crc/gcp/spot_vm/cleaner.py:82-88`) calls `runlog.current().region_skipped(zone, e)` when zone instance listing fails and emits no `REGION` line for that failed zone.
  - `ACTION` lines (`would_delete`, `deleted`, `would_stop`, `stopped`) are emitted once per acted-on (or dry-run) candidate in both adapter (`Disk`, `VM`, `IP`, `SpotVM`) and `Cleaner` (`plan` in dry-run, `apply` in real run) execution paths.
  - Skipped resources are counted via `runlog.current().skip(reason)` with snake_case reasons (`state`, `exception_tag`, `exception_regex`, `filter_mismatch`, `attached`, `never_detached`, `too_young`, `not_spot`, `managed_or_gke`, `disk_exception_tag`).
  - Per-resource errors in `disk.py:254`, `vm.py:244`, `ip.py:184` (recorded before re-raising), `spot_vm/cleaner.py:51,208`, and `spot_vm/disks.py:92,119,124` call `runlog.current().fail(...)` while preserving inline error logging.

### 2. Parity, Safety Locks & Scope Guardrails

- Oracles in `tests/legacy/gcp_{disk,ip,vm,spot_vm}.txt` are untouched (`git diff 7437ac5..HEAD -- tests/legacy/` is empty).
- `tests/helpers_log_fix.py` adds `_bug_358` to `RULES["BUG-358"]`, changing only `INFO` raw dict/address dumps and the Spot leftover-disk skip line to `DEBUG` without dropping or altering message text.
- All four parity test modules assert `new.logs == expected_logs(old.logs, {"BUG-358"})`, keep all non-log assertions (`calls`, `deleted`/`stopped`/`disks`, `count`, `stdout`, `error`) exact against the oracle, and include an explicit `old.logs != new.logs` test for `BUG-358`.
- `tests/archtest/safety_lock.json` and all locked safety test files (`tests/test_gcp_spot_vm.py`, etc.) are untouched.
- `crc/core`, `crc/cli`, other cloud packages, and shared docs (`BUGS.md`, `STATUS.md`, `PLAN.md`) are untouched.
- `crc/gcp/README.md` and `crc/gcp/spot_vm/README.md` document the new public symbols (`ZoneScan`, `log_action`, `scan_zone`, `reject_reason`) and satisfy `ARCH-002`.

### BLOCKING

None.

### NIT

- In the adapter path (`Disk.delete()`, `VM.delete()`/`stop()`, `SpotVM.delete()`), `plan_zone` / `scan_zone` yields candidates lazily while iterating the zone listing, so per-candidate `ACTION` lines for a zone are logged before `scan.finish()` emits that zone's `REGION` line (whereas `Cleaner.plan()` followed by `Cleaner.apply()` logs all `REGION` lines before `ACTION` lines). This is a direct consequence of preserving `main`'s interleaved list-and-act generator order while needing the full zone iteration to know `scanned` and `selected`.

### Gates

Ran `pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 175 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 42286 passed |

Test count: 42256 before (`7437ac5`), 42286 after (+30).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 7437ac5..HEAD` (commits `6b430cc`, `e05fddf`), checked against
`docs/refactor/LOGGING.md`, `crc/core/runlog.py`, `crc/core/engine.py` and
`tests/legacy/gcp_{disk,ip,vm,spot_vm}.txt`. Round 1 verdict and its NIT re-checked first, then
every check below was done again independently. No code changes in this round.

### Round 1 follow-up

- Round 1 NIT (adapter path logs a zone's ACTION lines before that zone's REGION line, because
  `plan_zone` / `scan_zone` stay lazy generators like `main`): confirmed, and it is acceptable. The
  contract does not fix the order of REGION and ACTION lines, and an eager listing would change
  the order of SDK list and delete calls, which the rung must not do.
- Round 1 found no BLOCKING issue; I confirm that.

### Checks

1. Only log text, log levels and runlog calls changed. In `disk.py`, `vm.py` and `spot_vm/instances.py`, the filter chains became
   `_reject()` / `reject_reason()`. They keep the same order and short-circuits as before (attached, exception
   regex, exception labels, filter labels, detach timestamp, age; status, exception/notags, filter,
   age; status, spot, managed/GKE, exception, filter, age). `is_selected()` is `reject_reason() is
   None`. SDK calls and their order are unchanged: `scan_zone` lists eagerly where `list_zone` did,
   and `plan_zone` stays lazy. The IP `_delete()` records `fail()` and re-raises the same exception.
   The "Deleting IP address" line is still logged after a successful delete only. Summary lines,
   Slack and InfluxDB inputs, CLI flags and exit codes are untouched. The parity tests still assert
   that calls, results, counts, stdout and errors equal the oracle.
2. No duplicate ACTION lines. `crc/core/engine.py:99-100` runs `plan()` only in a dry run, so
   `would_*` comes from `plan()` there and `deleted`/`stopped` from `apply()` otherwise. The
   adapters never call `plan()`; they log ACTION once from `apply_*`. VM `stop` gives
   `would_stop`/`stopped` through `Operation.value`. All line formats come from `crc/core/runlog.py`
   (untouched), so key names, key order and levels match the contract.
3. Parity: `tests/legacy/` is untouched. Every log divergence goes through
   `expected_logs(..., {"BUG-358"})`. The BUG-358 rule only changes INFO to DEBUG, and only for
   bare `{...}` / `namespace(...)` dumps and the Spot disk skip line. Every oracle summary line
   is an f-string with a prefix, so the regex cannot match it (I checked all bare `logging.info(`
   calls in the four oracles). Each parity file has an `old.logs != new.logs` case that also asserts
   the same message texts, the same calls and the same stdout. There is no global loosening: the
   `Counter` comparisons in `test_cleaner_protocol_matches_main` were there before this rung.
4. `tests/archtest/**` (including `safety_lock.json`), `tests/test_gcp_spot_vm.py`, `crc/core`,
   `crc/cli`, the other cloud packages and `BUGS.md` / `STATUS.md` / `PLAN.md` are untouched
   (`git diff --stat` empty).
5. ARCH-002: `ZoneScan`, `log_action` (`crc/gcp/README.md`), `scan_zone` and `reject_reason`
   (`crc/gcp/spot_vm/README.md`) are documented. `disk.py` and `vm.py` are at exactly 400 lines
   (ARCH-004 limit; archtest passes).

### BLOCKING

None.

### NIT

- `crc/gcp/_scan.py:18,24`: the REGION `seconds` come from the module's `time.monotonic`, not from
  the run's injectable clock. `RunLog._clock` (`crc/core/runlog.py:74`) is private, and the lane may
  not touch `crc/core`. The tests stay deterministic by monkeypatching `_scan.time`. This is a
  runlog API gap that the coder summary does not mention. Proposed follow-up for the LOG-0 owner:
  add a read-only `RunLog.clock` property, then use
  `clock = runlog.current().clock` in `ZoneScan.__init__`.
- `crc/gcp/spot_vm/cleaner.py:208` and `crc/gcp/spot_vm/disks.py:119` pass a `str` to `fail()`.
  The API allows this (`BaseException | str`), but the resulting FAILED line is
  `error=instance delete failed or incomplete`, without the `<ExceptionType>: ` prefix that
  `LOGGING.md:59` shows. Proposed change: either document in `LOGGING.md` that string errors are
  printed as-is, or have runlog print them as `error=Incomplete: <message>`. This is an
  owner/LOG-0 decision, so it is not changed here.
- `crc/gcp/spot_vm/disks.py:95`: the per-disk "Skipping leftover Spot disk" line is filed under
  BUG-358 (label dumps). It is really a per-resource "skipping X because Y" line from contract
  item 1. The DEBUG move itself is in contract. When the rows are merged, BUG-358's text in
  `BUGS.md` could mention it. Also, `skip("disk_exception_tag")` counts disks inside a
  `resource=spot_instances` run, so `skipped=` mixes VMs and disks. This is documented in the
  coder notes and is acceptable.
- `crc/gcp/ip.py:125`: the dry-run `plan()` calls `runlog.current().action("would_delete", ...)`
  directly. Every other site uses `log_action()`. The output is the same; this is only a
  consistency point.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt`, then `make check`: ruff passed, mypy
passed (no issues in 210 source files), lint-imports kept 5 contracts and broke 0, and pytest
passed `42286` tests (exit 0).

Test count: 42256 before (`7437ac5`), 42286 after (+30), the same as round 1.

ROUND 2 VERDICT: APPROVE

## Rule move

Mechanical test-helper move, no production code change. After merging `logging/core` (`7c27986`),
`tests/helpers_log_fix.py` and `tests/test_log_fix_helpers.py` are byte-identical to
`origin/logging/core`. The BUG-358 rule (`_bug_358`, `_RAW_DUMP`, `_LABEL_SKIP`, same patterns and
docstring) moved to the new `tests/log_fix_gcp.py`, which defines
`RULES = {**helpers_log_fix.RULES, "BUG-358": _bug_358}`; this lane did not change the core BUG-356
rule. The four `tests/test_gcp_*_parity.py` files pass `rules=RULES` to `expected_logs`. The BUG-358
unit tests moved from `tests/test_log_fix_helpers_gcp.py` to `tests/test_log_fix_gcp.py` (plus two
tests that `RULES` extends the core rules and that BUG-356 and BUG-358 apply together).
`tests/README.md` has one added entry for the new files.
