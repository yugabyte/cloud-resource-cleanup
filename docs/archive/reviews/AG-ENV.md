# AG-ENV review

## Coding summary

Lane AG-ENV (branch `ag/env`): reproducible green gate, offline smoke, the last ARCH-003 loophole.

**Routed / adapters.** No kind was routed or unrouted and no adapter was deleted or shimmed; the
CLI parity tests reach the cleaners exactly as before.

**1. Reproducible gate.**
- `pyproject.toml`: mypy overrides with `ignore_missing_imports` for `google.*`, `azure.*`,
  `msrest*`, `msrestazure*` (next to the existing boto3, oci and googleapiclient ones), so a release
  without `py.typed` cannot break `make lint`. `follow_imports = "skip"` was tried and rejected:
  it turns the SDK types into `Any` and makes existing `type: ignore` comments in
  `tests/test_gcp_base.py` fail (`no-any-return`), and those files belong to other lanes.
- `constraints.txt`: `pip freeze` of a fresh Python 3.11.17 venv built from `requirements.txt` and
  `requirements-dev.txt` on 2026-10-24 (`make check` green in that venv: 44415 tests before my
  change). CI (`.github/workflows/ci.yml`, also the pip cache key) and `.cursor/environment.json`
  install with `-c constraints.txt`.
- `Makefile`: new `smoke-offline` target.

**2. `make smoke-offline`** (`scripts/smoke_offline.py`, `scripts/offline_hook/sitecustomize.py`,
`tests/test_smoke_offline.py`). One `python crc.py ... --dry_run` subprocess per (cloud, kind) of
`tests/fixtures/jenkins_commands.txt` (17 pairs, `--scan_tag` lines skipped, `$RESOURCE_GROUP` and
`${AWS_JENKINS_SLAVE}` substituted). The runner puts `scripts/offline_hook` on `PYTHONPATH` and sets
`CRC_OFFLINE_SMOKE=1`; the `sitecustomize` replaces `boto3.client/resource`, the Azure,
GCP (`compute_v1.*Client`, `discovery.build`), OCI, Slack and Influx clients with an in-memory
`Fake` and denies `socket` connects and DNS. Each pair must exit 0 with exactly one `RUN START` and
one `RESULT` line. Tests also pin: no effect without the switch, sockets denied, and no reference
to the hook anywhere under `crc/` or `crc.py` (so no Jenkins flag or variable reaches it). The
subprocess environment is rebuilt from `PATH`, `HOME`, `LANG` plus dummy credentials.

**3. ARCH-003.**
- `crc/core/age.py` no longer imports `os`. It has `set_max_age_source()` and `max_age_override()`;
  `crc/cli/context.py` `install_max_age_source()` (first call of `main`) installs
  `read_max_age_env()`, which reads `MAX_AGE` at each decision, so `--max_age` (exported by
  `set_max_age`) and a `MAX_AGE` set by Jenkins keep working exactly as before. Without a source no
  override applies. The core `RunContext` was left unchanged: cloud cleaners call `is_old` without a
  context and those files belong to other lanes, so an injected source is the only way to keep their
  signatures. `tests/conftest.py` installs the same production function so tests that set `MAX_AGE`
  with `monkeypatch` and call a cleaner without `main` behave as before.
- `tests/archtest/bans.py`: `crc/core` now bans `env`; new kind `clock_ref` (the clock passed as a
  value: default argument, `default_factory=`, attribute, `time.time`) is banned in `crc/core` and
  `crc/cli`, never double-reported for a call. `cloud_clock_refs()` reports the cloud hits only.
  Self-tests replace `core_env_ban_is_deferred_to_wave_b` with the inverse (env is banned in core).

**Test count:** before 44415, after 44461 (floor raised to 44461).

## Owner action

