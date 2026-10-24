## Coding summary

Rung LOG-AZU (Azure lane of the LOG family, contract in `docs/refactor/LOGGING.md`). Log text and
levels only: what is selected, deleted or stopped, the SDK calls and their order, Slack, Influx,
CLI flags and exit codes are unchanged.

Files changed:

- `crc/azu/_runlog.py` (new): thin Azure helpers over `crc.core.runlog.current()` (`started`,
  `region_done`, `skip`, `acted`, `failed`, `reason_slug`). Azure has no regions, so one REGION line
  per resource group with `region=<resource group>`. Skip reasons are mapped to short snake_case
  slugs (`filter_tags`, `exception_tag`, `exception_or_notags`, `notags`, `too_young`, `state`,
  `attached`, `name_regex`, `exception_regex`, `not_spot`, `no_creation_time`, ...; unknown
  reasons are slugged). With no `begin()` the calls count and log nothing.
- `crc/azu/disk.py`, `ip.py`, `nic.py`, `vm/cleaner.py`, `vm/adapter.py`, `spot_vm/cleaner.py`,
  `spot_vm/removal.py`, `spot_vm/__init__.py`, `vm_delete_helpers.py`: REGION / ACTION / skip /
  fail calls in the adapters `crc.py` runs and in the Cleaner `plan()` / `apply()` paths (a dry-run
  `plan()` logs the `would_*` ACTION lines; `apply()` logs only real ones, so plan + apply never
  double count). `VmCleaner.process` now returns a bool (acted on); `AzureDiskCleaner.delete_disk`
  and `AzureNicCleaner.attempt_delete` gained a keyword `record` (default True).
- `crc/azu/README.md`: Public API entry for the helpers, logging invariant.
- Tests: `tests/test_azure_runlog.py` (new, 15 tests: disk, ip, nic, vm, spot VM real run, dry run,
  failure, skipped reasons, DEBUG tag dumps, no double action from plan+apply, unattached run logs
  nothing); `tests/helpers_log_fix.py` (new rule `BUG-358`: raw tag dump = str of a dict or
  `None` becomes DEBUG; `BUG-356` also matches `Skipping leftover Spot disk ...`);
  parity tests `test_azure_disk_parity.py`, `test_azure_vm_parity.py`,
  `test_azure_spot_instances_parity.py` compare logs through `expected_logs(old.logs, BUGS)` and
  assert `oracle != new` for at least one case per bug (repeat-delete cases for disk and VM,
  a new leftover-disk case for the Spot VM). IP, NIC and the delete helpers parity tests are
  unchanged (their existing lines keep level and text).

Bug IDs handled:

