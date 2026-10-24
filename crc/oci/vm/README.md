# crc.oci.vm

## Purpose

OCI compute instance cleaner (`--cloud oci --resource vm`). `VmCleaner` is a registered `Cleaner`
(`@register("oci", "vm")`): `plan()` lists the instances of every region and decides on each one,
`apply()` terminates (`delete`) or stops (`stop`) the selected instances. `crc/cli/routes/oci.py`
routes the pair through `crc.core.engine`.

Modules, split by responsibility:

- `client.py`: the typed Protocols of the OCI compute calls used, their SDK implementation and the per-region factory on top of `Base`
- `settings.py`: `VmSettings`, the skip vocabulary and the summary logging
- `cleaner.py`: `VmCleaner` and the registry factory (re-exports the settings names)

## Public API

- `SdkInstance`: Protocol of the attributes read from an OCI compute instance (including defined tags and metadata, read only by the OKE guard).
- `ComputeApi`: Protocol of the three OCI calls the VM cleaner uses (list instances, terminate instance, stop instance); tests pass fakes.
- `ApiFactory`: callable returning the `ComputeApi` of one region.
- `STOP_ACTION`: the instance_action value that stops an instance.
- `OciComputeApi`: `ComputeApi` over the compute client of one region; pages through every result and maps stop to the instance_action call with the STOP action.
- `compute_api_for()`: builds the per-region `ComputeApi` factory on top of Base, which caches clients per region.
- `DEFAULT_INSTANCE_STATES`: the default allowed state, running.
- `VmSettings`: frozen dataclass with the filters and thresholds of one run (filter tags, exception tags, notags, age, custom age tag key, allowed instance states).
- `VmCleaner`: the Cleaner (supports delete and stop, read-only plan, mutating apply, plus decide and act); built by the registry factory with the engine flag, see Safety Invariants.
- `build_vm_cleaner()`: registry factory behind @register("oci", "vm"); reads filter_tags, exception_tags, notags, age, custom_age_tag_key and (delete only; stop always uses RUNNING) instance_state from RunContext.options.
- `log_summary()`: the closing log lines of a VM run (settings module); the "nothing to do" warning looks at both name lists.
- `log_for()`: `log_summary()` for a run that only fills one of the two lists.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`. Applicable IDs: SAF-006 to SAF-013 (age gates, shared by all clouds).
- `plan()` only lists and decides (ARCH-005); `apply()` is the only caller of `terminate_instance` / `stop_instance`.
- Only instances in an allowed state (`RUNNING` by default) are candidates; `filter_tags` needs every key.
- An empty age matches every instance (SAF-006) and `MAX_AGE` can shorten the age (SAF-010); a present but bad custom age tag keeps the instance (`get_retention_age` returns `KEEP_AGE`, counted as skip `bad_age_tag`; SAF-009, `BUG-004` fixed).
- OKE node-pool and instance-pool members are never terminated or stopped (`BUG-158`, same guard as `crc/oci/spot_vm`, `policy.oci_is_managed_or_oke_node`: an `oke` / `instancepool` / `instance_pool` / `instance-pool` marker, case-insensitive, in any freeform tag `key:value`, defined tag `namespace:key:value` or metadata entry). It runs right after the state check, also when `filter_tags` is empty; the skip reason is `managed`.
- An unexpected error while evaluating one instance (missing or naive `time_created`, malformed `MAX_AGE`; `BUG-162`) is logged at ERROR, recorded as a run-log failure and the instance is kept; the loop goes on with the next instance and region (skip reason `evaluation failed`, in `plan().skipped` only). The run no longer aborts.
- A failed terminate or stop is logged and skipped, never raised; list and region errors are not isolated per instance (connectivity errors skip the region, other errors propagate).
- Known bugs and quirks (do not fix outside their own change): `docs/refactor/BUGS.md` (`BUG-159` to `BUG-161`, `BUG-163` to `BUG-171`, `BUG-352`; `BUG-158` and `BUG-162` are fixed).
- Engine path: the factory builds `VmCleaner(..., engine=True)`, so a dry-run `plan()` logs the would_delete / would_stop ACTION lines per region and names its candidates by display name, because `crc.core.engine` never calls `apply()` in a dry run. (`crc/cli/routes/oci.py`, BUG-408): `plan()` lists every region before `apply()` acts, so a listing error in a later region stops the pair before any terminate or stop.
- Do not call real cloud APIs in tests; use fakes for `ComputeApi`.

## Extension Rules

1. A behaviour fix names its `BUG-NNN` (`docs/refactor/BUGS.md`); do not fix a quirk inside an unrelated change.
2. Tag, age and state decisions call `crc.core.policy` / `crc.core.age`; no `re`, no wall-clock read in the cleaner (the clock is injected), no `os.environ`, no `print` (ARCH-003).
3. Every SDK call goes through `ComputeApi` in `client.py`; add a method to the Protocol only for a call the cleaner really makes.
4. `plan()` stays read-only: every mutating call belongs in `apply()` / `act()`.
5. Record every new bug or quirk in `docs/refactor/BUGS.md` (a new row cites the current `file:line` in `crc/`).
