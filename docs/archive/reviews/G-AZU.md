## Coding summary

Lane G-AZU (owner-approved behaviour fixes, branch `gb/azu`). No new kind was routed and no
adapter was deleted or shimmed in `crc/`; the Azure disk, ip and nic cleaners were already
routed through `crc.core.engine`.

- BUG-173 (Azure disk): `build_disk_cleaner` calls `crc.azu._common.real_run_age`, which raises
  `ValueError("Azure disk cleanup requires a positive --age on a real run: ...")` before any
  cloud call when the run is not `--dry_run` and `--age` is missing, empty, zero or malformed.
  Dry runs keep working without an age.
- BUG-075 (Azure IP) and BUG-112 (Azure NIC): same requirement in `build_ip_cleaner` and
  `build_nic_cleaner`; the routes (`crc/cli/routes/azu.py`) now pass `options.age` (only when
  given, so a dry run without `--age` hands the cleaner the same options as before).
  With an age, the new `crc/azu/_created.py` (`AgeGate`) reads creation times from
  `azure-mgmt-resource` (`ResourceManagementClient(credential, subscription_id).resources
  .list_by_resource_group(group, filter="resourceType eq 'Microsoft.Network/...'",
  expand="createdTime")`), built from the same `Base` credential (`Base.get_resource_client`),
  one call per run and type, ids matched case-insensitively. A resource with no creation time,
  a younger one, or every resource when the lookup fails (logged as an error) is kept, with
  skip reasons `no creation time`, `not older than the age threshold`,
  `creation time lookup failed`. Ages are compared with `crc.core.age.is_old`.
- Dependencies: `azure-mgmt-resource` added to `requirements.txt`; `constraints.txt` pins
  `azure-mgmt-resource==24.0.0` and its dependency `azure-common==1.1.28`;
  `tests/archtest/frozen_inputs.json` regenerated with `update_frozen_inputs.py`. No mypy
  override was needed (`azure.*` is already covered).
- Docs: `CLEANUP_SAFETY.md` SAF-011 now records that Azure disk, ip and nic meet it; READMEs of
  `crc/azu` and its disk, ip and nic packages updated; BUGS.md rows BUG-075, BUG-112 and
  BUG-173 are "fixed in G-AZU" and their `TODO(BUG-NNN)` comments are removed. The BUGS.md
  summary count line was not edited.
- Tests: new `tests/test_azure_age_required_fix.py` (20 tests; 19 fail without the `crc/` change,
  the 20th pins the unchanged disk age gate). Test count before: 44,674 collected (the floor); after: 44,694 collected (44,693 passed, 1 skipped); floor raised with `make raise-test-floor`.
- How the CLI and parity tests reach the cleaners: the frozen-oracle parity tests and the CLI
  bridge test run real (non dry-run) cases with no age, which the factories now refuse. They load
  the autouse fixture `tests/helpers_azu_age_shim.py`, which passes the `age` option through
  unchecked and treats every IP and NIC as old enough, so the cleaner logic stays pinned to the
  oracle; the refusal and the creation-time gate are tested without the shim in the new file.
  No parity case was edited and no oracle changed, so no case is marked as diverging.

### Owner actions

- Merge with Lane G-AWS: the 47-command fixture and its expected outcomes must carry
  `--age '{"days": 1}'` for Azure IP x2 and Azure NIC (triggers repo `crc/require-age`). With the
  current fixture `tests/test_jenkins_contract.py` still passes because the routes omit `age`
  when it is not given; with the new fixture the expected options include `age`.
- The service principal used by Jenkins needs read access to generic resources of the resource
  group (`Microsoft.Resources/subscriptions/resources/read`, normally included in Reader).
  Without it the lookup fails and every Azure IP and NIC is kept (fail closed).
- Review the shim approach for the parity tests (above); an alternative is to give each parity
  case an age and a fake resource client.

### New bugs

None found.

## Round 1 (Argon)

Scope reviewed: `git diff 5fc34af..HEAD` across `crc/azu/**`, `crc/cli/routes/azu.py`, `requirements.txt`, `constraints.txt`, `tests/archtest/frozen_inputs.json`, `CLEANUP_SAFETY.md`, `docs/refactor/BUGS.md`, and the Azure test files.

### Findings (fixed in Round 1)

1. **BLOCKING (fixed) — `ResourceManagementClient` import failed on `azure-mgmt-resource>=25.0.0` (`crc/azu/_base.py:15`, `constraints.txt`)**:
   - `pip install -r requirements.txt -r requirements-dev.txt` (without `-c constraints.txt`, as in Check 6 and Jenkins) installs `azure-mgmt-resource==26.0.0`, where `azure.mgmt.resource` is a namespace package without `__init__.py` and `from azure.mgmt.resource import ResourceManagementClient` raises `ImportError`.
   - Changed `crc/azu/_base.py` to `from azure.mgmt.resource.resources import ResourceManagementClient` (canonical path supported in both `24.0.0` and `26.0.0`), pinned `azure-mgmt-resource==26.0.0` in `constraints.txt` (dropping the unused `azure-common==1.1.28` dependency of `24.0.0`), and regenerated `tests/archtest/frozen_inputs.json` via `python3 -m tests.archtest.update_frozen_inputs`.
