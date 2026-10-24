# ARCHITECTURE.md

How CRC is built today. Safety rules for destructive behaviour are in
[CLEANUP_SAFETY.md](CLEANUP_SAFETY.md); mechanical checks are in
[tests/archtest/README.md](tests/archtest/README.md) and are not repeated here.

## Run flow

```
python crc.py <flags>
  crc/cli/args*.py        parse flags (get_argparser), JSON-style values via ast.literal_eval
  crc/cli/main.py         read tokens, validate flags and targets, set MAX_AGE, build clients
  crc/cli/dispatch.py     validate every pair, then loop over (cloud, resource); begin/finish
                          the run log per pair
    crc/cli/bridge.py     route_for(cloud, kind) -> Route; run_routed runs the pair
  crc/core/engine.py      registry.get(cloud, kind)(context) -> plan() -> apply() -> Report
  crc/notify/*            Slack then Influx send the Report
```

`crc.py` only re-exports `crc/cli`. `--scan_tag` is a separate read-only path
(`crc/cli/scan.py`) that never deletes.

## Layers

| Package | Role |
|---------|------|
| `crc/cli` | Flags, validation, dispatch, the bridge to the engine, route tables. The layer that reads env, prints and exits (what other layers may not do: ARCH-003) |
| `crc/core` | Engine, registry, `Cleaner` protocol, report model, run log, pure policy and age code. Imports no cloud package or SDK |
| `crc/<cloud>` | `aws`, `azu`, `gcp`, `oci`: one package per resource kind, built on `crc.core`; clouds never import each other |
| `crc/notify` | Slack and InfluxDB sinks that consume only the `Report` |

Import direction is enforced by import-linter (`make lint`); read the contracts in `pyproject.toml`
before moving code between layers.

## Bridge and routes

`crc/cli/bridge.py` runs one routed pair: optional `Route.precheck`, `init_run_logging()`, a
`RunContext(dry_run, operation, options, now)`, a one-pair `Selection`, then `engine.run` with the
notifiers from `build_notifiers` (Slack when a client is set, then Influx when one is set; none for
`Route.notify=False`). A route table per cloud (`crc/cli/routes/<cloud>.py`) maps a kind to a
`Route(options, notify, precheck)`. The `options` builder turns parsed flags into the
`context.options` keys that the cleaner factory reads; values stay raw (`None` stays `None`).
`bridge.build_registry` and `build_notifiers` are looked up at call time so tests can patch them
(`tests/helpers_cli_routes.py`). Every supported pair has a route, so every pair runs through the
engine; the two warn-only pairs (`spot_instances` on AWS, `spot_instance_requests` off AWS) have
skip-only routes (`crc/cli/routes/skips.py`) and register no cleaner.

## Engine and registry

`crc/core/engine.py` `run(selection, context, registry, notifiers)` handles each `(cloud, kind)` in
order: build the cleaner with `registry.get(cloud, kind)(context)`, require
`supports(operation)`, call `plan()`, call `apply()` only when `context.dry_run` is false, build a
`Report` (`Report.from_plan` or `from_result`) and send it to the notifiers. A cleaner exception
propagates and ends the run. A notifier with `fail_open` has its error logged and swallowed;
others re-raise. `KIND_LABELS` maps each kind to its report label.

`crc/core/registry.py` maps `(cloud, kind)` to a factory `Callable[[RunContext], Cleaner]`.
`@register("aws", "keypair")` stores it in `DEFAULT_REGISTRY`; a duplicate raises
`DuplicateCleanerError`, a missing pair `UnknownCleanerError`. Importing the cloud package in the
route file is what registers the cleaner. Tests build their own `Registry()`.

## Cleaner: plan and apply

```python
class Cleaner(Protocol):
    cloud: str
    kind: str
    def supports(self, operation: Operation) -> bool: ...
    def plan(self, context: RunContext) -> Plan: ...                 # read-only
    def apply(self, plan: Plan, context: RunContext) -> Result: ...  # all mutations
```

Types are frozen dataclasses in `crc/core/cleaner.py`: `Candidate`, `Skipped`, `Plan`, `Failure`,
`Result`, `RunContext`, `Operation` (delete, stop).

- `plan()` lists, normalises SDK data to `Candidate`, applies policy and decides; it makes no
  mutating call. `--dry_run` is `plan()` without `apply()`. The plan covers every region before
  `apply()` mutates, so a listing error in a later region stops the pair before any delete.
- `apply()` performs the mutations for the plan only, in the order `CLEANUP_SAFETY.md` requires,
  and isolates per-resource errors only where that file allows.
