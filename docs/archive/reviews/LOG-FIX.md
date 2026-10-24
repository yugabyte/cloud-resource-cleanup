## Coding summary

Rung LOG-FIX (owner-approved 2026-10-22; contract `docs/refactor/LOGGING.md`). Fixes the
regressions of the Jenkins dry run `crc-dry-run-compare` build 4 in the LOG work. Log text and
levels only: nothing selected, deleted, stopped, posted or written differs, no CLI flag, no exit
code, no oracle edited.

Bug IDs handled: BUG-363 (new, fixed in LOG-FIX; exists on main: no); BUG-361 follow-up (REGION
level); BUG-360 follow-up (`--scan_tag` path).

Files changed:

- `crc/core/logger.py`: `RunLog` writes through `logging.getLogger("crc.run")` (never a
  module-level `logging.*` call, which runs `basicConfig()` on a root logger without handlers).
  `begin()` holds the RUN START line (`RunLog.hold_header`) until the first run-log line emitted
  after the root logger has handlers, then emits it once before that line; `finish()` emits a
  still-held RUN START before SKIPPED/ERRORS/RESULT. `RunLog.region()` logs DEBUG when
  `selected == 0`, INFO otherwise (a skipped region never calls `region()`; counters unchanged).
- `crc/cli/dispatch.py`: the three `logging.warning` calls of the spot handlers (they run before
  any cleaner, so before `init_logging`) use a module logger (`_LOG`). Grep of `crc/` for other
  module-level `logging.*` calls in the CLI path: `crc/cli/*`, `crc/service.py`, `crc/notify/*`
  and `crc/notifications.py` have none besides these and `init_logging` itself (called after the
  handlers exist); all cleaner code runs after `Service.__init__`.
- `crc/cli/scan.py`: `_scan_cloud` wraps the scan in `logger.begin(cloud, "vm", "scan", True,
  ...)` / `logger.finish()` in a try/finally (one pair per cloud); the old body is
  `_scan_and_report`, unchanged. Options come from `dispatch._run_options`.
- Docs: `docs/refactor/LOGGING.md` (named logger, held header, REGION level, scan), `BUGS.md`
  (BUG-363), `PLAN.md` and `STATUS.md` (LOG-FIX row).
- Tests: new `tests/test_cli_logging_init.py` (CLI path from a root logger without handlers with
  a fake cleaner that calls `init_logging`: only the init_logging handlers exist afterwards, no
  line printed twice, RUN START once before REGION and RESULT; no-handler run still emits
  RUN START then RESULT; dispatch warning does not run basicConfig; no module-level logging call
  in logger/dispatch/scan; fails on the old code). Updated `tests/test_core_logger.py` (REGION
  DEBUG when nothing selected) and `tests/test_cli_main_parity.py` (scan pass now logs RUN START /
  RESULT with operation=scan; outcome still equal to the oracle).

Test count (pytest -q): 42432 before (df0c911), 42437 after (4 new tests in `tests/test_cli_logging_init.py`, 1 new in `tests/test_core_logger.py`, 0 removed; `tests/test_oci_logger.py` and `tests/test_gcp_logger.py` updated for the DEBUG REGION level). `make check` green.

Left: nothing. Parity tests of the cloud lanes needed no change
(they assert oracle lines only).

No locked safety test asserts these log levels; no `## Owner action`.

## Round 1 (Argon)

Scope reviewed: `git diff df0c911..HEAD` on branch `logging/fix-handler` (rung `LOG-FIX`).

### Checks

1. **Behaviour & scope (`crc/core/logger.py`, `crc/cli/dispatch.py`, `crc/cli/scan.py`)**:
   - `crc/core/logger.py` writes all run-log lines through `logging.getLogger("crc.run")` and never calls module-level `logging.*` (`BUG-363`). `begin()` holds the `RUN START` line (`RunLog.hold_header`) until the first run-log line emitted after the root logger has handlers (or `finish()` if the run ends without root handlers), emitting it once before that line.
   - `crc/cli/dispatch.py` replaces the three pre-cleaner `logging.warning` calls in `_spot_instance_requests` and `_spot_instances` with a module logger `_LOG = logging.getLogger(__name__)`. Verified by grepping `crc/` that no other module-level `logging.*` call can run before `Service.__init__` (`init_logging`) in the CLI path (every cleaner adapter calls `super().__init__()` / `Service.__init__(self)` as the first statement of `__init__`).
   - `RunLog.region()` logs `REGION` at `logging.DEBUG` when `selected == 0` and `logging.INFO` otherwise; skipped regions still call `region_skipped()` and emit no `REGION` line, and `regions=` in `RESULT` still counts all walked regions.
   - `crc/cli/scan.py` wraps each cloud pass in `logger.begin(str(cloud), "vm", "scan", bool(context.dry_run), _run_options(options))` / `try ... finally: logger.finish()` (`dry_run` is enforced `True` before the loop).
   - No changes to what is selected, deleted, or stopped, SDK call order, Slack text, InfluxDB points, CLI flags, or exit codes.
