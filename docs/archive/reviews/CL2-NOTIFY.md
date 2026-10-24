## Coding summary

Lane CL2-NOTIFY (test-only): `tests/test_notify_parity.py` no longer runs the current legacy CLI
notify code. No production file, oracle, locked test or other test file was edited.

- **What is routed.** Every Slack and Influx assertion of the file now reaches the notifiers the
  way production does. `tests/helpers_cl2_notify.py` builds the real `crc.cli.runner.CRC` only as the
  context object, asks `crc.cli.bridge.build_notifiers` for `SlackNotifier` / `InfluxNotifier`, and
  `routed_run` lets `crc.core.engine.run` build the report from a stub cleaner's plan and result
  (`Report.from_plan` / `from_result`: related NIC and disk sections, the Azure VM `slack_ids`,
  `link_names` on disks, by-owner pings). `routed_call` runs a bare notifier call (user lookup,
  group list, one `InfluxNotifier.write`) where the oracle method has no engine counterpart.
- **Legacy side.** `tests.helpers_oracle.load_legacy_crc()` (`tests/legacy/crc_main.txt`) replaces
  `crc.py` loaded from the working tree. `tests/legacy_crc_notify_reference.txt` is no longer read by
  this file (other docs still mention it; I did not touch them).
- **Adapters deleted or shimmed.** None; nothing under `crc/` changed.
- **How the parity tests reach the cleaners.** They do not drive any cleaner: the stub cleaner
  returns the ids of each case, so only the report and notifier path is compared. The CRC
  `notify_*`, `ping_on_slack`, `write_influxdb`, `slack_lookup_user_by_email`,
  `get_user_groups_list` and `get_msg` methods are no longer called on the current `CRC`.
- **Run-log lines.** This file asserts none of RUN START, REGION, ACTION, SKIPPED, ERRORS, RESULT
  (the engine is called directly, not `run_routed`), so no frozen run-log data was needed.
- **Inputs the engine cannot produce.** The oracle outcome is asserted against frozen data
  (`UNROUTED` in the test file, plus explicit values in the tests):
  - `disk-tuple`, `disk-none` (silently not notified), `vm-without-ids` (`TypeError ... has no len()`);
  - `get_msg(..., None)` (`TypeError: object of type 'NoneType' has no len()`), compared to
    `crc.core.report.get_msg` for every list case (text plus the printed newline);
  - bad owner groups (`None`, `int`, a list): oracle error and the single `usergroups_list` call are
    asserted as before; the routed run fails earlier, when the `Plan` freezes the groups, with a
    `TypeError`/`AttributeError`, no Slack call and no output (fail closed, unreachable from real
    cleaners). The exact routed message differs from the oracle's, so only its type is asserted.
- **Fixed-bug divergences.** None found: every compared case is equal to the oracle. BUG-028,
  BUG-030, BUG-033, BUG-034 quirks stay visible in the equal outcomes (asserted as before).
- **Test count.** Before 44400 (995 in this file), after 44400 (995 in this file); floor unchanged,
  `make raise-test-floor` not needed. `make check` green.
- **Owner actions.** None. Once no other test reads `tests/legacy_crc_notify_reference.txt` it can
  be removed by an owner (it is a hash-locked/oracle-style file I must not edit).
- **Lint note.** The only place left that names the current CRC is `context_crc` in the helper,
  as the context object the bridge reads.

## New bugs

None.

## Round 1 (Argon)

Scope reviewed: `git diff 2bd8e67..HEAD` on branch `cl2/notify` (lane `CL2-NOTIFY`).

### Findings & fixes made in review