1. `requirements.txt` (not edited by this lane): `paramiko`, `requests` and `beautifulsoup4` are never
   imported (`rg "paramiko|bs4|beautifulsoup|import requests|from requests" --glob '*.py'` finds
   nothing; only `requirements.txt` names them). `requests` stays installed as a dependency of the
   Azure, OCI and Slack SDKs (pinned in `constraints.txt`). Proposed diff:

   ```diff
   --- a/requirements.txt
   +++ b/requirements.txt
   @@ -1,3 +0,0 @@
   -paramiko
   -requests
   -beautifulsoup4
   ```
   and update the SHA-256 lock in `tests/archtest/frozen_inputs.json` (`ARCH-007`):

   ```diff
   --- a/tests/archtest/frozen_inputs.json
   +++ b/tests/archtest/frozen_inputs.json
   @@ -1,3 +1,3 @@
    {
   -  "requirements.txt": "913955ef30813d6241f061f616f8109dc1bcd0ea080c517edfd37096ab87aacd",
   +  "requirements.txt": "0dd734c4cea0ec95978ac2837de7bc958ed15437aed40fd6755fba003ce49299",
   ```
   then regenerate `constraints.txt` the same way (fresh 3.11 venv, `pip freeze`).

## Wave next

Docs (AG-DOCS): `README.md:89` and `AGENTS.md:17` install lines need `-c constraints.txt`;
`ARCHITECTURE.md` "One exception: `is_old` reads the `MAX_AGE` environment variable" and
`tests/archtest/README.md:24` "(no env ban: `crc/core/age.py` reads `MAX_AGE`); `crc/cli` bans `re`
only" are now false (`crc/core` reads no environment and bans `env` + `clock_ref`; `crc/cli` installs
the reader and bans `clock_ref`).

Cloud packages: the clock passed as a value (`clock_ref`), reported only (not failing):
`crc/aws/disk/__init__.py:38`, `crc/aws/spot_instance_requests/runtime.py:21`,
`crc/aws/vm/adapter.py:58`, `crc/azu/spot_vm/__init__.py:105`, `crc/azu/vm/adapter.py:40`,
`crc/gcp/spot_vm/adapter.py:51`, `crc/gcp/spot_vm/cleaner.py:165`, `crc/gcp/spot_vm/cleaner.py:188`,
`crc/gcp/spot_vm/disks.py:112`, `crc/gcp/vm_adapter.py:45`, `crc/oci/spot_vm/adapter.py:44`,
`crc/oci/vm/adapter.py:30`. When their owners remove them, add `clock_ref` to the cloud entries of
`PACKAGE_BANS` (`_ALL_KINDS`).

Smoke covers the 17 pairs of the Jenkins fixture only (no `vpc`, `gcp spot_vm`, `oci spot_instances`).

## New bugs

None found.

## Round 1 (Argon)

### Findings Fixed in Review

1. `tests/test_smoke_offline.py:57-66` (`test_hook_fakes_clients_and_denies_sockets`): previously
   only caught `OSError` on `socket.create_connection(('127.0.0.1', 9))` and checked
   `result.stdout.startswith("denied")`. Because `ConnectionRefusedError` on closed port 9 is an
   `OSError`, the test still passed when `_deny_sockets()` was removed in `sitecustomize.py`. Fixed
   to assert that `socket.create_connection`, `socket.socket().connect` and `socket.getaddrinfo`
   each raise `OSError` containing `"denied by the offline smoke hook"`; verified the test now fails
   when `_deny_sockets()` is removed.
2. `scripts/offline_hook/sitecustomize.py:82,83,89,93`: removed 4 unused `# type: ignore[assignment]`
   comments on modules already covered by `ignore_missing_imports = true` so `python3 -m mypy scripts`
   passes with 0 errors.
3. `tests/archtest/bans.py:16-20,29`: updated the stale comment saying `crc/core` `env` ban waits for
   Wave B and added `clock_ref` to the allowlist kinds comment.
4. `docs/archive/reviews/AG-ENV.md:61-62`: corrected `## Owner action` item 2 (which stated
   `requirements.txt` was not hash-locked) to include the exact `tests/archtest/frozen_inputs.json`
   SHA-256 diff (`ARCH-007`), and added `tests/archtest/README.md:24` to `## Wave next`.

### Checklist & Gate Verification

