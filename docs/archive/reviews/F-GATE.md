## Coding summary

Lane F-GATE (gates and dependency hygiene). This lane routes no kinds and deletes or shims no adapters: that work belongs to the other two lanes of the wave. No CLI flag, Jenkins command, selection, delete, Slack/Influx output or exit code changed; `tests/test_jenkins_contract.py` and the parity tests are untouched. The CLI parity tests reach the cleaners exactly as before.

- **Test count:** floor 44597 before, 44647 after (`make raise-test-floor`); 44646 passed, 1 skipped.
- **(1) constraints.txt:** dropped `paramiko`, `beautifulsoup4` and their now-orphaned dependencies (`bcrypt`, `pynacl`, `invoke`, `soupsieve`); `requests` stays (required by `msrest`, `azure-core`, `oci`-side packages; `pip check` is clean in a fresh Python 3.11 venv). Added `pip-audit` and its dependencies, so the file is a complete lock of `requirements.txt` + `requirements-dev.txt` (verified: `pip freeze` of a fresh venv equals the file). `constraints.txt` is now an ARCH-007 frozen input next to `requirements.txt`; `frozen_inputs.json` regenerated with `update_frozen_inputs.py` (owner approved in the contract), with a self-test.
- **(2) Ratchet:** `tests/archtest/legacy_baseline.txt` is empty (comment only); `test_arch001_baseline_is_empty_and_pyproject_has_no_exclusions` replaces the "not empty" test, and two new self-tests show an empty baseline admits no file. `crc.py` is now type-checked: the `exclude = ['^crc\.py$']` line is gone from pyproject.toml, and `tests/test_crc_entry_types.py` runs strict mypy (project settings) on a copy named `crc_entry.py` with the repo on `MYPYPATH` (a type error in the copy makes the second test fail, proven by a self-test). It is a test and not part of `make lint`'s `files` because the file name `crc` clashes with the `crc/` package; this cost about 5 s per run.
- **(3) ARCH-003:** `SpotRuntime.clock` (AWS) was never read by the cleaner (it uses `RunContext.now`), so the field, `Clock` and `_UTC_CLOCK` are deleted and the tests that passed `clock=` no longer do. GCP `SpotVMCleaner.preview_instance` / `apply_instance` now require the `clock` (the cleaner passes `lambda: context.now`, as before); `disk_ok_to_delete` takes `clock: Clock | None = None` and keeps the disk (`disk_age_unknown`, fail closed) when a custom age label needs a clock and none was injected. The default could not be removed: the locked test `GcpDeleteOrderTests::test_disk_exception_labels_skip_delete` calls it without a clock (it never reaches the age check). `clock_ref` is now banned in `crc/aws|azu|gcp|oci`; `cloud_clock_refs` (report only) is deleted; self-tests cover all four packages. `tests/test_gate_clock_injection.py` covers the new behaviour.
- **(4) Network deny:** autouse `_deny_network` in `tests/conftest.py` makes `connect`, `connect_ex`, `sendto` (hence `create_connection`) and `getaddrinfo` raise `NetworkDeniedError` for anything but `AF_UNIX` and loopback. `tests/test_network_deny.py` proves it fires and that loopback/UNIX sockets still work. The whole suite stayed green with it: no test needed the network, so no test was changed.
- **(5) CI/doctor:** `make smoke-offline` (target already existed) now runs in CI after `make check`. `make doctor` (`scripts/doctor.py`) checks the Python version against `requires-python` and ruff/mypy/pytest against `constraints.txt`, and prints the exact install command (the venv-creating one when run outside a venv); `tests/test_doctor.py`. CI also runs `make doctor` before `make check`.
- **(6) pip-audit:** pinned in `requirements-dev.txt` (and the lock); new `audit` job in `.github/workflows/ci.yml` with `continue-on-error: true`, so findings are visible and never fail the build. Locally: "No known vulnerabilities found".

**Owner actions:** none blocking. Review the regenerated `frozen_inputs.json` (new `constraints.txt` hash). Decide later whether to make the audit job blocking.

**New bugs:** none. (`pip-audit` reports nothing for the current lock.)

