## Coding summary

Rung LOG-FIX2 (owner-approved 2026-10-23; contract `docs/refactor/LOGGING.md`). Fixes the two
log-only regressions of the Jenkins dry run `crc-dry-run-compare` build 5 (A main 544441b vs B
8073087). Log text only: nothing selected, deleted, stopped, posted or written differs, no CLI
flag, no exit code, no oracle, no locked safety test edited.

Bug IDs handled: BUG-364 (RUN START printed when logging is initialised), BUG-365 (FAILED line
always one line). Both new rows, exist on main: no, severity low, status "fixed in LOG-FIX2".

Files changed:

- `crc/core/logger.py`: new public `logging_ready()` (emits the current run's held RUN START;
  no-op without a current run or held header; idempotent) backed by `RunLog.release_header()`.
  The existing fallbacks stay (next run-log line, `finish()`). `_error_text` collapses the
  message of the FAILED line: split on line breaks, strip, drop empty parts, join with ` | `
  (`error=<ExceptionType>: <message>` otherwise unchanged; per-resource error lines untouched).
- `crc/utils.py`: `init_logging` calls `logging_ready()` after the handlers are added (imports
  `crc.core.logger`, allowed by the import-linter contracts; `crc.core` imports nothing new).
- Docs: `docs/refactor/LOGGING.md`, `crc/core/README.md` (Public API), `docs/refactor/BUGS.md`
  (BUG-364, BUG-365 after BUG-363; Counts 365 rows, low 271), `PLAN.md` and `STATUS.md`
  (LOG-FIX2 row).
- Tests: `tests/test_cli_logging_init.py` (fake cleaner logs its own line right after
  `init_logging`; RUN START appears exactly once, before that line and before RESULT, on the
  console and in the file); `tests/test_core_logger.py` (`logging_ready` with no run, with a held
  header called twice, after emission, unattached run; multi-line FAILED message gives one line,
  single-line message unchanged).

Test count (pytest -q): 42438 before (8073087, per `STATUS.md`), 42444 after (6 new tests in `tests/test_core_logger.py`, 0 removed; the CLI test was extended in place). `make check` green.

Left: nothing.

## Round 1 (Argon)

Scope reviewed: `git diff 8073087..HEAD` on branch `logging/fix-header` (rung `LOG-FIX2`).

### Checks

1. **Behaviour & scope (`crc/core/logger.py`, `crc/utils.py`)**:
   - `crc/core/logger.py` adds `logging_ready()` and `RunLog.release_header()` to emit the current run's held `RUN START` line (`BUG-364`). `logging_ready()` is a no-op when there is no current run, no held header, or an unattached run (`self.attached` check in `_emit_header`), and is idempotent when called multiple times. Existing fallbacks (`_log()` releasing on the next `RunLog` call once root handlers exist, and `finish()` forcing emission when still held) remain intact. No module-level `logging.*` call is used in `crc/core/logger.py`.
   - `crc/utils.py` imports `from crc.core import logger as run_log` and calls `run_log.logging_ready()` in `init_logging` after the root handlers are added and noisy loggers are configured, right before the `Logging initialized with file: ...` line. `crc/core` imports nothing new (`lint-imports` keeps all 5 contracts).
   - `crc/core/logger.py` adds `_one_line(text)` (`" | ".join(part for part in (p.strip() for p in text.splitlines()) if part)`) and applies it in `_error_text()` so `FAILED` lines are always a single line while preserving `error=<ExceptionType>: <message>` (`BUG-365`). Per-resource error lines in cloud cleaners are untouched.
   - No change to what is selected, deleted, or stopped, SDK call order, Slack text, InfluxDB points, CLI flags, or exit codes.
2. **Line formats & levels (`crc/core/logger.py`, `docs/refactor/LOGGING.md`)**:
   - `RUN START`, `REGION`, `ACTION`, `SKIPPED`, `ERRORS`, `FAILED`, and `RESULT` formats, key names, key order, and levels match `docs/refactor/LOGGING.md`.
3. **Parity & regression tests (`tests/test_cli_logging_init.py`, `tests/test_core_logger.py`, `tests/test_crc_utils_parity.py`)**:
   - All frozen oracles under `tests/legacy/` are untouched (`git diff 8073087..HEAD -- tests/legacy/` is empty), and no `expected_logs` rules were altered or loosened.
   - `tests/test_cli_logging_init.py` logs `"cleaner own line"` immediately after `init_logging` (before any `RunLog` call) and asserts `RUN START` appears once, before `"cleaner own line"`, before `REGION`, and before `RESULT` both on stderr and in `crc.log`.
   - `tests/test_core_logger.py` adds 6 unit tests covering `logging_ready()` (no current run, held header called twice, called after header already emitted, unattached run) and `FAILED` line formatting (multi-line exception and plain string messages collapsed with ` | `, single-line message unchanged).
4. **Safety lock**:
   - `tests/archtest/safety_lock.json` and all five hash-locked safety test files are untouched.
