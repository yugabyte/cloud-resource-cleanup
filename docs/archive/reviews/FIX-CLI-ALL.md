## Coding summary

- Bugs fixed: BUG-017, BUG-018 (`crc/cli/dispatch.py`, new `validate_targets` called first in `run_dispatch`).
  - BUG-017: every (cloud, resource) pair is checked before the first cleaner. An unsupported pair (`keypair`/`kms` off AWS, `nic` off Azure, `ip` on OCI) with an explicit cloud raises `ValueError` (same message as the `CRC` method) before anything runs; under `--cloud all` it is skipped with a WARNING and a `RUN START`/`SKIPPED reason=unsupported_cloud_resource`/`RESULT` log triple, and no `CRC` method is called for it.
  - BUG-018: `--cloud all --resource disk|all` can never complete (AWS EBS is opt-in for `--cloud aws --resource disk` only), so it now raises the existing opt-in `ValueError` before any `CRC` is built or cleaner runs. It is not made to run. `--cloud aws --resource all` already fails in `check_flag_combinations` before any cleaner (unchanged). The breaker in `_disk` stays as defence in depth.
- Bugs left: none.
- Files changed: `crc/cli/dispatch.py`, `crc/cli/README.md`, `tests/test_cli_main_parity.py`, `tests/test_core_selection.py` (cases that trigger the bugs are derived from what the oracle did, marked BUG-017/BUG-018, and assert the exact new outcome; all other cases still equal the oracle; plus a test that both markers occur and differ from the oracle), new `tests/test_cli_dispatch_fix.py`, this file.
- Tests (`pytest -q`): 42444 collected at 182466d, 42476 after (+32: 40 new tests in `tests/test_cli_dispatch_fix.py` and one parity check, minus parametrised skips removed). `tests/test_core_selection.py` (the `crc.core.selection` model compared against `main()`) also changed: its three comparison tests now expect the BUG-017 outcome for unsupported pairs (explicit cloud raises with nothing reached, `--cloud all` skips with the warning); every other pair is still compared unchanged.
- Owner actions: none (no locked test or `CLEANUP_SAFETY.md` change needed).

## Notes

- Production Jenkins commands: none reach this code with `--cloud all` or an unsupported pair, so no production command changes.
- On the new up-front errors `main()` has already run `set_max_age`, so `MAX_AGE` stays set and "MAX_AGE reset." is not printed, as for every other error after that point.
- The pair table lives in `dispatch.SUPPORTED_CLOUDS`; it mirrors the cloud guards inside `ResourceOps` (`delete_keypairs`, `delete_nic`, `delete_kms`, `_get_ip`).

## New bugs

- `crc/cli/dispatch.py` `_snapshot` ignores the cloud: `--cloud all --resource snapshot` runs the AWS snapshot cleanup once per cloud (4 times), and `delete_vpc` builds the AWS VPC cleaner for every cloud. Exists on main: yes (`crc.py` snapshot branch and `delete_vpc`, same shape). Not fixed.

## Owner decision

- `crc/core/selection.py` (`build_selection`) still models `main` and returns unsupported pairs (for example `azure`+`keypair`) as selected. It is outside this rung's files, so it is unchanged; FIX-CLI-ROUTE or a later rung should make it agree with `dispatch.validate_targets`. Its test now documents the BUG-017 difference.

## Round 1 (Argon)

Reviewed `git diff 182466d..HEAD` against the `FIX-CLI-ALL` task, `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md` and the wave `FIX-1` overrides.

### Findings

