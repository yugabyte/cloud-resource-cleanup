# crc.azu.nic

## Purpose

Azure NIC cleaner. `AzureNicCleaner` is a registered `Cleaner` (`@register("azure", "nic")`) that deletes NICs not in use whose name matches `name_regex`. The CLI runs it through `crc.core.engine` (`crc/cli/routes/azu.py`).

Modules:

- `client.py`: SDK Protocols
- `cleaner.py`: `AzureNicCleaner`, pattern compilation and the registry factory

## Public API

- `NicOperations`: Protocol of the two network_interfaces calls used, list and begin_delete (positional arguments); tests pass fakes.
- `NicNetworkClient`: Protocol of the network client part used (the network_interfaces operations).
- `AzureNicCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus prepare (compiles name_regex / exception_regex, ValueError before any SDK call), list_nics, decide and attempt_delete); the client factory is injected through the constructor.
- `build_nic_cleaner()`: registry factory behind @register("azure", "nic"); reads resource_group, name_regex and exception_regex from RunContext.options and builds the client from the Azure base.
- `DeletePoller`: Protocol of the handle begin_delete returns (result()).
- `log_none_to_delete()`: logs the "No Azure NICs to delete." warning (called by the cleaner).
- `DELETE_ATTEMPTS`: the three delete attempts per NIC.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`; the per-module invariants are in `crc/azu/README.md` (Azure NICs). `plan()` makes no mutating call and never in a dry run (ARCH-005); the delete calls run from `apply()` only.
- Patterns are compiled before any SDK call (`ValueError` when invalid); a failed delete is retried three times, logged and swallowed; no tag filter; a real run needs a positive `--age` (BUG-112; creation time read from `azure-mgmt-resource`, unknown or failed lookup keeps the NIC; `ValueError` naming `--age` otherwise; a dry run needs none).
- Do not call real cloud APIs in tests; mock every SDK client.

## Extension Rules

1. Keep call order, log text and swallowed errors unless a `BUG-NNN` row says otherwise.
2. Reach the SDK only through the Protocols in `client.py`; shared log and skip helpers live in `crc/azu/_common.py`.
3. Update this README with any public change.
