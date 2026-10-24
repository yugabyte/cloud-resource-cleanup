# OWN-1: safety lock move and dependency cleanup

## Coding summary

- **Routed / adapters:** nothing changed in `crc/**`. No kind was routed, no adapter class was deleted or shimmed (a later lane deletes `Disk`, `SpotInstanceRequests`, `SpotVM` ...). Only tests, guardrail data and `requirements.txt` changed.
- **Locks:** all 46 SAF IDs are now locked (94 entries, 0 gaps). No lock points at a legacy file. 65 old entries moved to engine-path tests (table 1), 16 gaps closed (table 2).
- **Tests:** 18 new engine copies in `tests/test_aws_disk_engine.py`, 11 in `tests/test_aws_spot_instance_requests_engine.py`, several existing engine assertions tightened (never loosened), and `tests/test_own1_saf_gaps.py` (11 test items) for gaps with no test.
- **Deleted:** the five legacy safety files (110 tests, table 3). Ruff/mypy legacy lists are empty; `legacy_baseline.txt` lost the 5 test files and 17 entries for files that no longer exist (`crc/aws/disk.py`, `crc/azu/spot_vm.py` ... are packages or gone).
- **CLI parity tests:** unchanged; they already reach the cleaners through `crc.cli.bridge` and `FakeCleaners` (`tests/helpers_cli_routes.py`). The two new CLI-level tests (SAF-002) use the same seam.
- **Test count:** 44415 before, 44350 after (110 deleted, 45 added). `test_count_floor.txt` 44415 -> 44305 (exactly the 110 deleted tests, every one covered, table 3). ARCH-008 passes.
- **Owner actions:** (1) confirm in the `cloud-resource-cleanup-triggers` repo that no job other than the `crc.py` jobs installs this `requirements.txt` (the repo is not visible from here, see section 4). (2) Review the lock moves in table 1 (guardrail data).
- **New bugs:** none found. Observation, not a bug: SAF-004 and SAF-001 are locked by the unchanged `crc.aws._base` / `crc.aws.connectivity` tests plus engine-level tests; the legacy adapter-only behaviours of these classes are still covered by the parity tests until the adapters go.

## 1. Pairing table: old lock -> new lock

Every new test was read next to the old one: same inputs, assertions at least as strict. Where the engine copy was weaker I strengthened it first (marked *stricter*).

