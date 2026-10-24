## Coding summary

Rung LOG-OCI (OCI lane of the LOG family, contract in `docs/refactor/LOGGING.md`). Log text and
levels only: nothing selected, deleted, stopped, posted or written differs, no CLI flag, no exit
code, SDK call order unchanged.

Files changed:

- `crc/oci/_runlog.py` (new, private): glue to `crc.core.runlog` (`region_started/done/skipped`,
  `record_action/skip/failure`). `monotonic` is bound by name so the spot VM terminate-wait tests
  that patch `time.monotonic` are not disturbed.
- `crc/oci/vm/{cleaner,adapter}.py`, `crc/oci/disk/{cleaner,adapter}.py`: age-check freeform tag
  dict `logging.info(tags)` -> `logging.debug` (BUG-353 disk, BUG-358 vm and disk); REGION per walked
  region (`scanned` = resources listed, `selected` = candidates); `region_skipped` where the
  connectivity error skips the region; one ACTION per resource in `act()` / `remove()` (kind
  `deleted|stopped`, or `would_*` in a dry run); `skip()` with reasons `state`, `attached`,
  `exception_tag`, `filter_mismatch`, `too_young` from `decide()`; `fail()` where the per-resource
  error line is logged. `VmCleaner.apply()` in a dry run emits the `would_*` ACTION lines.
- `crc/oci/spot_vm/{decide,leftovers,cleaner,adapter}.py`: skip reasons (`state`, `not_preemptible`,
  `oke_member`, `exception_tag`, `filter_mismatch`, `too_young`, `leftover_exception_tag`);
  "Skipping leftover volume ..." INFO -> DEBUG; REGION / region_skipped; ACTION for each instance
  and each leftover volume (volume lines have `region=-`, `name=-`); `fail()` for attachment-list,
  poll, timeout, get_volume, delete_volume and per-instance errors. `SpotVM._process` gains an
  optional `region` argument and returns 1/0 (selected); no behaviour change.
- `crc/oci/README.md`: module list and logging invariant.
- Tests: `tests/helpers_log_fix.py` (rules `BUG-353`, `BUG-358`; `BUG-361`/`BUG-362` are identity
  rules because new lines only appear in an attached run), `tests/test_oci_{vm,disk,spot_instances}_parity.py`
  (every `logs` comparison goes through `expected_logs(old.logs, BUGS)`; new
  `test_*_diverges_from_oracle` cases assert oracle != new, text and calls equal, only DEBUG
  levels differ), `tests/test_oci_runlog.py` (new: exact REGION / ACTION / SKIPPED / ERRORS /
  FAILED / RESULT lines for vm, disk and spot VM adapters and cleaners; dry run and real run,
  a failure, a skipped region, no run means no new lines).

Bug IDs handled: BUG-353 (OCI tag dict level), BUG-358 (OCI vm and disk tag dumps; also the OCI
per-resource "Skipping leftover volume" line, tagged BUG-358 in the helper), BUG-361 (REGION per
region, `region_skipped`), BUG-362 (`fail()` calls). BUG-356/357/359/360 are other lanes.

Test count: 42256 before (at 7437ac5), 42276 after. `make check` green (ruff, mypy, lint-imports, pytest) on Python 3.12.3.

Left / notes:

- "Updating age for instance/volume/preemptible instance" lines stay INFO (they are not in the
  contract list and only appear for resources with a custom age tag).
- `region_skipped` is called where a region is skipped on a list failure. The region subscription
  fallback in `Base.get_all_regions` and the partial skips (attachments, boot attachments) only keep
  their warning: they do not skip a whole named region.
- A non-connectivity listing error in spot VM (`failed listing instances`) is recorded with
  `fail(region, "-", e)`.
- Contract question: the spot VM leftover volumes are reported as ACTION lines (id = volume OCID),
  so `selected` / `done` of `oci spot_instances` count instances plus volumes. Drop the volume
  ACTION lines if the dry-run pipeline expects instances only.
- No locked safety test (`tests/test_oci_spot_vm.py`) asserts these levels; none was touched.
- The runlog API had everything needed; no workaround.

## Round 1 (Argon)

### Findings