## Lines for the F-DOCS lane

- `AGENTS.md` / `ARCHITECTURE.md` commands list: `make doctor` (checks the Python version and that ruff, mypy and pytest are installed at the versions of `constraints.txt`; prints the install command when not) and `make smoke-offline` (one `python crc.py --dry_run` per (cloud, kind), SDKs faked, sockets denied; also runs in CI).
- `AGENTS.md` / `tests/archtest/README.md`: ARCH-007 now also freezes `constraints.txt` (change it only with the owner, then `update_frozen_inputs.py`); ARCH-001 baseline is empty, nothing may be added to ruff `extend-exclude`; ARCH-003 bans `clock_ref` in `crc/core`, `crc/cli` (and the cloud packages), cloud packages take `now`/a clock parameter wired to `RunContext.now`; the cloud-packages "reported only" note is gone.
- `AGENTS.md` / `tests/README.md`: the whole suite runs with the network denied (`tests/conftest.py`); a test that needs a cloud, Slack or InfluxDB answer must fake the SDK client.
- `crc/cli/README.md` or `README.md` install section: `pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt`; CI has a non-blocking `pip-audit` report job.
- `crc.py` is type-checked by `tests/test_crc_entry_types.py`, not by `mypy` with no arguments.
- `pyproject.toml` comment text above `[tool.mypy]` already describes the crc.py check.

## Round 1 (Argon)

Reviewed `git diff f8ed6dd..HEAD` on branch `fw/gate` against the Lane F-GATE specification, the third-round audits (`AGENT-NATIVE-AUDIT-3.md`, `AGENT-NATIVE-AUDIT-3-GPT.md`), and the final wave contract.

### Findings (fixed in round 1)

1. **`tests/archtest/test_arch003_bans.py:217-239`**: Commit `3262bb3` removed three existing boundary/notify self-tests (`test_arch003_selftest_boundary_packages_ban_re`, `test_arch003_selftest_notify_gets_config_injected`, and `test_arch003_selftest_boundary_keeps_its_legitimate_constructs`, 5 parameterized test items) when replacing `test_arch003_cloud_clock_refs_are_reported_not_failed` with the cloud `clock_ref` self-tests. Restored all three self-tests so `crc/cli` and `crc/notify` ban coverage in `test_arch003_bans.py` is not weakened, and raised the test-count floor (`44647` -> `44652`) via `make raise-test-floor`.
2. **`tests/test_crc_entry_types.py:18,47,53`**: `_type_check` wrote both the valid `crc.py` copy and the intentionally broken copy to the same filename (`workdir / "crc_entry.py"`) while sharing `workdir / "mypy_cache"`. Parameterized the target filename (`crc_entry.py` vs `crc_entry_broken.py`) so the two tests still share the cached `crc.*` dependency graph without overwriting the same target file path in the shared cache.
3. **`tests/archtest/frozen_inputs.py:13`**: Updated `_REMEDIATION` to name `constraints.txt` alongside `requirements.txt`, matching the updated module docstring and `update_frozen_inputs.py`.

### Checklist Verification

