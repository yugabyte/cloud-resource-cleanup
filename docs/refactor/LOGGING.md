# Logging contract (rung family LOG)

Owner-approved 2026-10-22. The LOG rungs (`LOG-0`, `LOG-AWS1`, `LOG-AWS2`, `LOG-AZU`,
`LOG-GCP`, `LOG-OCI`, listed in `docs/refactor/PLAN.md`) change log text and levels only.
They never change what is selected, deleted, stopped, posted to Slack or written to
InfluxDB, add no CLI flag and keep every exit code. Bug rows: `BUG-356` to `BUG-362` in
`docs/refactor/BUGS.md` (plus `BUG-353`, OCI tag dict level only). Rung `LOG-FIX` fixes `BUG-363` (the run log must not run
`logging.basicConfig()`) and adjusts the REGION level and the `--scan_tag` path.

## Why

One Jenkins run of the real job prints about 34,000 lines. About 19,000 are per-resource
age-check lines (main `crc/service.py:231,252,259,263,294,297`, now `crc/core/age.py`),
about 4,200 are AWS KMS "KEY SKIPPED" warnings (main `crc/aws/kms.py:181-182`), over 1,000
are raw tag or label dumps (main `crc/aws/vm.py:222`, `crc/azu/disk.py:113`,
`crc/azu/vm.py:140`, `crc/gcp/disk.py:120`, `crc/gcp/vm.py:253`, `crc/oci/disk.py:145`,
`crc/oci/vm.py:117`; the OCI age-check dict is BUG-353) and about 840 are per-snapshot
"used by AMI" lines (main `crc/aws/snapshot.py:79`). There is no run header, no per-region
progress or timing, summaries differ per cleaner, and per-resource errors are logged inline
and swallowed with no end-of-run list.

## Bugs

| ID | What |
|----|------|
| BUG-356 | per-resource age-check lines at INFO/WARNING flood the console (core age helpers) |
| BUG-357 | AWS KMS logs a WARNING `KEY SKIPPED <id>` for every key it cannot read or that is not ours, from a bare `except:` (severity medium) |
| BUG-358 | raw tag/label dicts of every listed resource are logged at INFO (cross-reference BUG-353) |
| BUG-359 | AWS snapshots log one INFO line per snapshot skipped because an AMI uses it |
| BUG-360 | no run header and no single machine-readable result line |
| BUG-361 | no per-region progress or timing |
| BUG-362 | per-resource errors are logged where they happen and swallowed; no end-of-run list or count |
| BUG-363 | the run log called module-level `logging.*`, which runs `basicConfig()` on a root logger without handlers (the CLI calls `begin()` before `init_logging`) and floods the console with duplicated lines (introduced by LOG-0; severity high) |

## Rules

1. Per-resource diagnostics (age checks, tag/label dumps, "skipping X because Y" for each
   resource, KMS key skips, snapshot AMI skips) move to `logging.DEBUG` with the same text.
   The console handler stays at INFO and the file handler at DEBUG (`crc/utils.py`), so
   `logs/crc.log` keeps everything.
2. The existing summary lines (`List of ... deleted`, `Number of ... deleted`, ...) stay
   exactly as they are, at INFO (or the level they have today).
3. `crc/core/logger.py` is the per-run collector (pure stdlib; imports nothing from a cloud
   package or `crc.cli`). `crc/cli` calls `begin()` before and `finish()` after each cleaner
   it runs, in a `try/finally`, once per `--cloud` x `--resource` pass (so also for
   `--cloud all`), and the exception of a failing cleaner still propagates.
4. Cloud lanes call `logger.current().region/action/skip/fail/region_skipped` from their own
   code, and move their per-resource lines to DEBUG. They do not change what is selected or
   the order of SDK calls.

## Line formats

The run log writes through the named logger `logging.getLogger("crc.run")`, which propagates to
the root logger. It never calls a module-level `logging.log/info/...` function: on a root logger
without handlers those run `logging.basicConfig()`, which adds a stderr handler without level
(BUG-363). The same holds for every log call that can run before `init_logging` in the CLI path
(`crc/cli/dispatch.py` uses a module logger). `crc/cli` calls `begin()` before any cleaner is
built, and logging is only initialised when a cleaner is built (`Service.__init__`), so
`RUN START` is held until the first run-log line is emitted after the root logger has handlers
and is then emitted once, before that line. When the run ends without root handlers, `finish()`
still emits `RUN START` and then `RESULT` through the named logger.

