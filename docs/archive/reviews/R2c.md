# R2c review

Scope reviewed: `git diff 27e6870..41b5dfe` (commit `41b5dfe`, R2c bug register
in `docs/refactor/BUGS.md`, directory `README.md` files across the repository,
and extended `ARCH-002` in `tests/archtest/readmes.py` and
`tests/archtest/test_arch002_readmes.py`) against `main`, `AGENTS.md`,
`CLEANUP_SAFETY.md`, `docs/refactor/FILTER_VARIANTS.md`,
`docs/refactor/STATUS.md`, and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

## 1. `docs/refactor/BUGS.md` verification (`git show main:<path>`)

Verified all 39 rows in `docs/refactor/BUGS.md` against `git show main:<path>`,
including both `critical` rows (`BUG-001`, `BUG-002`), all seven `high` rows
(`BUG-003`..`BUG-009`), all nine `medium` rows (`BUG-010`..`BUG-018`), and the
21 `low` rows (`BUG-019`..`BUG-039`):

| ID | Risk | `main` location | Verified via `git show main:<path>` | Real-world effect |
|----|------|-----------------|-------------------------------------|-------------------|
| `BUG-001` | critical | `crc/gcp/ip.py:91-95` | Confirmed (`has_matching_exception_regex = self.exception_regex and not any(regex in name for regex in self.exception_regex)` then `and not has_matching_exception_regex`) | With `--exception_regex` set, addresses whose name matches an exception pattern are appended to `ips_to_delete` and deleted, while non-matching names are skipped (`deletes what it should keep`). |
| `BUG-002` | critical | `crc/azu/vm.py:134`, `:257`, `:273`, `:279`, `:293-294` | Confirmed (`virtual_machines.list_all()` at `:134`, while `_get_vm_status`, `_delete_vm`, `delete_vm_primary_nic`, and `_stop_vm` pass `self.base.resource_group`) | A matching VM in another resource group triggers `instance_view`, `begin_delete_vm_and_wait`, NIC deletion, or `begin_power_off` against any same-named VM in `self.base.resource_group` (`deletes what it should keep`). |
| `BUG-003` | high | `crc/azu/vm.py:293-294` | Confirmed (`virtual_machines.begin_power_off(self.base.resource_group, vm_name)`) | Stopping an Azure VM powers off the OS without deallocating compute (`begin_deallocate`), so Azure continues billing (`keeps what it should delete`). |
| `BUG-004` | high | `crc/service.py:268-274`, `:298-318`; callers `crc/aws/disk.py:400`, `crc/aws/kms.py:155`, `crc/aws/vm.py:223`, `crc/azu/disk.py:114`, `crc/azu/vm.py:141`, `crc/gcp/disk.py:121`, `crc/gcp/vm.py:254`, `crc/oci/disk.py:146`, `crc/oci/vm.py:118` | Confirmed (all 9 cleaner call sites verified with `git grep -n get_retention_age main`) | When a resource carries a custom retention age tag whose value is empty or unparseable, `get_retention_age` logs and returns `None`, so `retention_age or self.age` falls back to `--age` instead of skipping the resource (`deletes what it should keep`). |
| `BUG-005` | high | `crc/gcp/disk.py:80`, `:150-161` | Confirmed (`self.notags = notags` at `:80`; `_should_skip_disk` at `:150-161` checks only `exception_regex` and `exception_labels`) | `--notags` is ignored by `GCP_Disk`, so disks matching `notags` are still deleted (`deletes what it should keep`). |
| `BUG-006` | high | `crc/gcp/disk.py:81` | Confirmed (`self.name_regex = name_regex` at `:81`, never read elsewhere in `crc/gcp/disk.py`) | `--name_regex` is ignored by `GCP_Disk`, so disks whose names do not match `--name_regex` are still deleted (`deletes what it should keep`). |
| `BUG-007` | high | `crc/aws/kms.py:137-142` | Confirmed (`filter_set = {(k, v) for k, values in self.filter_tags.items() for v in values}`) | A tag filter key with an empty value list (`{"k": []}`) produces no `(k, v)` pairs, leaving `filter_set` empty so `filter_set.issubset(response_set)` matches every KMS key (`deletes what it should keep`). |
| `BUG-008` | high | `crc/aws/kms.py:131-136`, `:155-158`; `crc/service.py:302` | Confirmed (`list_resource_tags` returns `{"TagKey": ..., "TagValue": ...}` dicts at `:131-136`, while `get_retention_age` checks `tag.get("Key") == key` at `crc/service.py:302`) | Custom retention age tags on KMS keys are never recognized and always fall back to `self.age` (`deletes what it should keep`). |
| `BUG-009` | high | `crc.py:1629-1647` | Confirmed (`elif resource == "snapshot": snapshot = Snapshot(dry_run=dry_run, age_days=90, exception_tags=exception_tags); snapshot.delete()`) | Running `--resource snapshot` on any cloud (`azure`, `gcp`, `oci`, or `all`) executes AWS `Snapshot(age_days=90).delete()` and sends no Slack or InfluxDB notifications (`deletes what it should keep`). |
| `BUG-010` | medium | `crc/azu/vm.py:257-259`, `:146-157` | Confirmed (`instance_view(...).statuses[1].display_status` inside `try ... except Exception` at `:146-157`) | VMs with fewer than 2 status entries (such as failed provisioning) raise `IndexError`, which is caught and logged so those VMs are never cleaned (`keeps what it should delete`). |
| `BUG-011` | medium | `crc/aws/elastic_ips.py:102-107`, `:127` | Confirmed (`all(in_no_tags and key in self.notags and (...),)` passes a single `bool` to `all()`, and `:148` only catches `CONNECTIVITY_ERRORS`) | Passing `--notags` to AWS Elastic IP cleanup raises `TypeError: 'bool' object is not iterable` on any tagged EIP not skipped by `exception_tags`, aborting the run (`run fails`). |
| `BUG-012` | medium | `crc/aws/vpc.py:94`, `:160-167` | Confirmed (`self.vpc_ids_to_delete.extend(...)` runs at `:161` before `_should_skip_vpc(vpc)` at `:167`, which passes a boto3 `Vpc` object to `for tag in tags:`) | Every matched VPC is recorded as deleted before skip checks, and non-empty `exception_tags`/`notags` in a non-dry run iterates the `Vpc` object instead of a tag list (`run fails`). |
| `BUG-013` | medium | `crc/azu/ip.py:70-78` | Confirmed (`key in ip.tags` without guarding `ip.tags` when `None`) | Untagged Azure public IPs (`ip.tags is None`) raise `TypeError` whenever `exception_tags` or `notags` is non-empty (`run fails`). |
| `BUG-014` | medium | `crc/aws/kms.py:137-145` | Confirmed (`filter_set.issubset(response_set)` requires every `(k, v)` pair simultaneously) | A KMS tag filter with multiple allowed values for one key (`{"env": ["dev", "test"]}`) can never match a single-valued KMS tag (`keeps what it should delete`). |
| `BUG-015` | medium | `crc/aws/kms.py:90-91`, `:147`, `:181-183` | Confirmed (`_should_skip_kms` indexes `tag["Key"]`/`tag["Value"]` on `TagKey`/`TagValue` dicts inside `try ... except:` at `:130-183`) | With `exception_tags` set, every tagged KMS key raises `KeyError` and is skipped by the bare `except:` (`keeps what it should delete`). |
| `BUG-016` | medium | `crc/service.py:268-274`, `:306`, `:314` | Confirmed (`_parse_literal` returns `ast.literal_eval(value)` without `require_positive_age`) | Unvalidated custom age tag literals such as `{'days': 1e999}` (`inf`) or zero/negative dicts are returned as-is (`deletes what it should keep`). |
| `BUG-017` | medium | `crc.py:799-800`, `:823` | Confirmed (`delete_keypairs` raises `ValueError` when `self.cloud != "aws"`; `delete_nic` raises when `self.cloud != "azure"`) | `--cloud all --resource keypair` deletes AWS keypairs and then raises `ValueError` on `azure` (`run fails`). |
| `BUG-018` | medium | `crc.py:1367`, `:1394`, `:1546-1552` | Confirmed (`aws_disk_opt_in = clouds == "aws" and resources == "disk"`, checked at `:1546`) | `--cloud all --resource all` and `--cloud all --resource disk` always raise `ValueError` on the first pair `("aws", "disk")` (`run fails`). |