1. **Every lane task item done and verified in code:**
   - **(1) `constraints.txt` & `ARCH-007`:** `constraints.txt` drops `paramiko`, `beautifulsoup4`, and their orphaned transitive dependencies (`bcrypt`, `pynacl`, `invoke`, `soupsieve`), keeps `requests` (required by `msrest`, `azure-core`, `oci`, `google-api-core`, `pip-audit`; verified `pip check` reports `No broken requirements found` in a fresh isolated venv), and adds `pip-audit==2.10.1` and its dependencies. `tests/archtest/frozen_inputs.py:23-25` locks `constraints.txt` alongside `requirements.txt`, `tests/archtest/frozen_inputs.json` was regenerated via `tests/archtest/update_frozen_inputs.py` (owner-approved in contract), and `tests/archtest/test_arch007_frozen_inputs.py:29,59-64` tests it.
   - **(2) Empty `legacy_baseline.txt` & type-check `crc.py`:** `tests/archtest/legacy_baseline.txt` contains only a comment (`frozenset()`), `tests/archtest/test_arch001_ratchet.py:38-41,85-96` verifies the empty baseline and that no file can re-enter `extend-exclude`, `pyproject.toml` removes `exclude = ['^crc\.py$']`, and `tests/test_crc_entry_types.py` type-checks `crc.py` under strict project mypy settings (with a negative test proving a type error fails).
   - **(3) `ARCH-003` cloud clock references:** `crc/aws/spot_instance_requests/runtime.py` drops the unused `SpotRuntime.clock` field, `Clock`, and `_UTC_CLOCK` (`SpotRequestCleaner` already uses `RunContext.now`); `crc/gcp/spot_vm/cleaner.py:164,187` requires `clock: Clock` on `preview_instance` and `apply_instance` (wired to `lambda: context.now` in `plan` and `apply`); `crc/gcp/spot_vm/disks.py:87,98-99,114` changes `disk_ok_to_delete` default from `datetime.datetime.now` to `clock: Clock | None = None` and fails closed (`disk_age_unknown`) if a custom age label is present without an injected clock (preserving the 4-arg call in hash-locked `GcpDeleteOrderTests::test_disk_exception_labels_skip_delete`). `tests/archtest/bans.py:11` adds `"clock_ref"` to `_ALL_KINDS` (banning it across `crc/aws`, `crc/azu`, `crc/gcp`, `crc/oci`) and removes `cloud_clock_refs`. Covered by `tests/archtest/test_arch003_bans.py:191-215` and `tests/test_gate_clock_injection.py`.
   - **(4) Suite-wide network deny:** `tests/conftest.py:42-97` installs an `autouse=True` `_deny_network` fixture that raises `NetworkDeniedError` on `socket.socket.connect`, `connect_ex`, `sendto`, and `socket.getaddrinfo` for any target other than `AF_UNIX` and localhost/loopback (`localhost`, `127.0.0.0/8`, `::1`, `0.0.0.0`, `""`). `tests/test_network_deny.py` proves all 5 denial paths and both local allowances. All 44,651 non-skipped tests pass without network access.
   - **(5) `make smoke-offline` in CI & `make doctor`:** `.github/workflows/ci.yml:22-24` runs `make doctor`, `make check`, and `make smoke-offline`. `Makefile:47-48` and `scripts/doctor.py` check the Python version against `pyproject.toml` `requires-python` and `ruff`/`mypy`/`pytest` against `constraints.txt`, printing the exact install command on mismatch (`tests/test_doctor.py`). Neither is added to docs owned by `F-DOCS`; the doc lines for `F-DOCS` are listed above.
   - **(6) `pip-audit` in CI:** `pip-audit==2.10.1` is pinned in `requirements-dev.txt` and `constraints.txt`, and `.github/workflows/ci.yml:27-41` runs `pip-audit --desc --progress-spinner off` in a non-blocking `audit` job (`continue-on-error: true`).
2. **CLI & behaviour parity:** `python3 crc.py --help` output is identical to `f8ed6dd`. `crc.py`, `crc/cli/**`, `crc/core/**`, `crc/notify/**`, and `tests/test_jenkins_contract.py` have zero diff against `f8ed6dd`. The only parity test edit (`tests/test_aws_spot_instance_requests_parity.py:821,847`) removes the unused `clock=lambda: NOW` keyword argument from `SpotRuntime(...)` construction; all assertions are unchanged.
3. **Guardrails & local sabotage verification:** `tests/legacy/*.txt`, `tests/archtest/safety_lock.json`, all 69 hash-locked safety test functions, `requirements.txt`, `CLEANUP_SAFETY.md`, and `docs/refactor/BUGS.md` are untouched. Locally broke three new checks without committing and confirmed each fired before restoring:
   - Re-adding `clock: Clock = datetime.datetime.now` in `crc/gcp/spot_vm/disks.py` failed `test_arch003_bans.py` with `ARCH-003: crc/gcp/spot_vm/disks.py:114 uses banned clock_ref`.
   - Appending a line to `constraints.txt` failed `test_arch007_frozen_inputs.py` with `ARCH-007: constraints.txt changed`.
   - Adding `"crc.py"` to `extend-exclude` in `pyproject.toml` failed `test_arch001_ratchet.py` with `ARCH-001: ruff extend-exclude gained entries not in legacy_baseline.txt: ['crc.py']`.
