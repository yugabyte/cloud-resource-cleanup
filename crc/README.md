# crc

## Purpose

Top-level package of the cleanup tool. The CLI entry point is `crc.py` in the repository root
(same name as this package; `main()`, argument parsing, validators and the `CRC` class live in `cli/`, `crc.py` is a thin entry point that re-exports `main`, the adapter names, the constants and `CRC` and ends with `if __name__ == "__main__": main()`); the cloud code lives in the sub-packages. Direct modules:

- `service.py`: `Service` (refactored, strict-checked), the base class the adapters inherit (age gate, custom age tag, `is_old`; it initialises logging).
  Age handling delegates to `crc/core/age.py`. Parity: `tests/test_crc_service_parity.py`, `tests/test_crc_service_is_old_parity.py` against `tests/legacy/crc_service.txt`.
- `notifications.py`: compatibility shim re-exporting the Slack helpers that moved to `notify/slack.py`.
- `utils.py`: `init_logging` (refactored, strict-checked). Parity: `tests/test_crc_utils_parity.py` against `tests/legacy/crc_utils.txt`.
- `cli/` (refactored, see `crc/cli/README.md`): `main()` (split into inputs, clients, scan and dispatch), the argparse definition (`get_argparser`), the input validators and the `CRC` class (split into notify, VM and resource mixins) that `crc.py` imports back under the same names.
- `core/` (refactored, see `crc/core/README.md`), `notify/` (refactored, see `crc/notify/README.md`), `aws/`, `azu/`, `gcp/`, `oci/` (refactored cleaners registered with `crc.core.registry`, one README each; the adapter classes remain the entry point `crc/cli` uses).

`crc/__init__.py` holds only a copyright comment. Every module under `crc/` is refactored and strict-checked; none is in the legacy ratchet (`pyproject.toml` lists only five hash-locked safety test files). Any change must follow `ARCHITECTURE.md`.

## Public API

Not synced with ARCH-002: the check skips the modules directly in `crc/` (`package_dirs` in `tests/archtest/readmes.py`) and `crc/__init__.py` has no `__all__`, so this list is kept by hand. The sub-packages are synced in their own READMEs.

- `Service`: age gate and logging base class (`crc/service.py`).
- `init_logging()`: log file setup (`crc/utils.py`).
- `human_readable_timedelta()`, `send_slack_message()`, `notify_cleanup()`: Slack helpers, now defined in `crc/notify/slack.py` and re-exported by `crc/notifications.py`.

## Safety Invariants

- Authoritative text: `CLEANUP_SAFETY.md` (age gates SAF-006 to SAF-013).
- Dependency rules (import-linter): cloud packages never import each other; `crc.core` never
  imports a cloud package, `crc.notifications`, `crc.service`, `crc.notify` or an SDK; `crc.notify`
  imports `crc.core` only (never a cloud package or `crc.service`).
- Notification bugs and quirks (`BUG-028` to `BUG-034`, `BUG-041` to `BUG-044`) are listed in
  `docs/refactor/BUGS.md`; their behaviour now lives in `crc/notify`, see its README.
- Known bugs and quirks of these modules: `docs/refactor/BUGS.md` rows `BUG-004`, `BUG-016`, `BUG-024` to
  `BUG-027`, `BUG-327` to `BUG-332` (`crc/service.py` and `crc/utils.py` on `main`).
- Log messages are part of the behaviour; keep them identical.

## Extension Rules

1. New code for cloud resources goes in `crc/<cloud>/` following `ARCHITECTURE.md`; shared pure
   logic goes in `crc/core/`.
2. Do not add modules to the legacy lists in `pyproject.toml`; they may only shrink.
3. A new sub-directory needs a `README.md` with the four sections (ARCH-002).
4. Record every new bug or quirk in `docs/refactor/BUGS.md` with its `main` file:line.
