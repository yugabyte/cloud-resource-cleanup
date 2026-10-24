# scripts

## Purpose

Developer and CI tooling that is not part of the CLI. `smoke_offline.py` runs `python crc.py
--dry_run` once per (cloud, kind) of the production Jenkins commands with every SDK faked and
sockets denied (`make smoke-offline`; `tests/test_smoke_offline.py` runs the same pairs).
`doctor.py` checks the Python version and the pinned ruff, mypy and pytest (`make doctor`).

## Public API

- `Command`: one smoke command (cloud, kind, argv).
- `load_commands`: the first dry-run command per (cloud, kind) of `tests/fixtures/jenkins_commands.txt`.
- `offline_env`: the subprocess environment (dummy credentials, the hook path, the switch).
- `run_command`: run one command offline.
- `problems_of`: what is wrong with one run (exit code, count of `RUN START` and `RESULT` lines).
- `main`: run every pair and print a summary (`smoke_offline.py`; `doctor.py` has its own `main`).
- `pinned_versions`, `minimum_python`, `installed_version`, `problems_of`, `install_command`: the checks of `doctor.py`.

## Safety Invariants

- Nothing here calls a cloud: the hook replaces every SDK client and denies sockets.
- Nothing under `crc/` imports or mentions this directory; Jenkins flags cannot reach the hook.

## Extension Rules

1. A new SDK client must be faked in `offline_hook/sitecustomize.py` in the same change.
2. Never weaken the assertions of a pair (exit 0, one `RUN START`, one `RESULT`).