```
RUN START cloud=<cloud> resource=<resource> operation=<delete|stop> dry_run=<true|false> <key>=<json> ...
REGION cloud=<c> resource=<r> region=<region> scanned=<n> selected=<n> seconds=<int>
ACTION <kind> cloud=<c> resource=<r> region=<region|-> id=<id> name=<name|->
SKIPPED cloud=<c> resource=<r> reason=<reason> count=<n>
ERRORS count=<n>
FAILED cloud=<c> resource=<r> region=<region|-> id=<id> error=<ExceptionType>: <message>
RESULT cloud=<c> resource=<r> operation=<op> dry_run=<true|false> selected=<n> done=<n> failed=<n> skipped=<n> regions=<n> regions_skipped=<n> duration_s=<int>
```

- `RUN START` options: each non-empty one of `age`, `filter_tags`, `exception_tags`,
  `notags`, `name_regex`, `exception_regex`, `custom_age_tag_key`, `resource_group`,
  `project_id`, in that order, as `json.dumps(value, sort_keys=True, separators=(",", ":"))`.
- `REGION` is logged at INFO, or at DEBUG when `selected=0` (a region that was walked but
  selected nothing; a skipped region never logs one). It is still counted in `regions=`.
- `--scan_tag` (`crc/cli/scan.py`) is wrapped like a cleaner pass: one `RUN START` / `RESULT`
  pair per cloud with `resource=vm operation=scan dry_run=true` (scan enforces a dry run). It
  selects and deletes nothing, so `RESULT` shows `selected=0 done=0`.
- `ACTION` kind is one of `would_delete`, `deleted`, `would_stop`, `stopped`; one line per
  resource acted on (or that would be in a dry run).
- `RunLog.skip(reason)` counts a skipped resource under a short snake_case reason (for
  example `too_young`, `exception_tag`, `used_by_ami`, `not_ours`, `state`); nothing is
  logged per skip.
- `RunLog.region_skipped(region, error)` counts a region skipped for a connectivity error;
  the existing warning line stays.
- `RunLog.fail(region, resource_id, error)` records a failure; the existing per-resource
  error line stays where it is.
- `finish()` logs, in this order: one INFO `SKIPPED` line per reason sorted by reason; if
  there are failures, ERROR `ERRORS count=<n>` and one ERROR `FAILED` line per failure in
  order; then always INFO `RESULT`.
- `RESULT`: `selected` = ACTION lines, `done` = ACTION lines of kind `deleted` or `stopped`
  (0 in a dry run), `failed` = `fail()` calls, `skipped` = sum of skip counts, `regions` =
  `region()` calls, `regions_skipped` = `region_skipped()` calls. Key order and spelling are
  fixed; the dry-run comparison pipeline parses this line.
- `current()` returns the current run, or a fresh unattached `RunLog` when no run began:
  it counts but logs nothing, so cleaners and tests never crash and log nothing extra.
- Time comes from an injectable clock (default `time.monotonic`).

## Parity-test rule

Oracles in `tests/legacy/*.txt` stay frozen. Parity tests compare `(logger, levelno,
message)` of the new code with the oracle through `tests/helpers_log_fix.py`:
`expected_logs(oracle_logs, bugs)` rewrites the oracle log list deterministically, one rule
per bug ID (for example BUG-356: the age-check messages become `logging.DEBUG`). That file
holds the core rules only; each cloud lane keeps its rules in `tests/log_fix_<lane>.py`
(`RULES = {**helpers_log_fix.RULES, ...}`) and passes them as `expected_logs(..., rules=RULES)`,
so no lane edits the shared helper and the lane branches merge without conflicts. A rule may
only change a level or insert the documented new lines; it never drops an oracle line or
changes its text. Each parity test asserts `new_logs == expected_logs(oracle_logs,
{<bug IDs this lane fixes>})` and `oracle_logs != new_logs` for at least one case per bug.
The new `RUN START`, `REGION`, `ACTION`, `SKIPPED`, `ERRORS`, `FAILED` and `RESULT` lines
are matched by a separate exact assertion, not by dropping them. Every non-log part of the
outcome (calls, call order, results, stdout, errors) stays exactly equal to the oracle.
Locked safety tests (`tests/archtest/safety_lock.json`) are never edited; if one asserts a
log level of these lines, the proposed change goes under `## Owner action` in the review
file.
