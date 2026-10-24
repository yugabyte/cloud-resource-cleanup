## Coding summary

Rung LOG-AWS1 (logging lane for `crc/aws/vm`, `crc/aws/spot_instance_requests`, `crc/aws/disk`;
contract in `docs/refactor/LOGGING.md`). Log text and levels only: nothing selected, deleted,
stopped, posted or written changed, no SDK call or its order changed, no CLI flag or exit code.

Files changed:

- `crc/aws/vm/cleaner.py`, `crc/aws/vm/adapter.py`: the raw tag list of each instance is logged at
  DEBUG (BUG-358); REGION via the new `note_region()`; ACTION via `apply_region()` (real run) and the
  new `note_dry_run()` (dry run, Cleaner and adapter paths); `skip()` with `no_tags`, `exception_tag`,
  `too_young`; `fail()` for the per-instance processing error, the missing Name tag (logged at ERROR)
  and a failed terminate/stop; `region_skipped()` inside `skip_region()`.
- `crc/aws/disk/volumes.py`, `cleaner.py`, `detach.py`: the INFO `Skipping volume ...` lines are DEBUG
  with the same text; skip reasons (`state`, `attached`, `multi_attach`, `untagged`, `filter_tags`,
  `name_regex`, `sensitive_yb_task`, `kubernetes_csi`, `exception_tag`, `notags`, `exception_regex`,
  `too_young`, `no_create_time`, `invalid_age_tag`, `no_age_floor`, `recently_attached`,
  `cloudwatch_incomplete`, `detach_age_zero`); REGION in `plan_region()`; ACTION `would_delete` in
  `note_dry_run()` and `deleted` after `delete_volume`; `fail()` for every swallowed per-volume
  error, region error, failed delete and CloudWatch lookup error; `region_skipped()` for connectivity
  and unauthorised region skips.
- `crc/aws/spot_instance_requests/{cleaner,adapter,requests,cleanup,preview,leftover,volumes}.py`:
  the INFO `Skipping leftover volume`, `Skipping tagged leftover volume` and `Skipping cancelled spot
  request` lines are DEBUG with the same text; REGION in `list_matches()` (listing plus filtering
  time); ACTION `deleted` for each cancelled request, terminated instance and deleted volume,
  `would_delete` for the same three in a dry run (`preview_request` got an optional `region`);
  skip reasons (`no_tags`, `cancelled_no_instance`, `exception_tag`, `filter_tags`,
  `invalid_age_tag`, `too_young`, `volume_protected`, `already_cancelled_done`, `sweep_gates`);
  `fail()` for describe, cancel, terminate, tag, wait/delete, sweep and region-listing errors;
  `region_skipped()` for connectivity skips. `filtered_requests` got an optional `region`.
- READMEs of the three packages (Public API for `note_region`/`note_dry_run`, logging invariant).
- Tests: `tests/helpers_log_fix.py` (BUG-358 rule `_bug_358_aws_vm` behind a `_BUG_358_PARTS` tuple
  so other lanes can append theirs; the BUG-356 rule now also turns the INFO `Skipping <resource>`
  lines of this lane into DEBUG; BUG-361/362 are identity rules), the three parity tests (every log
  comparison goes through `expected_logs(old.logs, FIXED_BUGS)`, plus an explicit
  `oracle != new` test per package), and the new `tests/test_aws_log_fix.py` (exact REGION/ACTION/
  SKIPPED/ERRORS/FAILED/RESULT lines: dry run, real run, failure, connectivity skip, skip reasons).

Bug IDs handled: BUG-358 (AWS VM tag dump to DEBUG, fixed for AWS VM), BUG-356 (the per-resource
`Skipping ...` lines of disk and spot to DEBUG; the age-check lines were done in LOG-0), BUG-361
(REGION per region, `region_skipped`) and BUG-362 (`fail()` at every swallowed per-resource error) for
these three cleaners. BUG-360 is not in this lane.

Test count: 42256 at 7437ac5 (LOG-0 review), 42271 after (`make check` green: ruff, mypy, lint-imports, pytest).

## Notes and choices

- Parity runs have no begun run, so `runlog.current()` is unattached and logs nothing: the new lines
  never appear in the parity logs. BUG-361/362 therefore have identity rules in `expected_logs` and
  are pinned by `tests/test_aws_log_fix.py` (separate exact assertion, as the contract says). The
  `oracle != new` requirement is met by the BUG-358 (VM) and the `Skipping ...` (disk, spot) tests.
- Lines of an acted resource (`Deleted volume`, `Instance X deleted.`, `Cancelled spot request`,
  `Dry run: would delete ...`) stay at INFO: they are few and the contract does not list them. The
  ACTION line is the machine-readable twin. WARNING and ERROR skip lines (missing CreateTime, invalid
  custom age tag, incomplete CloudWatch answer) keep their level because they flag an anomaly.