- BUG-358 (Azure disk and VM): `logging.info(disk.tags)` and `logging.info(vm.tags)` are DEBUG.
- BUG-356 (Azure part): the per-leftover-disk skip lines of the Spot VM remover ("Skipping leftover
  Spot disk ...", two lines) are DEBUG with unchanged text.
- BUG-361 (Azure): one REGION line per resource group for vm, spot_instances, disk, ip, nic.
- BUG-362 (Azure): swallowed per-resource errors are recorded with `fail()`: VM processing errors
  (adapter `process` and `apply()`), VM status read error, Spot VM delete error and dry-run preview
  error, leftover Spot disk delete errors, NIC delete after three attempts, VM primary NIC after
  24 attempts (`vm_delete_helpers`). Existing inline error lines stay.
- BUG-360 (Azure): ACTION and SKIPPED lines feed the RESULT line (header and footer are LOG-0).

Test count: 42256 before (LOG-0 tip 7437ac5), 42272 after. `make check` (ruff, mypy, lint-imports,
pytest) green on Python 3.12.3.

## Notes and left

- `Skipping Spot VM <name>: no creation time (age check not possible)` stays WARNING: it is a
  fail-closed signal for an uncertain age, not routine per-resource noise. It is counted as
  `no_creation_time` in SKIPPED. Change it to DEBUG in a follow-up if the owner prefers.
- "Updating age for disk/vm/Spot VM", "Deleted ...", "Deleting ...", the per-leftover-disk
  "has delete_option=" lines and every summary line stay INFO / WARNING (not in the contract list).
- REGION is logged after the loop, so in the adapters the ACTION lines come before it; in the
  Cleaner path `plan()` logs REGION first and `apply()` logs the ACTION lines later.
- Per-leftover-disk skips (outside the resource group, protected by tags) are counted under
  `leftover_disk_outside_resource_group` and `leftover_disk_exception_tag` in the Spot VM SKIPPED
  lines; they count disks, not VMs.
- The NIC deleted together with a VM and the Spot VM leftover disks produce no ACTION line (the
  contract is one ACTION per resource of the run's `--resource`); only their failures are listed.
- The `BUG-358` rule in `tests/helpers_log_fix.py` is generic (str of a dict, or `None`); the GCP and
  OCI lanes edit the same file in parallel, so expect a trivial merge there.
- Errors that propagate (disk, IP delete, Spot VM list failure) are not recorded with `fail()`:
  they are not swallowed, the CLI still prints RESULT from its `finally`.
- No locked safety test asserts these log levels (`tests/test_azu_spot_vm.py` has no level
  assertion and stays untouched). No `## Owner action` needed.
- No bugs found beyond those already in BUGS.md.

## Round 1 (Argon)

Scope reviewed: `git diff 7437ac5..HEAD` (`crc/azu/README.md`, `crc/azu/_runlog.py`, `crc/azu/disk.py`, `crc/azu/ip.py`, `crc/azu/nic.py`, `crc/azu/spot_vm/__init__.py`, `crc/azu/spot_vm/cleaner.py`, `crc/azu/spot_vm/removal.py`, `crc/azu/vm/adapter.py`, `crc/azu/vm/cleaner.py`, `crc/azu/vm_delete_helpers.py`, `docs/refactor/reviews/LOG-AZU.md`, `tests/helpers_log_fix.py`, `tests/test_azure_disk_parity.py`, `tests/test_azure_runlog.py`, `tests/test_azure_spot_instances_parity.py`, `tests/test_azure_vm_parity.py`) against `docs/refactor/LOGGING.md` and `crc/core/runlog.py`.

### Verification

1. **Behaviour and log-only contract:**
   - Only log levels (`BUG-358` raw `disk.tags` / `vm.tags` dumps at `crc/azu/disk.py:180` and `crc/azu/vm/cleaner.py:270`; `BUG-356` leftover Spot disk skip lines at `crc/azu/spot_vm/removal.py:76,116`) and `crc.core.runlog.current()` reporting calls (`crc/azu/_runlog.py`) changed.
   - Resource selection, deletion/power-off behaviour, SDK call order, summary log lines (`_log_summary` / `log_summary`), Slack notifications, InfluxDB points, CLI flags, and exit codes are unchanged.
   - Both legacy adapter paths (`Disk`, `IP`, `NIC`, `VM`, `SpotVM`) and `Cleaner` protocol paths (`plan()` / `apply()`) report one `REGION` line per resource group (`region=<resource_group>`), one `ACTION` line (`deleted`, `would_delete`, `stopped`, `would_stop`) per acted/previewed resource (with `record=not context.dry_run` in `AzureDiskCleaner.apply` and `AzureNicCleaner.apply`, and early return in `AzureIpCleaner.apply`, `VmCleaner.apply`, and `AzureSpotVmCleaner.apply`, preventing duplicate dry-run `ACTION` lines), short snake_case `SKIPPED` reasons, and `FAILED` entries for swallowed per-resource errors (`NIC` after 3 attempts, `delete_vm_primary_nic` after 24 attempts, VM status/action errors, Spot VM delete/preview errors, leftover Spot disk delete errors).
2. **Line formats (`docs/refactor/LOGGING.md`):**
   - `crc/azu/_runlog.py` delegates to `crc.core.runlog.current().region`, `.action`, `.skip`, and `.fail`, producing exact contract formats for `REGION`, `ACTION`, `SKIPPED`, `ERRORS`, `FAILED`, and `RESULT` (verified in `tests/test_azure_runlog.py`). When no run is begun, `runlog.current()` is unattached and logs nothing extra.
3. **Parity tests and oracles:**
   - All frozen oracles in `tests/legacy/*.txt` (`azu_disk.txt`, `azu_ip.txt`, `azu_nic.txt`, `azu_spot_vm.txt`, `azu_vm.txt`, `azu_vm_delete_helpers.txt`, `azu_base.txt`) are untouched.
   - `tests/helpers_log_fix.py` rewrites only log levels to `logging.DEBUG` for `BUG-356` (`^Skipping leftover Spot disk `) and `BUG-358` (`_is_tag_dump`), never dropping or altering message text.
   - `tests/test_azure_disk_parity.py`, `tests/test_azure_vm_parity.py`, and `tests/test_azure_spot_instances_parity.py` compare logs via `expected_logs(old.logs, BUGS)`, keep exact equality on all non-log fields (`calls`, `deleted`, `stopped`, `nics`, `disks`, `counts`, `stdout`, `error`), and assert `new != old` on at least one case per bug ID.
4. **Locked safety tests and forbidden files:**
   - `tests/archtest/safety_lock.json` and all five hash-locked safety test files (including `tests/test_azu_spot_vm.py`) are untouched.
   - `docs/refactor/BUGS.md`, `docs/refactor/STATUS.md`, `docs/refactor/PLAN.md`, `crc/core/**`, `crc/cli/**`, and other cloud packages are untouched.
5. **READMEs and guardrails (`ARCH-001..005`):**
   - `crc/azu/README.md` lists `started()`, `region_done()`, `skip()`, `acted()`, `failed()`, and `reason_slug()` under `## Public API` and documents the `LOG-AZU` logging invariant under `## Safety Invariants`.

### Findings

- **BLOCKING:** none.
- **NIT (`tests/test_azure_spot_instances_parity.py:44`):** `BUGS = {"BUG-356", "BUG-358"}` includes `BUG-358` even though `crc/azu/spot_vm` does not log raw tag dicts (only `BUG-356` leftover-disk skip lines diverge in Spot VM parity; `BUG-358` is exercised and asserted in `test_azure_disk_parity.py` and `test_azure_vm_parity.py`). Harmless since `_bug_358` is a no-op on non-dict messages.
- **NIT (`crc/azu/vm/README.md`, `crc/azu/spot_vm/README.md`):** The subpackage READMEs do not repeat the `LOG-AZU` logging bullet documented in `crc/azu/README.md:60`.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed! |
| `mypy` (`crc`, `tests`) | Success: no issues found in 204 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 42272 passed in 161.56s |

Test count: 42256 passed on base (`7437ac5`), 42272 passed on `HEAD` (+16).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 7437ac5..HEAD` against `docs/refactor/LOGGING.md` and `crc/core/runlog.py`.
Round 1 findings checked first: both NITs are valid; the Spot VM parity one is fixed below, the
subpackage README one stays a NIT (the invariant is in `crc/azu/README.md`, which is enough for
ARCH-002 since no public name of `crc/azu/vm` or `crc/azu/spot_vm` was added).

### Independent checks

1. Behaviour: the diff only adds `_runlog` calls, moves `logging.info(disk.tags)` /
   `logging.info(vm.tags)` and the two "Skipping leftover Spot disk" lines to DEBUG, adds
   bookkeeping counters, the `record` keyword (default True, only suppresses the ACTION line) and
   the `process()` return value. No SDK call was added, removed or reordered, no selection branch
   changed, the summary lines, Slack, Influx, CLI flags and exit codes are untouched. The
   `ACTION deleted` line of a Spot VM is logged after the delete returns, and no ACTION is logged
   when a delete raises. Checked plan + apply for double counting in every Cleaner: a dry-run
   `apply()` either returns early (IP, VM, Spot VM) or passes `record=False` (disk, NIC), so each
   resource gets exactly one ACTION; skips are counted once per resource (in `decide()`, or in the
   status check that only one path runs).
2. Formats: every line goes through `RunLog.region/action/skip/fail`, so key names, order and
   levels are exactly the contract's. All skip reasons are static strings (no names or states in
   a slug).
3. Parity: `tests/legacy/` untouched. `expected_logs` rules only change levels (BUG-358: str of a
   dict or `None`; BUG-356: anchored `^Skipping leftover Spot disk `), never drop or change text;
   every non-log field is still compared with `==` to the oracle; each bug has a diverging case
   (disk and VM repeat-delete cases for BUG-358, the leftover-disk case for BUG-356).
4. `tests/archtest/safety_lock.json` and the five locked tests are untouched;
   `tests/test_azu_spot_vm.py` asserts no log level. BUGS.md, STATUS.md, PLAN.md, `crc/core`,
   `crc/cli`, other cloud packages, AGENTS.md, ARCHITECTURE.md, CLEANUP_SAFETY.md, `.cursor/**`
   untouched.
5. ARCH-002: new names documented in `crc/azu/README.md` Public API; files under 400 lines
   (largest `crc/azu/disk.py`, 318).

### Fixed in this round

- `crc/azu/vm/cleaner.py:207-221` (`VmCleaner.process`): the legacy `VM` adapter left a VM that
  passed the status check but whose delete or power-off then raised out of REGION `selected`, while
  `plan()` (and the NIC and Spot VM adapters) count it. `process()` now returns True once the
  status check passes, even if the action fails. Behaviour unchanged (same calls, same lines).
  New test `test_vm_failed_delete_counts_as_selected_without_action` in
  `tests/test_azure_runlog.py`.
- `tests/test_azure_spot_instances_parity.py:45`: `BUGS = {"BUG-356"}` (spot_vm has no tag dump,
  so BUG-358 had no diverging case there; stricter now).

### Findings

- BLOCKING: none.
- NIT (`tests/helpers_log_fix.py:23`): the Spot VM "Skipping leftover Spot disk ..." lines are
  tagged BUG-356 (age-check lines) and added to `_AGE_CHECK_PATTERNS`. They are "skipping X because
  Y" lines (contract rule 1), which has no own bug row. Proposed for LOG-0 / the owner: either
  widen BUG-356's wording in BUGS.md to "per-resource age-check and skip lines", or add a row and
  move the pattern to its own rule. Level-only and anchored, so not unsafe.
- NIT (`crc/azu/spot_vm/removal.py:82,121`): leftover-disk skips are counted in the Spot VM
  SKIPPED lines and in RESULT `skipped=`, so a `resource=spot_instances` run counts disks next to
  VMs, while leftover disks and NICs get no ACTION line (review notes above). Consistent between
  dry run and real run, so the comparison pipeline is not affected; proposed: drop these two
  `skip()` calls in a follow-up if the owner wants `skipped=` to count only VMs.
- NIT (`crc/azu/_runlog.py:48,53`, runlog API gap not noted by the coder): REGION `seconds` uses
  `time.monotonic()` directly, not the injectable clock of the current `RunLog` (`_clock` is
  private). Tests mask `seconds=` with a regex instead. Proposed for LOG-0: a public
  `RunLog.clock` (or `RunLog.elapsed(since)`), then `_runlog.started/region_done` use it.
- NIT: in the legacy adapters the REGION line comes after the resource group's ACTION lines; the
  contract does not fix the order, so this is fine. Noted for readers of the log.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check` on `HEAD`:
ruff "All checks passed!", mypy "Success: no issues found in 209 source files", lint-imports
"Contracts: 5 kept, 0 broken", pytest `42273 passed` (round 1: 42272, base 7437ac5: 42256; +1 new
test this round).

ROUND 2 VERDICT: APPROVE


## Rule move

The LOG-AZU bug rules moved out of `tests/helpers_log_fix.py` (now identical to `logging/core`) into
`tests/log_fix_azure.py`, so the lane branches merge without conflicts. `RULES` there is
`{**helpers_log_fix.RULES, "BUG-356": ..., "BUG-358": ...}`: the lane's BUG-356 applies the core rule and
then also turns the `Skipping leftover Spot disk ` lines into DEBUG; BUG-358 (raw tag dump) is unchanged.
The Azure disk, VM and Spot VM parity tests pass `rules=RULES` to `expected_logs`. The unit tests of the
moved rules are in `tests/test_log_fix_azure.py`. No production code changed.