| SAF | Old lock (legacy file) | New lock (engine path) | Reason |
|---|---|---|---|
| SAF-003 | `test_aws_disk.py::AwsDiskDeletePathTests::test_had_errors_logs_without_raising` | `test_aws_disk_engine.py::test_saf_003_had_errors_logs_without_raising` | same inputs through `engine.run`; also asserts `had_errors`, the succeeded label and the EC2 delete list |
| SAF-007 | `test_aws_disk.py::AwsDiskAgeGateTests::test_rejects_unknown_or_zero_age_units` | `test_aws_disk_engine.py::test_saf_007_and_012_unknown_or_zero_age_units_are_rejected` | same four bad ages plus `days: 900`, through `build_settings` (the validator `DiskCleaner` is built from) |
| SAF-007 | `test_aws_spot_instance_requests.py::SpotFilterTests::test_requires_positive_age` | `test_aws_spot_instance_requests_engine.py::test_saf_007_011_027_a_positive_age_is_required` | same `{}` and `{'days': 0}` plus `None`, through the registered factory |
| SAF-008 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_invalid_custom_age_tag_skips_request` | `test_aws_spot_instance_requests_engine.py::test_saf_008_an_invalid_or_unparseable_custom_age_tag_skips_the_request` | same five bad tag values (one test each before) through `plan()` |
| SAF-008 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_unparseable_custom_age_tag_skips_request` | `test_aws_spot_instance_requests_engine.py::test_saf_008_an_invalid_or_unparseable_custom_age_tag_skips_the_request` | same five bad tag values (one test each before) through `plan()` |
| SAF-008 | `test_gcp_spot_vm.py::GcpSpotDetectTests::test_invalid_custom_age_skips_even_when_old` | `test_gcp_spot_vm_engine.py::GcpSpotDetectTests::test_invalid_custom_age_skips_even_when_old` | same test body against `spot_instances.is_old_instance` (the function `SpotVMCleaner` calls) |
| SAF-011 | `test_aws_disk.py::AwsDiskAgeGateTests::test_requires_an_age_gate` | `test_aws_disk_engine.py::test_saf_011_and_018_an_age_gate_is_required` | same `None` and `{}` ages; also pins the message (`requires an age gate`) |
| SAF-011 | `test_aws_spot_instance_requests.py::SpotFilterTests::test_requires_positive_age` | `test_aws_spot_instance_requests_engine.py::test_saf_007_011_027_a_positive_age_is_required` | same `{}` and `{'days': 0}` plus `None`, through the registered factory |
| SAF-012 | `test_aws_disk.py::AwsDiskAgeGateTests::test_rejects_unknown_or_zero_age_units` | `test_aws_disk_engine.py::test_saf_007_and_012_unknown_or_zero_age_units_are_rejected` | same four bad ages plus `days: 900`, through `build_settings` (the validator `DiskCleaner` is built from) |
| SAF-012 | `test_aws_disk.py::AwsDiskCreationFloorTests::test_zero_custom_age_tag_skips_volume` | `test_aws_disk_engine.py::test_saf_012_zero_custom_age_tag_skips_volume` | same volume and tag; asserted on `DiskCleaner.plan()` instead of `_is_candidate` |
| SAF-013 | `test_aws_disk.py::AwsDiskCreationFloorTests::test_max_age_env_cannot_shorten_detach_floor` | `test_aws_disk_engine.py::test_saf_013_max_age_env_cannot_shorten_detach_floor` | same `MAX_AGE` env and 2-day volume; asserted on `plan()` |
| SAF-015 | `test_aws_disk.py::AwsDiskDetachAgeTests::test_incomplete_status_keeps_the_volume` | `test_aws_disk_engine.py::test_saf_015_incomplete_status_keeps_the_volume` | same Forbidden status result; scripted CloudWatch through `plan()` |
| SAF-015 | `test_aws_disk.py::AwsDiskDetachAgeTests::test_cloudwatch_failure_drops_the_batch` | `test_aws_disk_engine.py::test_saf_015_cloudwatch_failure_drops_the_batch` | same AccessDenied; also asserts `had_errors` on the cleaner |
| SAF-017 | `test_aws_disk.py::AwsDiskDeletePathTests::test_age_gate_message_does_not_claim_last_detach` | `test_aws_disk_engine.py::test_saf_017_age_gate_message_does_not_claim_last_detach` | same message assertions on `build_settings` error |
| SAF-018 | `test_aws_disk.py::AwsDiskAgeGateTests::test_detach_age_alone_is_enough` | `test_aws_disk_engine.py::test_saf_018_detach_age_alone_is_enough` | same assertions on the built settings |
| SAF-018 | `test_aws_disk.py::AwsDiskAgeGateTests::test_requires_an_age_gate` | `test_aws_disk_engine.py::test_saf_011_and_018_an_age_gate_is_required` | same `None` and `{}` ages; also pins the message (`requires an age gate`) |
| SAF-019 | `test_aws_disk.py::AwsDiskAgeGateTests::test_detach_age_only_still_floors_on_create_time` | `test_aws_disk_engine.py::test_saf_019_detach_age_only_still_floors_on_create_time` | same 5-second-old volume; asserted on `plan()` |
| SAF-020 | `test_aws_disk.py::AwsDiskDetachAgeTests::test_merge_metric_pages_keeps_earlier_values` | `test_aws_disk_engine.py::test_saf_020_merge_metric_pages_keeps_earlier_values` | same PartialData then Complete pages through `plan()` |
| SAF-020 | `test_aws_disk.py::AwsDiskDetachAgeTests::test_paginates_next_token` | `test_aws_disk_engine.py::test_saf_020_paginates_next_token` | same two pages; asserts call count and kept ids |
| SAF-025 | `test_aws_disk.py::AwsDiskCreationFloorTests::test_detach_age_only_uses_max_of_retention_and_detach` | `test_aws_disk_engine.py::test_saf_025_detach_age_only_uses_max_of_retention_and_detach` | same tags and ages; stricter, also scripts the CloudWatch answer and checks the exact planned ids |
| SAF-026 | `test_aws_spot_instance_requests.py::SpotFilterTests::test_matches_tags_on_request_not_instance` | `test_aws_spot_instance_requests_engine.py::test_saf_026_tags_are_matched_on_the_request_not_the_instance` | same request; stricter, asserts the exact planned ids `[sir-1, i-untagged]` (request and instance) |
| SAF-026 | `test_aws_spot_instance_requests.py::SpotFilterTests::test_skips_untagged_even_without_filters` | `test_aws_spot_instance_requests_engine.py::test_saf_026_untagged_requests_are_skipped_even_without_filters` | same untagged request and empty filter through `plan()` |
| SAF-027 | `test_aws_spot_instance_requests.py::SpotFilterTests::test_requires_positive_age` | `test_aws_spot_instance_requests_engine.py::test_saf_007_011_027_a_positive_age_is_required` | same `{}` and `{'days': 0}` plus `None`, through the registered factory |
| SAF-028 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_cancel_before_terminate_and_delete_leftover_volume` | `test_aws_spot_instance_requests_engine.py::test_saf_028_cancel_comes_before_terminate_and_leftover_volume_is_deleted` | same order; stricter, now asserts every mutating call with its arguments (tags, ids) |
| SAF-028 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_failed_cancel_does_not_terminate_persistent` | `test_aws_spot_instance_requests_engine.py::test_saf_028_a_failed_cancel_does_not_terminate_a_persistent_request` | same denied cancel; also asserts no leftover volume is reported |
| SAF-028 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_failed_volume_tag_does_not_terminate` | `test_aws_spot_instance_requests_engine.py::test_saf_028_a_failed_volume_tag_does_not_terminate` | same denied CreateTags; stricter, asserts no delete_volume and the exact `delete_tags` call |
| SAF-029 | `test_aws_spot_instance_requests.py::SpotFilterTests::test_retries_cancelled_with_instance` | `test_aws_spot_instance_requests_engine.py::test_saf_029_a_cancelled_request_with_an_instance_is_retried` | same cancelled request; asserts the exact planned ids `[sir-1, i-1]` |
| SAF-029 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_already_cancelled_skips_cancel_then_terminates` | `test_aws_spot_instance_requests_engine.py::test_saf_029_an_already_cancelled_request_skips_cancel_then_terminates` | same request; stricter, asserts one terminate and the `delete_volume` call |
| SAF-030 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_scope_tags_overwrite_existing_prefix_keys` | `test_aws_spot_instance_requests_engine.py::test_saf_030_scope_tags_overwrite_existing_prefix_keys` | same filter and tag lists through `scope_tags_for_volume` |
| SAF-030 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_tags_bdm_volumes_when_instance_shutting_down` | `test_aws_spot_instance_requests_engine.py::test_saf_030_tags_bdm_volumes_when_instance_is_shutting_down` | same shutting-down instance; stricter, one `create_tags` and the exact `delete_volume` call |
| SAF-030 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_retry_uses_tagged_leftover_volumes` | `test_aws_spot_instance_requests_engine.py::test_saf_030_retry_uses_tagged_leftover_volumes` | same orphan volume; asserts result and related volumes |
| SAF-031 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_volume_exception_tag_skips_leftover_delete` | `test_aws_spot_instance_requests_engine.py::test_saf_031_volume_exception_tag_skips_leftover_delete` | same volume exception tag through `engine.run` |
| SAF-031 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_volume_sensitive_yb_task_skips_leftover_delete` | `test_aws_spot_instance_requests_engine.py::test_saf_031_volume_sensitive_yb_task_skips_leftover_delete` | same sensitive yb_task on the volume through `engine.run` |
| SAF-031 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_sweep_skips_volume_own_exception_on_live_request` | `test_aws_spot_instance_requests_engine.py::test_saf_031_sweep_skips_volume_own_exception_on_live_request` | same sweep volume and live request through `engine.run` |
| SAF-032 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_terminate_failure_clears_leftover_tag` | `test_aws_spot_instance_requests_engine.py::test_saf_032_terminate_failure_clears_leftover_tag` | same failure; stricter, asserts the exact single `delete_tags` call (marker and scope key) |
| SAF-032 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_cancel_botocore_error_invalidates_marker` | `test_aws_spot_instance_requests_engine.py::test_saf_032_cancel_botocore_error_invalidates_marker` | same cancel error; stricter, asserts the exact `delete_tags` call |
| SAF-032 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_terminate_failure_sentinel_when_delete_tags_denied` | `test_aws_spot_instance_requests_engine.py::test_saf_032_terminate_failure_writes_sentinel_when_delete_tags_denied` | same denied DeleteTags; also asserts the scope-key overwrite call |
| SAF-033 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_botocore_terminate_error_invalidates_marker` | `test_aws_spot_instance_requests_engine.py::test_saf_033_botocore_terminate_error_invalidates_marker` | same transport error; asserts exactly one `delete_tags` |
| SAF-033 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_botocore_terminate_error_keeps_marker_if_shutting_down` | `test_aws_spot_instance_requests_engine.py::test_saf_033_botocore_terminate_error_keeps_marker_if_shutting_down` | same shutting-down re-check; asserts one `delete_volume` with its arguments |
| SAF-033 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_botocore_terminate_unknown_state_invalidates_marker` | `test_aws_spot_instance_requests_engine.py::test_saf_033_botocore_terminate_unknown_state_invalidates_marker` | same unknown state after the error; asserts one `delete_tags` |
| SAF-035 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_skips_cancelled_when_instance_already_done` | `test_aws_spot_instance_requests_engine.py::test_saf_035_a_cancelled_request_whose_instance_is_gone_is_skipped` | same terminated instance; asserts no mutating call at all |
| SAF-035 | `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_retry_already_gone_volume_not_recounted` | `test_aws_spot_instance_requests_engine.py::test_saf_035_retry_already_gone_volume_is_not_recounted` | same gone volume; asserts no delete and empty related list |
| SAF-038 | `test_azu_spot_vm.py::AzureDetachedDiskTests::test_collects_detach_os_and_data_disks` | `test_azu_spot_vm_engine.py::test_saf_038_collects_detach_os_and_data_disks` | same VM with Detach, Delete and ephemeral disks through `detached_disk_refs` |
| SAF-038 | `test_azu_spot_vm.py::AzureDetachedDiskTests::test_delete_option_empty_is_detach_default` | `test_azu_spot_vm_engine.py::test_saf_038_empty_delete_option_is_detach_default` | same disks through `detached_disk_refs` |
| SAF-038 | `test_azu_spot_vm.py::AzureDeleteOrderTests::test_delete_vm_removes_leftover_disks_after_vm` | `test_azu_spot_vm_engine.py::test_saf_038_apply_removes_the_leftover_disks_after_the_vm` | same VM; asserts VM, disk, NIC order through `apply()` and the reported disk |
| SAF-039 | `test_gcp_spot_vm.py::GcpNonAutodeleteDiskTests::test_collects_auto_delete_false_only` | `test_gcp_spot_vm_engine.py::GcpNonAutodeleteDiskTests::test_collects_auto_delete_false_only` | same instance through `non_autodelete_disks` |
| SAF-039 | `test_gcp_spot_vm.py::GcpDeleteOrderTests::test_delete_waits_for_zone_op_before_disk` | `test_gcp_spot_vm_engine.py::GcpDeleteOrderTests::test_delete_waits_for_zone_op_before_disk` | same order through `SpotVMCleaner.apply()`; also asserts the operation wait |
| SAF-040 | `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_attached_volumes_collected_detached_skipped` | `test_oci_spot_vm_engine.py::test_attached_volumes_collected_detached_skipped` | same three attachments through `leftovers.attached_volume_ids` |
| SAF-040 | `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_delete_terminates_waits_then_deletes_volume` | `test_oci_spot_vm_engine.py::test_delete_terminates_waits_then_deletes_volume` | same order through `SpotVmCleaner.apply()`; asserts the real terminate, get_instance, delete_volume calls |
| SAF-041 | `test_azu_spot_vm.py::AzureDetachedDiskTests::test_requires_positive_age` | `test_azu_spot_vm_engine.py::test_saf_041_requires_a_positive_age` | same `{}` age through the registered factory; also pins the message |
| SAF-041 | `test_gcp_spot_vm.py::GcpSpotDetectTests::test_requires_positive_age` | `test_gcp_spot_vm_engine.py::GcpSpotDetectTests::test_requires_positive_age` | same `{}` age through `build_spot_cleaner` |
| SAF-041 | `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_requires_positive_age` | `test_oci_spot_vm_engine.py::test_requires_positive_age` | same `{}` age through `build_spot_vm_cleaner`; pins the message |
| SAF-042 | `test_azu_spot_vm.py::AzureDetachedDiskTests::test_untagged_always_skipped` | `test_azu_spot_vm_engine.py::test_saf_042_untagged_spot_vm_is_always_skipped` | same empty filter; covers `None` and `{}` tags and asserts the skip list |
| SAF-043 | `test_gcp_spot_vm.py::GcpSpotDetectTests::test_empty_filter_allows_unlabeled` | `test_gcp_spot_vm_engine.py::GcpSpotDetectTests::test_empty_filter_allows_unlabeled` | same inputs through `has_matching_filter_label` |
| SAF-043 | `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_filter_requires_tags_on_instance` | `test_oci_spot_vm_engine.py::test_filter_requires_tags_on_instance` | same two assertions through `matches_filter_tags` |
| SAF-043 | `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_empty_filter_allows_untagged` | `test_oci_spot_vm_engine.py::test_empty_filter_allows_untagged` | same two assertions through `matches_filter_tags` |
| SAF-044 | `test_azu_spot_vm.py::AzureDeleteOrderTests::test_disk_exception_tag_skips_delete` | `test_azu_spot_vm_engine.py::test_saf_044_exception_tag_on_the_disk_keeps_it` | same disk tag; through `apply()`, asserts no disk delete call |
| SAF-044 | `test_azu_spot_vm.py::AzureDeleteOrderTests::test_disk_outside_resource_group_skipped` | `test_azu_spot_vm_engine.py::test_saf_044_disk_in_another_resource_group_is_not_touched` | same disk id; asserts no disk delete and no disk read |
| SAF-044 | `test_gcp_spot_vm.py::GcpDeleteOrderTests::test_disk_exception_labels_skip_delete` | `test_gcp_spot_vm_engine.py::GcpDeleteOrderTests::test_disk_exception_labels_skip_delete` | same labels through `disk_ok_to_delete` |
| SAF-044 | `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_volume_exception_tags_skip_delete` | `test_oci_spot_vm_engine.py::test_volume_exception_tags_skip_delete` | same tags through `volume_passes_tag_check` |
| SAF-045 | `test_gcp_spot_vm.py::GcpDeleteOrderTests::test_gke_node_skipped` | `test_gcp_spot_vm_engine.py::GcpDeleteOrderTests::test_gke_node_skipped` | same instance through `is_managed_or_gke_node` |
| SAF-045 | `test_gcp_spot_vm.py::GcpDeleteOrderTests::test_mig_created_by_non_list_sequence` | `test_gcp_spot_vm_engine.py::GcpDeleteOrderTests::test_mig_created_by_non_list_sequence` | same `UserList` metadata through `is_managed_or_gke_node` |
| SAF-045 | `test_oci_spot_vm.py::OciPreemptibleDetectTests::test_oke_freeform_tag_skipped` | `test_oci_spot_vm_engine.py::test_oke_freeform_tag_skipped` | same instance through `is_managed_or_oke_node` |
| SAF-045 | `test_oci_spot_vm.py::OciPreemptibleDetectTests::test_instance_pool_defined_tag_skipped` | `test_oci_spot_vm_engine.py::test_instance_pool_defined_tag_skipped` | same defined tag through `is_managed_or_oke_node` |
| SAF-046 | `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_skip_terminate_when_attachment_list_fails` | `test_oci_spot_vm_engine.py::test_skip_terminate_when_attachment_list_fails` | same failing attachment list through `plan()` and `apply()`; asserts no terminate |

## 2. Gap table (16 gaps, all closed)

| SAF | Lock | How |
|---|---|---|
| SAF-001 | `tests/test_aws_connectivity.py::test_log_skipped_region_warns_with_region_context_and_error`, `tests/test_aws_disk_cleaner.py::test_connectivity_error_skips_the_region`, `tests/test_aws_vpc.py::test_plan_skips_region_on_connectivity_error` | existing; the disk and VPC cleaners skip an unreachable region and continue, `log_skipped_region` logs it |
| SAF-002 | `tests/test_own1_saf_gaps.py::test_saf_002_the_only_explicit_exit_is_the_scan_tag_path`, `::test_saf_002_scan_tag_forces_a_dry_run_and_exits_zero_without_cleaning` | new; static scan of `crc/` and `crc.py` finds exactly two `sys.exit(0)` in `crc/cli/scan.py`; `main()` with `--scan_tag` and no `--dry_run` builds the CRC with `dry_run=True`, exits 0 and runs no cleaner |
| SAF-004 | `tests/test_aws_base.py::test_auth_error_propagates_without_fallback`, `tests/test_own1_saf_gaps.py::test_saf_004_an_auth_failure_listing_regions_raises_out_of_the_engine` | existing + new; an `AuthFailure` from `describe_regions` raises out of `engine.run` and nothing is deleted |
| SAF-005 | `tests/test_azure_spot_instances.py::test_plan_list_error_is_raised`, `::test_apply_isolates_a_failing_vm` | existing; list failure raises, a failing VM delete is isolated |
| SAF-006 | `tests/test_core_age.py::test_is_old_empty_age_matches_all` | existing; `Service.is_old` delegates to `crc.core.age.is_old` |
| SAF-009 | `tests/test_core_age_fix.py::test_bad_tag_returns_keep_age_and_keeps_resource`, `::test_keep_age_ignores_max_age`, `::test_bad_tag_counts_skip_and_logs_at_debug_only` | existing; bad tags give `KEEP_AGE`, never old (MAX_AGE included), counted as `bad_age_tag` |
| SAF-010 | `tests/test_core_age.py::test_is_old_max_age_override` | existing; includes `MAX_AGE={'days': 0}` making a 1-hour-old resource old |
| SAF-014 | `tests/test_cli_dispatch_fix.py::test_bug_018_cloud_all_with_disk_raises_before_any_cleaner`, `tests/test_cli_logger.py::test_aws_disk_opt_in_refusal_still_closes_the_run`, `tests/test_cli_dispatch_fix.py::test_aws_disk_with_explicit_cloud_still_runs` | existing; `run_dispatch` raises on aws+disk without opt-in, runs with it |
| SAF-016 | `tests/test_own1_saf_gaps.py::test_saf_016_a_zero_detach_window_says_it_deletes_nothing`, `::test_saf_016_only_dropped_volumes_are_logged_as_skipped` | new; the detach guard logs 'not deleting any' for an empty candidate list and only logs dropped volumes as skipped |
| SAF-021 | `tests/test_gcp_disk.py::test_plan_filters`, `::test_plan_uses_the_context_clock` | existing; detach timestamp against `detach_age` |
| SAF-022 | `tests/test_own1_saf_gaps.py::test_saf_022_gcp_disk_never_reads_age` | new; `age` 100 days passed to the registered factory does not keep a 3-day-detached disk |
| SAF-023 | `tests/test_own1_saf_gaps.py::test_saf_023_gcp_disk_without_detach_age_deletes_every_detached_disk` | new; no `detach_age` plans a 0.001-day-old disk, skips attached and never-detached ones |
| SAF-024 | `tests/test_gcp_disk.py::test_plan_custom_age_label_replaces_detach_age`, `tests/test_own1_saf_gaps.py::test_saf_024_a_custom_age_label_replaces_detach_age` | existing + new; label shorter and longer than `detach_age` both win |
| SAF-034 | `tests/test_own1_saf_gaps.py::test_saf_034_the_sweep_isolates_a_failing_volume` | new; sweep delete of `vol-a` fails, `vol-b` is still deleted and reported |
| SAF-036 | `tests/test_aws_spot_instance_requests_cleaner.py::test_apply_runs_in_safety_order`, `::test_dry_run_plan_reports_and_apply_does_nothing` | existing; `influx_payload()` is `{VM: [request, instance], DISK: [volume]}` |
| SAF-037 | `tests/test_cli_route_skip.py::test_saf_037_spot_instances_under_cloud_all_warns_and_runs_nothing`, `::test_saf_037_spot_instances_with_an_explicit_cloud_runs_the_cleaner` | existing; GCP/OCI spot_instances are skipped under `--cloud all` and run with an explicit cloud |

No gap remains, so none is explained as untestable offline.

## 3. Deleted tests (110) and their covering tests

Floor lowered by 110 (44415 -> 44305): every deleted test has a cover below.

| Deleted test | Covering test |
|---|---|
| `test_aws_disk.py::AwsDiskSkipTests::test_skips_infra_yb_task` | `test_aws_disk_engine.py::test_infrastructure_and_prod_yb_task_volumes_are_kept` |
| `test_aws_disk.py::AwsDiskSkipTests::test_allows_eng_dev_unattached` | `test_aws_disk_engine.py::test_an_eng_dev_unattached_volume_is_a_candidate` |
| `test_aws_disk.py::AwsDiskSkipTests::test_allows_itest` | `test_aws_disk_engine.py::test_filter_tags_allow_itest` |
| `test_aws_disk.py::AwsDiskSkipTests::test_skips_sensitive_yb_task_regardless_of_case` | `test_aws_disk_engine.py::test_sensitive_yb_task_is_kept_regardless_of_case`, `test_sensitive_yb_task_key_case_is_folded` |
| `test_aws_disk.py::AwsDiskSkipTests::test_skips_k8s_unless_filtered` | `test_aws_disk_engine.py::test_kubernetes_volumes_are_skipped_unless_filtered` |
| `test_aws_disk.py::AwsDiskSkipTests::test_exception_tags_autoclean` | `test_aws_disk_engine.py::test_exception_tags_keep_the_volume` |
| `test_aws_disk.py::AwsDiskSkipTests::test_filter_tags_and` | `test_aws_disk_engine.py::test_filter_tags_are_anded_across_keys` |
| `test_aws_disk.py::AwsDiskSkipTests::test_filter_tags_honour_globs` | `test_aws_disk_engine.py::test_filter_tags_honour_globs` |
| `test_aws_disk.py::AwsDiskAgeGateTests::test_requires_an_age_gate` | `test_aws_disk_engine.py::test_saf_011_and_018_an_age_gate_is_required` |
| `test_aws_disk.py::AwsDiskAgeGateTests::test_rejects_unknown_or_zero_age_units` | `test_aws_disk_engine.py::test_saf_007_and_012_unknown_or_zero_age_units_are_rejected` |
| `test_aws_disk.py::AwsDiskAgeGateTests::test_rejects_detach_age_past_cloudwatch_retention` | `test_aws_disk_engine.py::test_saf_007_and_012_unknown_or_zero_age_units_are_rejected (the `days: 900` case)` |
| `test_aws_disk.py::AwsDiskAgeGateTests::test_detach_age_alone_is_enough` | `test_aws_disk_engine.py::test_saf_018_detach_age_alone_is_enough` |
| `test_aws_disk.py::AwsDiskAgeGateTests::test_creation_age_gate` | `test_aws_disk_engine.py::test_creation_age_gate` |
| `test_aws_disk.py::AwsDiskAgeGateTests::test_detach_age_only_still_floors_on_create_time` | `test_aws_disk_engine.py::test_saf_019_detach_age_only_still_floors_on_create_time` |
| `test_aws_disk.py::AwsDiskAgeGateTests::test_attached_volume_is_never_a_candidate` | `test_aws_disk_engine.py::test_attached_volumes_are_never_candidates` |
| `test_aws_disk.py::AwsDiskAgeGateTests::test_multi_attach_is_never_a_candidate` | `test_aws_disk_engine.py::test_multi_attach_volumes_are_never_candidates` |
| `test_aws_disk.py::AwsDiskAgeGateTests::test_untagged_skipped_unless_notags_set` | `test_aws_disk_engine.py::test_untagged_volumes_are_skipped_unless_notags_is_set` |
| `test_aws_disk.py::AwsDiskDetachAgeTests::test_volume_with_datapoints_is_dropped` | `test_aws_disk_engine.py::test_volume_with_datapoints_is_dropped` |
| `test_aws_disk.py::AwsDiskDetachAgeTests::test_incomplete_status_keeps_the_volume` | `test_aws_disk_engine.py::test_saf_015_incomplete_status_keeps_the_volume` |
| `test_aws_disk.py::AwsDiskDetachAgeTests::test_without_detach_age_nothing_is_dropped` | `test_aws_disk_engine.py::test_without_detach_age_cloudwatch_is_not_called` |
| `test_aws_disk.py::AwsDiskDetachAgeTests::test_cloudwatch_failure_drops_the_batch` | `test_aws_disk_engine.py::test_saf_015_cloudwatch_failure_drops_the_batch` |
| `test_aws_disk.py::AwsDiskDetachAgeTests::test_paginates_next_token` | `test_aws_disk_engine.py::test_saf_020_paginates_next_token` |
| `test_aws_disk.py::AwsDiskDetachAgeTests::test_zero_detach_window_deletes_nothing` | `test_own1_saf_gaps.py::test_saf_016_a_zero_detach_window_says_it_deletes_nothing` |
| `test_aws_disk.py::AwsDiskDetachAgeTests::test_merge_metric_pages_keeps_earlier_values` | `test_aws_disk_engine.py::test_saf_020_merge_metric_pages_keeps_earlier_values` |
| `test_aws_disk.py::AwsDiskDeletePathTests::test_dry_run_never_calls_delete_volume` | `test_aws_disk_engine.py::test_dry_run_never_deletes_a_volume` |
| `test_aws_disk.py::AwsDiskDeletePathTests::test_live_delete_calls_delete_volume` | `test_aws_disk_engine.py::test_live_run_deletes_the_volume` |
| `test_aws_disk.py::AwsDiskDeletePathTests::test_had_errors_logs_without_raising` | `test_aws_disk_engine.py::test_saf_003_had_errors_logs_without_raising` |
| `test_aws_disk.py::AwsDiskDeletePathTests::test_age_gate_message_does_not_claim_last_detach` | `test_aws_disk_engine.py::test_saf_017_age_gate_message_does_not_claim_last_detach` |
| `test_aws_disk.py::AwsDiskDeletePathTests::test_invalid_name_regex_raises_at_init` | `test_aws_disk_engine.py::test_an_invalid_name_regex_raises_at_build_time` |
| `test_aws_disk.py::AwsDiskDeletePathTests::test_tag_without_value_does_not_raise` | `test_aws_disk_engine.py::test_a_tag_without_a_value_does_not_raise` |
| `test_aws_disk.py::AwsDiskCreationFloorTests::test_detach_age_only_uses_max_of_retention_and_detach` | `test_aws_disk_engine.py::test_saf_025_detach_age_only_uses_max_of_retention_and_detach` |
| `test_aws_disk.py::AwsDiskCreationFloorTests::test_zero_custom_age_tag_skips_volume` | `test_aws_disk_engine.py::test_saf_012_zero_custom_age_tag_skips_volume` |
| `test_aws_disk.py::AwsDiskCreationFloorTests::test_max_age_env_cannot_shorten_detach_floor` | `test_aws_disk_engine.py::test_saf_013_max_age_env_cannot_shorten_detach_floor` |
| `test_aws_spot_instance_requests.py::SpotFilterTests::test_matches_tags_on_request_not_instance` | `test_aws_spot_instance_requests_engine.py::test_saf_026_tags_are_matched_on_the_request_not_the_instance` |
| `test_aws_spot_instance_requests.py::SpotFilterTests::test_includes_persistent_and_one_time` | `test_aws_spot_instance_requests_engine.py::test_persistent_and_one_time_requests_are_both_planned` |
| `test_aws_spot_instance_requests.py::SpotFilterTests::test_skips_cancelled_without_instance` | `test_aws_spot_instance_requests_engine.py::test_saf_029_a_cancelled_request_without_an_instance_is_not_planned` |
| `test_aws_spot_instance_requests.py::SpotFilterTests::test_retries_cancelled_with_instance` | `test_aws_spot_instance_requests_engine.py::test_saf_029_a_cancelled_request_with_an_instance_is_retried` |
| `test_aws_spot_instance_requests.py::SpotFilterTests::test_skips_untagged_even_without_filters` | `test_aws_spot_instance_requests_engine.py::test_saf_026_untagged_requests_are_skipped_even_without_filters` |
| `test_aws_spot_instance_requests.py::SpotFilterTests::test_requires_positive_age` | `test_aws_spot_instance_requests_engine.py::test_saf_007_011_027_a_positive_age_is_required` |
| `test_aws_spot_instance_requests.py::SpotFilterTests::test_skips_young_requests` | `test_aws_spot_instance_requests_engine.py::test_young_requests_are_skipped` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_cancel_before_terminate_and_delete_leftover_volume` | `test_aws_spot_instance_requests_engine.py::test_saf_028_cancel_comes_before_terminate_and_leftover_volume_is_deleted` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_failed_cancel_does_not_terminate_persistent` | `test_aws_spot_instance_requests_engine.py::test_saf_028_a_failed_cancel_does_not_terminate_a_persistent_request` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_dry_run_lists_leftover_volumes_without_api_mutations` | `test_aws_spot_instance_requests_engine.py::test_a_dry_run_lists_the_leftover_volume_without_mutating` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_volume_exception_tag_skips_leftover_delete` | `test_aws_spot_instance_requests_engine.py::test_saf_031_volume_exception_tag_skips_leftover_delete` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_volume_sensitive_yb_task_skips_leftover_delete` | `test_aws_spot_instance_requests_engine.py::test_saf_031_volume_sensitive_yb_task_skips_leftover_delete` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_scope_tags_overwrite_existing_prefix_keys` | `test_aws_spot_instance_requests_engine.py::test_saf_030_scope_tags_overwrite_existing_prefix_keys` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_already_cancelled_skips_cancel_then_terminates` | `test_aws_spot_instance_requests_engine.py::test_saf_029_an_already_cancelled_request_skips_cancel_then_terminates` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_skips_cancelled_when_instance_already_done` | `test_aws_spot_instance_requests_engine.py::test_saf_035_a_cancelled_request_whose_instance_is_gone_is_skipped` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_retry_uses_tagged_leftover_volumes` | `test_aws_spot_instance_requests_engine.py::test_saf_030_retry_uses_tagged_leftover_volumes` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_retry_already_gone_volume_not_recounted` | `test_aws_spot_instance_requests_engine.py::test_saf_035_retry_already_gone_volume_is_not_recounted` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_failed_volume_tag_does_not_terminate` | `test_aws_spot_instance_requests_engine.py::test_saf_028_a_failed_volume_tag_does_not_terminate` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_create_tags_timeout_invalidates_failing_volume` | `test_aws_spot_instance_requests_engine.py::test_a_create_tags_timeout_invalidates_both_volumes` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_invalid_custom_age_tag_skips_request` | `test_aws_spot_instance_requests_engine.py::test_saf_008_an_invalid_or_unparseable_custom_age_tag_skips_the_request` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_unparseable_custom_age_tag_skips_request` | `test_aws_spot_instance_requests_engine.py::test_saf_008_an_invalid_or_unparseable_custom_age_tag_skips_the_request` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_sweep_tagged_leftover_volumes` | `test_aws_spot_instance_requests_engine.py::test_the_sweep_deletes_a_tagged_orphan_volume` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_sweep_skips_young_live_request` | `test_aws_spot_instance_requests_engine.py::test_the_sweep_skips_a_young_live_request` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_sweep_skips_untagged_live_request` | `test_aws_spot_instance_requests_engine.py::test_the_sweep_skips_a_live_untagged_request` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_sweep_skips_volume_own_exception_on_live_request` | `test_aws_spot_instance_requests_engine.py::test_saf_031_sweep_skips_volume_own_exception_on_live_request` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_sweep_skips_other_owner_and_in_use` | `test_aws_spot_instance_requests_engine.py::test_the_sweep_skips_another_owner_and_in_use_volumes` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_terminate_failure_clears_leftover_tag` | `test_aws_spot_instance_requests_engine.py::test_saf_032_terminate_failure_clears_leftover_tag` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_cancel_botocore_error_invalidates_marker` | `test_aws_spot_instance_requests_engine.py::test_saf_032_cancel_botocore_error_invalidates_marker` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_terminate_failure_sentinel_when_delete_tags_denied` | `test_aws_spot_instance_requests_engine.py::test_saf_032_terminate_failure_writes_sentinel_when_delete_tags_denied` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_sweep_rejects_terminate_failed_sentinel` | `test_aws_spot_instance_requests_engine.py::test_the_sweep_rejects_the_terminate_failed_sentinel` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_botocore_terminate_error_invalidates_marker` | `test_aws_spot_instance_requests_engine.py::test_saf_033_botocore_terminate_error_invalidates_marker` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_botocore_terminate_error_keeps_marker_if_shutting_down` | `test_aws_spot_instance_requests_engine.py::test_saf_033_botocore_terminate_error_keeps_marker_if_shutting_down` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_botocore_terminate_unknown_state_invalidates_marker` | `test_aws_spot_instance_requests_engine.py::test_saf_033_botocore_terminate_unknown_state_invalidates_marker` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_tags_bdm_volumes_when_instance_shutting_down` | `test_aws_spot_instance_requests_engine.py::test_saf_030_tags_bdm_volumes_when_instance_is_shutting_down` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_abort_when_describe_instances_fails` | `test_aws_spot_instance_requests_engine.py::test_a_failed_describe_instances_aborts_before_cancel_or_terminate` |
| `test_aws_spot_instance_requests.py::SpotCleanupOrderTests::test_filter_tags_glob_match` | `test_aws_spot_instance_requests_engine.py::test_filter_tags_honour_globs` |
| `test_azu_spot_vm.py::AzureDetachedDiskTests::test_collects_detach_os_and_data_disks` | `test_azu_spot_vm_engine.py::test_saf_038_collects_detach_os_and_data_disks` |
| `test_azu_spot_vm.py::AzureDetachedDiskTests::test_delete_option_empty_is_detach_default` | `test_azu_spot_vm_engine.py::test_saf_038_empty_delete_option_is_detach_default` |
| `test_azu_spot_vm.py::AzureDetachedDiskTests::test_ephemeral_os_disk_without_managed_id_skipped` | `test_azu_spot_vm_engine.py::test_saf_038_ephemeral_os_disk_without_managed_id_is_skipped` |
| `test_azu_spot_vm.py::AzureDetachedDiskTests::test_disk_name_from_managed_id` | `test_azu_spot_vm_engine.py::test_saf_038_disk_name_from_managed_id` |
| `test_azu_spot_vm.py::AzureDetachedDiskTests::test_untagged_always_skipped` | `test_azu_spot_vm_engine.py::test_saf_042_untagged_spot_vm_is_always_skipped` |
| `test_azu_spot_vm.py::AzureDetachedDiskTests::test_requires_positive_age` | `test_azu_spot_vm_engine.py::test_saf_041_requires_a_positive_age` |
| `test_azu_spot_vm.py::AzureDetachedDiskTests::test_disk_ref_parses_other_resource_group` | `test_azu_spot_vm_engine.py::test_saf_038_disk_ref_parses_another_resource_group` |
| `test_azu_spot_vm.py::AzureDeleteOrderTests::test_delete_vm_removes_leftover_disks_after_vm` | `test_azu_spot_vm_engine.py::test_saf_038_apply_removes_the_leftover_disks_after_the_vm` |
| `test_azu_spot_vm.py::AzureDeleteOrderTests::test_dry_run_records_leftover_disks_without_deleting_vm` | `test_azu_spot_vm_engine.py::test_saf_038_dry_run_records_leftover_disks_without_deleting_the_vm` |
| `test_azu_spot_vm.py::AzureDeleteOrderTests::test_disk_exception_tag_skips_delete` | `test_azu_spot_vm_engine.py::test_saf_044_exception_tag_on_the_disk_keeps_it` |
| `test_azu_spot_vm.py::AzureDeleteOrderTests::test_disk_outside_resource_group_skipped` | `test_azu_spot_vm_engine.py::test_saf_044_disk_in_another_resource_group_is_not_touched` |
| `test_gcp_spot_vm.py::GcpSpotDetectTests::test_preemptible_true_is_spot` | `test_gcp_spot_vm_engine.py::GcpSpotDetectTests::test_preemptible_true_is_spot` |
| `test_gcp_spot_vm.py::GcpSpotDetectTests::test_provisioning_model_spot_is_spot` | `test_gcp_spot_vm_engine.py::GcpSpotDetectTests::test_provisioning_model_spot_is_spot` |
| `test_gcp_spot_vm.py::GcpSpotDetectTests::test_standard_vm_is_not_spot` | `test_gcp_spot_vm_engine.py::GcpSpotDetectTests::test_standard_vm_is_not_spot` |
| `test_gcp_spot_vm.py::GcpSpotDetectTests::test_missing_scheduling_is_not_spot` | `test_gcp_spot_vm_engine.py::GcpSpotDetectTests::test_missing_scheduling_is_not_spot` |
| `test_gcp_spot_vm.py::GcpSpotDetectTests::test_empty_filter_allows_unlabeled` | `test_gcp_spot_vm_engine.py::GcpSpotDetectTests::test_empty_filter_allows_unlabeled` |
| `test_gcp_spot_vm.py::GcpSpotDetectTests::test_requires_positive_age` | `test_gcp_spot_vm_engine.py::GcpSpotDetectTests::test_requires_positive_age` |
| `test_gcp_spot_vm.py::GcpSpotDetectTests::test_invalid_custom_age_skips_even_when_old` | `test_gcp_spot_vm_engine.py::GcpSpotDetectTests::test_invalid_custom_age_skips_even_when_old` |
| `test_gcp_spot_vm.py::GcpNonAutodeleteDiskTests::test_collects_auto_delete_false_only` | `test_gcp_spot_vm_engine.py::GcpNonAutodeleteDiskTests::test_collects_auto_delete_false_only` |
| `test_gcp_spot_vm.py::GcpNonAutodeleteDiskTests::test_disk_name_from_source` | `test_gcp_spot_vm_engine.py::GcpNonAutodeleteDiskTests::test_disk_name_from_source` |
| `test_gcp_spot_vm.py::GcpNonAutodeleteDiskTests::test_disk_ref_parses_regional` | `test_gcp_spot_vm_engine.py::GcpNonAutodeleteDiskTests::test_disk_ref_parses_regional` |
| `test_gcp_spot_vm.py::GcpNonAutodeleteDiskTests::test_dry_run_delete_disk_records_without_api` | `test_gcp_spot_vm_engine.py::GcpNonAutodeleteDiskTests::test_dry_run_delete_disk_records_without_api` |
| `test_gcp_spot_vm.py::GcpDeleteOrderTests::test_delete_waits_for_zone_op_before_disk` | `test_gcp_spot_vm_engine.py::GcpDeleteOrderTests::test_delete_waits_for_zone_op_before_disk` |
| `test_gcp_spot_vm.py::GcpDeleteOrderTests::test_disk_exception_labels_skip_delete` | `test_gcp_spot_vm_engine.py::GcpDeleteOrderTests::test_disk_exception_labels_skip_delete` |
| `test_gcp_spot_vm.py::GcpDeleteOrderTests::test_gke_node_skipped` | `test_gcp_spot_vm_engine.py::GcpDeleteOrderTests::test_gke_node_skipped` |
| `test_gcp_spot_vm.py::GcpDeleteOrderTests::test_mig_created_by_non_list_sequence` | `test_gcp_spot_vm_engine.py::GcpDeleteOrderTests::test_mig_created_by_non_list_sequence` |
| `test_oci_spot_vm.py::OciPreemptibleDetectTests::test_preemptible_config_present` | `test_oci_spot_vm_engine.py::test_preemptible_config_present` |
| `test_oci_spot_vm.py::OciPreemptibleDetectTests::test_regular_instance_not_preemptible` | `test_oci_spot_vm_engine.py::test_regular_instance_not_preemptible` |
| `test_oci_spot_vm.py::OciPreemptibleDetectTests::test_oke_freeform_tag_skipped` | `test_oci_spot_vm_engine.py::test_oke_freeform_tag_skipped` |
| `test_oci_spot_vm.py::OciPreemptibleDetectTests::test_instance_pool_defined_tag_skipped` | `test_oci_spot_vm_engine.py::test_instance_pool_defined_tag_skipped` |
| `test_oci_spot_vm.py::OciPreemptibleDetectTests::test_plain_itest_not_managed` | `test_oci_spot_vm_engine.py::test_plain_itest_not_managed` |
| `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_attached_volumes_collected_detached_skipped` | `test_oci_spot_vm_engine.py::test_attached_volumes_collected_detached_skipped` |
| `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_attachment_list_failure_returns_none` | `test_oci_spot_vm_engine.py::test_attachment_list_failure_returns_none` |
| `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_volume_exception_tags_skip_delete` | `test_oci_spot_vm_engine.py::test_volume_exception_tags_skip_delete` |
| `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_dry_run_delete_volume_records_without_api` | `test_oci_spot_vm_engine.py::test_dry_run_plan_records_volume_without_api_call` |
| `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_filter_requires_tags_on_instance` | `test_oci_spot_vm_engine.py::test_filter_requires_tags_on_instance` |
| `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_empty_filter_allows_untagged` | `test_oci_spot_vm_engine.py::test_empty_filter_allows_untagged` |
| `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_requires_positive_age` | `test_oci_spot_vm_engine.py::test_requires_positive_age` |
| `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_delete_terminates_waits_then_deletes_volume` | `test_oci_spot_vm_engine.py::test_delete_terminates_waits_then_deletes_volume` |
| `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_skip_terminate_when_attachment_list_fails` | `test_oci_spot_vm_engine.py::test_skip_terminate_when_attachment_list_fails` |
| `test_oci_spot_vm.py::OciLeftoverVolumeTests::test_delete_still_tries_volumes_when_wait_fails` | `test_oci_spot_vm_engine.py::test_delete_still_tries_volumes_when_wait_fails` |

## 4. requirements.txt proof

Removed `paramiko`, `requests`, `beautifulsoup4`; every other line unchanged.

- `git grep -n -E "^\s*(import|from)\s+(paramiko|requests|bs4|beautifulsoup4)\b"` over the whole repository: no match.
- `git grep -n -i -E "paramiko|bs4|beautifulsoup|import requests|requests\.(get|post|Session)"` outside `docs/archive`: only `requirements.txt` itself. `docs/archive` has no match either (the grep prints nothing for it).
- `requests` stays installed as a transitive dependency of `azure-core`, `msrest`, `msal`, `google-api-core` and `requests-oauthlib` (`pip show requests`: Required-by), so nothing that imports it indirectly changes.
- Consumers of this file in this repository: `.github/workflows/ci.yml` and `.cursor/environment.json` (`pip install -r requirements.txt -r requirements-dev.txt`) and `README.md`; none runs another script. ARCH-007 hashes the file: `update_frozen_inputs.py` was run.
- Triggers repo: `yugabyte/cloud-resource-cleanup-triggers` is not visible from this environment (`gh api` returns 404), so the statement that its Jenkins jobs install this file only to run `crc.py` and that the portal job has its own `requirements.txt` is taken from the task contract. Owner action: confirm.

## Verification

`make check` green (ruff, mypy, import-linter, pytest, ARCH-008: 44350 tests, floor 44305).

## Docs to update

- `tests/archtest/README.md` "SAF coverage": now 46 of 46 IDs are locked, no gaps (it still says 30 of 46 and lists the 16 gaps). The sentence "Do not invent tests to close a gap" no longer applies.
- `README.md` Prerequisites already lists none of the three removed packages; no change needed.
- Package READMEs (`crc/aws/README.md` item 3 and similar in azu/gcp/oci) list `tests/test_aws_disk.py`, `tests/test_aws_spot_instance_requests.py` and the other four deleted files as tests: point them at the `*_engine.py` files.
- `CLEANUP_SAFETY.md`: no rule changed. Optionally name the new lock tests.

## Round 1 (Argon)

### Findings

1. **Lock pairs (65 entries across 30 previously-locked SAF IDs):** Inspected every old test in `tests/test_aws_disk.py`, `tests/test_aws_spot_instance_requests.py`, `tests/test_azu_spot_vm.py`, `tests/test_gcp_spot_vm.py`, and `tests/test_oci_spot_vm.py` at `a0b3d09` side-by-side against its replacement in `tests/test_*_engine.py`. Every replacement drives the production `Cleaner` / `crc.core.engine.run` / registered factory or production rule helper with the same inputs and assertions at least as strict (and in several cases stricter, e.g. asserting exact mutating call arguments, `outcome.result` fields, and CloudWatch queries through `plan()`). No lock points at a legacy file.
2. **Closed SAF gaps (16 IDs: SAF-001, 002, 004, 005, 006, 009, 010, 014, 016, 021, 022, 023, 024, 034, 036, 037):** Checked each rule in `CLEANUP_SAFETY.md` against its locked test(s) in `tests/archtest/safety_lock.json` (total 46 SAF IDs, 94 locked test entries, `"gaps": []`). Each locked test directly exercises and asserts the invariant described in `CLEANUP_SAFETY.md` on the production path.
3. **Deleted legacy tests and floor math:** Verified that the five deleted files contained 110 tests (`test_aws_disk.py`: 33, `test_aws_spot_instance_requests.py`: 36, `test_azu_spot_vm.py`: 11, `test_gcp_spot_vm.py`: 15, `test_oci_spot_vm.py`: 15). Every deleted test has a named covering engine test in Table 3, and `tests/archtest/test_count_floor.txt` dropped by exactly 110 (`44415` -> `44305`). `pyproject.toml` ruff `extend-exclude` and mypy legacy ratchet overrides are now empty, and `tests/archtest/legacy_baseline.txt` only shrank (dropped the 5 deleted test files and 17 non-existent legacy paths).
4. **Dependencies and guardrail tamper check:** Confirmed via `git grep` that `paramiko`, `requests`, and `beautifulsoup4` are not imported anywhere in the repository, no other lines in `requirements.txt` changed, and `tests/archtest/frozen_inputs.json` was regenerated. Mutated an assertion in `tests/test_aws_disk_engine.py::test_saf_003_had_errors_logs_without_raising` locally and confirmed `tests/archtest/test_safety_lock.py::test_saf_lock_locked_tests_are_unchanged` failed with `SAF-LOCK: ... changed (AST hash mismatch)` before restoring.
5. **Scope:** Only allowed files in the lane contract were modified (`docs/archive/reviews/OWN-1.md`, `pyproject.toml`, `requirements.txt`, `tests/archtest/{frozen_inputs.json,legacy_baseline.txt,safety_lock.json,test_count_floor.txt}`, the 5 deleted legacy test files, and `tests/{test_aws_disk_engine.py,test_aws_spot_instance_requests_engine.py,test_own1_saf_gaps.py}`). Zero changes to `crc/**`, docs outside `docs/archive/reviews/OWN-1.md`, `Makefile`, or CI.

### Gates & Verification

- `pip install -r requirements.txt -r requirements-dev.txt`: pass
- `make check` (`ruff check`, `ruff format --check`, `mypy`, `lint-imports`, `pytest`, `tests.archtest.test_count check`): pass
- **Test count & runtime:** `44350 passed in 51.78s` (floor `44305`, ARCH-008 green; 174 archtest items passed)

VERDICT: APPROVE


## Round 2 (Opus)

### Findings

No blocking or non-blocking defects found; no file changed except this review.

1. **Round 1 fixes and findings:** round 1 reported no findings and made no fixes; its claims were re-checked independently below.
2. **Lock pairs (check 1):** no `"test"` entry in `tests/archtest/safety_lock.json` (94 entries, 46 SAF IDs, `gaps: []`) points at one of the five legacy files. Every removed line in `tests/test_aws_disk_engine.py` and `tests/test_aws_spot_instance_requests_engine.py` was replaced by a stricter assertion (`[0] == "sir-1"` became the full planned id list; `"x" in methods` became exact counts plus exact call arguments; the mutating-call order now includes arguments). I read these old and new tests side by side: SAF-007/012 (all five bad ages, `days: 900` included, through `build_settings`), SAF-015 (Forbidden status and AccessDenied through `DiskCleaner.plan()`, plus `had_errors`), SAF-028/029/030/032/033 (spot, through `engine.run`), SAF-038/044 Azure (`apply()` order VM, disk, NIC; no `disk_begin_delete`, no `disk_get` for another resource group), SAF-039 GCP (`instance, operation, disk` through `SpotVMCleaner.apply()`), SAF-040/046 OCI (terminate, poll, delete order; failed attachment list gives no candidate and no terminate). Each runs the production Cleaner, the registered factory or the production rule helper, with the same inputs and assertions at least as strict. No weaker lock.
3. **Gaps (check 2):** I read every rule text in `CLEANUP_SAFETY.md` next to its lock. New tests in `tests/test_own1_saf_gaps.py` assert the rule itself (SAF-002 exit-0 inventory and forced dry run through `main()`; SAF-004 AuthFailure raises out of `engine.run` with nothing deleted; SAF-016 no "keep" wording for a zero window, only dropped volumes logged as skipped; SAF-022/023/024 through the registered `gcp`/`disk` factory; SAF-034 a failing sweep delete does not stop the next volume). Reused locks also match their rules: SAF-036 asserts `influx_payload() == {VM: [request, instance], DISK: [volume]}` for plan and apply; SAF-005 list error raises, a failing VM is isolated; SAF-006 empty age matches all, even with `MAX_AGE`; SAF-010 `MAX_AGE` override parametrized. No gap remains.
4. **Deleted tests and floor (check 3):** at `a0b3d09` the five files define 33 + 36 + 11 + 15 + 15 = 110 tests (no parametrize or subTest), table 3 has 110 rows, and every covering test it names exists (`def` found in the named file). Floor 44415 -> 44305 = 110. The `legacy_baseline.txt` diff has only removals, and none of the removed paths exists.
5. **Requirements (check 4):** `git grep` finds no `import`/`from` of `paramiko`, `requests` or `bs4` in any `.py` file. The only `requests` imports are the local `crc.aws.spot_instance_requests.requests` module. The diff removes exactly those three lines, `frozen_inputs.json` changes only the `requirements.txt` hash, and ARCH-007 passes. In-repo consumers (`ci.yml`, `.cursor/environment.json`, `AGENTS.md`/`README.md` instructions) install it only to run CRC and its tests. `gh api repos/yugabyte/cloud-resource-cleanup-triggers` still returns 404 from here, so the claim about the triggers repo rests on the task contract (owner action kept from section 4).
6. **Tamper test (check 4):** locally (not committed) I replaced `assert "disk_get" not in _names(azure)` with `pass` in `test_saf_044_disk_in_another_resource_group_is_not_touched`, and loosened `exit_info.value.code == 0` to `in (0, 1)` in `test_saf_002_scan_tag_forces_a_dry_run_and_exits_zero_without_cleaning`. `test_safety_lock.py` failed with `SAF-LOCK: SAF-044 ... changed (AST hash mismatch)` and the same for SAF-002. After restoring, it passed again and `git status` was clean.
7. **Scope (check 5):** `git diff --name-only a0b3d09..HEAD` touches nothing under `crc/**` and no `Makefile`, CI, `AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md` or README. Only the lane files listed by round 1 changed. The adapter classes are still in place.

### Gates

- `pip install -r requirements.txt -r requirements-dev.txt`: pass.
- `make check` (ruff check, ruff format --check, mypy, lint-imports, pytest, ARCH-008): pass, `44350 passed in 53.31s`, `ARCH-008: 44350 tests, floor 44305`, wall time 1m14s.

ROUND 2 VERDICT: APPROVE
