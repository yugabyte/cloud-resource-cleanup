# crc.aws.spot_instance_requests

## Purpose

Cancels old, tagged AWS spot instance requests, terminates their instances and deletes the
`DeleteOnTermination=false` volumes they leave behind, in every region. A `Cleaner`
(`aws` / `spot_instance_requests`) that `crc/cli` runs through `crc.core.engine` (route in
`crc/cli/routes/aws.py`). Split by responsibility: `model.py` (settings, state, constants),
`client.py` (SDK Protocol), `rules.py` (pure tag and marker decisions), `requests.py` (list,
filter, age), `volume_tags.py` (volume tag reads), `lookups.py` (instance and leftover volume
reads), `sweep.py` (sweep gates), `volumes.py` (re-exports those three), `preview.py`
(dry-run path, reads only), `leftover.py` (marker tags, wait and delete), `cleanup.py` (one
request, region sweep), `runtime.py` (`SpotRuntime`, settings), `regions.py` (region listing,
error isolation), `summary.py` (closing log lines), `cleaner.py` (`plan()` / `apply()`).

## Public API

- `SpotRequestCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus apply_region).
- `build_spot_request_cleaner`: registry factory behind @register("aws", "spot_instance_requests"); reads filter_tags, exception_tags, notags, age (required) and custom_age_tag_key from RunContext.options.
- `SpotRuntime`: (runtime.py) frozen dataclass with the injected client factory, region lister and `Waiter`.
- `settings_from_options` (runtime.py), `log_summary` (summary.py), `list_matches`, `guarded_sweep`, `guarded_cleanup` (regions.py): helpers used by the cleaner and re-exported by cleaner.py (settings, closing warnings, per-region listing, error isolation).
- `SpotClient`, `VolumePaginator`: Protocols of the EC2 calls used; tests pass fakes. `boto3_client`, `all_regions`: the real client and region factories.
- `SpotSettings`, `RequestMatch`, `SpotState`, `Waiter`, `Inspection`: frozen/slot dataclasses of model.py.
- `tag_map`, `should_skip`, `matches_filter`, `volume_tags_protect`, `is_crc_marker`, `own_volume_tags`, `own_tags_for_protect`, `effective_sweep_tags`, `scope_tags_for_volume`: pure decisions (rules.py, through crc.core.policy).
- `build_filter`, `describe_all_requests`, `passes_age_gate`, `filtered_requests`: read-only request discovery (requests.py, age through crc.core.age).
- `describe_volume_tag_list`, `volume_tag_keys`, `prefixed_scope_keys_on_volume`, `volume_ok_to_delete`, `filter_deletable_leftover_volumes`, `instance_state_and_dot_false_volumes`, `describe_tagged_leftover_volumes`, `volumes_tagged_leftover`, `inspect_instance`, `live_spot_request_tags`, `volume_passes_sweep_gates`, `run_sweep`, `error_code`: read-only lookups and sweep gates (volume_tags.py, lookups.py, sweep.py, re-exported by volumes.py).
- `preview_request`, `preview_sweep`: the dry-run path (preview.py), reads only.
- `invalidate_leftover_marker`, `tag_volumes_as_leftover`, `wait_volume_available_then_delete`: mutating leftover steps (leftover.py).
- `CleanupEnv`, `cleanup_one`, `sweep_region`: mutating cleanup of one request and of a region (cleanup.py).

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md` (SAF-028 cancel before terminate, SAF-006 to SAF-013 age gates, SAF-029 to SAF-033 leftover volumes). Quirks: `docs/refactor/BUGS.md` (BUG-004, BUG-021, BUG-022, BUG-272 to BUG-281).
- `plan()` only calls describe operations. In a dry run it also performs the reads of the real path and records what would be done; `cancel_spot_instance_requests`, `terminate_instances`, `create_tags`, `delete_tags` and `delete_volume` live in `leftover.py` and `cleanup.py` and are reached from `apply()` only.
- Order of one request: tag DoT=false volumes, cancel (an already cancelled request skips it), terminate, wait for `available`, delete. A failed tag or cancel aborts before the next step; a failed cancel or terminate invalidates the `crc_leftover_of` / `crc_leftover_tag_*` markers.
- Fail closed: untagged requests, a bad custom age tag, unreadable volume tags, an unknown instance state and a failed live-request lookup keep the resource. Sensitive `yb_task` and exception/notags protect leftover volumes only.
- Listing errors are logged and skip the region; a `describe_regions` failure logs and ends the run; connectivity errors skip the region; per-request and per-volume errors are logged and isolated.
- `plan()` lists every region before `apply()` mutates, so the SDK calls and log lines of different regions are not interleaved; each region's call stream is the same as a region-by-region run.
- Logging (BUG-356/361/362): the INFO `Skipping ...` lines of leftover volumes and cancelled requests are DEBUG with the same text; REGION (listing and filtering time), ACTION (`deleted` / `would_delete` per request, instance and volume), skip counts and failures (describe, cancel, terminate, tag, wait/delete and sweep errors) go to `crc.core.logger.current()`.

## Extension Rules

1. Behaviour changes need a `BUG-NNN` row in `docs/refactor/BUGS.md` and a test that fails without them.
2. Keep reads in `requests.py`, `volume_tags.py`, `lookups.py`, `sweep.py`, `preview.py` and mutations in `leftover.py`,
   `cleanup.py` (ARCH-005). Decide through `crc.core.policy` and `crc.core.age`.
3. Changes need `tests/test_aws_spot_instance_requests_parity.py` (oracle
   `tests/legacy/aws_spot_instance_requests.txt`, never edit it),
   `tests/test_aws_spot_instance_requests_cleaner.py` and `tests/test_aws_spot_instance_requests_engine.py`
   to stay green.
4. Every file at most 250 lines; update this README and the symbol list above.