5. **Docs & READMEs (`crc/core/README.md`, `docs/refactor/LOGGING.md`, `docs/refactor/BUGS.md`, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`)**:
   - `crc/core/README.md` documents `release_header` and `logging_ready()` under `## Public API` (ARCH-002).
   - **Fixed in review**: moved `BUG-364` and `BUG-365` (`Risk = low`) in `docs/refactor/BUGS.md` from after `BUG-363` (`Risk = high`, where `LOG-FIX` review had moved `BUG-363`) to the end of the `low` risk group (after `BUG-362`) so `BUGS.md` stays strictly sorted by risk (`critical` 5, `high` 27, `medium` 62, `low` 271; 365 rows total) per Rule 4; added `BUG-364` and `BUG-365` to the `## Bugs` table in `docs/refactor/LOGGING.md`; updated `LOG-FIX2` state in `docs/refactor/STATUS.md`.

### BLOCKING

None.

### NIT

None. No issues in do-not-edit files (`AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `docs/refactor/PLAN.md`, `.cursor/**`, `tests/archtest/**`).

### Gates & test count

- `python3 -m ruff check .`: passed
- `python3 -m mypy`: passed (228 source files)
- `lint-imports`: passed (5 contracts kept, 0 broken)
- `python3 -m pytest -q`: 42444 passed (42438 at `8073087`, +6 in `tests/test_core_logger.py`)

VERDICT: APPROVE


## Round 2 (Opus)

Scope reviewed: `git diff 8073087..HEAD` on `logging/fix-header` (coder commit `4f01bc6`, Round 1
commit `df754cb`).

### Round 1 fixes and findings

- Round 1 moved BUG-364 and BUG-365 from directly after BUG-363 (`docs/refactor/BUGS.md:63`, risk
  group `high`) to the end of the `low` group (after BUG-362, now lines 395-396). Confirmed
  correct: `BUGS.md` Rules (line 18) put a new row at the end of its risk group. The row text and
  column layout match the task exactly; status `fixed in LOG-FIX2`; Counts line says 365 rows, low
  271.
- Round 1 added BUG-364/365 to the `## Bugs` table of `docs/refactor/LOGGING.md`. That's fine.
- Round 1 reported no BLOCKING or NIT findings. I agree, apart from the test nit below.

### Independent checks

1. **Only log text changed.** `crc/core/logger.py` adds `_one_line`, `RunLog.release_header` and
   `logging_ready()` (lines 50, 98, 203). `crc/utils.py:47` calls `run_log.logging_ready()`
   after the handlers are added and the noisy loggers are set. No cleaner, selection, SDK call,
   Slack, InfluxDB, CLI flag or exit-code code is touched. `_error_text` is used only by the
   FAILED line, so the per-resource error lines and every other line are unchanged.
   `logging_ready()` is a no-op without a current run, after the header was emitted (it is
   idempotent) and for an unattached run (`_emit_header` checks `attached`). The fallbacks stay:
   `_log` releases a held header once the root logger has handlers, and `finish()` forces it out.
   No module-level `logging.*` call was added to `crc/core`. `crc.core` imports nothing new;
   `crc.utils -> crc.core.logger` is allowed by every import-linter contract.
2. **Formats.** RUN START, REGION, ACTION, SKIPPED, ERRORS and RESULT are unchanged. FAILED keeps
   `FAILED cloud=<c> resource=<r> region=<region|-> id=<id> error=<ExceptionType>: <message>` at
   ERROR; the only change is that the message is collapsed to one line (split with
   `str.splitlines`, each part stripped, empty parts dropped, joined with ` | `).
3. **Parity.** `git diff 8073087..HEAD -- tests/legacy` is empty. `tests/helpers_log_fix.py` and
   every `expected_logs` call are unchanged, so there is no loosening. With
   `run_log.logging_ready()` removed from `crc/utils.py`, the extended CLI test
   `tests/test_cli_logging_init.py::test_cli_path_with_init_logging_in_cleaner_adds_no_stray_handler`
   fails at line 106 (RUN START came after `cleaner own line`); with the fix it passes. It also
   asserts that RUN START is printed exactly once and before RESULT, both on the console and in
   the file. The unit tests cover: no current run, a held header with two calls, an already
   emitted header, an unattached run, a multi-line message giving one FAILED line, and a
   single-line message left unchanged.
4. **Safety lock.** `git diff 8073087..HEAD -- tests/archtest` is empty; `safety_lock.json` and
   the five locked tests are untouched. No owner action is needed.
5. **Docs.** `crc/core/README.md` Public API lists `release_header` and `logging_ready()`
   (ARCH-002). `docs/refactor/LOGGING.md`, `PLAN.md` (LOG-FIX2 row, owner-approved 2026-10-23,
   BUG-364 and BUG-365) and `STATUS.md` are updated as the task asks.

### Fixed in review

- `tests/test_core_logger.py:249` (NIT): removed the `_handled_root()` helper, which only returned
  `logging.getLogger()`, and called `logging.getLogger()` inline. The test behaviour is unchanged.
- `docs/refactor/STATUS.md:58`: the LOG-FIX2 state now reads
  `Round 1 (Argon) APPROVE, Round 2 (Opus) APPROVE`.

### BLOCKING

None.

### NIT

None in do-not-edit files (`AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`,
`docs/refactor/PLAN.md`, `.cursor/**`, `tests/archtest/**`).

### Gates & test count

`pip install -r requirements.txt -r requirements-dev.txt`, then `make check`:

- `ruff check .`: passed
- `mypy` (strict): passed, 228 source files
- import-linter: passed, 5 contracts kept
- `pytest -q`: 42444 passed (42438 at `8073087`, +6). Rerun of the touched tests and `tests/archtest`
  after the review edit: 120 passed

ROUND 2 VERDICT: APPROVE