2. **Line formats & levels (`crc/core/logger.py`, `docs/refactor/LOGGING.md`)**:
   - `RUN START`, `REGION`, `ACTION`, `SKIPPED`, `ERRORS`, `FAILED`, and `RESULT` formats, key names, key order, and levels match the contract in `docs/refactor/LOGGING.md`.
   - **Fixed in review**: added `self.attached and` guard in `RunLog._emit_header` (`crc/core/logger.py:94`) so an unattached `RunLog` stays completely silent even if `hold_header` and `finish()` are called on it directly.
3. **Parity & regression tests (`tests/test_cli_logging_init.py`, `tests/test_cli_main_parity.py`, `tests/test_cli_logger.py`, `tests/test_core_logger.py`, `tests/test_gcp_logger.py`, `tests/test_oci_logger.py`)**:
   - All frozen oracles under `tests/legacy/` are untouched (`git diff df0c911..HEAD -- tests/legacy/` is empty).
   - `tests/test_cli_logging_init.py` tests the CLI path starting from a root logger without handlers with a fake cleaner calling `init_logging`, asserting only `FileHandler` and `StreamHandler` (level `INFO`) exist on root afterwards, no line is printed twice, `RUN START` is printed once before `REGION` and `RESULT`, `DEBUG` `REGION` (`selected=0`) and `botocore` `DEBUG` lines go to `crc.log` but not stderr, and a no-handler run still emits `RUN START` then `RESULT`.
   - Added `test_scan_logs_run_start_and_result_per_cloud` in `tests/test_cli_logger.py` covering multi-cloud `run_scan` `RUN START` / `RESULT` with `operation=scan`.
4. **Safety lock**:
   - `tests/archtest/safety_lock.json` and all five hash-locked safety test files are untouched.
5. **Docs & READMEs (`docs/refactor/BUGS.md`, `docs/refactor/LOGGING.md`, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`, `crc/core/README.md`, `crc/cli/README.md`, `tests/README.md`)**:
   - **Fixed in review**: moved `BUG-363` (`Risk = high`) in `docs/refactor/BUGS.md` from the bottom of the `low` group to the end of the `high` group (right after `BUG-355`) to keep `BUGS.md` sorted by risk per Rule 4, and added exact line citations on branch `logging/fix-handler`.
   - **Fixed in review**: updated `crc/core/README.md` (`hold_header` and held `RUN START` behaviour), `crc/cli/README.md` (`scan.py` `begin()`/`finish()` wrapping), and `tests/README.md` (`test_cli_logging_init.py`).

### BLOCKING

None (all issues fixed in-scope above).

### NIT

None. No issues in do-not-edit files (`AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `docs/refactor/PLAN.md`, `.cursor/**`, `tests/archtest/**`).

### Gates & test count