1. **Tightened assertions in `tests/test_notify_parity.py`**:
   - `test_ping_on_slack_bad_input_matches_legacy`: asserted the exact `routed_expected` `(type_name, message)` tuple per parametrized bad input (`TypeError: 'NoneType' object is not iterable`, `TypeError: 'int' object is not iterable`, `AttributeError: 'list' object has no attribute 'items'`) instead of `routed_error[0] in {"TypeError", "AttributeError"}`.
   - `test_slack_and_user_lookups_match_legacy`: wrapped both the oracle (`crc.slack_lookup_user_by_email` / `crc.get_user_groups_list`) and bridge-notifier (`n["slack"].lookup_user_by_email` / `n["slack"].user_groups`) calls in `print(...)` so the returned lookup values are also compared via `stdout`, not only the mock calls and error stdout.
   - `test_engine_with_notifiers_matches_legacy_section_and_owner_flows` and `test_engine_with_notifiers_matches_legacy_on_disk_owner_pings`: added `routed_run(...) == old` assertions alongside the direct `(SlackNotifier, InfluxNotifier)` engine wiring assertions.

### Checklist verification

1. **No legacy CLI imports or calls**: Grepped `tests/test_notify_parity.py` and `tests/helpers_cl2_notify.py`. Neither file imports `crc.cli.resource_ops`, `crc.cli.vm_ops`, `crc.cli.notify`, `crc.cli.adapter_args`, `crc.cli.report_calls`, or `tests.helpers_legacy_handlers`, and neither calls any `delete_*`, `stop_*`, `notify_*`, `ping_on_slack`, `write_influxdb`, `slack_lookup_user_by_email`, `get_user_groups_list`, or `get_msg` method on the current `crc.cli.runner.CRC` (`CRC` is constructed only as the context object passed to `crc.cli.bridge.build_notifiers`).
2. **Parity comparisons & case count**: All 995 test cases in `tests/test_notify_parity.py` are preserved and run the legacy side against `tests.helpers_oracle.load_legacy_crc()`. Every routable Slack and Influx case is compared against `crc.cli.bridge.build_notifiers` (`SlackNotifier` / `InfluxNotifier`) driven through `crc.core.engine.run` (`routed_run`) or direct notifier calls (`routed_call`), and unroutable inputs (`disk-tuple`, `disk-none`, `vm-without-ids`, `get_msg(..., None)`, non-iterable `by_owner` values) are asserted against frozen expected outcomes. Documented quirks (`BUG-028`, `BUG-030`, `BUG-031`, `BUG-033`, `BUG-034`, `BUG-041`, `BUG-042`) remain explicitly verified.
3. **Mutation check (non-vacuous)**: Tested two local mutations (reverted without committing):
   - Mutated `crc/core/report.py::get_msg` Slack message text -> 343 of 995 tests in `tests/test_notify_parity.py` failed immediately.
   - Mutated `crc/notify/influx.py::InfluxNotifier._emit` tag `"resource"` -> 113 of 995 tests in `tests/test_notify_parity.py` failed immediately.
4. **Scope & protected files**: Only `tests/test_notify_parity.py`, `tests/helpers_cl2_notify.py`, and `docs/archive/reviews/CL2-NOTIFY.md` are modified (`git diff 2bd8e67..HEAD`). No production code (`crc/**`), other tests, hash-locked safety tests, `tests/legacy/*.txt`, or `tests/archtest/**` files were touched.

### Gates & test count

Ran `pip install -r requirements.txt -r requirements-dev.txt` followed by `make check`:

