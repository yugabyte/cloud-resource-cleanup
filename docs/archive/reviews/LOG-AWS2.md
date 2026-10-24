## Coding summary

Rung LOG-AWS2 (AWS KMS, snapshot, keypairs, Elastic IPs, VPC). Log text and levels only: no
selection, deletion, SDK call or order, Slack, Influx, flag or exit code changed.

Files changed:

- `crc/aws/_regionlog.py` (new, private): `start`, `region_done`, `region_skipped`, thin wrappers
  over `crc.core.runlog.current()` with an injectable `clock` for `seconds=`.
- `crc/aws/kms.py`: `KEY SKIPPED <id>` WARNING -> DEBUG, same text (BUG-357); the bare `except
  BaseException` is unchanged, it only counts `skip("unreadable")`, or `skip("not_ours")` when the
  key policy has no `Principal.AWS` (a private `_NotOursError` replaces the `KeyError`/`TypeError`
  of that one lookup; the key is still skipped and a key found earlier stays selected). Also
  `skip("exception_tag")`, `region()`, `action("would_delete"/"deleted")`, `fail()` on a scheduling
  error (the error still propagates), `region_skipped()`.
- `crc/aws/snapshot.py`: `Skipping snapshot X, used by AMI` INFO -> DEBUG and `skip("used_by_ami")`
  (BUG-359); `Snap X has tag K` INFO -> DEBUG (per-resource skip line, contract rule 1);
  `skip("too_young" / "exception_tag")`; `delete_one` (new keyword `region`) reports
  `would_delete` / `deleted` / `fail`; `REGION` per region; `region_skipped`.
- `crc/aws/keypairs.py`: `not old enough` and `in exception_regex` INFO -> DEBUG; skips
  `too_young`, `exception_regex`, `name_regex_mismatch`; `REGION` in `plan_region`;
  `ACTION`/`fail` in `apply_region` (errors still propagate); `region_skipped`.
- `crc/aws/elastic_ips.py`: skips `exception_tag`, `no_filter_match`; `REGION` in `plan_region`;
  `ACTION`/`fail` in `apply_region`; `region_skipped`.
- `crc/aws/vpc/cleaner.py`, `crc/aws/vpc/teardown.py` (new): the teardown helpers moved out of
  `cleaner.py` unchanged (ARCH-004, 400 lines) as `tear_down`; `delete_vpc(..., region=None)`;
  `ACTION deleted` after `delete_vpc` succeeds, `fail()` when the five retries fail (the error lines
  stay) or a teardown step raises (still propagates), `would_delete` in a dry run, `skip("protected")`,
  `REGION`, `region_skipped`.
- READMEs of `crc/aws` and `crc/aws/vpc` (Public API, invariants).
- Tests: `tests/helpers_log_fix.py` (rules BUG-357, BUG-359; BUG-356 also covers the two keypair
  skip lines), `tests/test_aws_{kms,snapshot,keypair}_parity.py` (log comparisons go through
  `expected_logs`, plus explicit oracle != new cases), `tests/test_aws_misc_runlog.py` (new: exact
  `REGION/ACTION/SKIPPED/ERRORS/FAILED/RESULT` lines per cleaner for dry run, real run, failure,
  skipped region, unattached run). `test_aws_elastic_ips`/`vpc` parity needed no change (their
  oracle logs are unchanged).

Bug IDs handled: BUG-357 (fixed), BUG-359 (fixed), BUG-361 and BUG-362 for these five modules
(REGION lines, `region_skipped`, `fail`). The rows are shared with other lanes, so their status in
`BUGS.md` is left to the integrator.

Test count: 42256 before (LOG-0 at 7437ac5), 42284 after (full run: 42283 passed plus the ARCH-002 test fixed afterwards). `make check` steps (ruff, mypy, lint-imports, pytest) green on
Python 3.12.3.

Left / notes:

- `crc/aws/_base.py` and `connectivity.py` are untouched: `get_all_regions` is global (not per
  region) and `log_skipped_region` is shared with the AWS1 lane, so the skipped-region count is
  taken in each module's own `_skip_region` wrapper (no double counting if AWS1 does the same
  in its modules).
- Order inside a region: legacy paths that delete as they go print `ACTION` lines before `REGION`
  (snapshot, VPC); `plan_region` of keypairs and Elastic IPs prints `REGION` before the deletions.
- `Snap X has tag K` (snapshot) and the two keypair skip lines are not in the contract's explicit
  list; moved to DEBUG as per-resource "skipping X because Y" lines (rule 1). Revert the three
  `logging.debug` calls if the owner wants them at INFO.
- `ruff format` is not enforced on the repo (19 files already differ at 7437ac5); not run on the tree.
- No locked safety test asserts these log levels; none was touched.
- New bugs found: none beyond BUG-012/015/056 already recorded (VPC lists protected VPCs as selected
  in plan; `ACTION` lines only count VPCs actually deleted or, in a dry run, listed).

