---
name: crc-maintain
description: Implement a change in cloud-resource-cleanup (CRC): add or change a cleaner, fix behaviour, change a CLI flag, write offline tests. Use for any edit under crc/ or tests/. Reviews and bug fixes have their own playbooks, linked below.
---

# crc-maintain (implement)

Read `AGENTS.md` first (commands, rules no check enforces). Then `ARCHITECTURE.md` for the
engine path and `CLEANUP_SAFETY.md` for the destructive invariants of the code you touch. Fixing
a registered bug: [fix-bug.md](fix-bug.md). Reviewing a PR: [review.md](review.md).

## Steps

1. Scope: write one sentence naming the behaviour you change. Anything else you notice goes in
   `docs/refactor/BUGS.md` as a new row, not in this change.
2. Read the package `README.md` of every package you edit and the nearest similar cleaner.
3. Write or extend the offline test first; run it with `make test-file F=tests/test_x.py` and
   see it fail for the right reason.
4. Implement. Loop with `make test-file`, then `make check-fast`.
5. Update the package README (Public API symbols, Safety Invariants), and `CLEANUP_SAFETY.md`
   when destructive behaviour changed.
6. `make check`, then `make raise-test-floor` if you added tests, then commit.

## Conventions the checks cannot see

- Cleaners fetch and normalise to `Candidate`; `crc/core/policy*.py` and `crc/core/age*.py`
  decide. Do not write new regex, tag or age logic in a cloud package.
- Inject SDK clients and policy through constructors or the registered factory; never build a
  client at import time. Tests pass fakes without patching module globals.
- Frozen `@dataclass(frozen=True, slots=True)` for values; `typing.Protocol` for SDK ports;
  full annotations; `Any` only at an untyped SDK edge, narrowed at once.
- Parse and validate flag values at the CLI boundary, before any cloud call. Keep JSON-style
  values such as `'{"days": 1}'` accepted.
- Raise specific exceptions with context (what, resource id, region, why) and chain with
  `raise ... from e`. List and auth failures, and the AWS disk opt-in breaker, raise; they are
  never swallowed in a batch loop.
- Isolate per-resource errors only where `CLEANUP_SAFETY.md` says so: catch the specific SDK
  error, log the id, record the failure in the result, continue. Never `except Exception: pass`.
- Keep destructive order, for example AWS spot: tag volumes, cancel the request, terminate,
  wait for `available`, delete volumes; a failed cancel aborts termination.
- `--max_age` / `MAX_AGE` must never become a new place that shortens a floor set by another flag.

## Tests

- Offline pytest with `unittest.mock`; fake the SDK client, paginators, pollers and
  `ClientError`. No credentials, no network.
- `parametrize` for tag, age and regex matrices; `pytest.raises(Exc, match=...)` for breakers and
  invalid input; `monkeypatch` for env; `tmp_path` for files; function-scoped fixtures only.
- Cover the fail-closed branch and the call order of destructive steps, not just the happy path.
- A `plan()` test asserts that no mutating client method was called.
- A test that needs a locked file changed (`tests/archtest/safety_lock.json` lists them) is a
  stop: describe the change in the PR for the owner.
