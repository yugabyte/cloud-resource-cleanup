# R1b review

Scope reviewed: `git diff 3d71b0e..99dff81` (commit `99dff81`, R1b filter
variants catalogue in `docs/refactor/FILTER_VARIANTS.md` and pure filtering
policy in `crc/core/policy.py`) against every cleaner module under `crc/aws`,
`crc/azu`, `crc/gcp` and `crc/oci`, plus `AGENTS.md`, `ARCHITECTURE.md`,
`CLEANUP_SAFETY.md`, `docs/refactor/PLAN.md` and
`.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

## 1. Catalogue verification (`docs/refactor/FILTER_VARIANTS.md`)

Verified every `file:line` citation and described semantics against the
codebase:

- **AWS VM (`crc/aws/vm.py`)**: server-side `instance-state-name` + `tag:key`
  filters (`:174-185`), default `["running"]` (`:33`); `_should_skip_instance`
  exception any-key and `notags` all-keys (`:255-280`); untagged skipped when
  `"Tags" not in i` (`:200-201`) or `Name` tag missing/empty (`:205-210`,
  `:290-293`); `get_retention_age` + `is_old` on primary NIC `AttachTime`
  (`:218-235`), per-instance `except Exception` skips (`:241-244`).
- **AWS disk (`crc/aws/disk.py`)**: client-side `_matches_filter_tags` all keys
  with `fnmatch.fnmatchcase` globs (`:210-221`) plus server-side `status=available`
  and `tag:key` / `tag-key` (`:273-280`); `_should_skip` (`:235-271`) checks
  sensitive `yb_task` (`:67-74`, `:173-192`, `:236-242`), Kubernetes CSI tags
  unless `filter_tags` names a Kubernetes key (`:194-208`, `:244-249`),
  `exception_tags` any-key (`:251-258`), `notags` all-keys (`:260-265`), and
  `exception_regex` (`:228-233`, `:267`); untagged skipped unless `notags` is
  set (`:51-52`, `:379-383`); `name_regex` / `exception_regex` `re.search` on
  `Name` tag or `VolumeId` (`:135-142`, `:223-233`, `:386`); `State == "available"`,
  no `Attachments`, not `MultiAttachEnabled` (`:370-377`); `get_retention_age` +
  `_normalize_age`, `_creation_age_floor`, `_is_older_than_floor` ignoring
  `MAX_AGE` (`:400-452`).
- **AWS spot requests (`crc/aws/spot_instance_requests.py`)**: client-side
  `_matches_filter_tags` all-keys with `fnmatchcase` (`:190-203`, applied `:148`,
  `:750`, `:759`) + server `tag:key` (`:109-115`); `_should_skip_spot_request`
  exception any-key (`:215-219`) and `notags` all-keys (`:222-226`); leftover
  volume protection via `_volume_tags_protect_from_delete` (`:250-254`, `:294`,
  `:733`) with exact `yb_task` / `yb-task` sensitive value check (`:67-74`,
  `:229-248`); untagged requests always skipped (`:135-139`, `:746-747`),
  `cancelled` without `InstanceId` skipped (`:141-144`), `Type` lowercased
  default `one-time` (`:153`); `validated_retention_age` (fail closed) + `is_old`
  (`:155-172`, `:308-326`).
- **AWS KMS (`crc/aws/kms.py`)**: `list_resource_tags` gives `{TagKey, TagValue}`
  (`:131-136`); `filter_set` requires all `(k, v)` pairs as a subset of
  `response_set` (`:137-145`); `_should_skip_kms` (`:85-99`, called `:147`),
  `KeyState == "Enabled"` and `kms_key_description in key_des` (`:161-163`),
  `"default"` key policy `Statement` `Principal.AWS == kms_user` (`:170-180`),
  `get_retention_age` + `is_old` (`:155-169`), bare `except:` skips key
  (`:181-183`).
- **Other AWS modules**: `aws/elastic_ips.py` (`:91-109`, `:125-139`),
  `aws/keypairs.py` (`:112-124`), `aws/snapshot.py` (`:28-37`, `:59`, `:72-80`),
  `aws/vpc.py` (`:86-127`, `:159-167`).
- **Azure VM (`crc/azu/vm.py`)**: untagged skipped (`:211-212`); `filter_tags`
  all-keys (`:217-221`); `exception_tags` any-key (`:232-238`), `notags` all-keys
  (`:239-244`); allowed state is a substring of `statuses[1].display_status`
  (`:21`, `:149`, `:255-260`); `get_retention_age` + `is_old` (`:141-145`).
- **Azure disk (`crc/azu/disk.py`)**: `filter_tags` any-key (`:83-89`),
  `exception_tags` any-key when `disk.tags` truthy (`:92-99`), `notags` all-keys
  when `disk.tags` truthy (`:102-109`); `disk.disk_state in state` for
  `default_disk_state = ["Unattached"]` (`:16`, `:124-126`); `get_retention_age`
  + `is_old` (`:114-122`).
- **Other Azure modules**: `azu/ip.py` (`:62-84`), `azu/nic.py` (`:63-65`,
  `:85`, `:96-101`), `azu/spot_vm.py` (`:17-18`, `:73-77`, `:91-121`,
  `:244-261`, `:366-395`).
- **GCP VM (`crc/gcp/vm.py`)**: `instance.status in instance_state` exact with
  default `["RUNNING"]` (`:30`, `:140`); `exception_labels` any-key (`:210-216`),
  `notags` all-keys (`:217-222`); `filter_labels` all-keys (`:234-239`);
  `get_retention_age` + `is_old` (`:250-257`).
- **GCP IP (`crc/gcp/ip.py`)**: `address.status == "RESERVED"` (`:21`, `:83`);
  `filter_regex` plain substring any, empty matches (`:87-89`);
  `exception_regex` plain substring with inverted `not any(...)` (`:91-95`).
- **Other GCP modules**: `gcp/disk.py` (`:36`, `:77-81`, `:117-165`,
  `:184-191`), `gcp/spot_vm.py` (`:25`, `:62-126`, `:301`, `:366-371`).
- **OCI VM (`crc/oci/vm.py`)**: `lifecycle_state in ["RUNNING"]` exact (`:21`,
  `:145`); `exception_tags` any-key (`:81-87`), `notags` all-keys (`:88-94`);
  `filter_tags` all-keys (`:101-107`); `get_retention_age` + `is_old`
  (`:116-121`).
- **Other OCI modules**: `oci/disk.py` (`:17`, `:109-162`), `oci/spot_vm.py`
  (`:25`, `:62-135`, `:226`, `:262-267`).

## 2. Quirk verification

| Quirk | Confirmed | Real-world effect (delete-what-should-keep vs keep-what-should-delete) | Risk level |
|-------|-----------|------------------------------------------------------------------------|------------|
| AWS EIP `notags` never skips (`crc/aws/elastic_ips.py:91-109`) | Yes | **Deletes what it should keep (fail-open).** `in_no_tags` starts `False` (`:91`) and is updated via `all(in_no_tags and ...,)` (`:103-107`), so it is permanently `False`. Unassociated tagged EIPs matching `--notags` are never excluded and will be released if they pass `filter_tags`. | High |
| GCP IP `exception_regex` inverted (`crc/gcp/ip.py:91-95`) | Yes | **Both: deletes what it should keep AND keeps what it should delete.** `has_matching_exception_regex = self.exception_regex and not any(regex in name for regex in self.exception_regex)` (`:91-93`) excludes IPs that match **none** of `exception_regex` and allows deletion of IPs whose names **do** contain an `exception_regex` substring. | Critical |
| GCP disk ignores `notags` and `name_regex` (`crc/gcp/disk.py:80-81, 126-161, 183-192`) | Yes | **Deletes what it should keep (fail-open).** `self.notags` (`:80`) and `self.name_regex` (`:81`) are stored in `__init__` and never read (`self.age` at `:77` and `disk_state` at `:165` are also unused). Unattached disks matching `--notags` (meant to be excluded) or failing `--name_regex` (meant to restrict candidates) are still deleted when they pass `filter_labels` and `detach_age`. | High |
| KMS multi-value filter never matches (`crc/aws/kms.py:134-145`) | Yes | **Keeps what it should delete (fail-closed) for multi-value lists; deletes what it should keep (fail-open) for empty value lists.** `filter_set = {(k, v) for k, values in self.filter_tags.items() for v in values}` (`:138-140`) requires every `(k, v)` pair to be in `response_set`. Since a KMS key has one value per `TagKey`, a key with `>=2` distinct values in `filter_tags` never matches (keeps all keys). Conversely, `{"k": []}` adds no pair to `filter_set`, silently dropping the key-presence requirement. (Additionally, `_should_skip_kms` at `:89-91` and `get_retention_age` at `:155-157` index `"Key"`/`"Value"` on `{TagKey, TagValue}` dicts, so non-empty `exception_tags` on any tagged key raises `KeyError` into the bare `except:` at `:181-183` and skips the key, and custom age tags never match.) | Medium (multi-value) / High (empty value list) |
| Azure disk state substring `"attached"` vs `"Unattached"` (`crc/azu/disk.py:16, 124-126`) | Yes | **Deletes what it should keep (fail-open) on substring states.** `any(disk.disk_state in state for state in ["Unattached"])` (`:124-126`) checks whether `disk_state` is a substring of `"Unattached"`. Lowercase `"attached"` or `""` passes; Azure ARM's standard `"Attached"` (capital `A`) does not. | Medium |
| Azure IP raising on `None` tags (`crc/azu/ip.py:70-78`) | Yes | **Keeps what it should delete (aborts run mid-loop).** `exception_tags_match` (`:70-73`) and `no_tags_match` (`:75-78`) iterate `key in ip.tags` without guarding `ip.tags is not None`. Whenever `exception_tags` or `notags` is non-empty and `list_all()` encounters an untagged IP (`ip.tags is None`), `TypeError` aborts `IP.delete()`, leaving remaining eligible IPs undeleted. | Medium |
| AWS VPC calling `_should_skip_vpc` with a resource (`crc/aws/vpc.py:94-127, 159-168`) | Yes | **Keeps what it should delete (crashes on non-dry run when `exception_tags`/`notags` set); misreports dry-run deletions; deletes without age/untagged gates when `exception_tags`/`notags` empty.** `vpc_ids_to_delete` is extended at `:161` before skip checks and `dry_run` returns at `:163-164`. In non-dry-run mode, `_should_skip_vpc(vpc)` (`:167`) passes the `ec2.Vpc` resource instead of a tag list; if `exception_tags` or `notags` is non-empty, `for tag in tags:` (`:109`) raises `TypeError: 'ec2.Vpc' object is not iterable`. | High |

## 3. Policy & tests verification (`crc/core/policy.py`, `tests/test_core_policy.py`)

- Every function in `crc/core/policy.py` matches its catalogued variant in
  `docs/refactor/FILTER_VARIANTS.md` with no merged semantics behind flags.
- `crc/core/policy.py` is pure: no cloud/SDK imports, no environment reads, no
  clock reads, and no I/O.
- `tests/test_core_policy.py` (205 parametrized and direct unit tests) covers
  every public function, branch, and catalogued quirk (`filter_tags_any_key`,
  `filter_tags_all_keys`, `filter_tags_glob_all_keys`, `filter_tags_exact_pairs`
  including multi-value False and empty-list True, `should_skip_by_tags`,
  `aws_eip_should_skip`, regex search/match/substring variants including
  `gcp_ip_exception_excludes` inversion, `state_in`,
  `state_is_substring_of_any` including `"attached"` in `["Unattached"]`,
  `status_contains_any_allowed`, `is_sensitive_yb_task`, `aws_disk_yb_task`,
  `aws_disk_has_sensitive_yb_task`, `aws_spot_has_sensitive_yb_task`,
  Kubernetes CSI volume skip, GCP GKE/MIG skip, OCI OKE/instance-pool skip,
  `aws_disk_creation_floor`, `is_older_than_floor`, `snapshot_old_enough`,
  `UntaggedMode` and `crc.core.__all__`).
- Scope and ratchet: only `crc/core/README.md`, `crc/core/__init__.py`,
  `crc/core/policy.py`, `docs/refactor/FILTER_VARIANTS.md`,
  `docs/refactor/STATUS.md` and `tests/test_core_policy.py` changed;
  `pyproject.toml` ratchet lists and `tests/archtest/` are untouched.

## BLOCKING (fixed in this run)

1. Incomplete KMS semantics and slightly wide Azure disk line citations in
   `docs/refactor/FILTER_VARIANTS.md` (`docs/refactor/FILTER_VARIANTS.md:20, 35, 64, 81, 188-194`):
   - `aws/kms.py` (`crc/aws/kms.py:85-99, 131-136, 147, 155-183`): the
     catalogue noted `{TagKey, TagValue}` from `list_resource_tags` and
     `_should_skip_kms` / `get_retention_age`, but omitted that both
     `_should_skip_kms` (`:90-91`) and `get_retention_age` (`:155-157`) index
     `"Key"`/`"Value"` on `{TagKey, TagValue}` dicts (so non-empty
     `exception_tags` on any tagged KMS key raises `KeyError` into the bare
     `except:` at `:181-183` and skips the key, and custom age tags never
     match), and omitted the `"default"` key policy `Statement`
     `Principal.AWS == kms_user` filter (`:170-180`).
   - `azu/disk.py` (`crc/azu/disk.py:83-109`): line citations `:83-91`,
     `:92-101`, `:102-111` included trailing blank lines and the next check's
     comment; tightened to `:83-89`, `:92-99`, `:102-109`.
   - `gcp/disk.py` (`crc/gcp/disk.py:36, 77, 165`): added the `:77` citation
     for the stored-but-unused `self.age` attribute and `:36, :165` for
     `default_disk_state`.

## NIT (left)

- `crc/core/policy.py:261`: in `is_sensitive_yb_task`, the trailing
  `folded.startswith((s + "-", s + "_"))` check is reachable only when the
  preceding `_YB_TASK_SPLIT.split(folded)` token check is False; because
  `_YB_TASK_SPLIT` already splits on `-` and `_` and no entry in
  `SENSITIVE_YB_TASK` contains `-` or `_`, any value matching `startswith`
  already matched the token check on line 259. Kept as-is because it mirrors
  `crc/aws/disk.py:186-192` and `crc/aws/spot_instance_requests.py:235-241`
  verbatim.

## Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 61 source files |
| `lint-imports` | 3 kept, 0 broken ("Cloud packages are independent", "Core never imports cloud packages or services", "Core never imports cloud or telemetry SDKs") |
| `pytest -q` | 553 passed (348 pre-R1b + 205 `test_core_policy.py`) |

Test count: 348 on `3d71b0e`, 553 on `99dff81`.

VERDICT: APPROVE