4. **Owned files:** All 31 changed files belong to `F-GATE` (`crc/aws/spot_instance_requests/cleaner.py` and `crc/aws/spot_instance_requests/README.md` only drop the dead `Clock` re-export and `clock` field mention from `SpotRuntime` in `runtime.py`, and are disjoint from `origin/fw/docs` and `origin/fw/kind`). `tests/archtest/doc_links.py`, `tests/archtest/test_arch011_doc_links.py`, and `tests/archtest/README.md` are untouched.
5. **Size limits & README Public API sync:** Every touched non-test file is `<= 250` lines (max: `crc/gcp/spot_vm/cleaner.py` at 244 lines, `crc/aws/spot_instance_requests/cleaner.py` at 195 lines, `crc/gcp/spot_vm/disks.py` at 164 lines, `tests/archtest/bans.py` at 159 lines, `scripts/doctor.py` at 97 lines) and every function is `<= 60` lines (`ARCH-004` green). Package `README.md` `Public API` sections are in sync (`ARCH-002` green).
6. **Gates:**
   - `pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt && make check`:
     - `ruff check .`: All checks passed
     - `ruff format --check .`: 376 files already formatted
     - `mypy`: Success: no issues found in 375 source files
     - `lint-imports`: 5 contracts kept, 0 broken
     - `pytest -q -n auto`: **44,651 passed, 1 skipped in 57.34s** (44,652 collected, floor 44,652; +55 from `f8ed6dd`)
     - `python3 -m tests.archtest.test_count check`: `ARCH-008: 44652 tests, floor 44652`
     - Total `make check` runtime: **1m03.9s** wall clock
   - `make doctor`: `doctor: ok (python 3.12.3, ruff, mypy, pytest at pinned versions)`
   - `make smoke-offline`: `17/17 pairs passed`

VERDICT: APPROVE


## Round 2 (Opus)

Re-checked round 1's three fixes, then did every check again myself on `git diff f8ed6dd..HEAD`. I read both third-round audits (`AGENT-NATIVE-AUDIT-3.md` items 7, 8, 10 and the ARCH-001/ARCH-003 rows; `AGENT-NATIVE-AUDIT-3-GPT.md` items 4, 5 and 8). Every F-GATE gap they name is closed: the four cloud `clock_ref` hits, the dead `paramiko`/`beautifulsoup4` pins, the 21 stale baseline names, the `^crc\.py$` mypy exclude, the suite-wide network deny and the dependency audit. `print` in `crc/notify` and the doc drift belong to other lanes.

### Round 1 fixes

All three hold. The `test_arch003_bans.py` boundary/notify self-tests are restored and pass. `test_crc_entry_types.py` uses separate target names for the valid and the broken copy. `_REMEDIATION` names `constraints.txt`.

### Findings

No blocking findings, and I made no code change. Three non-blocking notes:

1. `_deny_network` (`tests/conftest.py`) is a function-scoped autouse fixture. It covers every test body and every function-scoped fixture, but not import or collection time, and not session-scoped or module-scoped fixtures. No such fixture opens a socket today. If one ever needs the guard, a session-level patch in `pytest_configure` would close the gap.
2. `crc/aws/spot_instance_requests/cleaner.py` and its `README.md` are not on the lane's file list. They only drop the `Clock` re-export and the `clock` field mention that `runtime.py` no longer has, so they are a direct consequence of the owned change. Neither file is touched by `fw/docs` or `fw/kind`, so I accept them.
3. `tests/test_doctor.py::test_main_is_ok_on_this_environment` makes the suite fail when the dev tools are not at the pinned versions. This is consistent with `make doctor` and CI installs with `-c constraints.txt`, so I keep it.

### Item-by-item (code opened for each)