- A missing Name tag on an instance is logged at ERROR on main, so it is a `fail()` (not a `skip()`).
- Unauthorised/opt-in region skips of the disk cleaner also count as `region_skipped` (the contract
  names connectivity skips only; this is a skipped region either way).
- `REGION seconds` for spot is the time of listing plus filtering, not of the cleanup that follows
  (the cleanup loop is a per-request closure with no region boundary); VM and disk measure the same
  span (list and filter). The spot `scanned` is the number of requests the region returned.
- `fail()` is not called for the secondary marker-invalidation errors of spot (`Failed to clear
  scope tag ...`, `Failed to invalidate ...`): they follow a primary failure that is recorded.
- A failed region (`describe_*` error, region-level exception) has `id=-` in its FAILED line.
- `ACTION name` is `-` for disk and spot (the disk label has spaces; spot has no name).
- Runlog API workaround: none needed. `RunLog.region()` takes `seconds` from the caller, so the
  cleaners use `time.monotonic()` (not injectable); the unit tests accept `seconds=0`.
- No locked safety test asserts these log levels; none touched.

## Left

Nothing. BUG rows in `docs/refactor/BUGS.md` are not edited here (other lanes edit it); the status
of BUG-358 (AWS VM part), BUG-361 and BUG-362 (AWS VM, disk, spot parts) should be set at
integration.

## Round 1 (Argon)

### Findings and fixes

- **Out-of-scope formatting edits (fixed)**: `11e31d2` included formatting-only edits across 17 files outside the LOG-AWS1 lane (`crc/cli/inputs.py`, `crc/core/{engine,policy,report,selection}.py`, `crc/notify/{influx,slack}.py`, `tests/test_aws_{keypair,snapshot}_parity.py`, `tests/test_core_{engine,policy,registry,report,selection}.py`, `tests/test_notify_{influx,parity,slack}.py`). Reverted all 17 files to `7437ac5` so the branch only touches in-scope files and avoids merge conflicts with sibling lanes.
- **Swallowed errors and region propagation in `crc/aws/spot_instance_requests` (fixed)**:
  - `describe_tagged_leftover_volumes()` and `live_spot_request_tags()` (`crc/aws/spot_instance_requests/volumes.py`) logged `ERROR` and swallowed `ClientError` / `BotoCoreError` without calling `runlog.current().fail()`. Added `fail()` calls at both sites (BUG-362).
  - `volume_ok_to_delete()`, `inspect_instance()`, and `wait_volume_available_then_delete()` passed `region=None` to `fail()` even when called from `cleanup_one()`, `sweep_region()`, or `preview_request()` where the region is known. Threaded optional `region: str | None = None` through those helpers so `FAILED` lines report the region; added `test_spot_volume_and_sweep_failures_carry_region` in `tests/test_aws_log_fix.py`.
- **Missing `InstanceId` fallback in `crc/aws/vm/cleaner.py` (fixed)**: passed `decision.id or "-"` and `_safe_id(inst) or "-"` to `runlog.current().fail()` so malformed instance dicts without `InstanceId` emit `id=-` instead of an empty `id=`.
- **Package re-exports in `crc/aws/vm/__init__.py` (fixed)**: re-exported `note_dry_run` and `note_region` in `crc/aws/vm/__init__.py` and `__all__` to match `crc/aws/vm/README.md` Extension Rule 5.

### Checks and gates

1. **Behaviour invariance**: Only log levels (`logging.debug` for VM tag dumps and disk/spot per-resource INFO skip lines) and `crc.core.runlog` collector calls changed. Selection, deletion/stop decisions, SDK call order, Slack messages, InfluxDB points, CLI flags, and exit codes are unchanged.
2. **Contract formats**: `RUN START`, `REGION`, `ACTION`, `SKIPPED`, `ERRORS`, `FAILED`, and `RESULT` lines match `docs/refactor/LOGGING.md` key names, order, and log levels.
3. **Parity tests**: Oracles in `tests/legacy/` are untouched; all log level changes go through `expected_logs()` with `BUG-356` / `BUG-358`, `oracle_logs != new_logs` is asserted for each cleaner, and all non-log outcomes match the oracle.
4. **Safety lock**: `tests/archtest/safety_lock.json` and all locked safety tests are untouched.
5. **READMEs / ARCH-002**: `crc/aws/vm/README.md`, `crc/aws/disk/README.md`, and `crc/aws/spot_instance_requests/README.md` are synced with all public symbols.
6. **`make check`**: ruff, mypy, import-linter (5/5 contracts kept), and `pytest -q` all pass with **42272 passed** (up from 42256 at `7437ac5`).

VERDICT: APPROVE

## Round 2 (Opus)

Scope `git diff 7437ac5..HEAD` (commits `11e31d2`, `949cce5`).