1. **FIXED (in-scope consistency): `crc/oci/spot_vm/{adapter,cleaner,leftovers}.py` region propagation on leftover volume `ACTION` and `FAILED` lines.**
   - Before this fix, `SpotVmCleaner.terminate()` (live run) emitted `ACTION deleted ... region=<region> id=<volume_id> name=-`, whereas `SpotVmCleaner.apply()` (dry run) and `SpotVM._delete_volume()` (adapter) emitted `region=-` for the same leftover volumes, and `leftovers.py` (`attached_volume_ids`, `wait_terminated`, `volume_passes_tag_check`, `delete_leftover`) always recorded `fail(None, ...)` (`region=-`) even when called inside a regional pass.
   - Threaded `region: str | None = None` through `leftovers.py` and `SpotVM._region` (with class default `None` so direct unit-test calls without `delete()` still work and hook signatures for `tests/test_oci_spot_vm.py` remain unchanged), and aligned `SpotVmCleaner.apply()` dry-run volume `ACTION` lines to pass `region=candidate.region` in per-candidate order. Updated `tests/test_oci_runlog.py` and parametrized `test_spot_vm_cleaner_plan_apply_reports` over live and dry-run modes.

### Checklist

1. **Behaviour / log-only contract**: Verified against `git diff 7437ac5..HEAD` and the oracle parity suites. Only log levels (`logging.info(tags)` -> `logging.debug(tags)` in `crc/oci/disk/cleaner.py` and `crc/oci/vm/cleaner.py`; `"Skipping leftover volume ..."` -> `logging.debug` in `crc/oci/spot_vm/leftovers.py`) and `crc.core.runlog` reporting calls (`crc/oci/_runlog.py`) changed. Selection, deletion/stopping, SDK call order, Slack messages, InfluxDB points, CLI flags, and exit codes are unchanged.
2. **Line formats**: `REGION`, `ACTION`, `SKIPPED`, `ERRORS`, `FAILED`, and `RESULT` lines match `docs/refactor/LOGGING.md` and `crc/core/runlog.py` key names, order, and log levels.
3. **Parity tests**: `tests/legacy/oci_{disk,vm,spot_vm}.txt` are untouched. All log comparisons go through `expected_logs(old.logs, BUGS)` with in-scope bug IDs (`BUG-353`, `BUG-358`, `BUG-361`, `BUG-362`), changing only the level of the documented lines to `DEBUG` without dropping or altering message text. Each parity module includes a `test_*_diverges_from_oracle` test proving `old.logs != new.logs` on matching cases while calls and message texts remain identical.
4. **Locked safety tests**: `tests/archtest/safety_lock.json` and all 5 locked safety test files (`tests/test_oci_spot_vm.py`, etc.) are untouched.
5. **READMEs & docs (ARCH-002)**: `crc/oci/README.md` documents `_runlog.py` and its symbols; `crc/oci/{disk,vm,spot_vm}/README.md` remain in sync. Protected files (`AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `docs/refactor/PLAN.md`, `docs/refactor/BUGS.md`, `docs/refactor/STATUS.md`, `.cursor/**`, `tests/archtest/**`, `crc/core/**`, `crc/cli/**`) are untouched.
6. **Gates & test count**: `make check` passed (`ruff check .`, `mypy` on 209 source files, `lint-imports` 5/5 contracts kept, `pytest -q`: **42277 passed**).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 7437ac5..HEAD` (commits 1b6b7cd, 3afd9b1, c150e08). No code change in this round.

### Round 1 fix check

- The region threading through `crc/oci/spot_vm/leftovers.py`, `SpotVM._region` (class default
  `None`) and `SpotVmCleaner.terminate()/apply()` is correct. Live and dry-run leftover-volume
  ACTION lines and the leftover FAILED lines now carry the instance's region in both the adapter
  and the Cleaner path. `tests/test_oci_spot_vm.py` hook signatures are unchanged.

### Independent checks

1. **Log-only.** The `crc/` diff touches only `crc/oci/**`. It changes two levels:
   `logging.info(tags)` becomes `logging.debug(tags)` in `crc/oci/{vm,disk}/cleaner.py`, and
   "Skipping leftover volume ..." becomes DEBUG in `crc/oci/spot_vm/leftovers.py`. Everything
   else is a `crc.oci._runlog` call or a local counter. The `_process` return values (now
   `int`) are only summed for REGION `selected`. No branch, SDK call, call order, Slack or
   InfluxDB code, CLI flag or exit code changed. `record_skip` runs exactly once per resource:
   `decide()` / `skip_reason()` have a single caller per path, and
   `volume_passes_tag_check` runs once per volume per path. A failed call returns before
   `record_action`, so it is never counted as both done and failed (one exception, NIT 3).
2. **Formats.** Every line goes through `crc.core.runlog`. The tests assert the exact REGION /
   ACTION / SKIPPED / ERRORS / FAILED / RESULT strings and levels, and they match the contract.
   Kinds come from `Operation` and `dry_run` (`deleted`, `would_delete`, `stopped`,
   `would_stop`). `region_skipped` is called only where a connectivity error skips a whole
   region.
3. **Parity.** `tests/legacy/` is untouched. Every OCI parity `logs` comparison goes through
   `expected_logs(old.logs, BUGS)` with in-scope IDs. The rules only lower levels to DEBUG,
   never drop a line and never change text. `test_{vm,disk,spot_vm}_diverges_from_oracle`
   assert oracle != new, identical text and calls, and DEBUG as the only changed level. Calls,
   results, stdout and errors still compare equal to the oracle. Nothing was loosened globally.
4. **Safety lock.** `tests/archtest/**`, `safety_lock.json` and the 5 locked tests are untouched.
   `tests/test_oci_spot_vm.py` does not assert the levels of the changed lines.
5. **Docs (ARCH-002).** `crc/oci/README.md` lists `_runlog.py` and its helpers under the
   Public API and adds a logging invariant. No new public name in the subpackages. Protected
   files (`crc/core`, `crc/cli`, BUGS/STATUS/PLAN, AGENTS, ARCHITECTURE, CLEANUP_SAFETY,
   `.cursor`) are untouched.

### Findings

- NIT `tests/helpers_log_fix.py:66-73` (`_bug_358`): the rule also lowers the
  per-resource "Skipping leftover volume ...: exception_tags/notags" line. That is a "skipping
  X because Y" line (contract item 1), not a tag dump, and none of BUG-356..362 names it.
  `_bug_358` also applies `_bug_353`, so `{"BUG-358"}` alone covers the BUG-353 lines.
  Proposal: give the leftover-skip predicate its own rule under an owner-assigned bug ID, or
  add one sentence to LOGGING.md saying OCI per-resource skip lines fall under BUG-358. Merge
  note: the AWS, Azure and GCP lanes will also register `"BUG-358"` in `RULES`. The integrator
  must merge the predicates (OCI `_is_tag_dump` matches any message shaped `{...}`) rather
  than keep one side.
- NIT `crc/oci/_runlog.py:16,31,36`: REGION `seconds` uses the module's own `time.monotonic`,
  not the injectable run clock, because `RunLog` has no public clock accessor (runlog API gap).
  Tests are still deterministic because the int truncation gives 0. Proposal for a later
  LOG-0 follow-up: add `RunLog.now()` or let `region()` take a start time.
- NIT (contract question, carried over): leftover volumes are ACTION lines under
  `resource=spot_instances`, so RESULT `selected`/`done` count instances plus volumes. A
  poll failure or timeout after a successful terminate gives both `ACTION deleted` and
  `FAILED` for the same instance id. Both are defensible. The dry-run comparison pipeline
  owner should confirm.
- Environment: `make check` first failed in mypy with `import-untyped` on `google.cloud` /
  `azure.identity`. The cause was a stale `.mypy_cache`. With a fresh cache
  (`--cache-dir=/tmp/mypycache`) mypy is clean. This is not a code problem.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt`, then the `make check` steps on
Python 3.12.3:

- `ruff check .`: all checks passed.
- `mypy`: no issues in 209 source files (fresh cache).
- `lint-imports`: 5 kept, 0 broken.
- `pytest -q`: **42277 passed** (42256 at 7437ac5).

ROUND 2 VERDICT: APPROVE

## Rule move

The lane's bug rules (BUG-353, BUG-358, BUG-361, BUG-362 and their helpers) moved from
`tests/helpers_log_fix.py` to `tests/log_fix_oci.py`; `RULES = {**helpers_log_fix.RULES, ...}`. The
`test_oci_*_parity.py` tests pass `rules=RULES` to `expected_logs`. The rule unit test moved from
`tests/test_oci_logger.py` to `tests/test_log_fix_oci.py`. `tests/helpers_log_fix.py` and
`tests/test_log_fix_helpers.py` are identical to `logging/core` (7c27986). The lane did not change
the core BUG-356 rule, so it keeps the core entry. No production code changed.