## Round 1 (Argon)

Scope reviewed: `git diff 7437ac5..HEAD` (commits `87ba285`, `aac2d93`).

Findings:

- None (0 blocking, 0 nit).
- Verified (1) behaviour and safety: only log levels and `crc.core.runlog` / `crc.aws._regionlog` calls changed across `crc/aws/{kms,snapshot,keypairs,elastic_ips}.py` and `crc/aws/vpc/{cleaner,teardown}.py`. Selection, deletion, SDK call order, Slack messages, InfluxDB points, CLI flags and exit codes are unchanged. In `kms.py`, the bare `except BaseException` behaviour is preserved (`KEY SKIPPED <id>` moved from `WARNING` to `DEBUG` with identical text, and `skip("not_ours")` vs `skip("unreadable")` distinguished via private `_NotOursError` on missing `Principal.AWS`). In `snapshot.py`, `Skipping snapshot <id>, used by AMI` and `Snap <id> has tag <key>` moved from `INFO` to `DEBUG` with `skip("used_by_ami")` / `skip("exception_tag")` / `skip("too_young")`. In `keypairs.py`, per-keypair skip lines moved from `INFO` to `DEBUG`. `crc/aws/_base.py`, `crc/aws/connectivity.py`, `crc/core`, `crc/cli`, other cloud packages, and `docs/refactor/{BUGS,STATUS,PLAN}.md` are untouched.
- Verified (2) contract line formats: `REGION`, `ACTION` (`would_delete` / `deleted`), `SKIPPED`, `ERRORS`, `FAILED` and `RESULT` lines match `docs/refactor/LOGGING.md` key names, order and log levels across dry runs, real runs, failures and skipped regions (`tests/test_aws_misc_runlog.py`).
- Verified (3) parity tests and oracles: `tests/legacy/*.txt` are untouched; `tests/helpers_log_fix.py` adds `BUG-357` and `BUG-359` rules (and extends `BUG-356` for the two per-keypair skip lines) that rewrite log levels only without dropping or altering message text; parity tests assert `new.logs == expected_logs(old.logs, LOG_BUGS)`, `old.logs != new.logs` on diverging cases, and exact equality on all non-log fields.
- Verified (4) locked safety tests: `tests/archtest/safety_lock.json` and all locked safety test files are untouched.
- Verified (5) documentation and architecture guardrails: `crc/aws/README.md` and `crc/aws/vpc/README.md` document the new public symbols (`start`, `region_done`, `region_skipped` in `crc/aws/_regionlog.py`; `tear_down` in `crc/aws/vpc/teardown.py`), satisfying ARCH-001..005.

Gates (`pip install -r requirements.txt -r requirements-dev.txt && make check`):

- `ruff check`: passed
- `mypy`: passed (strict, 121 source files)
- `lint-imports`: passed (3 contracts kept, 0 broken)
- `pytest -q`: `42284 passed` (up from 42256 at `7437ac5`)

VERDICT: APPROVE


## Round 2 (Opus)

Scope reviewed: `git diff 7437ac5..HEAD` (commits `87ba285`, `aac2d93`, `1fb7fb0`), with round 1's
findings re-checked. Round 1 reported no findings. Its checks (1) to (5) hold, apart from the
count bug below, which round 1 missed.