1. **Task items complete and verified in code:**
   - **Reproducible gate:** `pyproject.toml:81-92` adds `ignore_missing_imports = true` overrides for
     `google`, `google.*`, `azure`, `azure.*`, `msrestazure`, `msrestazure.*`, `msrest`, `msrest.*`;
     `constraints.txt` pins all 90 resolved packages; `.github/workflows/ci.yml:19-20` and
     `.cursor/environment.json:2` pass `-c constraints.txt`; unused `paramiko`, `requests`,
     `beautifulsoup4` proven unused by grep and proposed under `## Owner action` with exact diffs for
     `requirements.txt` and `tests/archtest/frozen_inputs.json`.
   - **`make smoke-offline`:** `Makefile:20-21`, `scripts/smoke_offline.py`,
     `scripts/offline_hook/sitecustomize.py`, and `tests/test_smoke_offline.py` run all 17
     `(cloud, kind)` pairs from `tests/fixtures/jenkins_commands.txt` in subprocesses with
     `--dry_run`, SDK clients replaced by `Fake`, and sockets denied (`17/17 pairs passed` in
     `32.4s`), asserting exit `0`, one `RUN START`, and one `RESULT` line per pair. Unreachable from
     CLI/Jenkins flags and inert unless `PYTHONPATH` includes `scripts/offline_hook` and
     `CRC_OFFLINE_SMOKE=1`.
   - **ARCH-003:** `crc/core/age.py` no longer imports `os`; `crc/cli/context.py`
     (`install_max_age_source`, called first in `crc/cli/main.py:29` and in `tests/conftest.py`)
     injects `read_max_age_env`. `tests/archtest/bans.py` bans `env` and `clock_ref` in `crc/core`
     and `clock_ref` in `crc/cli`, and `cloud_clock_refs()` reports the 12 cloud-package hits listed
     in `## Wave next`.
2. **CLI & behaviour parity:** `python3 crc.py --help` verified intact; no CLI flag, Jenkins command,
   selection, delete, Slack/Influx output or exit code changed.
3. **Guardrails & mutation testing:** No check or test weakened (`core_env_ban_is_deferred_to_wave_b`
   replaced by `test_arch003_core_bans_env_and_clock_values` plus 15 new `ARCH-003` self-tests, 5
   `MAX_AGE` source tests, and 22 offline smoke tests). `safety_lock.json`, `update_safety_lock.py`,
   the 5 locked safety test files, `tests/legacy/*.txt`, `requirements.txt`, and
   `frozen_inputs.json` are untouched. Locally broke `crc/core/age.py` (`os.getenv` + `datetime.now`
   ref), `crc/cli/main.py` (removed `install_max_age_source()`), and
   `scripts/offline_hook/sitecustomize.py` (removed `_deny_sockets()`) and verified each check/test
   fired before restoring.
4. **Scope & size limits:** Only lane-owned files changed (`22` files). All non-test files `<= 250`
   lines (max `149` in `tests/archtest/bans.py`), all functions `<= 60` lines (max `40` in
   `crc/cli/main.py:main`), and `README.md` `## Public API` sections in sync (`ARCH-002`, `ARCH-004`).
5. **Gates:**
   - `make lint` (`ruff check .`, `mypy`, `lint-imports`): green (`python3 -m mypy scripts` also green).
   - `make smoke-offline`: `17/17 pairs passed` (`32.4s`).
   - `make check` after `pip install -r requirements.txt -r requirements-dev.txt`: **44,461 passed in
     62.27s** (`1m 08.3s` total wall time; `ARCH-008: 44461 tests, floor 44461`).

VERDICT: APPROVE

## Round 2 (Opus)

### Round 1 fixes re-checked

All four hold: the socket-deny test asserts the hook's message for `create_connection`,
`socket().connect` and `getaddrinfo`; `sitecustomize.py` has no unused ignores; the `bans.py`
comments and the Owner action / Wave next items are accurate.

### Findings fixed in this round

