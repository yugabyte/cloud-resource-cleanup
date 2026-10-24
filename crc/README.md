# crc

## Purpose

Top-level package of the cleanup tool. The CLI entry point is `crc.py` in the repository root
(same name as this package; `main()`, argument parsing, validators and the `CRC` run context live in `cli/`; `crc.py` is a thin entry point that re-exports `main`, `get_argparser` and the validators and ends with `if __name__ == "__main__": main()`); the cloud code lives in the sub-packages. Direct modules:

- `service.py`: `Service`, the age gate, custom age tag and `is_old` helpers over `crc/core/age.py`; constructing it initialises logging (`crc/cli/bridge.py` and `crc/cli/scan.py` do that).
  Age handling delegates to `crc/core/age.py`. Parity: `tests/test_crc_service_parity.py`, `tests/test_crc_service_is_old_parity.py` against `tests/legacy/crc_service.txt`.
- `notifications.py`: compatibility shim re-exporting the Slack helpers that moved to `notify/slack.py`.
- `utils.py`: `init_logging`. Parity: `tests/test_crc_utils_parity.py` against `tests/legacy/crc_utils.txt`.
- `cli/` (see `crc/cli/README.md`): `main()` (inputs, clients, scan and dispatch), the argparse definition (`get_argparser`), the input validators, the `CRC` run context and the bridge that runs each (cloud, kind) pair through `crc.core.engine`.
- `core/` (see `crc/core/README.md`), `notify/` (see `crc/notify/README.md`), `aws/`, `azu/`, `gcp/`, `oci/` (cleaners registered with `crc.core.registry`, one README each). The run path is CLI -> bridge -> routes -> engine -> registered cleaners -> notify.

`crc/__init__.py` holds only a copyright comment. Every module under `crc/` is strict-checked; the legacy ratchet in `pyproject.toml` is empty. Any change must follow `ARCHITECTURE.md`.

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
  `docs/refactor/BUGS.md`; their behaviour lives in `crc/notify`, see its README.
- Custom age tag (BUG-004, BUG-016): `Service.get_retention_age` returns the
  keep marker for a present but bad tag, so `is_old` keeps the resource (see `crc/core/README.md`).
- Known bugs and quirks of these modules: `docs/refactor/BUGS.md` rows `BUG-024` to
  `BUG-027`, `BUG-327` to `BUG-332` (`crc/service.py` and `crc/utils.py`).
- Log messages are part of the behaviour; keep them identical.

## Extension Rules

1. New code for cloud resources goes in `crc/<cloud>/` following `ARCHITECTURE.md`; shared pure
   logic goes in `crc/core/`.
2. Never add a module to the legacy ratchet in `pyproject.toml`; it is empty.
3. A new sub-directory needs a `README.md` with the four sections (ARCH-002).
4. Record every new bug or quirk in `docs/refactor/BUGS.md` (a new row cites the current `file:line` in `crc/`).
