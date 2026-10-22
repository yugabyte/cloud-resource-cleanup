# crc.aws.disk

## Purpose

AWS EBS disk cleaner (AWS-4). Deletes available (unattached) EBS volumes in every region that pass the tag, name and age rules, with an optional CloudWatch detach-age guard. The old import path `crc.aws.disk` keeps working: the package `__init__.py` holds the `Disk` class `crc.py` uses.

Modules, one per responsibility:

- `cleaner.py`: the `Cleaner` (`plan()` lists and filters, `apply()` deletes) and its registry factory.
- `client.py`: typed Protocols of the boto3 calls used and the default factories.
- `settings.py`: validated settings (age gates, filters, regex) and age normalisation.
- `volumes.py`: tag, name, state and creation-age decisions for one volume, labels and server side filters.
- `detach.py`: the CloudWatch detach-age guard.

## Public API

- `Disk`: adapter that crc.py calls (delete(), get_deleted, count, disks_to_delete, age, detach_age); runs the cleaner region by region in the order of main.
- `DiskCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus plan_region, note_dry_run, apply_region, region_guard and log_summary used by the adapter).
- `DiskRuntime`: frozen dataclass with the injected EC2 and CloudWatch client factories and the region lister.
- `build_disk_cleaner()`: registry factory behind the aws / disk registration; reads filter_tags, exception_tags, notags, name_regex, exception_regex, custom_age_tag_key, age and detach_age from RunContext.options.
- `VolumePaginator`: Protocol of the describe_volumes paginator (paginate).
- `Ec2Client`: Protocol of the two EC2 calls used, get_paginator and delete_volume; tests pass fakes.
- `CloudWatchClient`: Protocol of the one CloudWatch call used, get_metric_data.
- `boto3_ec2()`: default EC2 client factory (bounded timeouts and retries).
- `boto3_cloudwatch()`: default CloudWatch client factory.
- `all_regions()`: default region lister (describe_regions with the connectivity fallback).
- `DiskSettings`: frozen dataclass with the validated filters and age gates of one run.
- `build_settings()`: validate raw options in the order of main (ages, regexes, age gate, CloudWatch window).
- `normalize_age()`: validate an int or days/hours age, an empty dict when empty, ValueError otherwise.
- `VolumeFilter`: tag, name, state and creation-age decisions for one volume (is_candidate, should_skip, matches_filter_tags).
- `tag_map()`: tag list to a key to value dict.
- `volume_label()`: region/volume-id plus yb_task or Name, the id reported as deleted.
- `server_filters()`: describe_volumes filters (status available, tag:key or tag-key).
- `older_than_floor()`: CreateTime comparison with a floor, ignoring MAX_AGE.
- `DetachGuard`: the CloudWatch detach-age guard (drop_recently_attached).
- `cloudwatch_period()`: metric period of a window in seconds.
- `build_queries()`: GetMetricData queries of a batch and their ids per volume.
- `get_metric_data()`: all result pages of one batch.
- `merge_metric_results()`: merge pages per query id (Values concatenated, last status).
- `volume_recently_attached()`: skip decision and reason from the merged results.

## Safety Invariants

- `plan()` and `plan_region()` only call `get_paginator("describe_volumes").paginate` and CloudWatch `get_metric_data`; `delete_volume` is called from `apply()` / `apply_region()` only and never in a dry run (SAF-006, SAF-014: the AWS EBS cleanup is opt-in through `--cloud aws --resource disk`).
- An age gate is required in the constructor (`age` and/or `detach_age`, SAF-006); CreateTime is always compared with a floor that ignores `MAX_AGE` (SAF-010); `detach_age` is limited to the 455 day CloudWatch retention.
- Fail closed: a missing CreateTime, an invalid custom age tag, an incomplete or failed CloudWatch answer keeps the volume. Attached and Multi-Attach volumes, sensitive `yb_task` values and Kubernetes CSI volumes (without a kubernetes filter) are never deleted; untagged volumes are skipped unless `notags` is set.
- Region errors: connectivity errors skip the region, `AuthFailure`, `UnauthorizedOperation`, `OptInRequired` and `InvalidClientTokenId` skip it with a warning, every other error is logged and flagged and never raised. `describe_regions` errors (other than connectivity) propagate. Per volume errors are isolated and logged (CLEANUP_SAFETY.md).
- `Disk.delete()` keeps the call and log order of main (per region: client, listing, CloudWatch, deletes). The Cleaner path lists every region before `apply()` deletes, so delete calls and `Deleted volume` lines come after all listing lines; a dry run is identical because `plan()` also logs the dry-run lines.
- Log lines, labels (`region/volume-id yb_task=...`) and quirks are those of main; see `docs/refactor/BUGS.md` (`BUG-004`, `BUG-022`, `BUG-282` to `BUG-296`).
- Logging (LOG-AWS1, BUG-356/361/362): the per-volume `Skipping volume ...` INFO lines are DEBUG with the same text (warnings keep their level); REGION, ACTION (`deleted` / `would_delete`), skip counts and failures (delete errors, per-volume and region errors, CloudWatch lookup errors) go to `crc.core.logger.current()`; an unauthorised or connectivity skip of a region is a `region_skipped`.

## Extension Rules

1. No behaviour change in a refactor rung; behaviour fixes are separate rungs (`docs/refactor/PLAN.md`).
2. Keep filtering in `crc.core.policy` and age in `crc.core.age`; no `re`, clock reads or environment reads here (ARCH-003): `now` comes from `RunContext` (or the adapter clock).
3. Keep every mutating call in `apply_region()`; `plan()` and what it calls stay read-only (ARCH-005).
4. Changes need `tests/test_aws_disk_parity.py` (oracle `tests/legacy/aws_disk.txt`, a frozen copy of `main`, never edit it), `tests/test_aws_disk_cleaner.py` and `tests/test_aws_disk.py` to stay green.
5. Every file stays under 400 lines; split by responsibility and give a new subpackage its own README.md.