Fixed in this round (commit "LOG-AWS2 round 2: do not count a KMS key that stays selected as
skipped"):

- BLOCKING (fixed) `crc/aws/kms.py:199-207`: `plan_keys` counted `skip("not_ours")` /
  `skip("unreadable")` for every key that reached the bare `except`. That includes a key that the
  first statement had already appended to `active` for `kms_user` before a later statement without
  `Principal.AWS` raised (parity case `policy-match-then-statement-without-aws-stays-selected`).
  That key still gets an `ACTION would_delete` / `deleted` line, so one resource was counted in
  both `selected` and `skipped` of `RESULT`, against the contract ("skip counts a skipped
  resource"). The fix counts the skip only when `active` did not grow for that key. The
  `KEY SKIPPED <id>` DEBUG line, the bare-except behaviour, the selection and the SDK calls are
  unchanged. New test:
  `tests/test_aws_misc_runlog.py::test_kms_key_selected_before_the_error_is_not_counted_as_skipped`.

Open findings (not fixed; none of them change behaviour):

- NIT `tests/helpers_log_fix.py:23`: `Keypair <name> is in exception_regex ...` is not an
  age-check line, but it is rewritten under BUG-356 ("per-resource age-check lines"). The same goes
  for `tests/helpers_log_fix.py:28`: `Snap <id> has tag <key>` is rewritten under BUG-359, whose
  row covers only "used by AMI". Moving both lines to DEBUG is right (contract rule 1, "skipping X
  because Y" lines), but the bug tags are stretched. Proposed for the integrator (in `BUGS.md`,
  which this lane can't edit): either widen the BUG-356 and BUG-359 rows, for example "BUG-359 AWS
  snapshots log one INFO line per snapshot skipped (used by an AMI or carrying an exception tag)",
  or add a row "per-resource skip-reason lines at INFO (AWS keypair exception_regex, AWS snapshot
  exception tag)" and move the two patterns to its own rule. I left the shared BUG-356 rule alone
  to avoid a merge conflict with other LOG lanes. Both patterns are anchored (`^Keypair `,
  `^Snap `), so other lanes' oracles are not affected.
- NIT `crc/aws/snapshot.py:179` (the same pattern exists in the Cleaner `apply` of keypairs and
  Elastic IPs): a region that `plan()` reported with `REGION` and that then hits a connectivity
  error in `apply()` is also counted by `region_skipped`, so `regions + regions_skipped` can exceed
  the number of regions. In the same way, a delete that raises a connectivity error is recorded by
  `fail()` and the region loop then counts it again with `region_skipped`. Both are rare and
  correct line by line. Note this for the dry-run comparison pipeline; no change proposed.
- NIT `crc/aws/vpc/cleaner.py:340`: in a dry run, `ACTION would_delete` lists protected VPCs too,
  because `main` does not decide protection in a dry run (BUG-012, BUG-015, BUG-056; already in
  the coding summary). So for the same account state, a dry run's `selected` can be higher than a
  real run's `done`. This is expected; the comparison pipeline should allow for it on `aws/vpc`.
- NIT KMS: keys rejected by `filter_tags`, state, description, age or a different `kms_user` are
  not counted by any skip reason, so `selected + skipped < scanned`. The contract does not ask for
  every skip to be counted; recorded for the owner.
- NIT `tests/test_aws_keypair_parity.py:220`: the `get_available_regions` lambda was reformatted
  onto one line, a test-only change unrelated to the rung. Harmless.

Checks:

1. Log text, levels and documented runlog calls only. No change to selection, deletion,
   stop, SDK call order, Slack, InfluxDB, CLI flags or exit codes. Every new `try/except` around
   an SDK call re-raises the same exception after `fail()`. The VPC teardown move to `teardown.py`
   is byte-identical in behaviour (same helpers, same order). `delete_one` and `delete_vpc` gained
   only keyword-only `region` arguments with a `None` default.
2. Formats: `REGION`, `ACTION`, `SKIPPED`, `ERRORS`, `FAILED` and `RESULT` are produced only by
   `crc.core.runlog`, so their key names, order and levels are the contract's. The lane passes
   valid kinds (`would_delete`, `deleted`) and snake_case reasons. Exact lines are asserted in
   `tests/test_aws_misc_runlog.py`.
3. Parity: `tests/legacy/` is untouched. Every log divergence goes through `expected_logs` with an
   in-scope ID (BUG-357 for KMS, BUG-359 for snapshots, BUG-356 for keypairs; see the first NIT
   above). The rules only change the level, never drop or rewrite an oracle line. Each bug has an
   `oracle != new` case. Calls, results, stdout and errors are still compared exactly, with no
   global loosening.
4. `tests/archtest/safety_lock.json` and the five locked safety tests are untouched. None of them
   mentions `KEY SKIPPED`, `used by AMI` or the keypair lines.
5. ARCH-002: `crc/aws/README.md` documents `_regionlog.start/region_done/region_skipped`, and
   `crc/aws/vpc/README.md` documents `tear_down`. `crc/core`, `crc/cli`, other cloud packages,
   `crc/aws/_base.py`, `crc/aws/connectivity.py` and `docs/refactor/{BUGS,STATUS,PLAN}.md` are
   untouched.

Gates (`pip install -r requirements.txt -r requirements-dev.txt && make check`, Python 3.12, after
the fix):

- `ruff check`: passed
- `mypy`: passed (`Success: no issues found in 210 source files`)
- `lint-imports`: passed (5 kept, 0 broken)
- `pytest -q`: `42285 passed` (42284 after round 1 plus the new KMS test; 42256 at `7437ac5`);
  `make check` exit 0

ROUND 2 VERDICT: APPROVE

## Rule move

The lane rules (BUG-357, BUG-359 and the keypair skip-line patterns of BUG-356) moved from
`tests/helpers_log_fix.py` to `tests/log_fix_aws_misc.py` (`RULES = {**helpers_log_fix.RULES, ...}`,
with the lane's own `BUG-356` entry applying the core rule and then the keypair patterns), after
`logging/core` (`7c27986`) made `expected_logs` / `expected_outcome` take `rules`. The KMS, snapshot
and keypair parity tests pass `rules=RULES`; `tests/helpers_log_fix.py` and
`tests/test_log_fix_helpers.py` are identical to `origin/logging/core`, so lane branches no longer
conflict there. The rewriter unit test moved to `tests/test_log_fix_aws_misc.py` (plus tests for the
BUG-356 extension). No production code changed.
