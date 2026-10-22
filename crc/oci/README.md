# crc.oci

## Purpose

OCI cleaners (oci SDK). They delete or stop leftover instances and volumes across regions.

**Refactored.** Every module is strict-checked and none is in the legacy ratchet: `disk/` (rung OCI-1, a subpackage since rung ST-OCI-DISK) is a `Cleaner` registered as `oci` / `disk` (no behaviour change; see its README); `vm/` (rungs OCI-2 and ST-OCI-VM) is a subpackage with a `Cleaner` registered as `oci` / `vm` (no behaviour change; see its README); `spot_vm/` (rung OCI-3) is a subpackage with a `Cleaner` registered as `oci` / `spot_instances` (no behaviour change; see its README); the shared helpers `connectivity.py` and `__init__.py` (rung PL-OCI) are strict-clean (no behaviour change); `_base.py` (rung PL-OCI) is strict-clean too; its `os.environ` read of the private key is an owner-approved ARCH-003 allowlist entry (`tests/archtest/bans.py`), so the key is still read when an OCI cleaner is built, as on `main`. Any change must follow `ARCHITECTURE.md`; a behaviour change needs a behaviour-fix rung in `docs/refactor/PLAN.md` (`.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`), with `make check` green.

Modules (operations and filters as catalogued in `docs/refactor/FILTER_VARIANTS.md`):

- `_base.py` (rung PL-OCI): `Base`: API signing key config from `OCI_PRIVATE_KEY_CONTENT` / `OCI_PRIVATE_KEY_FILE`, identity, compute and block storage clients cached per region, region and availability domain lookup
- `_logger.py` (rung LOG-OCI): private glue from the OCI cleaners to `crc.core.logger` (REGION, ACTION, skip and failure reports; log text only, no public name)
- `connectivity.py` (refactored, rung PL-OCI): `CONNECTIVITY_ERRORS` and `log_skipped_region`
- `disk/`: the disk cleaner subpackage (`DiskCleaner` registered as `oci` / `disk`, the `Disk` adapter and the SDK Protocols); deletes available block and boot volumes not attached; freeform tag any-key filter
- `spot_vm/`: the Spot VM cleaner subpackage (`SpotVmCleaner`, the `SpotVM` adapter, the SDK Protocols and the leftover volume handling); terminates preemptible instances and deletes leftover volumes; skips OKE and instance pool members
- `vm/`: the VM cleaner subpackage (`VmCleaner` registered as `oci` / `vm`, the `VM` adapter, the SDK Protocols); terminates or stops `RUNNING` instances; freeform tag all-keys filter

## Public API

The shared helpers connectivity.py, _base.py and __init__.py (rung PL-OCI) are refactored, so the names below are synced with this README by
ARCH-002; the _base.py private key environment read is an owner-approved ARCH-003 allowlist entry. The disk, vm and spot_vm subpackages have their own README and symbol list. The package __init__.py holds only a copyright comment and declares
no __all__.

- `Base`: OCI signing key config from the OCI_PRIVATE_KEY_CONTENT or OCI_PRIVATE_KEY_FILE environment variable (KeyError when neither is set), identity, compute and block storage clients cached per region, region subscription and availability domain lookup with the connectivity fallback.
- `log_skipped_region()`: logs the warning that a region (or one call in it) was skipped for a connectivity error; used with CONNECTIVITY_ERRORS, the tuple of the three SDK errors (ConnectTimeout, RequestException, ServiceError) that skip a region.
- Rung LOG-OCI glue to the core run log (module _logger, log text only): `region_started()` and `region_done()` (the REGION line), `region_skipped()` (counts a region skipped for a connectivity error), `record_action()` (one ACTION line per resource acted on), `record_skip()` (counts a skipped resource by reason) and `record_failure()` (adds to the end-of-run failure list).

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md`. Applicable IDs: SAF-006 to SAF-013 (age gates, shared by all clouds), SAF-037, SAF-040, SAF-041, SAF-043, SAF-044, SAF-045, SAF-046.
- Filtering per module: `docs/refactor/FILTER_VARIANTS.md` sections 5 (OCI), 6 and 7.
- Known bugs and quirks (do not fix outside a behaviour-fix rung): `docs/refactor/BUGS.md` rows `BUG-004`, `BUG-096` to `BUG-110`, `BUG-158` to `BUG-171`, `BUG-255` to `BUG-271`, `BUG-308` to `BUG-317`, `BUG-352` to `BUG-354`.
- OCI disk (SAF-006 to SAF-013): see `crc/oci/disk/README.md`.
- OCI VM (SAF-006 to SAF-013): see the Safety Invariants of `crc/oci/vm/README.md`. `plan()` only lists and decides (ARCH-005); `apply()` is the only caller of `terminate_instance` / `stop_instance`. VM quirks are in `docs/refactor/BUGS.md` (BUG-158 to BUG-171, BUG-352).
- OCI Spot VMs skip OKE and instance pool members (SAF-045); `--resource spot_instances` needs an explicit `--cloud oci` (SAF-037).
- OCI shared helpers (`Base`, `connectivity.py`; no SAF ID names them, the AWS analogues are SAF-001 and SAF-004; parity-tested against `tests/legacy/oci_base.txt` and `tests/legacy/oci_connectivity.txt`): `Base()` fails closed before any SDK call when neither `OCI_PRIVATE_KEY_CONTENT` nor `OCI_PRIVATE_KEY_FILE` is set (KeyError; the content wins when both are set); the compartment, tenancy, user and fingerprint are constants, so only that compartment is ever listed; clients are built once per region and cached; `get_all_regions` falls back to the home region `us-sanjose-1` only when `list_region_subscriptions` raises a connectivity error (`ConnectTimeout`, `RequestException` or any `ServiceError`, so an auth error also takes that path, `BUG-101`, `BUG-107`) and propagates every other error; `get_availability_domains` does not catch anything and caches only on success. Quirks of these modules are in `docs/refactor/BUGS.md` (`BUG-308` to `BUG-317`).
- Logging (rung LOG-OCI, `docs/refactor/LOGGING.md`): the per-resource tag dict and the leftover-volume skip line are `DEBUG`; each cleaner reports REGION, ACTION, skip reasons and failures to `crc.core.logger.current()`. Log text and levels only: selection, call order and results are unchanged.
- Do not call real cloud APIs in tests; mock every SDK client.

## Extension Rules

1. Do not edit the modules to add features or fix bugs in a refactor rung; behaviour fixes are
   separate rungs (`docs/refactor/PLAN.md`).
2. New resources go in a new module with the `Cleaner` contract (`ARCHITECTURE.md`, "Add a resource
   cleaner"): a `Cleaner` registered with `@register("oci", ...)`. Follow the `disk/` or `vm/` subpackage: pure policy from `crc.core.policy`,
   age from `crc.core.age`, SDK calls behind a Protocol in a `api.py` or `client.py` module, injected through the
   constructor.
3. `plan()` of `DiskCleaner` (`disk/`) and `VmCleaner` (`vm/`) stays read-only: every mutating call belongs in `apply()` / `remove()`.
4. No module of this package is in the legacy ratchet (`pyproject.toml` lists only five hash-locked safety test files); never add one. Update this README and its symbol list with any public change.
5. Record every new bug or quirk in `docs/refactor/BUGS.md` with its `main` file:line.
