# Logging contract

How a CRC run logs. Log text and levels are part of the output Jenkins jobs and the dry-run
comparison pipeline read: change them only on purpose, and never change what is selected, deleted,
stopped, posted to Slack or written to InfluxDB through a log change.

## Rules

1. Per-resource diagnostics (age checks, tag and label dumps, "skipping X because Y" per resource,
   KMS key skips, snapshot AMI skips) are `logging.DEBUG`. The console handler is INFO and the
   file handler DEBUG (`crc/utils.py`), so `logs/crc.log` keeps everything.
2. Summary lines (`List of ... deleted`, `Number of ... deleted`, ...) stay at INFO with their text.
3. `crc/core/logger.py` is the per-run collector (stdlib only; imports nothing from a cloud package
   or `crc/cli`). `crc/cli` calls `begin()` before and `finish()` after each cleaner pass, in a
   `try/finally`, once per `--cloud` x `--resource` pair (also for `--cloud all`); the exception of
   a failing cleaner still propagates. The engine bridge does the same for routed kinds.
4. Cleaners call `logger.current().region/action/skip/fail/region_skipped`. They do not change what
   is selected or the order of SDK calls because of logging.
5. The run log writes through the named logger `logging.getLogger("crc.run")`. Never call a
   module-level `logging.log/info/...` on a path that can run before `init_logging`: on a root
   logger without handlers it runs `logging.basicConfig()` and duplicates console lines (BUG-363).
   `crc/cli/dispatch.py` uses a module logger for the same reason.
6. `RUN START` is held until logging is initialised (`crc.utils.init_logging` calls
   `logger.logging_ready()` after adding the root handlers, BUG-364), so it comes before any line
   of the cleaner. Fallbacks: the first run-log line emitted after the root logger has handlers
   releases a held header; if the run ends without handlers, `finish()` emits `RUN START` and then
   `RESULT`. The header is printed once.

## Line formats

```
RUN START cloud=<cloud> resource=<resource> operation=<delete|stop> dry_run=<true|false> <key>=<json> ...
REGION cloud=<c> resource=<r> region=<region> scanned=<n> selected=<n> seconds=<int>
ACTION <kind> cloud=<c> resource=<r> region=<region|-> id=<id> name=<name|->
SKIPPED cloud=<c> resource=<r> reason=<reason> count=<n>
ERRORS count=<n>
FAILED cloud=<c> resource=<r> region=<region|-> id=<id> error=<ExceptionType>: <message>
RESULT cloud=<c> resource=<r> operation=<op> dry_run=<true|false> selected=<n> done=<n> failed=<n> skipped=<n> regions=<n> regions_skipped=<n> duration_s=<int>
```

- `RUN START` options: each non-empty one of `age`, `filter_tags`, `exception_tags`, `notags`,
  `name_regex`, `exception_regex`, `custom_age_tag_key`, `resource_group`, `project_id`, in that
  order, as `json.dumps(value, sort_keys=True, separators=(",", ":"))`.
- `REGION` is INFO, or DEBUG when `selected=0` (the region was walked but selected nothing; a
  skipped region never logs one). It still counts in `regions=`.
- `--scan_tag` (`crc/cli/scan.py`) is wrapped like a cleaner pass: one `RUN START` / `RESULT` pair
  per cloud with `resource=vm operation=scan dry_run=true`; `RESULT` shows `selected=0 done=0`.
- `ACTION` kind is one of `would_delete`, `deleted`, `would_stop`, `stopped`; one line per resource
  acted on (or that would be in a dry run).
- `RunLog.skip(reason)` counts a skipped resource under a short snake_case reason (`too_young`,
  `exception_tag`, `used_by_ami`, `not_ours`, `state`, ...); nothing is logged per skip.
- `RunLog.region_skipped(region, error)` counts a region skipped for a connectivity error; the
  existing warning line stays.
- `RunLog.fail(region, resource_id, error, logged=False)` records a failure; the per-resource error
  line stays where it is. Pass `logged=True` when the caller has just logged a WARNING or ERROR line
  naming the resource (id, name, zone or region): the failure is then counted but gets no `FAILED`
  line, so each failure is printed once (BUG-410). Without that line, leave the default: the
  `FAILED` line is then the only place the resource id appears.
- `FAILED` is always one line (BUG-365): the message is split on line breaks, parts stripped, empty
  parts dropped, the rest joined with ` | `.
- `finish()` logs, in order: one INFO `SKIPPED` line per reason sorted by reason; if there are
  failures, ERROR `ERRORS count=<n>` (every failure) and one ERROR `FAILED` line per failure not
  recorded with `logged=True`, in order; then always INFO `RESULT`.
- `RESULT`: `selected` = ACTION lines, `done` = ACTION lines of kind `deleted` or `stopped` (0 in
  a dry run), `failed` = `fail()` calls, `skipped` = sum of skip counts, `regions` = `region()`
  calls, `regions_skipped` = `region_skipped()` calls. Key order and spelling are fixed; the
  dry-run comparison pipeline parses this line.
- `current()` returns the current run, or a fresh unattached `RunLog` when no run began: it counts
  but logs nothing, so cleaners and tests never crash or log extra lines.
- Time comes from an injectable clock (default `time.monotonic`).

## Testing log output

Parity tests compare `(logger, levelno, message)` with the frozen oracle through
`tests/helpers_log_fix.py` (`expected_logs(oracle_logs, bugs)` rewrites the oracle log list, one
rule per bug ID; cloud-specific rules live in `tests/log_fix_<lane>.py`). A rule may only change a
level or insert the documented new lines; it never drops an oracle line or changes its text. The
new lines above are matched by a separate exact assertion. A locked safety test that asserts a log
level of these lines is not edited; put the proposed change in the PR description for the owner.