- `python3 -m ruff check .`: passed
- `python3 -m mypy`: passed (228 source files)
- `lint-imports`: passed (5 contracts kept, 0 broken)
- `python3 -m pytest -q`: 42438 passed (42432 at `df0c911`, 42437 after coder, 42438 after review)

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff df0c911..HEAD` on `logging/fix-handler` (head `a16045a` before this round).

### Round 1 fixes and findings re-checked

- `self.attached and` guard in `RunLog._emit_header` (`crc/core/logger.py:94`): correct; an
  unattached `RunLog` stays silent (`tests/test_core_logger.py` calls `hold_header` on one).
- BUG-363 moved into the `high` group of `docs/refactor/BUGS.md` (after BUG-355): correct; counts
  line (`high 27`, 363 rows) matches. Its line citations were wrong, though (see Fixed below).
- README updates (`crc/core/README.md` `hold_header`, `crc/cli/README.md` scan wrap,
  `tests/README.md` new test file) and `test_scan_logs_run_start_and_result_per_cloud`: correct.

### Independent checks

1. Behaviour: the only code changes are `crc/core/logger.py` (named logger `crc.run`, held
   header, REGION level), `crc/cli/dispatch.py` (three spot warnings through `_LOG`, same text and
   level) and `crc/cli/scan.py` (`_scan_cloud` wraps the unchanged body, now `_scan_and_report`,
   in `begin()`/`finish()` with try/finally). Nothing selected, deleted, stopped, posted or written
   changes; no CLI flag, no exit code (scan still `sys.exit(0)` from `run_scan`). The scan parity
   case now asserts `new_scan == oracle_scan` in addition to the two run lines.
2. Other module-level `logging.*` calls before `init_logging` in the CLI path: none left. Grep of
   `crc/`: `crc/cli/*` and `crc/service.py` have none; `crc/utils.py:45` runs after the handlers
   are added; `crc/notify/slack.py:205` (`logging.exception`) is reached only after a cleaner (and
   so `init_logging`) exists (dispatch builds the cleaner first; scan calls `_get_vm` before any
   Slack call). An AST check of every `__init__` in `crc/` found no `logging.*` call before
   `super().__init__()` / `Service.__init__`.
3. Formats and levels: RUN START / REGION / ACTION / SKIPPED / ERRORS / FAILED / RESULT text,
   key order and levels unchanged except REGION, which is DEBUG only when `selected == 0`.
   Skipped regions never call `region()` in any lane (checked every `region_skipped(` call site:
   AWS `_regionlog`, disk `region_guard`, spot requests, VM, VPC, EIP, keypairs, snapshot, KMS;
   GCP spot; OCI disk/VM/spot), so a skipped region never logs a DEBUG REGION line, and
   `regions=` still counts every walked region. `operation=scan` is documented in
   `docs/refactor/LOGGING.md`.
4. Regression test `tests/test_cli_logging_init.py` covers what the task asks (bare root logger,
   fake cleaner calling `crc.utils.init_logging`, only FileHandler + StreamHandler(INFO) left,
   no duplicated stderr line, no `LEVEL:name:` line, RUN START once before REGION and RESULT,
   no-handler run emits RUN START then RESULT). Verified it fails on the pre-fix code: with
   `crc/core/logger.py` and `crc/cli/dispatch.py` from `df0c911`, all 4 tests fail; with the
   branch code all pass.
5. Parity: `git diff df0c911..HEAD -- tests/legacy` is empty; no `expected_logs` rule changed or
   added; test edits only lower the REGION `selected=0` expectations to DEBUG (core, GCP IP,
   OCI VM) and add the scan run lines as an exact assertion. No global loosening.
6. Safety lock: `tests/archtest/**` (incl. `safety_lock.json`) and the five locked tests untouched.
   `AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `.cursor/**` untouched.
7. ARCH-002: the only new public name is `RunLog.hold_header` (documented in
   `crc/core/README.md`); `_LOG`, `_emit_header`, `_scan_and_report` are private.

### Fixed in this round

- `docs/refactor/BUGS.md` BUG-363 row: the citations `crc/core/logger.py:86`, `:173` and
  `crc/cli/dispatch.py:92,104,110,173` matched neither the pre-fix nor the fixed code (on the
  branch head `logger.py:86` is `self._header = None` and `:173` is a parameter line). Now cites
  the introducing lines at `df0c911` (`logger.py:85,173`, `dispatch.py:92,104,110,171`) and the
  fixed lines on the branch (`logger.py:32,88-101,190`, `dispatch.py:12,94,106,112`).
- `docs/refactor/STATUS.md:57`: LOG-FIX status now reads `Round 1 (Argon) APPROVE, Round 2
  (Opus) APPROVE`.

### BLOCKING

None.

### NIT

- `crc/cli/scan.py:9` imports the private `crc.cli.dispatch._run_options` across modules.
  Proposed: rename it `run_options` (and list it in `crc/cli/README.md` Public API) in a later
  rung; harmless here and kept to stay in scope.
- Out of scope, exists on main: `crc/utils.py:20-39` `init_logging` adds a new File/Stream
  handler pair every time a `Service` is built, so a process that builds several cleaners
  (`--cloud all`, several `--resource` values, Azure resource-group loop) prints each line once
  per cleaner built so far. Not a LOG-FIX regression; proposed as a new BUGS.md row for a later
  behaviour-fix rung (guard: skip adding handlers when root already has init_logging handlers).
### Gates and test count

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`: ruff passed, mypy
strict passed, import-linter 5 kept / 0 broken, `pytest -q` **42438 passed** (42432 at
`df0c911`; same as Round 1, this round adds no test). Exit 0.

ROUND 2 VERDICT: APPROVE