Completeness check:
- Every bug and quirk recorded in `docs/refactor/STATUS.md` (R1a, R1b, R2a, R2b),
- Every item in `docs/refactor/FILTER_VARIANTS.md` section 7 (items 1..7), and
- All three Azure VM bugs from `docs/refactor/PLAN.md` (`BUG-002`, `BUG-003`, `BUG-010`)
are present in `docs/refactor/BUGS.md`.

## 2. Directory `README.md` verification

Checked all 11 new `README.md` files (`crc/README.md`, `crc/aws/README.md`,
`crc/azu/README.md`, `crc/gcp/README.md`, `crc/oci/README.md`, `tests/README.md`,
`docs/README.md`, `docs/refactor/README.md`, `docs/refactor/reviews/README.md`,
`.cursor/rules/README.md`, `.cursor/skills/crc-python-refactor/README.md`) plus
`crc/core/README.md` and `tests/archtest/README.md`:

- Every `README.md` has the four required sections (`## Purpose`, `## Public API`,
  `## Safety Invariants`, `## Extension Rules`).
- Spot-checked `crc/README.md`, `crc/aws/README.md`, `crc/azu/README.md`,
  `crc/gcp/README.md`, and `crc/oci/README.md` against the AST of every `.py`
  module in those directories: every module, public class, helper function, and
  `delete()` / `stop()` method matches the code.
