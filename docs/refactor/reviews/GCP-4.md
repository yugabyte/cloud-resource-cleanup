# GCP-4 review

## Coding summary

Rung GCP-4: `crc/gcp/spot_vm.py` refactored into the subpackage `crc/gcp/spot_vm/` (lane `refactor/lane-gcp-spot`). No behaviour change, with one forced deviation (below).

- **Summary.** `SpotVMCleaner` (`cloud="gcp"`, `kind="spot_instances"`, registered with `@register("gcp", "spot_instances")` through `build_spot_cleaner`) lists the zones and instances and filters in `plan()` (read-only) and calls `delete_instance`, the operation polls and `delete_disk` from `apply()` / `apply_instance()` only. Filtering uses `policy.state_in`, `policy.gcp_is_managed_or_gke_node`, `policy.should_skip_by_tags` (exception any, notags all), `policy.filter_tags_all_keys`, `policy.effective_age` and `policy.normalize_tags`; age uses `core_age.validated_retention_age` (fail closed) and `core_age.is_old` (`MAX_AGE` still read inside `core.age`). The SDKs sit behind the typed Protocols `GcpSpotClient` and `InstanceView`; `ComputeSpotClient` opens the discovery session lazily (an `ExitStack`, the `with` of main), builds `InstancesClient` right after the zones were listed, and `close()` ends the session. Split by responsibility (the old module would exceed 400 lines): `settings.py`, `client.py`, `instances.py`, `operations.py`, `disks.py`, `cleaner.py`, `adapter.py`, each package with a README; `__init__.py` keeps `crc.gcp.spot_vm.SpotVM`, `discovery` and `compute_v1` importable so `crc.py` and the existing tests are unchanged. `SpotVM` is the thin adapter (constructor, attributes, `delete()`, `get_deleted`, `delete_count`, `get_deleted_disks`, the private helpers the old tests call; two optional trailing arguments `client` and `clock`). `crc.py` is not changed. The clock comes from `RunContext.now` or the injected `clock`.
- **Plan in a dry run.** Main reads each leftover disk (`disks().get`) even in a dry run, so `plan()` does that read (a GET, still no mutation) when `context.dry_run` is true and reports the disks in `Plan.related["Disk"]`; in a real run the reads happen in `apply()` after the instance operation, as on main. `Result.related["Disk"]` holds the deleted disks (`project/location/name`).
- **Deviation (needs an owner decision).** ARCH-003 bans `time.time`, so the operation wait reads `time.monotonic` (main: `time.time`). Same 180 s and 5 s defaults, same logs; only a wall-clock jump during a wait would differ. Recorded in `docs/refactor/bugs/pending/GCP-4.md`.
- **Parity.** `tests/legacy/gcp_spot_vm.txt` is the byte-identical `git show main:crc/gcp/spot_vm.py`. `tests/test_gcp_spot_instances_parity.py` runs the oracle (frozen `datetime`, fake `time`) and the adapter against the same fake `googleapiclient.discovery.build` and `compute_v1.InstancesClient` over the case table (dry run, status, Spot detection, GKE and MIG skips, filters, exceptions, notags, untagged, age edges, `MAX_AGE`, custom age label, leftover disks zonal / regional / name-only, disk read, label and delete errors, operation errors / polls / timeouts, instance delete errors, zone and instance listing errors including auth and connectivity (no fallback on main), iteration errors, malformed instances) comparing SDK calls in order, results, counts, logs, stdout and errors. The same table runs through the registered Cleaner (plan then apply unless dry run) and compares results, errors, every call after listing in order, and log records (multiset; exact for at most one instance; same summary lines). Cleaner path differences, inherent to plan-then-apply (as GCP-1, GCP-2): every zone is listed and filtered before the first delete, so an error raised while listing stops before any delete, and the session is closed after `plan()` and reopened by `apply()`. Quirk checks assert main and the new code show the same behaviour.
- **Test counts.** Before: 4681 passed. After: 5043 passed (+362: parity table and quirk checks in `tests/test_gcp_spot_instances_parity.py`, 8 plan / apply unit tests in `tests/test_gcp_spot_instances.py`). No existing test changed (`tests/test_gcp_spot_vm.py` passes unchanged).
- **Gates.** `make check` passes. `crc/gcp/spot_vm.py` and `crc.gcp.spot_vm` are removed from both ratchet lists in `pyproject.toml`; nothing added (`googleapiclient` already has its per-SDK override from GCP-2).
- **Open issues.** New quirks, none fixed, in `docs/refactor/bugs/pending/GCP-4.md` (15 rows): unlabelled Spot VMs unprotected by exception labels, a delete whose operation is not confirmed leaks disks, unpaginated zone listing, no connectivity fallback, swallowed per-VM errors, unlisted instance states, timestamp format, custom age label shorter than `--age`, bare VM name without zone, wall clock. `Result.failed` and `Plan.skipped` stay empty because main reports neither failed deletes nor skip reasons.
- **Core change needed.** None. `crc/core/policy.py` and `crc/core/age.py` already have every function used.
- **FILTER_VARIANTS.md change needed.** None for behaviour. The `gcp/spot_vm.py` row cites line numbers of the old module; they now point into `crc/gcp/spot_vm/` (`instances.py`, `disks.py`), and `crc/gcp/spot_vm.py:25` is `settings.DEFAULT_INSTANCE_STATES`.
- **`crc/README.md` / `tests/legacy/README.md` / `tests/README.md` change needed.** Add `gcp_spot_vm.txt` (exact `git show main:crc/gcp/spot_vm.py`, used by `tests/test_gcp_spot_instances_parity.py`) to `tests/legacy/README.md`; `crc/README.md` and `crc/core/README.md` could say `gcp` / `spot_instances` is now registered. All outside this lane's edit rights.

