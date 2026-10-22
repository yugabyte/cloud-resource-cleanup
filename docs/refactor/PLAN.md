# CRC Python refactor plan

Branch: `refactor/python-agentic`. Target runtime: Python 3.11.

## Goal

Refactor the existing Python code in place into a clean codebase that AI
agents can maintain without human review, with heavy, mechanically enforced
guardrails. This is a refactor: CLI flags, environment variables and cleanup
behaviour stay the same. The Jenkins jobs in `cloud-resource-cleanup-triggers`
must keep working unchanged, including JSON-style flag values such as
`'{"days": 1}'`.

## Target architecture

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

Dependency direction: `crc.py` -> `crc.core.engine` -> `crc.core.*`; cloud
packages depend on `crc.core` only; cloud packages never import each other;
`crc.core` never imports a cloud package or cloud SDK. Enforced by
import-linter.

## Guardrails (from guardrail Option D, adapted to Python)

- `AGENTS.md` (short map), `CLAUDE.md` symlink, `ARCHITECTURE.md`.
- Ruff with `UP`, `FURB`, `SIM`, `PERF`, `B`, `C901`, `PLR` and target
  `py311`; mypy strict on refactored packages.
- import-linter contracts: layers, cloud independence, core forbids SDKs.
- `tests/archtest/`: README in every package with required sections and
  symbol sync, read-only `plan()`, bans on ad hoc `re` / `datetime.now` /
  `os.environ` in cloud packages, file and function size limits, safety
  test hash lock tied to `SAF-xxx` IDs in `CLEANUP_SAFETY.md`.
- Legacy ratchet: modules not yet refactored are listed in one allowlist and
  exempt from the new checks. Each rung removes the modules it refactors.
  The allowlist may only shrink.
- `.github/CODEOWNERS` requires the owner for guardrail files.
- Project skill `.cursor/skills/crc-python-refactor/` built from
  `docs/refactor/SKILL_SELECTION.md`.

## Rungs

Each rung is one Cursor Cloud Agent run that must finish in under 25
minutes, followed by one review run. Every rung must leave
`pytest`, `ruff check`, `lint-imports` and `mypy` green.

| Rung | Scope |
|------|-------|
| R0a | Tooling and docs: pyproject, dev requirements, Cursor environment, AGENTS.md, CLAUDE.md, ARCHITECTURE.md, CODEOWNERS, Cursor rules, project skill, Makefile `check` |
| R0b | `tests/archtest` with legacy allowlist, SAF IDs in CLEANUP_SAFETY.md, CI workflow |
| R1 | `crc/core/age.py`, `crc/core/policy.py` with tests (not wired) |
| R2 | registry, Cleaner protocol, engine, report (not wired) |
| R3 | move Slack and Influx into `crc/notify` |
| R4+ | one resource per rung: AWS keypair, AWS elastic IP, AWS KMS, AWS VPC, AWS snapshot, AWS VM, AWS disk, AWS spot requests (2-3 rungs), Azure, GCP, OCI |
| PL-* | shared helpers (`_base.py`, `connectivity.py`, `service.py`, `utils.py`, Azure VM delete helpers) strict-clean with parity oracles |
| CLI-1..3 | `crc.py` code moved into `crc/cli/` (args, validators, the `CRC` class, `main()`); `crc.py` is a thin entry point; flags unchanged |
| ST-* | sibling-file splits turned into subpackages (`crc/aws/vm`, `crc/aws/vpc`, `crc/oci/vm`, `crc/oci/disk`) |
| Final | docs say the refactor is done and how behaviour-fix rungs work (`BEHAVIOUR_FIX.md`), README wording sweep |

Deferred from Final, each needs an owner decision first:

- Remove the legacy adapter classes (`SpotVM`, `VM`, `Disk`, ...) so `crc/cli` runs the
  registered cleaners through `crc.core.engine`. The five hash-locked safety tests build
  the adapters directly, so this waits for the owner to rewrite and relock them.
- Wire `crc/cli` through `engine.run` instead of the `CRC` methods (changes the order of
  calls and notifications, so it is its own rung with parity tests).
- Extend `safety_lock.json` to the new parity tests (every later fix would then need an
  owner relock).

## Behaviour-fix rungs

Each rung fixes the `BUG-NNN` rows it lists, nothing else, following
`.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`: oracles stay frozen, each
diverging parity case is marked with its bug ID and asserts the new outcome, and a
change to a hash-locked safety test is handed to the owner. Work from
`docs/refactor/BUGS.md` by risk: critical first, then high. Known themes: fail closed on
missing or malformed age (cleaners where `is_old({})` is true), no `MAX_AGE` env
mutation, Azure deletes scoped to the listed resource group, the three Azure VM bugs,
and an empty `--scan_tag` running the destructive path (BUG-344).

Add each rung here before it starts, as a row with its ID (for example `FIX-1`), the
bug IDs, and whether it may edit `CLEANUP_SAFETY.md`. The LOG rungs change log text and
levels only; their contract (formats, levels, parity-test rule) is in `docs/refactor/LOGGING.md`:

| Rung | Bugs | May edit CLEANUP_SAFETY.md |
|------|------|----------------------------|
| LOG-0 | BUG-356, BUG-360, BUG-362 (logger plumbing; CLI header/footer) | no |
| LOG-AWS1 | BUG-358, BUG-361, BUG-362 (aws vm, spot_instance_requests, disk) | no |
| LOG-AWS2 | BUG-357, BUG-359, BUG-361, BUG-362 (aws kms, snapshot, keypairs, elastic_ips, vpc) | no |
| LOG-AZU | BUG-358, BUG-361, BUG-362 | no |
| LOG-GCP | BUG-358, BUG-361, BUG-362 | no |
| LOG-OCI | BUG-353 (tag dict level only), BUG-358, BUG-361, BUG-362 | no |
| LOG-FIX | BUG-363 (run log must not run `basicConfig`; owner-approved 2026-10-22); REGION at DEBUG when nothing selected; `--scan_tag` run header and result (operation=scan) | no |

Progress is logged in `docs/refactor/STATUS.md`. Reviews go to
`docs/refactor/reviews/<rung>.md`.