- Verified all `SAF-NNN` IDs against `CLEANUP_SAFETY.md` and all `BUG-NNN`
  references against `docs/refactor/BUGS.md` for each package (`crc`: `BUG-004`,
  `BUG-016`, `BUG-024`..`BUG-027`; `crc/aws`: `BUG-004`, `BUG-007`, `BUG-008`,
  `BUG-011`, `BUG-012`, `BUG-014`, `BUG-015`, `BUG-020`..`BUG-022`; `crc/azu`:
  `BUG-002`..`BUG-004`, `BUG-010`, `BUG-013`, `BUG-019`; `crc/gcp`: `BUG-001`,
  `BUG-004`..`BUG-006`, `BUG-023`; `crc/oci`: `BUG-004`).

## 3. `tests/archtest/readmes.py` (`ARCH-002`)

## BLOCKING (fixed)

1. **Vacuous `ARCH-002` check on `docs/` (and any parent directory whose only direct file is `README.md`) and missing `.cursor/skills` in `README_EXEMPT_DIRS`** (`tests/archtest/readmes.py:42, 116-126`, `tests/archtest/test_arch002_readmes.py:33-38, 88-130`):
   - `directories_with_files(root)` only included directories that *directly* contained files (`if files:`), counting `README.md` itself as a file. In `docs/`, `docs/README.md` is the only direct file (all other files under `docs/` are in `docs/refactor/`). If `docs/README.md` was deleted, `docs/` had `files == []`, so `directories_with_files(REPO_ROOT)` dropped `docs/` and `check_readmes` passed silently without requiring `docs/README.md`.
   - Likewise, in `test_arch002_selftest_fires_on_any_directory_with_files`, writing `docs/refactor/notes.md` only fired `ARCH-002` on `docs/refactor/` and did not fire on `docs/`, and `.cursor/skills` was only skipped because it had no direct files rather than being listed in `README_EXEMPT_DIRS`.
   - Fixed `directories_with_files(root)` in `tests/archtest/readmes.py` to include every non-exempt ancestor directory up to `root` whenever a directory in its non-excluded subtree contains non-bytecode files, and added `".cursor/skills"` to `README_EXEMPT_DIRS`.
   - Added `test_arch002_selftest_fires_on_parent_directory_without_direct_files` and `test_arch002_selftest_every_repo_directory_fires_when_readme_removed` (which simulates removing `README.md` from each of the 13 checked directories in `REPO_ROOT`, including `docs/`, and asserts `check_readmes` fires with `ARCH-002` and `REMEDIATION:` on every single one).

## NIT (left)

- None.

## 4. `AGENTS.md` and scope check

- `AGENTS.md` is 78 lines (`<= 120` limit), includes `docs/refactor/BUGS.md` in the doc map (`AGENTS.md:22`), and has hard rule 11 (`AGENTS.md:63`).
- No change to `crc.py`, `crc/**` Python code, or pre-existing non-ARCH-002 tests (`git diff 27e6870..HEAD`).

## 5. Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 71 source files |
| `lint-imports` | 3 kept, 0 broken ("Cloud packages are independent", "Core never imports cloud packages or services", "Core never imports cloud or telemetry SDKs") |
| `pytest -q` | 996 passed in 3.27s |

