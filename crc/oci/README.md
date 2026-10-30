# crc.oci

## Purpose

OCI cleaners (oci SDK). They delete or stop leftover instances and volumes across regions.

Every module is strict-checked. `_base.py` reads the private key from `os.environ`, an owner-approved ARCH-003 allowlist entry (`tests/archtest/bans.py`), so the key is read when an OCI cleaner is built. Any change must follow `ARCHITECTURE.md` and keep `make check` green.

Modules:

- `_base.py`: `Base`: API signing key config from `OCI_PRIVATE_KEY_CONTENT` / `OCI_PRIVATE_KEY_FILE`, identity, compute and block storage clients cached per region, region and availability domain lookup
- `_logger.py`: private glue from the OCI cleaners to `crc.core.logger` (REGION, ACTION, skip and failure reports; log text only, no public name)
- `_common.py`: helpers shared by the OCI cleaners (`group_by_region`)
- `connectivity.py`: `CONNECTIVITY_ERRORS` and `log_skipped_region`
- `disk/`: the disk cleaner subpackage (`DiskCleaner` registered as `oci` / `disk` and the SDK Protocols); deletes available block and boot volumes not attached; freeform tag any-key filter
- `spot_vm/`: the Spot VM cleaner subpackage (`SpotVmCleaner` registered as `oci` / `spot_instances`, the SDK Protocols and the leftover volume handling); terminates preemptible instances and deletes leftover volumes; skips OKE and instance pool members
- `vm/`: the VM cleaner subpackage (`VmCleaner` registered as `oci` / `vm`, the SDK Protocols); terminates or stops `RUNNING` instances; freeform tag all-keys filter

`crc/cli/routes/oci.py` routes `disk`, `vm` (delete and stop) and `spot_instances` through `crc.core.engine`; `spot_instances` is skipped under `--cloud all` (SAF-037).

## Public API

The names below are synced with this README by ARCH-002. The disk, vm and spot_vm subpackages have their own README and symbol list. The package init holds only a copyright comment and declares no export list.

- `Base`: OCI signing key config from the OCI_PRIVATE_KEY_CONTENT or OCI_PRIVATE_KEY_FILE environment variable (KeyError when neither is set), identity, compute and block storage clients cached per region, region subscription and availability domain lookup with the connectivity fallback.
- `log_skipped_region()`: logs the warning that a region (or one call in it) was skipped for a connectivity error; used with CONNECTIVITY_ERRORS, the tuple of the three SDK errors (ConnectTimeout, RequestException, ServiceError) that skip a region.
- Glue to the core run log (module _logger, log text only): `region_started()` and `region_done()` (the REGION line), `region_skipped()` (counts a region skipped for a connectivity error), `record_action()` (one ACTION line per resource acted on), `record_skip()` (counts a skipped resource by reason) and `record_failure()` (adds to the end-of-run failure list).
- `group_by_region()`: groups planned candidates by region in first-seen order (module _common, used by the disk and vm cleaners' apply()).

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`. Applicable IDs: SAF-006 to SAF-013 (age gates, shared by all clouds), SAF-037, SAF-040, SAF-041, SAF-043, SAF-044, SAF-045, SAF-046.
- Known bugs and quirks (do not fix outside their own change): `docs/refactor/BUGS.md` rows `BUG-004`, `BUG-096` to `BUG-110`, `BUG-158` to `BUG-171`, `BUG-255` to `BUG-271`, `BUG-308` to `BUG-317`, `BUG-352` to `BUG-354`.
- OCI disk (SAF-006 to SAF-013): see `crc/oci/disk/README.md`.
- OCI VM (SAF-006 to SAF-013): see the Safety Invariants of `crc/oci/vm/README.md`. `plan()` only lists and decides (ARCH-005); `apply()` is the only caller of `terminate_instance` / `stop_instance`. VM quirks are in `docs/refactor/BUGS.md` (BUG-158 to BUG-171, BUG-352).
- OCI Spot VMs skip OKE and instance pool members (SAF-045); `--resource spot_instances` needs an explicit `--cloud oci` (SAF-037).
- OCI shared helpers (`Base`, `connectivity.py`; no SAF ID names them, the AWS analogues are SAF-001 and SAF-004; parity-tested against `tests/legacy/oci_base.txt` and `tests/legacy/oci_connectivity.txt`): `Base()` fails closed before any SDK call when neither `OCI_PRIVATE_KEY_CONTENT` nor `OCI_PRIVATE_KEY_FILE` is set (KeyError; the content wins when both are set); the compartment, tenancy, user and fingerprint are constants, so only that compartment is ever listed; clients are built once per region and cached; `get_all_regions` falls back to the home region `us-sanjose-1` only when `list_region_subscriptions` raises a connectivity error (`ConnectTimeout`, `RequestException` or any `ServiceError`, so an auth error also takes that path, `BUG-101`, `BUG-107`) and propagates every other error; `get_availability_domains` does not catch anything and caches only on success. Quirks of these modules are in `docs/refactor/BUGS.md` (`BUG-308` to `BUG-317`).
- Logging (`docs/logging.md`): the per-resource tag dict and the leftover-volume skip line are `DEBUG`; each cleaner reports REGION, ACTION, skip reasons and failures to `crc.core.logger.current()`.
- Do not call real cloud APIs in tests; mock every SDK client.

## Extension Rules

1. A behaviour fix names its `BUG-NNN` (`docs/refactor/BUGS.md`); do not fix a quirk inside an unrelated change.
2. New resources go in a new module with the `Cleaner` contract (`ARCHITECTURE.md`, "Add a
   cleaner"): a `Cleaner` registered with `@register("oci", ...)`. Follow the `disk/` or `vm/` subpackage: pure policy from `crc.core.policy`,
   age from `crc.core.age`, SDK calls behind a Protocol in a client module (`crc/oci/disk/api.py`, `crc/aws/keypairs/client.py`), injected through the
   constructor.
3. `plan()` of `DiskCleaner` (`disk/`) and `VmCleaner` (`vm/`) stays read-only: every mutating call belongs in `apply()` / `remove()`.
4. Update this README and its symbol list with any public change.
5. Record every new bug or quirk in `docs/refactor/BUGS.md` (a new row cites the current `file:line` in `crc/`).
