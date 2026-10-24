# GCP-3 review

## Coding summary

Rung GCP-3: `crc/gcp/vm.py` refactored into a `Cleaner` (lane `refactor/lane-gcp`). No behaviour change.

- **Summary.** `VmCleaner` (`cloud="gcp"`, `kind="vm"`, registered with `@register("gcp", "vm")` through
  `build_vm_cleaner`, supports DELETE and STOP) lists the zones and their instances in `plan()` (read-only)
  and calls `delete_instance` / `stop_instance` from `apply()` / `apply_instance()` only. Filtering uses
  `policy.state_in` (status), `policy.should_skip_by_tags` (exception labels any key, notags all keys),
  `policy.filter_tags_all_keys`, `policy.effective_age` and `policy.normalize_tags`; age uses
  `core_age.get_retention_age` and `core_age.is_old` (`MAX_AGE` still read inside `core.age`). The SDKs sit
  behind the typed Protocols `GcpVmClient` and `InstanceView`; `ComputeVmClient` keeps main's `with
  discovery.build(...)` (service closed right after zones and `InstancesClient()`, later deletes still use
  it). Settings are the frozen `VmSettings`. `VM` is the thin adapter `crc.py` uses (constructor,
  attributes, `get_deleted`, `delete_count`, `get_stopped`, `stopped_count`, `delete(instance_state)`,
  `stop()` unchanged; two optional trailing arguments `client` and `clock`). `crc.py` is not changed. The
  clock is `RunContext.now` (Cleaner) or the injected `clock` (adapter, default `datetime.datetime.now`).
  `plan_zone` is a lazy generator so the adapter keeps main's instance-by-instance interleaving. The
  module is one file of under 400 lines, so no subpackage split. The module lists zones, not regions, so
  there is no region-listing connectivity fallback to preserve; zone listing errors (auth and
  connectivity) propagate as on main and are covered.
- **Parity.** `tests/legacy/gcp_vm.txt` is the byte-identical `git show main:crc/gcp/vm.py`.
  `tests/test_gcp_vm_parity.py` runs the oracle (frozen `datetime`) and the adapter against the same fake
  `googleapiclient.discovery.build` and `compute_v1.InstancesClient` over the case table (delete and stop,
  dry run, status states, age edges, `MAX_AGE`, custom age label, label filter / exception / notags,
  untagged, API and iteration errors, zone listing errors, action errors, empty results, malformed
  instances) comparing SDK calls in order (including lazy page iteration and `close`), results, counts,
  logs, stdout and errors, then runs the same table through the registered Cleaner (plan then apply) and
  compares results, errors, stdout, delete/stop calls and the logged summary lines. Cleaner-path
  differences, inherent to plan-then-apply (as accepted in GCP-1/GCP-2): all zones are listed and filtered
  before the first action, so an error while listing or filtering stops before any action, and log lines
  interleave differently for more than one instance (compared as multisets, exact for at most one).
  Quirk checks assert the oracle and the new code show the same behaviour.
- **Test counts.** Before: 4028 passed. After: 4323 passed (+295: 281 in `tests/test_gcp_vm_parity.py`,
  14 in `tests/test_gcp_vm.py`). No existing test changed.
- **Gates.** `ruff check .`, `mypy` (strict), `lint-imports` and `pytest -q` pass.
  `crc/gcp/vm.py` and `crc.gcp.vm` are removed from both ratchet lists in `pyproject.toml`; no
  new override (the `googleapiclient` one from GCP-2 is reused).
- **Open issues.** New quirks are in `docs/refactor/bugs/pending/GCP-3.md` (15 rows after round 2, none fixed;
  round 2 added the exact, case-sensitive `--resource_states` match): unvalidated
  age, swallowed action errors, unawaited operations, run-aborting errors outside the per-instance `try`,
  strict timestamp format, closed service reused, unpaginated zones, no zone-listing error/connectivity
  fallback, cumulative summary and side-effect count logs, ignored stop state / shared mutable default,
  substring label spec values, full label map logged at INFO, bare instance names without zone, and
  `-> str` / `age: int` annotations. `Result.failed` and `Plan.skipped` stay empty because main reports
  neither.
- **Core change needed.** None.
- **FILTER_VARIANTS.md change needed.** None for behaviour. Optional: the `gcp/vm.py` row could note that
  the status gate is `instance_state` (default `["RUNNING"]`, only `delete()` accepts another list).
- **`crc/README.md` / `tests/legacy/README.md` / `tests/README.md` change needed.** Add `gcp_vm.txt`
  (exact `git show main:crc/gcp/vm.py`, used by `tests/test_gcp_vm_parity.py`) to `tests/legacy/README.md`;
  `crc/core/README.md` could say `gcp` / `vm` is now registered. All outside this lane's edit rights.

## Round 1 (Argon)

Scope reviewed: `git diff eb84fab..HEAD` (`crc/gcp/vm.py`, `crc/gcp/README.md`, `pyproject.toml`,
`docs/refactor/bugs/pending/GCP-3.md`, `docs/refactor/reviews/GCP-3.md`, `tests/legacy/gcp_vm.txt`,
`tests/test_gcp_vm.py`, `tests/test_gcp_vm_parity.py`) against `git show main:crc/gcp/vm.py`,
`git show main:crc.py`, `AGENTS.md`, `docs/refactor/BUGS.md`, `docs/refactor/FILTER_VARIANTS.md`,
`CLEANUP_SAFETY.md`, and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity with `git show main:crc/gcp/vm.py`

- **SDK calls (order and arguments)** (`crc/gcp/vm.py:84-116`, `:164-233`, `:352-386` vs `main:crc/gcp/vm.py:131-170`, `:259-281`):
  - `ComputeVmClient.list_zones` enters `with discovery.build("compute", "v1") as service:`, stores `self._service = service`, calls `service.zones().list(project=project_id).execute()["items"]`, constructs `self._instances = compute_v1.InstancesClient()`, and exits the `with` block (closing `service`) before returning `zones`, matching `main:crc/gcp/vm.py:131-134` in exact call order (`discovery.build` -> `enter` -> `zones()` -> `zones.list` -> `zones.list.execute` -> `InstancesClient` -> `close`).
  - `VM._perform_operation` iterates `cleaner.zones()` in order, lazily iterates `plan_zone(zone, self._clock)` (which calls `self._instances.list(project=project_id, zone=zone)`), and calls `apply_instance()` immediately on each yielded candidate before advancing the zone's instance iterator.
  - When `not dry_run`, `apply_instance()` calls `service.instances().delete(project=project_id, zone=zone, instance=candidate.id).execute()` (for `Operation.DELETE`) or `service.instances().stop(project=project_id, zone=zone, instance=candidate.id).execute()` (for `Operation.STOP`), logs `Deleting instance <name>` or `Stopping instance <name>` at `INFO`, and appends `candidate.id` to `instance_names_to_delete` or `instance_names_to_stop`.
- **Filter, state, and age decisions** (`crc/gcp/vm.py:194-263` vs `main:crc/gcp/vm.py:140-146`, `:202-257`):
  - Status check (`crc/gcp/vm.py:200`): `policy.state_in(instance.status, settings.instance_state)` (default `("RUNNING",)`).
  - Exception and `notags` check (`_should_skip`, `crc/gcp/vm.py:235-243`): short-circuits without touching `instance.labels` when both `exception_labels` and `notags` are empty/falsy (`main:crc/gcp/vm.py:210`, `:217`), otherwise calls `policy.should_skip_by_tags(policy.normalize_tags(instance.labels), exception_labels, notags)` (`exception_labels` any key, `notags` all keys).
  - Filter check (`_has_matching_filter_label`, `crc/gcp/vm.py:245-249`): returns `True` when `filter_labels` is empty/falsy, otherwise `policy.filter_tags_all_keys(policy.normalize_tags(instance.labels), self._settings.filter_labels)` (all keys must match; unlabelled instances fail an active filter and pass an empty filter).
  - Age check (`_is_old`, `crc/gcp/vm.py:251-263`): parses `instance.creation_timestamp` with `%Y-%m-%dT%H:%M:%S.%f%z`, converts `clock()` to `created.tzinfo`, logs `logging.info(instance.labels)` (raw labels), calls `core_age.get_retention_age(instance.labels, settings.custom_age_tag_key)` (fail open, `BUG-004`, `SAF-009`), logs `Updating age for instance: <name>` when truthy, and calls `core_age.is_old(policy.effective_age(retention_age, default_age), now, created)` (`MAX_AGE` still applies inside `is_old`, `SAF-006`, `SAF-010`).
- **Log lines, errors, and dry-run path** (`crc/gcp/vm.py:131-146`, `:221-233`, `:328-350` vs `main:crc/gcp/vm.py:80-115`, `:147-200`):
  - Per-instance delete/stop errors in `apply_instance()` are caught (`except Exception as e:`), logged at `ERROR` (`Error occurred while delete instance <name>: <e>` / `Error occurred while stop instance <name>: <e>`), and omitted from the result lists.
  - Errors outside the per-instance `try` (`discovery.build`, `zones().list().execute()`, missing `"items"` or zone `"name"`, `InstancesClient()`, `instances.list`, mid-page iteration, missing `status` or `labels`, `strptime`, `MAX_AGE` parse) propagate unchanged.
  - `_log_summary` checks `if not deleted and not stopped:` across both lists (preserving the cumulative warning quirk across `delete()` and `stop()` calls on the same `VM` instance) and logs the exact `INFO`/`WARNING` summary lines of `main` in both normal and `dry_run` modes; nothing is printed to stdout.

### 2. Oracle and parity test verification (`tests/legacy/gcp_vm.txt`, `tests/test_gcp_vm_parity.py`)

- Diffed `tests/legacy/gcp_vm.txt` against `git show main:crc/gcp/vm.py`: byte-identical (SHA-256 `c22b5b4be5cac184254c14dc72e4f7becd941a6d5a7099ad3f3fcf879ebc0c2e`, 0 lines of diff).
- Verified `tests/test_gcp_vm_parity.py` executes the oracle (`tests/legacy/gcp_vm.txt`) and both the new `VM` adapter and registered `VmCleaner` against the same `FakeGcp` (`discovery.build` and `compute_v1.InstancesClient`) over 101 cases, comparing ordered SDK `calls` (including per-instance generator steps `instances.next` and `close`), `error`, `deleted`, `stopped`, `count`, `logs` (at `DEBUG`), and `stdout`.
- Tested a deliberate one-line behaviour change in `crc/gcp/vm.py` (`if not policy.state_in(...)` -> `if policy.state_in(...)` in `plan_zone`): `pytest -q tests/test_gcp_vm_parity.py` failed immediately with 219 failures; reverted.

### 3. Architecture and guardrails

- `VmCleaner` implements `Cleaner` (`cloud = "gcp"`, `kind = "vm"`, `supports(Operation.DELETE | Operation.STOP)`, read-only `plan`, mutating `apply`) and is registered with `@register("gcp", "vm")` on `build_vm_cleaner`.
- `plan()` never mutates (`ARCH-005` passes on `crc/gcp/vm.py`; `delete_instance` and `stop_instance` are only called in `apply_instance`, invoked from `apply` and `VM._perform_operation`).
- Filtering uses `policy.state_in`, `policy.should_skip_by_tags`, `policy.filter_tags_all_keys`, `policy.effective_age`, and `policy.normalize_tags` as named in `docs/refactor/FILTER_VARIANTS.md`; no banned constructs (`ARCH-003`).
- SDK is behind the typed `GcpVmClient` and `InstanceView` `Protocol`s; file is 386 lines (`ARCH-004`).
- `crc/gcp/vm.py` (`crc.gcp.vm`) was removed from both ratchet lists in `pyproject.toml`; nothing was added (`ARCH-001`), and no new mypy override was added (reuses the per-SDK `googleapiclient` override from GCP-2).

### 4. Scope, lane rules, and documentation

- Verified `git diff eb84fab..HEAD --name-status`: none of the lane-forbidden files (`docs/refactor/BUGS.md`, `docs/refactor/STATUS.md`, `docs/refactor/FILTER_VARIANTS.md`, `crc/README.md`, `tests/README.md`, `tests/legacy/README.md`, `crc.py`, `crc/core/**`, or other cloud packages) were modified.
- `crc/gcp/README.md` is accurate and passes `ARCH-002` symbol sync (`InstanceView`, `GcpVmClient`, `ComputeVmClient`, `VmSettings`, `VmCleaner`, `build_vm_cleaner()`, `VM`).

### BLOCKING (fixed)

1. **`build_vm_cleaner` did not ignore `options["instance_state"]` on `Operation.STOP`, and `_cleaner_context` in `tests/test_gcp_vm_parity.py` masked it** (`crc/gcp/vm.py:281-283`, `tests/test_gcp_vm_parity.py:633-634`):
   - On `main`, `VM.stop()` always passes `self.default_instance_state` (`["RUNNING"]`) and ignores any custom state list (`main:crc/gcp/vm.py:281`). However, `build_vm_cleaner` applied `options["instance_state"]` for both `DELETE` and `STOP`, and `_cleaner_context` in `tests/test_gcp_vm_parity.py:633` hid the discrepancy in `Case("state-custom-ignored-by-stop")` by only copying `case.state` into `options["instance_state"]` when `case.op == "delete"`.
   - Fixed `build_vm_cleaner` to use `_DEFAULT_STATE` whenever `context.operation is Operation.STOP` (or `options.get("instance_state") is None`), and removed `and case.op == "delete"` from `_cleaner_context` in `tests/test_gcp_vm_parity.py:633`.
2. **`docs/refactor/bugs/pending/GCP-3.md` violated `BUGS.md` schema, ordering, and `main` line citations** (`docs/refactor/bugs/pending/GCP-3.md:10-19`):
   - Every row had free-form prose in the `Effect` column instead of the four values required by `docs/refactor/BUGS.md` Rule 5 (`deletes what it should keep`, `keeps what it should delete`, `run fails`, `cosmetic`).
   - Rows were not sorted by risk (`high` was placed at row 9 after `medium` and `low`, violating `BUGS.md` Rule 4).
   - The timestamp-format row cited `crc/gcp/vm.py:47` on `main` (which is `custom_age_tag_key: str` in `__init__`) instead of `crc/gcp/vm.py:35` (`time_format = "%Y-%m-%dT%H:%M:%S.%f%z"`).
   - Row 10 described a refactor note rather than a `main` quirk; replaced with the `main` substring label-value quirk (`crc/gcp/vm.py:212`, `:219`, `:237`, `BUG-082` / `BUG-108` shape) and added the remaining `main` quirks matching `GCP-1`/`GCP-2` (`BUG-067`/`BUG-135` zone listing errors, `BUG-073`/`BUG-139` `logging.info(instance.labels)`, `BUG-050`/`BUG-070`/`BUG-141` bare instance names across zones plus `same-name-two-zones` in `QUIRKS`, and `BUG-071` `-> str` / `age: int` annotations), all verified against `git show main:crc/gcp/vm.py` and `git show main:crc.py`.

### NIT (left)

- Calling `VmCleaner.apply()` directly with `context.dry_run=True` logs the dry-run summary warning a second time after `plan()` and returns candidate IDs in `Result.succeeded` (`crc/gcp/vm.py:174-176`, `:182-186`, `:222-229`); `crc.core.engine.run` never calls `apply()` when `context.dry_run` is `True` (`crc/core/engine.py:100`).
- `VM.__init__` uses `client or ComputeVmClient()` (`crc/gcp/vm.py:325`); `client if client is not None else ComputeVmClient()` would avoid replacing a falsy custom test client.
- On non-conforming inputs outside the typed contract (a plain `str` passed as `instance_state` instead of `Sequence[str]`, or non-`str` values / `None` in `instance.labels`), `policy.state_in` tests tuple element membership (`state in tuple(allowed)`) and `policy.normalize_tags` coerces label values to `str` (`None` map to `{}`), whereas `main` tests `instance.status not in instance_state` and `key in instance.labels` directly. Real callers pass `list[str]` and `compute_v1.Instance.labels` is a `str`-to-`str` map.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 101 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 4323 passed |

Test count: 4028 before GCP-3 (`eb84fab`), 4322 on `af32d6b`, 4323 after Round 1 (+295 total: 281 in `tests/test_gcp_vm_parity.py`, 14 in `tests/test_gcp_vm.py`).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff eb84fab..HEAD` (commits `97c1a22`, `af32d6b`, `7f45bbf`) against `git show main:crc/gcp/vm.py`,
`git show main:crc.py`, `crc/core/policy.py`, `crc/core/age.py`, `CLEANUP_SAFETY.md`, `docs/refactor/BUGS.md`,
`docs/refactor/FILTER_VARIANTS.md` and `REVIEW_CHECKLIST.md`.

### Round 1 fixes assessment

1. **STOP ignores `instance_state` in `build_vm_cleaner` (`crc/gcp/vm.py:281-283`)**: correct. It matches
   `main:crc/gcp/vm.py:281` (`stop()` always passes `default_instance_state`), and `crc.py` never passes states
   to `stop_vm` (`main:crc.py:1584-1585`). The test change in `_cleaner_context`
   (`tests/test_gcp_vm_parity.py:633-634`) strengthens the test: it now also feeds the custom state to the STOP
   Cleaner. Mutation check: reverting the fix fails
   `test_cleaner_protocol_matches_main[state-custom-ignored-by-stop]`. No behaviour of the `VM` adapter changed.
2. **Pending-bug table schema, order and citations**: correct. Every `main` citation I relied on was re-verified
   with `git show main:...` (`crc/gcp/vm.py:30`, `:35`, `:70-71`, `:131-134`, `:140`, `:150-165`, `:173-200`,
   `:212`, `:219`, `:237`, `:251-257`, `:261`, `:281`; `crc.py:117`, `:154`, `:354-389`, `:561-565`,
   `:750-754`, `:1097-1103`, `:1408`). Two wordings were inaccurate; see the second NIT below (fixed in the doc).
   No test was weakened: round 1 only removed a condition that hid a case from the Cleaner path.

### Independent review

- **Parity.** Status, exception/notags, filter and age decisions follow `main:crc/gcp/vm.py:140-146` in the same
  order. The `policy` helpers have the same semantics: `_key_matches` is `key in tags and (not values or
  tags[key] in values)` (`crc/core/policy.py:90-91`), the same as `main:crc/gcp/vm.py:212`, `:219`, `:237`.
  `effective_age` is `retention_age or age`. The labels are only read when a spec is set, as on main
  (`crc/gcp/vm.py:239-240`, `:246-247`); mutating that short-circuit fails 182 tests. Error text uses
  `Operation.value` (`"delete"` / `"stop"`, `crc/core/cleaner.py:21-22`), so it is identical to
  `operation_type`. The service closes after the zones are listed, and deletes and stops reuse it, as on main.
- **Oracle.** `tests/legacy/gcp_vm.txt` is byte-identical to `git show main:crc/gcp/vm.py` (`diff` empty).
- **Tests.** The parity tests compare SDK calls in order (including lazy page steps and `close`), results,
  counts, logs, stdout and errors for 101 cases. `QUIRKS` asserts concrete outcomes on both the oracle and the
  new code. The Cleaner-path relaxations (multiset logs when more than one instance is listed, no action on a
  listing error) match GCP-1 and GCP-2. Unit tests assert call lists and results; none is vacuous.
- **Safety (`CLEANUP_SAFETY.md`).** `plan()` makes only read calls, and `apply()` makes no SDK call in a dry run
  (`crc/gcp/vm.py:222`). No age gate was weakened, and the custom age label stays fail open as on main (BUG-004).
  `MAX_AGE` is still applied inside `core_age.is_old`. No SAF ID is specific to GCP VM.
- **Architecture and typing.** The ratchet only shrank (`pyproject.toml`: two lines removed, no override added).
  The SDK sits behind Protocols, registration uses the decorator, and nothing calls the cloud at import time.
  The `# type: ignore[arg-type]` in `tests/test_gcp_vm.py:194`, `:206` is justified: main's `age: int` annotation
  is kept and is BUG-071 shape. The README `### Public API` and safety bullets are accurate (ARCH-002 passes).

### BLOCKING (fixed)

None.

### NIT (left, or doc-only fix)

- New quirk added to `docs/refactor/bugs/pending/GCP-3.md`: `--resource_states` is matched exactly and case
  sensitively against the GCP status (`main:crc.py:1051-1058`, `:108-111`; `main:crc/gcp/vm.py:140`). Its help
  example `['RUNNING', 'STOPPED']` never matches a stopped GCP instance (GCP reports `TERMINATED`), and an
  AWS-style `['running']` matches nothing. Pinned by `state-lowercase-not-matched` and
  `state-exact-not-substring`.
- Pending-row wording fixed in place. Row 1: `crc.py` checks `isinstance(age, dict)` when `age` is not `None`
  (`main:crc.py:1264-1271`), not "when truthy". Substring row: `crc.py` rejects non-list `--filter_tags` /
  `--exception_tags` values (`main:crc.py:1404-1405`, `:1274-1283`), so from the CLI only the unvalidated
  `--notags` can be a string, which keeps more instances. The integrator may want the same note on `BUG-108`
  (it cites `crc.py:1063-1064` / `:1072-1073` but not the validation); `BUGS.md` is outside this lane.
- Round 1 NITs still stand: `VmCleaner.apply()` with `dry_run=True` logs the summary a second time; and
  `client or ComputeVmClient()` (`crc/gcp/vm.py:325`) should be `is not None`. Neither is reachable through
  `crc.py` or `crc.core.engine.run`.
- `FILTER_VARIANTS.md` (outside this lane): the `gcp/vm.py` row could say that the status list comes from
  `--resource_states` for `delete` (exact match), and that `stop` always uses `["RUNNING"]`.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` | Success: no issues found in 101 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 4323 passed |

Test count: 4323. This is the same as after round 1, and 4028 before the rung (+295). Round 2 changed docs only.

ROUND 2 VERDICT: APPROVE