- **BLOCKING (fixed):** `crc/cli/dispatch.py:251` called `crc = make_crc(context, cloud)` at the top of `for cloud in targets.clouds:`, before checking `if (cloud, resource) in skipped:`. Because `CRCBase.__init__` (`crc/cli/base.py:50-51`) raises `ValueError("project_id is mandatory Parameter for GCP")` when `cloud == "gcp"` and `project_id` is unset, running `--cloud all --resource keypair`, `--cloud all --resource nic --resource_group <rg>` or `--cloud all --resource kms --kms_key_description <d> --kms_user <u>` without `--project_id` still deleted on the supported cloud (`aws` or `azure`) and then raised `ValueError` on the skipped `gcp` iteration. Moved `make_crc(context, cloud)` after the `if (cloud, resource) in skipped:` branch so `CRC` is only constructed for clouds with a non-skipped resource (`crc/cli/dispatch.py:251-259`), updated `tests/test_cli_main_parity.py:589-594`, and added `test_bug_017_cloud_all_real_crc_does_not_build_skipped_clouds` plus `SKIPPED ... reason=unsupported_cloud_resource count=1` log assertions in `tests/test_cli_dispatch_fix.py:100-149`.
- **New bug noted (not fixed):** Under `--cloud all` for resources where `gcp` is supported (`vm`, `ip`, `disk`), `CRCBase.__init__` (`crc/cli/base.py:50-51`, `main:crc.py:87-88`) checks `if cloud == "gcp" and not project_id:` when `make_crc(context, "gcp")` runs in the loop, so omitting `--project_id` raises `ValueError` after `aws` and `azure` already ran (the `run_dispatch` counterpart of `BUG-343`). Exists on `main`: yes.

### Verification

1. **Bug fixes (`BUG-017`, `BUG-018`):**
   - `BUG-017`: `validate_targets` checks every `(cloud, resource)` pair before the loop. Explicit unsupported pairs (`keypair`/`kms` off `aws`, `nic` off `azure`, `ip` on `oci`) raise the exact `ResourceOps` `ValueError` before `make_crc` or any cleaner runs; under `--cloud all`, unsupported pairs are skipped with `_LOG.warning("%s is not supported on %s; skipped under --cloud all", resource, cloud)` and a `RUN START` / `SKIPPED cloud=<c> resource=<r> reason=unsupported_cloud_resource count=1` / `RESULT ... skipped=1` triple without constructing `CRC` or calling any cleaner for the skipped cloud.
   - `BUG-018`: `--cloud all --resource disk` and `--cloud all --resource all` raise `ValueError(AWS_DISK_OPT_IN_MESSAGE)` in `validate_targets` before any `CRC` is built or any cleaner runs; `_disk` retains the breaker as defence in depth.
   - `tests/test_cli_dispatch_fix.py` fails on `182466d` and passes on `HEAD`.
2. **Direction rule & parity (`tests/test_cli_main_parity.py`, `tests/test_core_selection.py`, `tests/legacy/crc_main.txt`):**
   - Nothing is deleted, stopped or released that was kept before; fixes only fail closed earlier or skip unsupported `--cloud all` pairs.
   - `tests/legacy/crc_main.txt` is untouched. `diverging_outcome` in `tests/test_cli_main_parity.py` derives `BUG-017` and `BUG-018` triggers from the oracle outcome and asserts the exact new `Outcome`; all other cases assert exact equality with the oracle, and `test_diverging_cases_exist_and_differ_from_the_oracle` verifies both bug IDs trigger and differ from the oracle.
3. **Production commands & guardrails:**
   - No production Jenkins command uses `--cloud all` or an unsupported `(cloud, resource)` pair; all supported explicit pairs run unchanged.
   - No locked safety test, `tests/archtest/safety_lock.json`, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`, `docs/refactor/BUGS.md` or `CLEANUP_SAFETY.md` was edited.
   - `crc/cli/README.md` `## Public API` and `## Safety Invariants` are updated and pass `ARCH-002`.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt`, then `make check`:
- `ruff check .`: All checks passed!
- `mypy`: Success: no issues found in 219 source files
- `lint-imports`: Contracts: 5 kept, 0 broken
- `pytest -q`: 42479 passed (up from 42444 on `182466d`, +35)

VERDICT: APPROVE


## Round 2 (Opus)

