# crc.azu.ip

## Purpose

Azure public IP cleaner. `AzureIpCleaner` is a registered `Cleaner` (`@register("azure", "ip")`) that deletes unused IPs of the configured resource group. The CLI runs it through `crc.core.engine` (`crc/cli/routes/azu.py`).

Modules:

- `client.py`: SDK Protocols
- `cleaner.py`: `AzureIpCleaner` and the registry factory

## Public API

- `PublicIpOperations`: Protocol of the two public IP calls used, list and begin_delete; tests pass fakes.
- `IpNetworkClient`: Protocol of the network client part used (the public_ip_addresses operations).
- `AzureIpCleaner`: the Cleaner (supports, read-only plan, mutating apply, plus list_ips, decide and delete_candidate); the client factory is injected through the constructor.
- `build_ip_cleaner()`: registry factory behind @register("azure", "ip"); reads resource_group, filter_tags, exception_tags and notags from RunContext.options and builds the client from the Azure base.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`; the per-module invariants are in `crc/azu/README.md` (Azure IPs). `plan()` makes no mutating call and never in a dry run (ARCH-005); the delete calls run from `apply()` only. A real run logs its `REGION` line at the end of `apply()` (a dry run in `plan()`), so a delete error leaves it out.
- Selection: any-key `filter_tags`, `exception_tags` any key, `notags` all keys, kept when in use; a real run needs a positive `--age` (BUG-075; creation time read from `azure-mgmt-resource`, unknown or failed lookup keeps the IP; `ValueError` naming `--age` otherwise; a dry run needs none).
- Do not call real cloud APIs in tests; mock every SDK client.

## Extension Rules

1. Keep call order, log text and error behaviour unless a `BUG-NNN` row says otherwise.
2. Reach the SDK only through the Protocols in `client.py`; shared log and skip helpers live in `crc/azu/_common.py`.
3. Update this README with any public change.