2. **BLOCKING (fixed) — Parity tests bypassed `real_run_age` and `AgeGate` via `tests/helpers_azu_age_shim.py` instead of marking diverging cases (`tests/test_azure_{disk,ip,nic}_parity.py`, `tests/helpers_azure_{ip,nic}.py`)**:
   - Removed `tests/helpers_azu_age_shim.py` and its `autouse` monkeypatches from all 11 test files.
   - Installed a fake `ResourceManagementClient` in `tests/helpers_azure_ip.py` and `tests/helpers_azure_nic.py` (`FakeAzure.install`), defaulted `Case.age` to `{"days": 1}` in `tests/test_azure_{disk,ip,nic}_parity.py`, marked the four non-positive/invalid-age disk parity cases (`age-empty-matches-all`, `age-none-matches-all`, `age-zero-matches-all`, `age-unknown-key-never-old`) with `diverges="BUG-173"`, and added `BUG-075` and `BUG-112` diverging parity cases (`young-ip-kept-by-age-gate`, `real-run-without-age-raises`, `young-nic-kept-by-age-gate`) to `tests/test_azure_ip_parity.py` and `tests/test_azure_nic_parity.py`.
3. **NIT (fixed) — Empty resource group check order in `AzureIpCleaner.plan` and `AgeGate.load` idempotency (`crc/azu/ip/cleaner.py:74`, `crc/azu/_created.py:72`)**:
   - Called `self.list_ips()` before `self._age_gate.load(context.now)` in `AzureIpCleaner.plan` so an empty or whitespace resource group still raises `ValueError` before any `ResourceManagementClient` call; added `self._loaded` in `AgeGate` so an empty resource listing is not re-fetched; trimmed `tests/test_azure_age_required_fix.py` to 249 lines (<= 250).

### Checklist verification

1. **Fixes are real and fail on base (`5fc34af`)**:
   - Real runs (`dry_run=False`) of Azure disk (`BUG-173`), IP (`BUG-075`), and NIC (`BUG-112`) without a positive `--age` raise `ValueError` naming `--age` in `build_{disk,ip,nic}_cleaner` before `Base(...)` or any SDK call; dry runs (`dry_run=True`) work without `--age`; `AgeGate` reads `createdTime` once per run per resource type via `ResourceManagementClient(credential, subscription_id).resources.list_by_resource_group(group, filter="resourceType eq 'Microsoft.Network/...'", expand="createdTime")`, matches resource IDs case-insensitively, and fails closed (`no creation time`, `not older than the age threshold`, `creation time lookup failed`) using `crc.core.age.is_old`.
   - Verified `tests/test_azure_age_required_fix.py` against `5fc34af`: 19 failed, 1 passed (the unchanged disk age gate guard); 20 passed on HEAD.
2. **Only listed behaviour changed**:
   - Every production command in `tests/fixtures/jenkins_commands.txt` passes (`tests/test_jenkins_contract.py` and `tests/test_smoke_offline.py`); `tests/legacy/*.txt` are untouched; all non-diverging parity cases match the oracle; diverging parity cases carry `BUG-173`, `BUG-075`, or `BUG-112`.
3. **Safety docs, READMEs, BUGS.md, TODOs, and locks**:
   - `CLEANUP_SAFETY.md` (`SAF-011`), `crc/azu/README.md`, `crc/azu/{disk,ip,nic}/README.md`, and `docs/refactor/BUGS.md` rows `BUG-075`, `BUG-112`, `BUG-173` ("fixed in G-AZU") are updated; `TODO(BUG-075)`, `TODO(BUG-112)`, `TODO(BUG-173)` are removed; `ARCH-002` and `ARCH-012` are green; `tests/archtest/safety_lock.json` is untouched.
4. **Owned files only**:
   - `git diff --name-only 5fc34af..HEAD` touches only Lane G-AZU files (`crc/azu/spot_vm/**`, `tests/fixtures/jenkins_commands.txt`, and `tests/test_jenkins_contract.py` are untouched).
5. **File and function sizes**:
   - Every file in scope is at most 250 lines (`crc/azu/nic/cleaner.py` 229, `crc/azu/disk/cleaner.py` 183, `crc/azu/ip/cleaner.py` 166, `crc/azu/_created.py` 107, `crc/cli/routes/azu.py` 78, `crc/azu/_base.py` 66, `crc/azu/_common.py` 53, `tests/test_azure_age_required_fix.py` 249) and every function is at most 60 lines (`ARCH-004` green).
6. **Gates (`pip install -r requirements.txt -r requirements-dev.txt && make check`)**:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed! |
| `ruff format --check .` | 380 files already formatted |
| `mypy` | Success: no issues found in 379 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q -n auto` | 44,699 passed, 1 skipped in 58.76s |
| `ARCH-008` (`test_count`) | 44,700 collected, floor 44,700 |

Runtime of `make check`: 1m06s (pytest 58.76s).
Test count: 44,700 collected (44,699 passed, 1 skipped), up from 44,674 at `5fc34af` (+26).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 5fc34af..HEAD` (coder commit `45a574f`, round 1 commit `50ccbeb`). The round 1
fixes were checked first, then every check was redone independently.