### Round 1 fixes re-checked

- Out-of-scope formatting revert: confirmed; the diff touches only `crc/aws/{vm,disk,spot_instance_requests}`, their parity tests, `tests/helpers_log_fix.py`, `tests/test_aws_log_fix.py` and this file.
- Spot `fail()` at `describe_tagged_leftover_volumes()` / `live_spot_request_tags()` and region threading through `volume_ok_to_delete()`, `inspect_instance()`, `wait_volume_available_then_delete()`: present and pinned by `test_spot_volume_and_sweep_failures_carry_region`.
- VM `id=-` fallback and `__init__` re-exports of `note_dry_run` / `note_region`: present, README in sync.

### Independent checks

1. Behaviour: the only non-log edits are added `runlog.current().*` calls, optional trailing `region` parameters (default `None`, positional order kept) and `time.monotonic()` reads. No condition, return value, SDK call or call order changed; Slack/Influx/CLI/exit codes untouched. Parity tests assert calls, results, errors and stdout unchanged against the live oracles.
2. Formats: all lines come from `crc.core.runlog` (not edited here); call sites pass kinds in `ACTION_KINDS` and snake_case skip reasons. Unit tests assert exact REGION/ACTION/ERRORS/FAILED/RESULT text and ERROR/INFO levels.
3. Parity: `tests/legacy/` untouched. Every log divergence goes through `expected_logs(old.logs, FIXED_BUGS)`; the new rules (`_bug_358_aws_vm`, the `_is_skip_line` extension of BUG-356) only lower INFO records to DEBUG, never drop or rewrite text; BUG-361/362 are identity rules (parity runs have no begun run, so no new lines), and the new lines are asserted exactly in `tests/test_aws_log_fix.py`. `oracle != new` is asserted per package (VM BUG-358, disk and spot BUG-356). No global loosening.
4. Safety lock: `tests/archtest/**` (including `safety_lock.json`) and the locked `test_aws_disk.py` / `test_aws_spot_instance_requests.py` are untouched; `crc/core`, `crc/cli`, BUGS/STATUS/PLAN, AGENTS/ARCHITECTURE/CLEANUP_SAFETY and `.cursor/**` are untouched.
5. READMEs: `note_region` / `note_dry_run` are listed in `crc/aws/vm/README.md` Public API and the logging invariant is in all three package READMEs.
6. Gates: `pip install -r requirements.txt -r requirements-dev.txt` then `make check`: ruff, mypy strict, lint-imports, pytest all green, **42272 passed** (42256 at `7437ac5`).

### Findings (all NIT, none blocking)

- NIT `crc/aws/spot_instance_requests/cleanup.py:97-107`: after an ambiguous terminate error, `fail()` is recorded and, when the instance turns out `shutting-down`, an `ACTION deleted` for the same instance follows, so it counts in both `failed` and `done`. This matches the ERROR line on main followed by the instance going into the deleted list; leave it as is, or skip the `fail()` when the state shows the terminate took effect (follow-up, log-only).
- NIT `crc/aws/disk/cleaner.py:123-125` (via `region_guard` in `apply_region`) and `crc/aws/spot_instance_requests/cleaner.py:257`: a connectivity error in the Cleaner `apply()` phase calls `region_skipped()` for a region that `plan()` already counted with `region()`, so `regions + regions_skipped` can be larger than the number of regions. This is a rare edge case and the counters still match the warning lines.
- NIT `tests/test_aws_disk_parity.py:1103-1108,1334-1336`: formatting-only edits (lambda wrap, two trailing blank lines removed) left over from the coder commit. They are harmless and in the lane's own file.
- NIT `tests/test_aws_log_fix.py` is 408 lines. ARCH-004 currently checks `crc/` only (gate green), but splitting the file per cleaner would keep it under the 400-line style.
- Note: the disk cleaner counts unauthorised/opt-in region skips as `region_skipped` (the contract names connectivity skips only), and the VM missing-Name-tag case is a `fail()` because main logs it at ERROR. Both are documented above and acceptable.

No runlog API gap found; no owner action needed.

ROUND 2 VERDICT: APPROVE

## Rule move

The LOG-AWS1 rules moved from `tests/helpers_log_fix.py` to `tests/log_fix_aws_compute.py` (test-only, no
production change), after `logging/core` made `expected_logs` / `expected_outcome` take a `rules` mapping.
`tests/helpers_log_fix.py` and `tests/test_log_fix_helpers.py` are now identical to `logging/core`. The lane's
`RULES` is the core rules plus BUG-358, BUG-361, BUG-362 and its own BUG-356 (core age rule, then the INFO
skip lines); the AWS disk, spot request and VM parity tests pass `rules=RULES`. Unit tests of the moved
rules are in `tests/test_log_fix_aws_compute.py`. ARCH-002 does not apply (no `crc/**` change).