Reviewed `git diff 182466d..HEAD` independently, after checking Round 1's fix (lazy `make_crc` after the skip check). Round 1's fix is correct: `CRC` is only built for a cloud with a non-skipped pair, and for every non-skipped input it is built just before that cloud's first cleaner, as before.

### Findings

- **BLOCKING (fixed): the direction rule was broken for `--cloud all --resource nic`.** Clouds run in the order `aws, azure, gcp, oci`. On `182466d` (and `main`) the first pair `aws`+`nic` raised `NICs operation is only supported on Azure.` before any delete, so nothing was deleted. Skipping it let `azure`+`nic` run, so Azure NICs got deleted where `main` deleted nothing. `test_bug_017_cloud_all_real_crc_does_not_build_skipped_clouds[nic]` asserted that deletion. The parity tests missed it because their recording fake `CRC` never raises for an unsupported cloud, so the oracle appeared to call `delete_nic` on all four clouds. Fix in `crc/cli/dispatch.py` `validate_targets`: under `--cloud all`, unsupported pairs are skipped only when no supported pair comes after the first unsupported one (that matches what `main` ran: `keypair`/`kms` run aws only, `ip` runs aws/azure/gcp). Otherwise the run fails closed with the first pair's `ValueError` message before any `CRC` is built. For the CLI this only affects `--cloud all --resource nic`; `--resource all` hits the BUG-018 error first. Tests:
  - `tests/test_cli_dispatch_fix.py`: removed the nic skip expectations and added `test_bug_017_cloud_all_nic_raises_before_any_cleaner` and `test_bug_017_cloud_all_nic_real_crc_deletes_nothing`. Both fail on `4346ab7`; the module does not import on `182466d`.
  - `tests/test_cli_main_parity.py` `diverging_outcome`: under `--cloud all`, a case where a supported call follows the first unsupported call now expects the up-front `ValueError` (still marked BUG-017, oracle untouched).
  - `tests/test_core_selection.py` `raises_up_front`: same rule.
  - `crc/cli/README.md` Safety Invariants: the BUG-017 line now states the nic case.
- **NIT (fixed):** `tests/test_cli_logger.py::test_each_cloud_and_resource_pass_gets_a_header_and_a_result` used synthetic `Targets` (`[aws, gcp]` × `[keypair, ip]`, cloud_arg `all`), which the CLI cannot produce. On `main` the real `CRC` would stop at `gcp`+`keypair`. I swapped `keypair` for `vm`, so the test still checks the log header and result per pass.
- New bugs: none beyond those already listed above.

### Checks

1. BUG-017: an explicit unsupported pair raises before any `CRC` or cleaner. Under `--cloud all`, `keypair`, `kms` and `ip` skip the unsupported clouds with a WARNING and a `SKIPPED reason=unsupported_cloud_resource` log line, while `nic` fails closed. BUG-018: `--cloud all --resource disk|all` raises the EBS opt-in error before any cleaner. The tests fail on `182466d` and pass now.
2. Direction rule: after this fix, no input deletes more than on `182466d`. Supported explicit pairs run unchanged (`test_supported_explicit_pairs_run_unchanged`). Parity oracles in `tests/legacy/` are untouched, each diverging case is marked BUG-017 or BUG-018 with the exact new outcome, and nothing is loosened globally.
3. Production commands: none listed; no supported explicit pair changes.
4. Fail-closed paths raise in `validate_targets`, before the loop, before any `make_crc` or SDK call.
5. No locked safety test, `safety_lock.json`, `tests/legacy/`, PLAN/STATUS/BUGS, `CLEANUP_SAFETY.md` or `.cursor/**` edited.
6. `crc/cli/README.md` Public API (`validate_targets`, `is_supported_pair`) and Safety Invariants are updated; ARCH-002 passes.
7. No bug is left unfixed; the existing Owner decision about `crc/core/selection.py` stands.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt`, then `make check`: ruff all checks passed; mypy no issues in 229 source files; lint-imports 5 kept, 0 broken; pytest **42479 passed**.

ROUND 2 VERDICT: APPROVE
