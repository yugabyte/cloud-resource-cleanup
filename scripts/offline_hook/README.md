# offline_hook

## Purpose

`sitecustomize.py` makes the offline smoke possible: when `scripts/smoke_offline.py` puts this
directory on `PYTHONPATH` and sets `CRC_OFFLINE_SMOKE=1`, Python imports it at startup, replaces
every SDK client with an in-memory fake and denies socket connections.

## Public API

- `Fake`: accepts any call or attribute and returns an empty, falsy, iterable result.
- `ENABLED`: True only when `CRC_OFFLINE_SMOKE` is `1`.

## Safety Invariants

- Without the switch the module changes nothing (`tests/test_smoke_offline.py` pins it).
- Production code never references it; no CLI flag or Jenkins variable reaches it.

## Extension Rules

1. Fake a new SDK client here before the smoke runs a command that builds it.
2. Keep sockets denied; never allow a connection to make a command pass.