## Round 1 (Argon)

Scope reviewed: `git diff e7926e2..HEAD` (`crc/gcp/spot_vm/**`, `crc/gcp/README.md`, `pyproject.toml`,
`docs/refactor/bugs/pending/GCP-4.md`, `docs/refactor/reviews/GCP-4.md`, `tests/legacy/gcp_spot_vm.txt`,
`tests/test_gcp_spot_instances.py`, `tests/test_gcp_spot_instances_parity.py`) against
`git show main:crc/gcp/spot_vm.py`, `git show main:crc.py`, `AGENTS.md`, `docs/refactor/BUGS.md`,
`docs/refactor/FILTER_VARIANTS.md`, `CLEANUP_SAFETY.md`, and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### 1. Behaviour parity with `git show main:crc/gcp/spot_vm.py`

- **SDK calls (order and arguments)** (`crc/gcp/spot_vm/client.py:79-160`, `cleaner.py:71-185`, `disks.py:81-121`, `operations.py:30-74`, `adapter.py:159-177` vs `main:crc/gcp/spot_vm.py:194-413`):
  - `ComputeSpotClient.list_zones` opens `discovery.build("compute", "v1")` via `ExitStack` (matching `with discovery.build(...) as service:`), calls `service.zones().list(project=project_id).execute().get("items", [])`, and constructs `compute_v1.InstancesClient()` immediately afterwards (`client.py:98-102`, `main:crc/gcp/spot_vm.py:347-351`).
  - `SpotVM.delete()` iterates zones in order, calls `list_zone(zone)` (`list(instance_client.list(project=project_id, zone=zone))`, logging and skipping the zone on `Exception`), then evaluates and acts on each instance in order (`adapter.py:164-174`, `main:crc/gcp/spot_vm.py:353-413`).
  - When `not dry_run`, `_delete_and_wait` calls `service.instances().delete(project=project_id, zone=zone, instance=candidate.id).execute()`, logs `Deleting GCP Spot VM <name>`, and polls `service.zoneOperations().get(project=project_id, zone=zone, operation=op_name).execute()` until `status == "DONE"` (without `error`) or timeout (`DEFAULT_WAIT_SEC = 180`, `DEFAULT_POLL_INTERVAL_SEC = 5`). Only after the zone operation completes does it record the instance and iterate `_leftover(candidate)`, calling `disks().get` / `regionDisks().get` for label protection and `disks().delete` / `regionDisks().delete` followed by operation polling (`SAF-039`, `SAF-044`).
  - `finally: self._client.close()` closes the `ExitStack` (calling `service.__exit__`) before `log_summary`, matching the `with` block on `main:crc/gcp/spot_vm.py:347`, `:415-430`.