- `python3 -m ruff check .`: passed
- `python3 -m mypy`: passed (365 source files)
- `lint-imports`: passed (5 contracts kept, 0 broken)
- `python3 -m pytest -q -n auto`: **44400 passed in 57.16s** (`tests/test_notify_parity.py`: 995 passed in 2.84s; `make check` total 78.0s)
- `python3 -m tests.archtest.test_count check`: `ARCH-008: 44400 tests, floor 44400`

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 2bd8e67..HEAD` (`de3a2b6` coder, `52265ae` round 1). No code changes made in this round.

### Round 1 fixes re-checked

- `test_ping_on_slack_bad_input_matches_legacy` now pins the exact routed `(type, message)` per bad input
  next to the unchanged oracle assertions (error, single `usergroups_list` call, empty stdout): correct,
  stricter than the coder's type-only check.
- `test_slack_and_user_lookups_match_legacy` prints the lookup return values on both sides, so the
  returned value is compared, not only mock calls: correct (the old `assert_same` compared only
  calls/stdout/error, so this is a strengthening).
- The two engine wiring tests additionally assert `routed_run(...) == old` (bridge-built notifiers)
  next to the hand-wired `SlackNotifier`/`InfluxNotifier` check: correct.

### Independent checks

1. **No legacy CLI code.** `rg` over `tests/test_notify_parity.py` and `tests/helpers_cl2_notify.py`: no
   import of `crc.cli.resource_ops`, `vm_ops`, `notify`, `adapter_args`, `report_calls`,
   `tests.helpers_legacy_handlers`, no load of `crc.py` or `tests/legacy_crc_notify_reference.txt`. The
   only `notify_*` calls left (`test_notify_parity.py:319`, `:666`, `:731`) run on the oracle instance
   inside `run(legacy, ...)`. The current `crc.cli.runner.CRC` is only built in
   `helpers_cl2_notify.context_crc` as the bridge's context object.
2. **Comparisons and counts.** Old file at `2bd8e67` and new file both collect and pass **995** cases.
   Every former `assert_same(legacy, current, ...)` is now oracle (`load_legacy_crc`) vs routed
   (`build_notifiers` + `engine.run` with a stub cleaner, or a bare notifier call), with the same
   outcome tuple (Slack calls, normalised Influx calls, stdout, error). The three inputs the engine
   cannot produce (`disk-tuple`, `disk-none`, `vm-without-ids`) and `get_msg(..., None)` are asserted
   oracle == frozen data (`UNROUTED`), not derived from the routed side; the list `get_msg` cases are
   compared to `crc.core.report.get_msg`. `test_ping_uses_overridden_lookups` moved from overriding CRC
   methods to injecting the lookups into `SlackNotifier.ping_owners`: same assertion (`<!subteam^S9>`).
   No run-log lines (RUN START/REGION/ACTION/SKIPPED/ERRORS/RESULT) were asserted before (checked the
   old file), so none needed freezing. No `fixed in` BUGS.md row applies to the notify path, so no
   BUG-tagged divergence is expected; BUG-028/030/034/041/042 assertions are still present.
3. **Mutations (local, reverted, not committed).**
   - `crc/notify/slack_owners.py:28`: dropped `link_names=True` on the owner-group channel post ->
     **117 failed** / 995.
   - `crc/core/report.py:58`: Azure VM NIC section shows the NIC ids instead of the VM ids (undoes the
     BUG-028 quirk) -> **12 failed** / 995.
   Round 1's two mutations (`get_msg` text, Influx `resource` tag) are independent of these. Not vacuous.
4. **Scope.** Changed files: `tests/test_notify_parity.py`, new `tests/helpers_cl2_notify.py` (lane
   prefix), `docs/archive/reviews/CL2-NOTIFY.md`. No `crc/**`, other tests, locked tests,
   `tests/legacy/*.txt` or `tests/archtest/**`.
6. **Gates.** `pip install -r requirements.txt -r requirements-dev.txt`, then `make check`: ruff clean,
   mypy clean (365 files), import-linter 5 kept / 0 broken, pytest **44400 passed in 54.42s**
   (`make check` wall time 74 s), `ARCH-008: 44400 tests, floor 44400`. Test count unchanged; no floor
   raise needed. `tests/test_notify_parity.py` alone: 995 passed in 2.8 s.

### Nits (not blocking)

- `tests/test_notify_parity.py:634` docstring still says "as `crc.py` issues them"; it now means the
  oracle (`crc_main.txt`). Cosmetic.

## New bugs (round 2)

None.

ROUND 2 VERDICT: APPROVE