1. `tests/archtest/bans.py` `_clock_refs`: the `clock_ref` ban matched dotted names literally,
   so in `crc/cli` (where `clock` imports are legal) `from time import time` then `def f(now=time)`,
   and anywhere `import datetime as dt` / `from datetime import datetime as DT` then
   `dt.datetime.now` / `DT.utcnow` as a value, passed. Added `_clock_aliases` (resolves `import`
   and `from datetime|time import` aliases, Load names only) so these are caught. New self-tests:
   `module-alias`, `class-alias` (x2 packages), `test_arch003_selftest_cli_clock_name_passed_as_value`
   (2 cases) and `test_arch003_selftest_cli_may_call_an_imported_clock_name` (no false positive on a
   call or a store). The cloud report is unchanged (the same 12 hits listed under Wave next).
2. `pyproject.toml` `[tool.mypy] files`: `scripts` was not type-checked by `make lint` (round 1 ran
   `mypy scripts` by hand). Added `scripts`; `mypy` is clean on 373 files.

### Checks

1. Task items, read in code: `pyproject.toml` SDK overrides, `constraints.txt` (90 pins) used in
   `ci.yml` (install and cache key) and `.cursor/environment.json`; `make smoke-offline` 17/17 pairs,
   exit 0, one `RUN START` and one `RESULT` per pair, hook inert without `PYTHONPATH` +
   `CRC_OFFLINE_SMOKE=1` and never mentioned under `crc/` or `crc.py`; `crc/core/age.py` imports no
   `os`, `crc/cli/context.py` installs `read_max_age_env` first in `main()` (re-read at each
   decision, as the original `os.getenv`); `crc/core` bans `env` + `clock_ref`, `crc/cli` bans
   `clock_ref`. Not done, with reasons already given above: `RunContext` was not used (cloud call
   sites belong to other lanes), `follow_imports = "skip"` rejected.
   Unverified here: the machine has Python 3.12.3 and no `python3.11`/`uv`, so the "fresh 3.11
   venv" claim was not reproduced; the install with `-c constraints.txt` resolved cleanly on 3.12.
2. `python3 crc.py --help` is byte-identical to `a0b3d09`. No flag, Jenkins command, selection,
   delete, Slack/Influx output or exit code changed.
3. Nothing weakened; `core_env_ban_is_deferred_to_wave_b` was replaced by its inverse plus the env
   self-tests. `requirements.txt`, `frozen_inputs.json`, `safety_lock.json`,
   `update_safety_lock.py`, the locked safety tests and `tests/legacy/*.txt` are untouched.
   Mutations (not committed), each fired: `os.getenv` + `datetime.datetime.now` value in
   `crc/core/age.py` (ARCH-003 and `test_core_package_reads_no_environment`); `install_max_age_source()`
   removed from `main` (`test_main_installs_the_source_before_anything_else`); `from time import time
   as _wall; CLOCK = _wall` in `crc/cli/context.py` (ARCH-003, missed before this round's fix);
   `boto3.client = Fake` removed from the hook (6 AWS smoke pairs fail).
4. Files: lane-owned, plus READMEs (Public API sync), the review file, the test floor and the new
   `tests/conftest.py` (test setup for the code changed). `tests/conftest.py` installs the reader
   for the whole session; `test_main_installs_the_source_before_anything_else` resets it first, so
   it does not hide a missing install in `main`.
5. Sizes: largest changed file `tests/archtest/test_arch003_bans.py` 225 lines, `bans.py` 171; no
   function over 40 lines. README Public API in sync (ARCH-002/004 green).
6. Gates after `pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt`
   (Python 3.12.3): `make check` green, **44468 passed in 58.75s** (65.4s wall, ruff, mypy,
   import-linter included); floor raised 44461 -> 44468; `make smoke-offline` 17/17.

### Wave next (additions)

- `scripts/offline_hook/sitecustomize.py` denies `connect`, `connect_ex`, `create_connection` and
  `getaddrinfo`, not `sendto` on an unconnected UDP socket; no faked client uses one, harmless today.
- The `clock` (call) kind still matches literal dotted names only (`dt.datetime.now()` after
  `import datetime as dt` is not a `clock` hit). Applying `_clock_aliases` to calls would put new
  failures on cloud files owned by other lanes; do it when those lanes are merged.

### New bugs

None found.

ROUND 2 VERDICT: APPROVE