- **Filter, state, Spot, GKE/MIG, and age decisions** (`crc/gcp/spot_vm/instances.py:22-94`, `disks.py:13-96` vs `main:crc/gcp/spot_vm.py:63-192`, `:274-306`, `:366-379`):
  - Status check uses `policy.state_in` against `("RUNNING", "TERMINATED", "SUSPENDED")` (exact match).
  - Spot check (`is_spot_instance`) checks `getattr(scheduling, "preemptible", False)` or `str(getattr(scheduling, "provisioning_model", "") or "").upper() == "SPOT"`.
  - Managed/GKE skip (`is_managed_or_gke_node`) uses `policy.gcp_is_managed_or_gke_node` (`goog-gke-node` label or `created-by` metadata item containing `instanceGroupManagers`, `SAF-045`).
  - Exception / notags (`labels_protect`, `should_skip_instance`, and leftover disk `disk_ok_to_delete`) uses `policy.should_skip_by_tags(policy.normalize_tags(labels), settings.exception_labels, settings.notags)` after `if not labels: return False` (`SAF-043`, `SAF-044`).
  - Filter labels (`has_matching_filter_label`) uses `policy.filter_tags_all_keys(policy.normalize_tags(_labels(instance)), settings.filter_labels)`.
  - Age gate (`is_old_instance`) parses `creation_timestamp` with `%Y-%m-%dT%H:%M:%S.%f%z`, converts `clock()` to `created.tzinfo`, calls `core_age.validated_retention_age` (fail closed on invalid/empty/zero custom age tag per `SAF-008`, `SAF-041`), logs `Updating age for Spot VM: %s` when truthy, and evaluates `core_age.is_old(policy.effective_age(retention_age, settings.age), now, created)` (`SAF-007`, `SAF-010`).
- **Log lines, errors, and dry-run path** (`crc/gcp/spot_vm/cleaner.py:33-48`, `:75-185` vs `main:crc/gcp/spot_vm.py:227-266`, `:294-305`, `:335-430`):
  - Every `logging.info`, `logging.warning`, and `logging.error` message (including `has autoDelete=false (will delete after VM)`, operation poll/error/timeout logs, leftover disk skip/delete logs, per-VM `Error processing GCP Spot VM %s: %s`, `No GCP Spot/preemptible VMs to delete.`, and the final VM and leftover disk summary warnings) matches `main`; nothing is printed to stdout.
  - In dry run, no `instances().delete` or `disks().delete` call is made, while leftover disks are still read via `disks().get` / `regionDisks().get` for the label check as on `main:crc/gcp/spot_vm.py:380-407`.

### 2. Oracle and parity test verification (`tests/legacy/gcp_spot_vm.txt`, `tests/test_gcp_spot_instances_parity.py`)

- Diffed `tests/legacy/gcp_spot_vm.txt` against `git show main:crc/gcp/spot_vm.py`: byte-identical (SHA-256 `813a60e9ecd5cd96e3e0a9a4c098e180206ea4dc956be453e2b29f70ac53f299`, 0 lines of diff).
- Verified `tests/test_gcp_spot_instances_parity.py` executes the oracle (`tests/legacy/gcp_spot_vm.txt`), the `SpotVM` adapter, and the registered `SpotVMCleaner` against the same `FakeGcp` (`discovery.build` and `compute_v1.InstancesClient`) across 144 cases and 66 quirk assertions, comparing ordered SDK `calls` (including `instances.next` generator steps and `sleep`), `error`, `deleted`, `disks`, `count`, `logs` (at `DEBUG`), and `stdout`.
- Tested a deliberate one-line behaviour change in `crc/gcp/spot_vm/instances.py` (`policy.filter_tags_any_key` instead of `policy.filter_tags_all_keys` in `has_matching_filter_label`), confirmed `pytest -q tests/test_gcp_spot_instances_parity.py` failed immediately (5 failed), and reverted it.

