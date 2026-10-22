# crc.oci.vm

## Purpose

OCI compute instance cleaner (`--cloud oci --resource vm`). `VmCleaner` is a registered `Cleaner`
(`@register("oci", "vm")`): `plan()` lists the instances of every region and decides on each one,
`apply()` terminates (`delete`) or stops (`stop`) the selected instances. `VM` is the adapter entry
point `crc.py` uses; `from crc.oci.vm import VM` keeps working. Converted in rung ST-OCI-VM from
the sibling files `crc/oci/vm.py` and `crc/oci/vm_api.py` (pure move, no behaviour change); the old
`crc.oci.vm_api` path is gone, its names are exported by this package.

Modules, split by responsibility:

- `client.py`: the typed Protocols of the OCI compute calls used, their SDK implementation and the per-region factory on top of `Base` (was `vm_api.py`)
- `cleaner.py`: settings, summary logging, `VmCleaner` and the registry factory
- `adapter.py`: `VmRuntime` and `VM`, the class `crc.py` calls, which drives the cleaner region by region and instance by instance

## Public API

- `SdkInstance`: Protocol of the attributes read from an OCI compute instance.
- `ComputeApi`: Protocol of the three OCI calls the VM cleaner uses (list instances, terminate instance, stop instance); tests pass fakes.
- `ApiFactory`: callable returning the `ComputeApi` of one region.
- `STOP_ACTION`: the instance_action value that stops an instance.
- `OciComputeApi`: `ComputeApi` over the compute client of one region; pages through every result and maps stop to the instance_action call with the STOP action.
- `compute_api_for()`: builds the per-region `ComputeApi` factory on top of Base, which caches clients per region.
- `DEFAULT_INSTANCE_STATES`: the default allowed state, running.
- `VmSettings`: frozen dataclass with the filters and thresholds of one run (filter tags, exception tags, notags, age, custom age tag key, allowed instance states).
- `VmCleaner`: the Cleaner (supports delete and stop, read-only plan, mutating apply, plus decide and act used by the adapter).
- `build_vm_cleaner()`: registry factory behind @register("oci", "vm"); reads filter_tags, exception_tags, notags, age, custom_age_tag_key and (delete only; stop always uses RUNNING, as on main) instance_state from RunContext.options.
- `VmRuntime`: frozen dataclass with the injected API factory, region lister and clock (now) of `VM`; unset parts come from Base.
- `VM`: adapter crc.py calls (delete(), stop(), get_deleted, get_stopped, delete_count, stopped_count, instance_names_to_delete, instance_names_to_stop); subclasses Service and Base; decides and acts instance by instance in the order of main.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`. Applicable IDs: SAF-006 to SAF-013 (age gates, shared by all clouds).
- `plan()` only lists and decides (ARCH-005); `apply()` is the only caller of `terminate_instance` / `stop_instance`.
- Only instances in an allowed state (`RUNNING` by default) are candidates; `filter_tags` needs every key (`docs/refactor/FILTER_VARIANTS.md`, section 5).
- An empty age matches every instance (SAF-006) and `MAX_AGE` can shorten the age (SAF-010); the age of an instance with a custom age tag fails open to `--age` (SAF-009, `BUG-004`).
- A failed terminate or stop is logged and skipped, never raised, as on main; list and region errors are not isolated per instance (connectivity errors skip the region, other errors propagate).
- Known bugs and quirks (do not fix outside a behaviour-fix rung): `docs/refactor/BUGS.md` (`BUG-158` to `BUG-171`, `BUG-352`).
- Do not call real cloud APIs in tests; use fakes for `ComputeApi`.

## Extension Rules

1. Behaviour fixes are separate rungs (`docs/refactor/PLAN.md`); do not fix a quirk inside a refactor change.
2. Tag, age and state decisions call `crc.core.policy` / `crc.core.age`; no `re`, no wall-clock read in the cleaner (the clock is injected), no `os.environ`, no `print` (ARCH-003).
3. Every SDK call goes through `ComputeApi` in `client.py`; add a method to the Protocol only for a call the cleaner really makes.
4. `plan()` stays read-only: every mutating call belongs in `apply()` / `act()`.
5. Record every new bug or quirk in `docs/refactor/BUGS.md` with its `main` file:line.