- Age decisions compare against `context.now`; cleaners never read the clock or environment.
  SDK clients, policy and logger are injected through the constructor.
- SDK objects do not cross the boundary; the plan holds `Candidate` values.

## Core policy and age

Pure functions on plain data, no I/O: `crc/core/policy*.py` (tags, `notags`, exception tags, name
regex, managed-resource checks, age) and `crc/core/age*.py` (parse `--age`, `--detach_age`,
`--max_age`, custom age tags, `is_old`). `crc/core` reads no environment (ARCH-003): `is_old` calls the
`MAX_AGE` source that `crc/cli/context.py` installs (`install_max_age_source`, which passes
`read_max_age_env` to `crc.core.age.set_max_age_source`); `main` exports the variable from `--max_age`. Fail closed: an unreadable custom age tag or bad age keeps
the resource and records a skip. Cleaners fetch and normalise; policy decides. See
`crc/core/README.md` for the function list.

## Notify and run log

`crc/notify/slack.py` and `crc/notify/influx.py` build messages and points from the `Report`
(`crc/core/report.py`); they hold no cloud logic. `crc/core/logger.py` is the per-run log
(`RUN START`, `ACTION`, `RESULT`, ...); format and rules are in [docs/logging.md](docs/logging.md).

## Add a cleaner

Use `crc/aws/keypairs/` (cleaner) and `crc/cli/routes/aws.py` (route) as the model.

1. Read `CLEANUP_SAFETY.md`, `docs/refactor/BUGS.md` rows for the resource, and the nearest cleaner
   of the same cloud and its package README.
2. Create `crc/<cloud>/<kind>/` with `crc/<cloud>/<kind>/client.py` (a `Protocol` for the SDK
   calls you use), `crc/<cloud>/<kind>/cleaner.py`, `crc/<cloud>/<kind>/__init__.py` with
   `__all__`, and a `README.md` with the four required sections.
3. Write the cleaner class with `supports`, `plan` and `apply` as above. Require a real age gate
   for destructive kinds; fail closed on missing or invalid age, tags or telemetry.
4. Register a factory: `@register("<cloud>", "<kind>")` on a function that builds the cleaner from
   `context.options` (use `crc/core/policy*.py`, do not write ad hoc regex or age code).
5. Route it: in `crc/cli/routes/<cloud>.py` import the package (`# noqa: F401`), add an options
   builder from `Options` to the keys the factory reads, and `"<kind>": Route(builder)`. Put gates
   the CLI must still raise (for example the AWS disk opt-in) in `Route.precheck`.
6. A kind that is new to the CLI also needs, in this order: the value in `RESOURCES`
   (`crc/cli/constants.py`, and `RESOURCES` or `EXTRA_RESOURCES` in `crc/core/selection.py`) and
   the `--resource` choices (`crc/cli/args.py`); its pair rule in `crc/cli/pairs.py`
   (`SUPPORTED_CLOUDS`) when some clouds lack it; a label constant in `crc/core/report.py` and its
   entry in `KIND_LABELS` (`crc/core/engine.py`). This changes the CLI contract: follow the flag
   workflow in [AGENTS.md](AGENTS.md). Add one `AddedResource` (and the new `RESOURCE_HELP`) to the
   ADDED tables of `tests/helpers_ag_cli_added.py`; the oracle `tests/legacy/crc_main.txt` is never
   edited, and new kinds are not blocked.
7. Tests, all offline: the cleaner (happy path, fail-closed path, dry run makes no mutating call,
   call order of destructive steps) and the routed CLI path. Extend the CLI parity tests that
   reach the cleaner through the route: `tests/test_cli_main_parity.py` `ROUTED_CALLS` (AWS kinds;
   the other clouds are in `tests/helpers_routed_<cloud>.py`) and the per-cloud
   `tests/test_cli_bridge_<cloud>.py` (`tests/test_cli_bridge_parity.py` covers `keypair`). They use
   `FakeCleaners` from `tests/helpers_cli_routes.py`, which records the options the route passed
   to the factory. The production Jenkins commands are in `tests/fixtures/jenkins_commands.txt`,
   checked by `tests/test_jenkins_contract.py`; keep it green unchanged.
8. Update the package README and, for destructive behaviour, `CLEANUP_SAFETY.md`; then
   `pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt` (once),
   `make check`, `make smoke-offline` (a dry run per Jenkins `(cloud, kind)` pair, SDKs faked, network denied) and
   `make raise-test-floor`.