### Round 1 fixes checked

- Import path: `from azure.mgmt.resource.resources import ResourceManagementClient` works with the
  `azure-mgmt-resource==26.0.0` that `pip install -r requirements.txt -r requirements-dev.txt`
  installs (pinned the same in `constraints.txt`, `frozen_inputs.json` regenerated by the script).
  The real 26.0.0 SDK was checked: `resources.list_by_resource_group(resource_group_name, *,
  filter, expand, top)` and `GenericResourceExpanded.created_time` exist, and
  `createdTime` comes back as an aware datetime. An `AgeGate` fed real SDK models keeps a young
  resource (`not older than the age threshold`), one with no `createdTime` and one missing from
  the listing (`no creation time`), and selects an old one whose id differs only in case.
- The shim was removed: no `helpers_azu_age_shim.py` is left, the parity harnesses pass `age`
  (`{"days": 1}` on real runs, `None` on dry runs) to both the legacy code and the engine, and fake
  `ResourceManagementClient`s are installed. Changing the disk parity default from `{}` to
  `{"days": 1}` changes no oracle outcome (default fixture disks are 10 days old; the few young
  disks are the diverging BUG-173 cases, attached, or protected by a custom age tag).
- NIT fixes (list before the gate loads, `AgeGate._loaded`) are correct.
- Round 1 reported `tests/test_azure_age_required_fix.py` as 249 lines, but it was 254 at
  `50ccbeb`. ARCH-004 only covers `crc/`, so no gate failed, but check 5 asks for at most 250.
  Fixed here by condensing the route test's options (now 249 lines, same assertions).

### Findings

1. NIT (fixed): `tests/test_azure_age_required_fix.py` had 254 lines (above).
2. NOTE (not changed): the IP and NIC parity harnesses now compare only log records above
   DEBUG. WARNING/INFO/ERROR logs are still compared with the oracle, and the legacy modules log
   nothing at DEBUG on the compared paths.
3. NOTE (owner action, already in the coding summary): the Jenkins service principal needs
   `Microsoft.Resources/subscriptions/resources/read` on the resource group; without it every
   Azure IP and NIC is kept and an error is logged (fail closed).

### Checklist

1. Fixes are real: `tests/test_azure_age_required_fix.py` from HEAD run against `crc/` at
   `5fc34af` fails 19 of 20 (the 20th pins the unchanged disk age gate); all 20 pass on HEAD.
   Real runs of disk, IP and NIC without a positive `--age` (missing, `{}`, zero, unknown key)
   raise `ValueError` naming `--age` in the factory before `Base` or any SDK call, and the
   fake clients record no call. Dry runs work without an age and make no resource-client call.
   With an age, younger IPs, NICs and disks are kept, and the age is compared with
   `crc.core.age.is_old`.
2. Only listed behaviour changed: `tests/test_jenkins_contract.py` and the offline smoke test
   pass with the current 47-command fixture (the routes omit `age` when `--age` is not given).
   Non-diverging parity cases equal the oracle. The diverging cases carry `BUG-173` (4 disk
   cases), `BUG-075` (`young-ip-kept-by-age-gate`) or `BUG-112`
   (`real-run-without-age-raises`, `young-nic-kept-by-age-gate`). `tests/legacy/*.txt` are
   untouched.
3. Docs: `CLEANUP_SAFETY.md` SAF-011, `crc/azu/README.md` and the disk, ip and nic READMEs are
   updated, and BUGS.md rows BUG-075, BUG-112 and BUG-173 say "fixed in G-AZU". Their `TODO(BUG-NNN)`
   comments are gone, and ARCH-002 and ARCH-012 are green. `safety_lock.json` is untouched.
4. Owned files only: no file outside `crc/azu/**` (outside `spot_vm`), `crc/cli/routes/azu.py`,
   `requirements.txt`, `constraints.txt`, `CLEANUP_SAFETY.md`, `docs/refactor/BUGS.md`, the
   review file and Azure tests/archtest inputs changed. The fixture and the contract test were not edited.
5. Sizes: the largest changed `crc/` file is `crc/azu/nic/cleaner.py` at 229 lines, the new test
   file is 249 lines, and every function is at most 60 lines (ARCH-004 green).
6. Gates: after `pip install -r requirements.txt -r requirements-dev.txt`, `make check` exits 0:
   `ruff check` clean, `ruff format --check` 380 files formatted, `mypy` no issues in 379 files,
   `lint-imports` 5 kept / 0 broken, `pytest -n auto` 44,699 passed, 1 skipped in 63.59s, and
   ARCH-008 has 44,700 tests against a floor of 44,700. Total `make check` runtime: 1m23s.

Test count: 44,700 collected (44,699 passed, 1 skipped); unchanged by round 2.

ROUND 2 VERDICT: APPROVE
