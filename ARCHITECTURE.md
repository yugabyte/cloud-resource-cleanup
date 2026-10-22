# ARCHITECTURE.md

Target architecture of the CRC Python refactor. `docs/refactor/PLAN.md` owns the
rung schedule; this file owns the rules. Modules that do not exist yet are
targets, built rung by rung.

## Layout

```
crc.py                    thin CLI: parse flags, build config, call engine
crc/core/age.py           age parsing, positive-age checks, is-old
crc/core/policy.py        tag, notags, exception tags, regex, age, custom age tag
crc/core/registry.py      provider and cleaner registration decorators
crc/core/cleaner.py       Cleaner protocol: plan() is read-only, apply() mutates
crc/core/engine.py        selects cleaners, runs plan/apply, builds the report
crc/core/report.py        report model used by notifiers and stdout
crc/notify/slack.py       Slack sink
crc/notify/influx.py      InfluxDB sink
crc/aws|azu|gcp|oci/      one module per resource, registered by decorator
```

## Dependency rules

- `crc.py` -> `crc.core.engine` -> `crc.core.*`.
- Cloud packages (`crc.aws`, `crc.azu`, `crc.gcp`, `crc.oci`) depend on
  `crc.core` only and never import each other.
- `crc.core` never imports a cloud package or a cloud SDK (`boto3`, `azure`,
  `google`, `oci`). Policy and age logic are pure functions on plain data.
- Notifiers (`crc.notify`) consume the report model only.
- Enforced by import-linter (`lint-imports`, config in `pyproject.toml`): the four
  cloud packages are independent, `crc.core` imports no cloud package, service,
  notify module or cloud/telemetry SDK, and `crc.notify` imports no cloud package
  or service.

## Cleaner contract

```python
class Cleaner(Protocol):
    cloud: str
    kind: str
    def supports(self, operation: Operation) -> bool: ...
    def plan(self, context: RunContext) -> Plan: ...   # discovery and policy, read-only
    def apply(self, plan: Plan, context: RunContext) -> Result: ...  # all mutating cloud calls
```

Defined in `crc/core/cleaner.py` with `Operation` (delete, stop), `Candidate`, `Skipped`,
`Plan`, `Failure`, `Result` and `RunContext`.

- `plan()` makes no mutating cloud call (no delete, stop, cancel, tag write).
  It backs `--dry_run`: a dry run is `plan()` without `apply()`.
- `apply()` performs the destructive calls for items already in the plan, in
  the order required by `CLEANUP_SAFETY.md` (for example AWS spot: cancel the
  request before terminating the instance).
- Plans, results and the run context are frozen dataclasses; their mappings are
  read-only copies. SDK objects do not cross the cleaner boundary; cleaners
  normalise to `Candidate`.
- `RunContext.now` is supplied by the caller. Cleaners never read the clock for age
  decisions; they compare against `context.now`.
- SDK clients, policy and logger are injected through the constructor so tests
  pass mocks.
- `crc/core/report.py` turns `Result` or `Plan` into a `Report` that yields the Slack
  texts, the Influx points and payload `crc.py` sends today. Notifiers only send it.

## Decorator registration

Cleaners self-register with a typed decorator, for example
`@register(cloud="aws", kind="disk")`, defined in `crc/core/registry.py`. The
registry maps `(cloud, kind)` to a cleaner factory: a callable that takes the
`RunContext` and returns a `Cleaner` (normally the class itself). `get(cloud, kind)`,
`kinds_for(cloud)` and `clouds()` read it back. Registering the same pair twice
raises `DuplicateCleanerError`; unknown lookups raise `UnknownCleanerError`. Rules:
no `@singleton`, no hidden mutable globals beyond the default `Registry` instance owned
by the registry module, no import-time cloud calls. Tests build their own `Registry()`
so registrations never leak. The engine selects cleaners from the registry using the
parsed `--cloud` / `--resource` values.

## Legacy ratchet

Modules that existed before the refactor (`crc.py`, each pre-existing
`crc/**/*.py` module and `tests/*.py` file) are exempt from ruff and mypy
through one explicit per-file list: ruff `extend-exclude` and the
`[[tool.mypy.overrides]]` `ignore_errors` block in `pyproject.toml`. No
directory or glob entries, so every new file (for example under `crc/core`,
`crc/notify` or new tests) is checked by default. mypy checks `crc/` and
`tests/` through `files`; `crc.py` is left out because its module name clashes
with the `crc` package, so `crc.py` is a thin entry point and its code lives in the
mypy-checked `crc/cli/` package. The list may only shrink. After the refactor rungs it
holds only the five hash-locked safety test files (kept as on `main` so their hashes
stay valid). A rung removes the modules it refactors from both places in
the same change, and the refactored code must then pass ruff (complexity 10,
statements 40, args 6) and mypy strict. Narrow the lists as modules move;
never widen them.

Enforced by `tests/archtest` (rule IDs in `tests/archtest/README.md`): ARCH-001 ratchet
only shrinks (baseline in `legacy_baseline.txt`), ARCH-002 package README sections and
symbol sync, ARCH-003 no `re`, wall clock, env reads, `print` or `sys.exit` in cloud
packages, ARCH-004 files of at most 400 lines, ARCH-005 read-only `plan()`, and SAF-LOCK
(`SAF-NNN` tags in `CLEANUP_SAFETY.md` tied to hashed tests in `safety_lock.json`).

## Add a resource cleaner

1. Read `CLEANUP_SAFETY.md` and the nearest existing cleaner in the same cloud.
2. Create `crc/<cloud>/<resource>.py`. Do not import another cloud package.
3. Normalise cloud data to `Candidate` (`crc.core.cleaner`); build `Plan` and `Result`
   from the frozen dataclasses there instead of defining new ones.
4. Implement the class with an injected SDK client and policy. Require a real
   age gate in `__init__` for destructive resources. Fail closed on missing or
   invalid age, tags or telemetry.
5. `plan(context)`: list, normalise, apply policy from `crc.core.policy`, compare ages
   with `context.now`. No mutation.
6. `apply(plan, context)`: perform the deletes or stops in the documented order, isolate
   per-resource errors only where `CLEANUP_SAFETY.md` says so, chain errors with
   `raise ... from e`.
7. Register it with `@register(cloud=..., kind=...)` from `crc.core.registry`; the
   factory takes the `RunContext` and returns the cleaner. Also implement `supports()`.
8. Add offline tests under `tests/` using mocks: happy path, fail-closed path,
   dry run makes no mutating call, call order for destructive sequences.
9. Update the package README and, for destructive behaviour, `CLEANUP_SAFETY.md`.
10. Remove the module from the legacy lists if it was listed, then run
    `make check`.
