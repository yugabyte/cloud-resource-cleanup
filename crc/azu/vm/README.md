# crc.azu.vm

## Purpose

Azure virtual machine cleaner. `VmCleaner` is a registered `Cleaner`
(`@register("azure", "vm")`): `plan()` lists the VMs of the configured resource group only, applies the tag and
age policy and reads each selected VM's instance view; `apply()` deletes the VM and waits for it,
then removes its `<vm>-NIC` NIC (through `crc/azu/vm_delete_helpers.py`), or deallocates the VM.
The CLI runs it through `crc.core.engine` (`crc/cli/routes/azu.py`).

Modules (split by responsibility):

- `client.py`: the Azure SDK calls used, as typed Protocols
- `cleaner.py`: `VmCleaner` and the registry factory
- `settings.py`: `VmSettings`, `Tally` and the summary logging
- `helpers.py`: name tally, power-state parsing and the processing-error log
- `selection.py`: the tag and age selection

## Public API

- `SdkVm`, `InstanceStatus`, `InstanceView`: Protocols of the SDK objects read (name, tags, time_created, statuses, code, display_status).
- `LongRunningOperation`: Protocol of the poller the begin_delete calls return (result()).
- `VmOperations`: Protocol of the four virtual_machines calls used (list, instance_view, begin_delete, begin_deallocate); tests pass fakes.
- `VmComputeClient`: Protocol of the compute client part used (virtual_machines).
- `NicOperations`: Protocol of the one network_interfaces call used (begin_delete, positional arguments).
- `VmNetworkClient`: Protocol of the network client part used (network_interfaces).
- `ComputeFactory`, `NetworkFactory`: callables returning those clients; injected through the constructor.
- `DEFAULT_INSTANCE_STATES`: the default allowed state, running.
- `VmSettings`: frozen filters and thresholds of one run.
- `Tally`: the VM, stopped VM and NIC names recorded so far.
- `log_summary()`: the closing log lines for an operation.
- `tally_of()`: the `Tally` of a plan's names for an operation (delete adds the NIC names).
- `power_state()`: the lower-cased code after the PowerState prefix of the first such status, or None.
- `log_processing_error()`: logs a swallowed per-VM error.
- `skip_reason()`: the tag and age skip reason of one VM, or None when selected.
- `is_old()`: the age check, with the custom age tag and the clock.
- `VmCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus list_vms, decide, state_skip_reason and act).
- `build_vm_cleaner()`: registry factory behind @register("azure", "vm"); reads resource_group, filter_tags, exception_tags, notags, age, custom_age_tag_key and instance_state from RunContext.options and builds the clients from Base.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md` (SAF-006, SAF-009, SAF-010, SAF-042; SAF-011 to SAF-013 are not met).
- `plan()` only calls `list` and `instance_view`; `begin_delete`, `begin_deallocate` and the NIC `begin_delete` are called from `apply()` only and never in a dry run (ARCH-005).
- Untagged VMs are skipped (SAF-042); filter tags need all keys, exception tags any key, notags all keys (`crc.core.policy`).
- Age uses `crc.core.age` (`get_retention_age`, fail closed (BUG-004), then `is_old`, where `MAX_AGE` still applies); the clock is injected (`RunContext.now`). There is no age gate in `__init__` (SAF-011 is not met): an empty age selects every VM that passes the tag and status checks (SAF-006).
- A VM is acted on only when an allowed state equals its power state: the code after `PowerState/` of the first instance view status with such a code, lower-cased (BUG-010, BUG-198: no substring match, an empty state matches nothing, the allowed state is compared as given, so `Running` never matches). A VM without a `PowerState/` status is kept and counted as skip `no_power_state`; a failing status read keeps the VM (fail closed) and is logged.
- Per-VM errors (status, delete, deallocate) are logged and swallowed; list errors and age errors propagate.
- Every call, the listing included, uses the configured resource group (`virtual_machines.list(resource_group)`, BUG-002 fixed): nothing outside it is listed or acted on, and an empty group raises `ValueError` before any SDK call. Stop is `begin_deallocate` (BUG-003 fixed); delete is unchanged. Other quirks in `docs/refactor/BUGS.md` (`BUG-199` to `BUG-212`).
- Do not call real cloud APIs in tests; mock every SDK client.

## Extension Rules

1. Keep the call order (list, age, status, delete, wait, NIC) unless a `BUG-NNN` row says otherwise.
2. Reach the SDK only through the Protocols in `client.py`; new SDK calls need a Protocol entry and a fake in `tests/helpers_azure_vm.py`.
3. Tag and age decisions go through `crc.core.policy` and `crc.core.age`, never inline.
4. Update this README and `tests/test_azure_vm_parity.py` (the oracle is `tests/legacy/azu_vm.txt`, never edited) with any change.