Test count: 984 before R2c (`27e6870`), 994 on `41b5dfe`, 996 after review fixes.

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff 27e6870..HEAD` (commits `41b5dfe` and round 1 `2d63513`). `crc.py`,
`crc/aws`, `crc/azu`, `crc/gcp`, `crc/oci` are identical to `main` apart from the new
`README.md` files (`git diff main --stat`), so legacy behaviour parity holds.

### Round 1 fixes assessment

- `directories_with_files` (`tests/archtest/readmes.py:116-132`): correct. The ancestor walk
  stops at the root, skips only `README_EXEMPT_DIRS`, and excluded trees are pruned before
  they are visited. On this checkout it returns the 13 directories that `git ls-files` implies,
  minus the exempt ones (`.`, `.cursor`, `.cursor/skills`) and excluded ones (`.claude`, `.github`).
- `.cursor/skills` in `README_EXEMPT_DIRS`: needed after the ancestor walk; matches the R2c brief.
- New self-tests are not vacuous: `test_arch002_selftest_fires_on_parent_directory_without_direct_files`
  fails on the pre-fix walk, and `test_arch002_selftest_every_repo_directory_fires_when_readme_removed`
  hides the real `README.md` from both `os.walk` and `Path.is_file`, so `docs/` must fire through
  the ancestor walk. No test was deleted or weakened. `test_arch002_selftest_ignores_legacy_only_packages`
  became `..._legacy_only_package_skips_symbol_sync` plus `..._still_needs_readme` and
  `..._needs_sections`, which is stricter and was in the R2c brief. ARCH-002 tests are not in
  `safety_lock.json`.

### BLOCKING (fixed)

1. Unjustified `# type: ignore[no-untyped-def]` on `fake_walk` in round 1's
   `test_arch002_selftest_every_repo_directory_fires_when_readme_removed` (checklist section 5).
   Replaced with a real return type `Iterator[tuple[str, list[str], list[str]]]`
   (`tests/archtest/test_arch002_readmes.py`). No behaviour change; mypy strict passes.

### New bug recorded

- `BUG-040` (low, cosmetic): `--cloud all --resource snapshot` runs AWS
  `Snapshot(age_days=90).delete()` once per cloud, because the snapshot branch is inside
  `for cloud in clouds` (`main:crc.py:39`, `:1532`, `:1544`, `:1629-1643`). Added to
  `docs/refactor/BUGS.md` (now 40 rows, low 22).

### Citations re-checked with `git show main:<path>`

`BUG-001` (`crc/gcp/ip.py:91-95`), `BUG-002`/`BUG-003`/`BUG-010` (`crc/azu/vm.py:134`, `:257-259`,
`:293-294`), `BUG-007`/`BUG-008`/`BUG-014`/`BUG-015` (`crc/aws/kms.py:131-158`), `BUG-009`
(`crc.py:1629-1647`), `BUG-011` (`crc/aws/elastic_ips.py:89-109`, `:125-127`; `all(<bool>,)` raises
`TypeError` on the first tag once `notags` is set), `BUG-017` (`crc.py:799-800`, `:823`), `BUG-018`
and `BUG-035`/`BUG-036` (`crc.py:1363-1397`, `:1546-1552`), `BUG-019` (`crc/azu/disk.py:16`,
`:124-126`). All correct. SAF IDs cited in the cloud READMEs (SAF-005, -014, -028, -038, -041,
-042, -044 spot-checked) exist in `CLEANUP_SAFETY.md` with the stated meaning.

### NIT (left)

1. `crc/azu/README.md` Purpose says the cleaners work "in one resource group"; `VM` lists
   subscription-wide (`BUG-002`, cited in the same README). Wording only.
2. `README_EXCLUDED_DIRS` matches by basename anywhere in the tree, so a future directory named
   `logs`, `build` or `dist` (for example under `docs/`) would escape the section check. `crc/`
   packages with non-legacy modules are still caught through `package_dirs`.
3. The ARCH-002 repo test walks the disk, not `git ls-files`: an untracked, non-excluded directory
   (for example `*.egg-info`, `.hypothesis`) fails `make check` locally. Already listed in STATUS.md.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check` on Python 3.12.3
(3.11 not installed here): `ruff check .` all checks passed; `mypy` no issues in 71 source files;
`lint-imports` 3 kept, 0 broken; `pytest -q` 996 passed.

Test count: 984 before R2c, 996 after round 1, 996 after round 2 (no tests added or removed).

ROUND 2 VERDICT: APPROVE