1. **constraints.txt.** I created a fresh venv (Python 3.12.3) and ran `pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt`. `pip check` reports "No broken requirements found". `pip freeze` lists the same packages at the same versions as `constraints.txt`; the only differences are name normalisation (`_`/`-`, case). `requests` is still needed (`msrest`, `azure-core`, `oci`, `google-api-core`, `pip-audit`). Nothing in `crc/`, `crc.py` or `scripts/` imports `paramiko`, `bs4` or `requests`. `constraints.txt` is in `frozen_paths`, the manifest and the ARCH-007 self-test.
2. **Ratchet.** `legacy_baseline.txt` holds only a comment. The ARCH-001 real-repo test asserts an empty baseline and an empty `extend-exclude`, and two self-tests show an empty baseline admits no file. The `crc.py` mypy check is done by a strict-mypy wrapper test, because the name clashes with the `crc/` package; the reason is in `pyproject.toml`.
3. **ARCH-003 clocks.** The behaviour is identical. At `f8ed6dd`, `SpotVMCleaner.plan`/`apply` already passed `lambda: context.now` to `preview_instance`/`apply_instance`, and `cleaner.py` is the only caller of `disk_ok_to_delete` in `crc/`. The new `clock=None` fail-closed branch is reachable only from the hash-locked test call, which never reaches the age check. AWS `SpotRuntime.clock` was never read at base (searched for `runtime.clock` and `.clock()`). `clock_ref` is now in `_ALL_KINDS`, with self-tests for all four cloud packages.
4. **Network deny.** The autouse fixture patches `connect`, `connect_ex`, `sendto` and `getaddrinfo`, and lets `AF_UNIX` and loopback through. No test needed changing.
5. **CI and doctor.** CI runs `make doctor`, `make check` and `make smoke-offline`. `make doctor` checks the Python version against `requires-python` and ruff/mypy/pytest against `constraints.txt`. Neither command is mentioned in docs; the lines for F-DOCS are listed above.
6. **pip-audit.** `pip-audit==2.10.1` is pinned in `requirements-dev.txt`. The `audit` job has `continue-on-error: true`, so it reports and never fails the build.

### Behaviour and guardrails

- **CLI help.** I ran `python crc.py --help` on a `f8ed6dd` worktree and on HEAD: the output is byte-identical.
- **Untouched files.** These have no diff against base: `tests/test_jenkins_contract.py`, `tests/legacy/`, `safety_lock.json`, `docs/refactor/BUGS.md`, `requirements.txt`, `crc.py`, `crc/cli`, `crc/core`, `crc/notify`, and the F-DOCS archtest files.
- **Parity tests.** The only change is in `tests/test_aws_spot_instance_requests_parity.py`: two calls no longer pass the unused `clock=` argument. The assertions are unchanged.
- **Sabotage.** I broke each check below locally, saw it fire, and restored the file without committing:
  - Setting the network fixture to `autouse=False` made 5 tests in `test_network_deny.py` fail. They really tried the network and failed after a 4.5-minute connect timeout.
  - Adding `crc/old.py` to `legacy_baseline.txt` failed `test_arch001_baseline_is_empty_and_pyproject_has_no_exclusions`.
  - Appending `BROKEN: int = "x"` to `crc.py` failed `test_crc_py_passes_strict_mypy` with mypy `[assignment]`.
  - Pinning `ruff==0.0.1` in `constraints.txt` made `scripts/doctor.py` exit 1 and print the exact install command.
- **Sizes.** Every touched file is at most 250 lines; the largest is `crc/gcp/spot_vm/cleaner.py` at 244. ARCH-004 (functions at most 60 lines) and ARCH-002 (README Public API) are green.

### Gates (fresh venv, `pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt`)

- `make check`: ruff, format, mypy and lint-imports are green. pytest: **44,651 passed, 1 skipped in 57.57 s** (44,652 collected, ARCH-008 floor 44,652). `make check` took **1m15s** wall time in total.
- `make doctor`: `doctor: ok (python 3.12.3, ruff, mypy, pytest at pinned versions)`.
- `make smoke-offline`: `17/17 pairs passed`.

ROUND 2 VERDICT: APPROVE
