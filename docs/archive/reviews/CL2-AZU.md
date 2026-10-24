## Coding summary

Lane CL2-AZU (test-only). `tests/test_cli_bridge_azu.py` no longer runs the legacy CLI code.

- **Routed / deleted / shimmed:** nothing in `crc/**` changed. No adapter was deleted or shimmed.
- **How the parity tests reach the cleaners now:**
  - Routed side: unchanged. `cli_dispatch._run_pair` runs `bridge.run_routed` and the registered
    cleaner, with `crc.cli.runner.CRC` only as the context object the bridge reads.
  - Legacy side: `main`'s frozen code. `tests/helpers_oracle.load_legacy_crc()` gives `crc_main.txt`
    with the Azure adapters bound to their module oracles. `tests/helpers_cl2_azu.run_oracle_pair`
    repeats the Azure branches of `main()`'s resource loop on that `CRC`. The fake Slack and Influx
    clients and `build` are copies in `tests/helpers_cl2_azu.py`, so the test no longer imports
    `tests.test_cli_crc_parity` (which imports `crc.cli.vm_ops` and `resource_ops`). The Influx fake
    records `SYNCHRONOUS` by name instead of an object address.
  - Run-log lines (RUN START, REGION, ACTION, SKIPPED, ERRORS, FAILED, RESULT): captured once from
    the former legacy handlers (current adapters through `CRC.delete_*` / `stop_*` and
    `run_legacy_handler`) for all 58 (kind, variant, dry) combinations and frozen in
    `tests/helpers_cl2_azu_frozen.py` (`RUN_LOG`). The routed side is asserted against that data.
  - Explicit divergences from `main` (fixed bugs), asserted against the oracle, never against the
    routed side:
    - BUG-358: the raw tag-dict INFO lines per VM or disk are gone (VM and disk kinds).
    - BUG-356: "Skipping leftover Spot disk" lines are DEBUG (`spot_instances`).
    - BUG-003: stop deallocates where `main` powers off (derived from the oracle's mutations).
    - BUG-013 / BUG-077 (IP), BUG-113 (NIC regex), BUG-174 (disk delete isolated): the outcome
      differs (result, Slack, Influx, stdout, logs, mutations). The pre-routing outcome of the
      differing fields is frozen in `FIXED` (45 cases); the test asserts each frozen value differs
      from `main` and a test checks every `FIXED` group has its BUG id.
- **Test count:** before 44400 collected (`test_cli_bridge_azu.py`: 292), after 44408
  (`test_cli_bridge_azu.py`: 300). Same 290 parametrized pair cases and 2 route tests, plus 8 new
  checks (`test_frozen_divergences_are_all_marked_with_a_bug`, and 7 for the BUG-358 / BUG-356 /
  BUG-003 divergences being present in `main`). Floor raised to 44408 with `make raise-test-floor`.
- **Gates:** `make check` green (ruff, mypy, import-linter, 44408 passed, ARCH-008).
- **Grep:** `tests/test_cli_bridge_azu.py`, `tests/helpers_cli_azu.py` and the new
  `tests/helpers_cl2_azu*.py` do not import `crc.cli.resource_ops`, `vm_ops`, `notify`,
  `adapter_args`, `report_calls` or `tests.helpers_legacy_handlers`, and call `delete_*`/`stop_*`
  only on `main`'s oracle `CRC`.
- **Owner actions:** if another lane's test floor conflicts, the integrator resolves it. The
  integrator can delete `tests/helpers_legacy_handlers.py` once the other bridge lanes are off it.
- **Left:** nothing for this file.

## New bugs

None.

## Round 1 (Argon)

Scope reviewed: `git diff 2bd8e67..HEAD` on branch `cl2/azu` (lane `CL2-AZU`). No code changes required in this round.

### Findings

1. **No imports or calls to the current legacy CLI layer (Check 1): OK.**
   - Grepped `tests/test_cli_bridge_azu.py`, `tests/helpers_cli_azu.py`, `tests/helpers_cl2_azu.py`, and `tests/helpers_cl2_azu_frozen.py`: none import `crc.cli.resource_ops`, `crc.cli.vm_ops`, `crc.cli.notify`, `crc.cli.adapter_args`, `crc.cli.report_calls`, `tests.helpers_legacy_handlers`, or `tests.test_cli_crc_parity`.
   - `crc.cli.runner.CRC` is constructed only as the context object passed to `cli_dispatch._run_pair` and `route.precheck` on the routed path; `delete_*` and `stop_*` are called exclusively on `oracle_module().CRC` from `tests.helpers_oracle.load_legacy_crc("legacy_crc_main_azu")`.
2. **Parity comparisons, frozen run-log data, and fixed-bug divergences (Check 2): OK.**
   - All 290 parametrized pair cases (`test_routed_azure_pair_matches_main`) and the 2 existing route/precheck tests remain intact and assert `result`, `slack`, `influx`, `stdout`, `logs`, `ordered`, `regions`, and `mutations`, plus 8 new tests (`test_frozen_divergences_are_all_marked_with_a_bug` and 7 `test_main_logs_what_the_fixed_bugs_removed_and_stop_powers_off` cases), raising `tests/test_cli_bridge_azu.py` from 292 to 300 tests (total suite 44,400 -> 44,408).
   - Independently verified across all 290 `(kind, variant, dry, slack, influx)` cases that `_expected(kind, variant, dry, slack, influx, old_oracle)` matches the output of the former `run_legacy_handler` byte-for-byte, confirming `RUN_LOG` and `FIXED` in `tests/helpers_cl2_azu_frozen.py` were captured from the former legacy side and not derived from the routed side.
   - Every fixed-bug divergence from `main`'s oracle (`BUG-003`, `BUG-013`/`BUG-077`, `BUG-113`, `BUG-174`, `BUG-356`, `BUG-358`) is explicitly asserted against `main` (`assert value != view[field]` in `_expected`, `test_frozen_divergences_are_all_marked_with_a_bug`, and `test_main_logs_what_the_fixed_bugs_removed_and_stop_powers_off`) and tagged with its `BUG-NNN`.
3. **Non-vacuity under deliberate cleaner mutation (Check 3): OK.**
   - Locally mutated `VmCleaner.apply` in `crc/azu/vm/cleaner.py` to drop one candidate (`plan.candidates[:1]`); `test_routed_azure_pair_matches_main` immediately failed on Slack, console logs, mutations, and run-log assertions (reverted after check).
4. **File ownership & scope (Check 4): OK.**
   - Only owned files (`tests/test_cli_bridge_azu.py`, `tests/helpers_cli_azu.py`), new helper files with prefix `tests/helpers_cl2_azu` (`tests/helpers_cl2_azu.py`, `tests/helpers_cl2_azu_frozen.py`), `tests/archtest/test_count_floor.txt` (via `make raise-test-floor`), and `docs/archive/reviews/CL2-AZU.md` were touched. No production code (`crc/**`), `tests/legacy/*.txt`, or hash-locked safety tests were modified.

### Gates & test count

- `python3 -m ruff check .`: passed
- `python3 -m mypy`: passed (366 source files)
- `lint-imports`: passed (5 contracts kept, 0 broken)
- `python3 -m pytest -q -n auto`: **44408 passed in 59.06s** (`make check` wall time ~80s; `tests/test_cli_bridge_azu.py`: 300 passed in 4.59s)
- `python3 -m tests.archtest.test_count check`: `ARCH-008: 44408 tests, floor 44408`

VERDICT: APPROVE


## Round 2 (Opus)

Scope: `git diff 2bd8e67..HEAD` on `cl2/azu`. Round 1 (Argon) approved and made no code changes, so
there were no round 1 fixes to check. I re-checked its findings independently. No code changes in
this round.

### Findings

1. **No legacy CLI code (check 1): OK.** Grepping the four lane files finds no import of
   `crc.cli.resource_ops`, `vm_ops`, `notify`, `adapter_args`, `report_calls`,
   `tests.helpers_legacy_handlers` or `tests.test_cli_crc_parity`. The only `delete_*`/`stop_*`
   calls are in `tests/helpers_cl2_azu.run_oracle_pair`, and they run on `oracle_module().CRC`
   (`load_legacy_crc`). No `notify_*` calls. The current `crc.cli.runner.CRC` is built only as the
   context object for `_run_pair` and `route.precheck`. `run_oracle_pair` matches the Azure
   branches of `main()` in `tests/legacy/crc_main.txt` (lines 1553-1627): the same arguments in the
   same order for disk, ip, nic, vm delete/stop and spot_instances.
2. **Comparisons kept, provenance of the frozen data (check 2): OK.**
   - Still 290 parametrized pair cases plus the 2 route tests (292 before, 300 after with 8 new
     checks). Every field the old `new == old` compared (result, slack, influx, stdout, logs,
     ordered, regions, mutations) is still compared field by field, and `set(new) == set(expected)`
     holds. The `RUN START` / `RESULT` / `ACTION` / Slack / Influx sanity asserts are kept.
   - Independent provenance check (temporary harness, not committed): I restored the pre-change test
     (`2bd8e67:tests/test_cli_bridge_azu.py`). For all 290 cases I ran its legacy side (the current
     `run_legacy_handler`), then compared every field with `_expected(...)` built from `main`'s oracle
     plus the BUG-marked view, `RUN_LOG` and `FIXED`. All 290 are equal. The only harness
     normalization was the fake Influx `write_options`: the old fake recorded the `SYNCHRONOUS`
     object, the new one records its name. So the frozen run log and `FIXED` values are what the
     former legacy side produces, not derived from the routed side.
   - Fixed-bug divergences are asserted against the oracle with their ids. `_oracle_view` covers
     BUG-358 (tag-dict echoes), BUG-356 (leftover-disk lines at DEBUG) and BUG-003 (power off vs
     deallocate). `FIXED` covers BUG-013/BUG-077 (ip), BUG-113 (nic) and BUG-174 (disk
     delete_error), and `assert value != view[field]` makes each one a real divergence. Every id has
     a "fixed in" row in `docs/refactor/BUGS.md`. BUG-174 is "partly fixed"; the part asserted (per
     disk delete isolation) is the fixed part.
3. **Not vacuous (check 3): OK.** I tried two cleaners other than round 1's and reverted both.
   - `crc/azu/ip/cleaner.py`: skipping the delete of the last candidate makes all 10 real-run IP
     cases fail. The dry runs make no deletes, so they still pass, as they should.
   - `crc/notify/slack_owners.py`: appending `"!"` to the channel post text makes 105 cases fail.
4. **Scope (check 4): OK.** Changed files: `tests/test_cli_bridge_azu.py`,
   `tests/helpers_cli_azu.py`, the new `tests/helpers_cl2_azu.py` and
   `tests/helpers_cl2_azu_frozen.py`, this review file, and `tests/archtest/test_count_floor.txt`.
   The floor file is inside `tests/archtest/**`, but the CLEAN-2 contract requires the
   `make raise-test-floor` bump (44400 to 44408). No `crc/**`, `tests/legacy/*.txt` or hash-locked
   safety test changed.

### Gates (check 6)

- `pip install -r requirements.txt -r requirements-dev.txt`, then `make check`: green (ruff, mypy,
  import-linter, pytest, ARCH-008).
- **44408 passed in 54.41s** (pytest -n auto); `make check` wall time 74s; floor 44408.
- `tests/test_cli_bridge_azu.py`: 300 passed in ~3.5s.

### New bugs

None.

ROUND 2 VERDICT: APPROVE