### 3. Architecture, scope, and lane rules

- `SpotVMCleaner` implements `Cleaner` (`cloud = "gcp"`, `kind = "spot_instances"`, `supports`, read-only `plan`, mutating `apply`) and is registered with `@register("gcp", "spot_instances")` on `build_spot_cleaner`.
- `plan()` never mutates (`ARCH-005` passes on `crc/gcp/spot_vm/**`; `delete_instance` and `delete_disk` are only reachable from `apply` / `apply_instance`).
- Filtering uses `policy.state_in`, `policy.gcp_is_managed_or_gke_node`, `policy.should_skip_by_tags`, `policy.filter_tags_all_keys`, `policy.effective_age`, and `policy.normalize_tags` as named in `docs/refactor/FILTER_VARIANTS.md`; `operations.py` uses `time.monotonic` to comply with `ARCH-003`.
- SDK sits behind typed `GcpSpotClient` and `InstanceView` `Protocol`s in `crc/gcp/spot_vm/client.py`.
- `crc/gcp/spot_vm.py` (`crc.gcp.spot_vm`) was removed from both legacy ratchet lists in `pyproject.toml`; nothing was added (`ARCH-001`), and the existing `googleapiclient` mypy override is per-SDK.
- Verified `git diff e7926e2..HEAD --name-status`: none of the lane-forbidden files (`docs/refactor/BUGS.md`, `docs/refactor/STATUS.md`, `docs/refactor/FILTER_VARIANTS.md`, `crc/README.md`, `tests/README.md`, `tests/legacy/README.md`, `crc.py`, `crc/core/**`, or another cloud's package) were modified.
- `crc/gcp/README.md` (only the `spot_vm` entries and `### spot_vm` subsection) and `crc/gcp/spot_vm/README.md` are accurate and pass `ARCH-002` symbol sync.
- Verified all rows in `docs/refactor/bugs/pending/GCP-4.md` against `git show main:crc/gcp/spot_vm.py` and `git show main:crc.py`: fixed the `zone = zone_obj["name"]` line citation on row 4 (`:346-354` instead of `:346-353`, `:357`), tightened row 11's dry-run disk-get citations (`:176-192`, `:274-306`, `:316-318`, `:379-407`), and added a missing quirk row for bare `instance.name` recorded without zone (`crc/gcp/spot_vm.py:401`, `crc.py:354-370`, `:630-646`) plus its `same-name-two-zones` assertion in `QUIRKS`.

### BLOCKING (fixed)

None.

### NIT (left)

- `SpotVM.__init__` uses `client or ComputeSpotClient()` (`crc/gcp/spot_vm/adapter.py:64`); `client if client is not None else ComputeSpotClient()` would avoid replacing a falsy custom test client.
- `labels_protect` normalises labels via `policy.normalize_tags(labels)` (`crc/gcp/spot_vm/instances.py:38`), which turns a non-mapping truthy `labels` (for example on a malformed disk dict) into `{}` (`False`), whereas `main`'s `_labels_protect` tests `key in labels` directly (`main:crc/gcp/spot_vm.py:72-85`); real `compute_v1` and discovery disk `labels` are always string maps.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 113 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 5043 passed |

Test count: 4681 before GCP-4 (`e7926e2`), 5042 on `050c45b`, 5043 after Round 1 (+362 total: 354 in `tests/test_gcp_spot_instances_parity.py`, 8 in `tests/test_gcp_spot_instances.py`).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff e7926e2..HEAD` (both commits), re-read against `git show main:crc/gcp/spot_vm.py`, `crc/core/policy.py`, `crc/core/age.py`, `crc/core/engine.py`, `CLEANUP_SAFETY.md` (SAF-039, SAF-044) and the review checklist.

### Round 1 fixes assessment

Round 1 changed only docs and one quirk assertion: the row 4 and row 11 citations in `docs/refactor/bugs/pending/GCP-4.md` (verified: `main:crc/gcp/spot_vm.py:346-354` is the `with` / zones / `InstancesClient()` / `zone_obj["name"]` block; `:176-192`, `:274-306`, `:316-318`, `:379-407` are the dry-run disk path), the new bare-name row (`:401` appends `instance.name`), and a `same-name-two-zones` quirk check. No code or existing test changed, nothing was weakened. Correct.

### Independent review

- Parity: every filter, log line, SDK call and order in `instances.py`, `disks.py`, `operations.py`, `cleaner.py` and `adapter.py` matches `main:crc/gcp/spot_vm.py:63-430`. Checked the non-obvious equivalences: `policy.should_skip_by_tags` / `filter_tags_all_keys` reproduce `key in labels and (not value or labels[key] in value)`; `policy.effective_age` is `retention_age or age`; `core_age.validated_retention_age(..., key or "")` returns `None` for a `None` key as `Service.validated_retention_age` does; `clock().astimezone(created.tzinfo)` with the default naive `datetime.now` equals `datetime.now(tz=created.tzinfo)`; `ExitStack` closes the discovery service before the summary and an exception still skips the summary, as the `with` on main does; `InstancesClient()` is built right after the zones listing, also for an empty listing.
- Safety: SAF-039 (disks only after the zone operation is DONE without error), SAF-041 (fail closed custom age, positive `age`), SAF-044 (disk label check), SAF-045 hold. `plan()` makes no mutating call; the dry-run disk GETs are reads. `engine.py:100` never calls `apply()` in a dry run, so the dry-run summary is logged once (by `plan()`).
- Tests: the parity table compares ordered SDK calls, results, logs and errors against the oracle (byte-identical to main); unit tests assert outcomes and call order; no vacuous, skipped or xfail'd test.
- Typing / ratchet: only the `spot_vm` lines were removed from both lists in `pyproject.toml`; no new override; no unjustified `type: ignore`.

### BLOCKING (fixed)

- Lane rule: `crc/gcp/README.md` renumbered the shared `## Extension Rules` list (inserted a Spot VM rule 5, shifting disk to 6 and BUGS to 7), outside the module's `### spot_vm` subsection and a merge-conflict risk with the other GCP lanes. Restored the list to its `e7926e2` text and moved the parity-oracle rule into `### spot_vm`. No behaviour or test change.

### NIT (left)

- `crc/gcp/README.md` still has one-line edits outside `### spot_vm` (the "Partly refactored" sentence, the `spot_vm/` module bullet, `SpotVM` dropped from the legacy list in `## Public API`). They keep the README accurate; left, but expect a trivial merge with other GCP lanes.
- `cleaner.py:205-206`: `instance_delete_wait_sec` / `instance_poll_interval_sec` factory options are not validated (a string would fail inside the wait); not reachable from `crc.py` today.
- Round 1 NITs (`client or ComputeSpotClient()` in `adapter.py:64`; `normalize_tags` turning a non-mapping disk `labels` into `{}` in `instances.py:38`) agreed, both unreachable with real SDK responses.
- New quirk recorded in `docs/refactor/bugs/pending/GCP-4.md`: a leftover `autoDelete=false` disk is gated only by `exception_labels` / `notags` on its own labels (no own age, `filter_labels` or `users` check), `main:crc/gcp/spot_vm.py:273-306`, `:402-407`.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`: `ruff check .` all passed; `mypy` no issues in 113 source files; `lint-imports` 5 kept, 0 broken; `pytest -q` 5043 passed. After the README fix, the archtest / README tests (`-k "arch or readme"`, 98) pass again.

Test count: 5043 passed (4681 before the rung, +362).

ROUND 2 VERDICT: APPROVE
