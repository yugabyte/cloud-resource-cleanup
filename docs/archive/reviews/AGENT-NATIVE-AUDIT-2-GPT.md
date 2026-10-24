# Independent Agent-Native Repository Audit 2

**Repository:** `cloud-resource-cleanup`  
**Audited commit:** `0e2bf38ffc4f9ce3ffde6668c7498e42d3ff1e27`  
**Date:** 2026-10-24  
**Verdict:** **7.0 / 10 — agent-friendly, not yet agent-native**

## Executive verdict

CRC has an unusually strong mechanical harness for a small Python CLI: strict typing and linting,
import boundaries, AST architecture checks with useful remediation text, locked safety tests,
offline unit/parity tests, a short root map, path-scoped Cursor rules, task skills, and issue/PR
templates. The migration to `crc.core.engine` is real in production dispatch.

It is not yet credible as a repository that agents can maintain indefinitely without a human
writing code. The canonical `make check` is red at the audited commit before pytest starts;
unpinned runtime dependencies make the environment non-reproducible; safety documentation already
contradicts fixed code in several packages; adding a resource kind conflicts with an immutable
parser oracle; owner-only guardrail updates make some normal changes stop conditions; there is no
safe offline end-to-end CLI; and 403 bugs plus compatibility adapters preserve too much refactor
scaffolding. Also, the supplied premise that the legacy adapter layer was deleted is overstated:
production delete/stop no longer dispatches through it, but `--scan_tag` still instantiates VM
adapters, and adapter classes, exports, and adapter-focused tests remain.

## 1. Day-one walkthrough and context cost

I started at `AGENTS.md`, then read the human `README.md`, followed the implementation path to
`ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `.cursor/skills/crc-maintain/SKILL.md`, and the relevant
cloud/package READMEs. The seven primary documents alone are about **9,900 words / 13,000
tokens**:

| Document | Lines | Words |
|---|---:|---:|
| `AGENTS.md` | 105 | 945 |
| `README.md` | 261 | 3,149 |
| `ARCHITECTURE.md` | 126 | 1,011 |
| `CLEANUP_SAFETY.md` | 269 | 2,143 |
| `crc-maintain/SKILL.md` | 52 | 484 |
| `crc/aws/README.md` | 60 | 1,938 |
| Example kind README (`crc/aws/keypairs/README.md`) | 30 | 262 |

That is only orientation. Following the links and instructions through the core/CLI READMEs,
archtest catalog, model cleaner and route, and relevant tests before a safe edit expands the
realistic minimal read to about **21 files, 1,879 lines, 18,441 words, or 34,300 tokens**. Reading
all of `BUGS.md` would add roughly 40,000 tokens; the usable workflow is a targeted search, though
`ARCHITECTURE.md` does not say so.

That is improved from the first audit: active refactor plans, status logs, skill-selection notes,
and reviews moved under `docs/archive/`, so they are no longer on the normal path. A competent agent
can search one `BUG-NNN` row instead of reading the whole register.

Where I got lost or received contradictory guidance:

1. `AGENTS.md:80-86` and `ARCHITECTURE.md:42-46` say kinds still use legacy handlers and are being
   migrated. In fact, every supported production pair is in `crc/cli/routes/{aws,azu,gcp,oci}.py`,
   `dispatch._run_pair` only calls `run_routed`, and `HANDLERS`, `resource_ops.py`, and `vm_ops.py`
   are gone. The docs describe an intermediate state.
2. The opposite half of the claim is also misleading: the adapter layer was **not deleted**.
   Examples remain at `crc/aws/vm/adapter.py`, `crc/azu/vm/adapter.py`,
   `crc/gcp/vm_adapter.py`, `crc/gcp/spot_vm/adapter.py`, and `crc/oci/*/adapter.py`; package
   `__init__.py` files still export them and tests still exercise them.
3. `AGENTS.md:18-24` says `make check` takes about 40 seconds and a failing check ends with
   `REMEDIATION:`. At this commit it fails in mypy before pytest, and ordinary mypy failures have no
   remediation paragraph. Only the custom architecture checks consistently provide one.
4. `AGENTS.md:55-58` says never edit `test_count_floor.txt`, while the command list and skill tell
   the agent to run `make raise-test-floor`, whose purpose is to edit that file. This distinction
   may be intended (“do not hand-edit”), but it is not what the rule says.
5. `docs/refactor/BUGS.md` is still a 450-line, 24,902-word Markdown table with **403 rows**. Its
   summary says only 63 are fixed and 5 partly fixed. Many location cells point to old monolithic
   paths while `docs/refactor/README.md` says locations must match current code.
6. Worse, the active register links to moved paths—`docs/refactor/{PLAN,STATUS,FILTER_VARIANTS}.md`
   now live under `docs/archive/`. ARCH-011 reports green because it does not scan nested
   `docs/refactor/BUGS.md`.
7. `tests/legacy/README.md:65` still sends an agent to the deleted
   `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md` instead of the current maintenance skill.
8. The 261-line root README, package READMEs, safety document, and CLI help all repeat behavioral
   details. Mechanical API-name sync does not stop those prose copies from disagreeing.

The repository contains about 157 tracked Markdown/MDC files and roughly 246,000 words. About 101
pre-existing review files (roughly 168,000 words; 102 including this audit) are now archived,
which is the right direction; broad search still retrieves them unless an agent scopes searches
carefully.

## 2. Current 2025–2026 guidance used

The current guidance is consistent:

- OpenAI, [Harness engineering: leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/):
  use a short `AGENTS.md` as a map, make repository knowledge the system of record, enforce
  architecture mechanically, and continuously garbage-collect drift.
- The [AGENTS.md convention](https://agents.md/) and OpenAI
  [Codex best practices](https://developers.openai.com/codex/learn/best-practices): document exact
  setup/test/review commands and “done”; keep guidance short, practical, and layered.
- OpenAI, [Codex customization](https://developers.openai.com/codex/concepts/customization):
  put guidance closest to its scope and pair prose with linters, hooks, and type checks.
- Anthropic, [Claude Code best practices](https://code.claude.com/docs/en/best-practices):
  keep always-loaded instructions concise, prefer targeted tests, and remove anything inferable
  from code. Put occasional workflows in skills.
- Anthropic, [Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills):
  use progressive disclosure and evaluate skills on representative tasks.
- Cursor, [Rules](https://cursor.com/docs/rules) and
  [Agent Skills](https://cursor.com/docs/skills): use short path-scoped rules and on-demand,
  optionally path-scoped skills rather than duplicated always-on instructions.
- Devin, [Rules & AGENTS.md](https://docs.devin.ai/cli/extensibility/rules): keep always-on rules
  as small as possible and prefer skills for repeatable workflows.
- Factory, [Agent Readiness](https://docs.factory.com/agent-readiness/overview): reproducible
  environments, fast tests, observable failures, security, and deterministic local validation
  are prerequisites to autonomous operation.
- GitHub, [Agentic primitives and context engineering](https://github.blog/ai-and-ml/github-copilot/how-to-build-reliable-ai-workflows-with-agentic-primitives-and-context-engineering/):
  retrieve only task-relevant instructions and separate exploration, implementation, and
  verification contexts.

Against that standard, CRC is strong on structural enforcement and weak on reproducibility,
semantic documentation freshness, end-to-end simulation, and autonomous evolution of its own
guardrails.

## 3. First audit top 10: disposition

| # | First-audit recommendation | Status | Evidence |
|---:|---|---|---|
| 1 | Add `check-fast`, xdist, prebuilt environment | **Done** | `Makefile:5-12` has `check-fast` and `-n auto`; `requirements-dev.txt:2` pins xdist; `.cursor/environment.json` installs a venv. The resulting environment is currently red, addressed below. |
| 2 | Apply queued safety/README corrections | **Partly done** | Root README and `CLEANUP_SAFETY.md` now correctly describe logs, VPC example, fail-closed `SAF-009`, and AWS disk floors. Several package READMEs still say `BUG-004` is fail-open after it was fixed. |
| 3 | Test a manifest of production Jenkins commands | **Open** | No trigger-command fixture or contract test exists under `tests/`; templates merely ask which trigger job is affected. |
| 4 | Close ARCH-003/005 loopholes and add locks/floor checks | **Partly done** | ARCH-005 follows sibling modules; ARCH-006..008 ban constructs, hash oracles/`requirements.txt`, and enforce a test floor. ARCH-003 still permits `crc/core` env reads and misses callable references such as `clock=datetime.datetime.now`; many remain in cloud adapters/cleaners. |
| 5 | Finish engine wiring and delete adapters | **Partly done** | Production delete/stop dispatch is engine-only (`dispatch.py:51-53` and all route tables). Adapter source, exports, `--scan_tag` dependencies, and adapter tests remain. |
| 6 | Replace frozen exhaustive parity with behavioral contracts | **Open** | Three slow CLI parity files remain in `SLOW_PARITY`; `test_cli_args_parity.py:538-548` still requires the parser definition, including choices, to equal frozen `crc_main.txt`. |
| 7 | Archive history and split `BUGS.md` | **Partly done** | PLAN/STATUS/FILTER_VARIANTS/SKILL_SELECTION and reviews moved to `docs/archive`; `BUGS.md` was not split and grew from 379 to 403 rows. |
| 8 | Rewrite instructions for steady-state maintenance | **Done** | `AGENTS.md`, `.cursor/rules/`, and `.cursor/skills/crc-maintain/` now describe normal implement/fix/review workflows. Residual migration wording should be removed. |
| 9 | Lock the 16 unmapped SAF invariants | **Open** | `tests/archtest/README.md:62-64` still reports **30/46 locked** and the same 16 gaps. |
| 10 | Pin runtime deps, remove unused deps, add offline smoke | **Open** | `requirements.txt` remains entirely unpinned and still lists `paramiko`, `requests`, and `beautifulsoup4`; no `make smoke` or offline CLI provider exists. |

Net: **2 done, 4 partly done, 4 open**.

## 4. Mechanical enforcement versus prose

What is genuinely enforced:

- ARCH-001 keeps the ruff/mypy legacy ratchet synchronized and shrinking.
- ARCH-002 requires package READMEs and syncs public names for eligible packages.
- ARCH-003 bans selected clocks, env reads, regex use, prints, and exits by layer.
- ARCH-004 caps production files at 250 lines and functions at 60.
- ARCH-005 follows local and sibling calls to keep `plan()` read-only.
- ARCH-006 bans async, threading, and selected dependencies.
- ARCH-007 hashes every frozen oracle and `requirements.txt`.
- ARCH-008 enforces a non-decreasing collected-test floor.
- ARCH-009 requires code/test `BUG-NNN` references to exist in the register.
- ARCH-010 budgets entry/rule/skill lengths; ARCH-011 checks links.
- SAF-LOCK hashes mapped safety tests; import-linter enforces layers; ruff and strict mypy cover
  nearly all Python source.

The 174 architecture self-tests pass in 3.62 seconds and their failures are agent-actionable.
This is the repository's strongest agent-native feature.

Important prose-only or partial rules:

| Rule | Reality |
|---|---|
| Never call a real cloud API | No network deny, fake-provider mode, or SDK-call interceptor enforces it. `--dry_run` still lists real cloud resources. |
| Fail closed everywhere | Many unit/locked tests enforce cases, but no structural rule proves every destructive cleaner has an age/equivalent gate. |
| Never weaken/delete tests | Hashes protect selected tests and a count floor prevents simple deletion, but unmapped tests can be semantically weakened while count stays flat. |
| Keep docs true | ARCH-002 checks headings/API names; ARCH-010/011 check size/links. None checks behavioral claims. Current drift proves this. |
| No config/dependency changes | `requirements.txt` is hashed; `requirements-dev.txt`, `pyproject.toml`, and Makefile policy are review conventions, not immutable mechanics. |
| Preserve destructive order/error isolation | Enforced only where a unit test is mapped and locked. Sixteen SAF IDs remain gaps. |
| Format code | `ruff check` runs, but `ruff format --check` does not. |
| Keep scope to one bug | ARCH-009 validates IDs that are written; it cannot require an agent to record every newly noticed bug. |

Some enforcement is too rigid for autonomous maintenance. A test-count floor rewards accumulation
and blocks legitimate consolidation; immutable parser oracles block additive CLI kinds; and rules
tell agents to stop for guardrail changes. A no-human-code future needs an approval path for
agent-generated guardrail updates, not an owner-only editing bottleneck.

## 5. Feedback loops

Measured on the audited checkout:

- `make check`: **failed after 6.795 s**, immediately after ruff, with **21 mypy errors in 17
  files** (367 files checked). Missing `py.typed`/stubs for `google.cloud`, `google.api_core`, and
  Azure packages dominate. Pytest, import-linter, ARCH checks, and the floor check never ran.
- `make check-fast`: **failed after 2.942 s** in mypy with 7 remaining import errors from cache;
  it also reached no tests.
- `pytest --collect-only -q`: **44,415 tests collected in 5.75 s** (6.96 s wall), exactly the
  floor in `test_count_floor.txt`.
- `pytest -q tests/archtest`: **174 passed in 3.62 s**.

Therefore the documented “about 10 s”/“about 40 s” loops are not usable at this commit. Even the
archived engine reviews report green full pytest runs around 50–60 seconds and full checks around
60–75 seconds, not 40. Failure output is clear for ARCH rules but generic for mypy and gives no
repository-specific repair. This broken gate is especially damaging because the first action an
agent is told to trust is not green on a clean prepared environment.

The likely root problem is reproducibility: runtime SDKs are unpinned while mypy is strict and
only some untyped SDK namespaces are overridden. CI installs whatever versions resolve that day.

## 6. Documentation drift: spot checks

| Claim | Code reality | Result |
|---|---|---|
| `ARCHITECTURE.md:42-46` and `AGENTS.md:84-86`: kinds remain on legacy dispatch | All supported pairs are routed; `dispatch._run_pair` has no fallback call | **Stale** |
| Context claim: legacy adapter layer deleted | Multiple adapter modules, exports, and tests remain; `crc/cli/scan.py` still builds VM adapters | **False** |
| `crc/oci/disk/README.md:39`: bad custom age tag fails open | `age_tags.get_retention_age` returns `KEEP_AGE`; `is_old` returns false for it | **Stale, safety-relevant** |
| `crc/azu/vm/README.md:47`: `get_retention_age` is fail-open | Same fail-closed core path is called by `azu/vm/selection.py:32-37` | **Stale, safety-relevant** |
| `crc/gcp/README.md:25,54-55`: disk/VM custom age is fail-open | Both use the fixed shared age helper; `BUGS.md` marks BUG-004 fixed | **Stale, safety-relevant** |
| `crc/core/README.md:179-180`: policy preserves inverted GCP IP and substring NIC quirks | Actual cleaners implement fixed behavior; old helpers remain unused, documented as BUG-370 | **Stale/dead abstraction** |
| `AGENTS.md`: every failed check ends in `REMEDIATION:` | Current mypy failure does not | **False** |
| `docs/refactor/README.md`: bug locations must match current code | Many rows, including fixed engine rows, cite old `crc.py` adapter-loop locations | **Not maintained** |
| `docs/refactor/BUGS.md:13,27-28`: active PLAN/STATUS/FILTER_VARIANTS links | Those files moved to `docs/archive`; ARCH-011 does not inspect this nested document | **Broken active workflow** |
| `tests/legacy/README.md:65`: use `crc-python-refactor/BEHAVIOUR_FIX.md` | That skill no longer exists; current playbook is `crc-maintain/fix-bug.md` | **Broken link** |

`CLEANUP_SAFETY.md` itself is much better than in the first audit, but package copies did not move
with it. A safety statement should be generated or referenced, not manually repeated in every
package README.

## 7. Over-engineering and missing pieces

Context/process that no longer pays for itself:

- `BUGS.md` is an issue tracker implemented as one 24,902-word table. It is hard to query, diff,
  assign, or retire, and package READMEs duplicate its status.
- 101 pre-existing archived review essays are appropriately archived but still dominate broad repository
  search. They should not be required evidence for current behavior.
- Compatibility adapters and adapter tests preserve two mental models after production dispatch
  moved to the engine.
- Exhaustive frozen parity contributes most of 44,415 tests and prevents intentional parser
  evolution. xdist treats the symptom, not the contract design.
- Public API synchronization makes large per-package READMEs look trustworthy while behavioral
  prose silently rots.
- Resource-kind wiring is duplicated among parser choices, `RESOURCES`, pair rules, route tables,
  registry decorators, report labels, tests, docs, and notifications.

Missing for a genuinely agent-native CRC:

1. A reproducible, green environment (runtime lock or constraints, verified snapshot, stable mypy
   SDK policy).
2. `make smoke-offline`: execute `python crc.py` end-to-end with fake provider clients and an
   explicit network/cloud deny. `--dry_run` alone is not offline.
3. A machine-readable contract for the production Jenkins invocations in
   `cloud-resource-cleanup-triggers`.
4. Representative agent evals: add/fix/review tasks, success rate, context consumed, retries,
   flaky-test history, and harness timing trends.
5. An autonomous but reviewed flow for changing locked tests, hashes, baselines, and safety docs.
6. Semantic doc checks or generated reference sections for routes, supported pairs, labels, SAF
   mappings, and bug status.
7. Formatter and security/dependency scanning in the same local/CI gate.

Task and PR templates now exist and are useful. What is missing is automation that turns their
answers into tested contracts and observable outcomes.

## 8. Mental “add an AWS NAT gateway cleaner” exercise

The guide is much more accurate now, and the implementation pattern is readable. I would need to:

1. Create `crc/aws/nat_gateway/{client.py,cleaner.py,__init__.py,README.md}` with Protocols,
   `plan`, `apply`, settings, tests, and `@register("aws", "nat_gateway")`.
2. Import it and add an options builder plus route in `crc/cli/routes/aws.py`.
3. Add `nat_gateway` independently to `crc/cli/constants.py:RESOURCES` and the literal choices/help
   in `crc/cli/args.py`. Explicit-only kinds are also split into `crc/core/selection.py`'s
   `EXTRA_RESOURCES`, so the agent must decide whether `--resource all` includes the new kind.
4. Add its supported-cloud rule/message in `crc/cli/pairs.py`.
5. Add its label/constants in `crc/core/engine.py` and possibly report/notifier handling.
6. Extend bridge, parser, selection, cleaner, notification, safety, and README tests; raise the
   test floor.

Guide step 7 still tells the agent to remove `dispatch.HANDLERS` and legacy CLI methods that no
longer exist, and step 8 points mainly at the one-kind bridge parity test rather than the current
multi-kind AWS bridge suite. The greenfield safety rule also requires a real age/equivalent gate
while the nearest VPC teardown model is tag-gated without age, so a NAT-gateway design needs an
explicit safety decision rather than mechanical copying.

The cleaner code itself is approachable: protocols, injected clients, pure policy, and plan/apply
are good agent-facing seams. The friction is manual cross-file wiring and a hard contradiction:
`tests/test_cli_args_parity.py:538-548` demands exact parser equality with the frozen
`tests/legacy/crc_main.txt`, while agents are forbidden to edit that oracle. `AGENTS.md` says to
extend expected tables for a new flag, but that does not satisfy the raw parser equality test.
An agent following every rule cannot complete this legitimate feature without an owner changing a
guardrail. That is not autonomous maintenance.

## 9. Top 10 next changes, ranked by value/effort

| Rank | Change | Value | Effort |
|---:|---|:---:|:---:|
| 1 | Make the prepared environment and CI reproducibly green: pin/lock runtime SDKs, add deliberate mypy overrides or stubs, and verify `make check` in the environment build | Very high | Low |
| 2 | Replace raw frozen-parser equality with an explicit compatibility contract that permits additive choices/flags; reduce the three combinatorial CLI parity suites | Very high | Medium |
| 3 | Delete unused adapters, exports, dead policy helpers, and adapter-only tests; keep a narrowly named scan client where scan still needs one | High | Medium |
| 4 | Correct the current safety doc drift, then generate route/support/SAF/bug-status facts instead of copying them across READMEs | Very high | Low–medium |
| 5 | Add a network-denied offline CLI simulator and `make smoke-offline` covering one safe fixture per cloud and resource family | Very high | Medium |
| 6 | Import the production Jenkins command matrix as a versioned fixture and test parse, validation, routing, and dry-run planning | High | Low |
| 7 | Move open bugs to queryable per-package files or GitHub issues; archive resolved rows and remove obsolete `main` locations from the active map | High | Low |
| 8 | Define resource kinds once in typed metadata and derive parser choices, supported pairs, routes/labels validation, and reference docs from it | High | Medium |
| 9 | Replace “owner must edit” dead ends with an agent-generated guardrail-change workflow requiring review/approval and showing old/new hashes plus rationale | High | Low |
| 10 | Add repository evals and observability: representative maintenance tasks, flaky-test/timing reports, agent-run evidence, formatter, and security/dependency gates | Medium | Medium |

## Final answer

CRC now **looks deliberately designed for agents**, and its AST/safety harness is better than most
repositories that claim that label. It does not yet **operate agent-natively**: the main feedback
command is broken, contracts are not reproducible, docs lie about safety behavior, additive
features hit immutable-oracle dead ends, and normal guardrail evolution still depends on an owner.
At **7.0/10**, it is a strong agent-assisted repository with a credible foundation, not yet a
self-sustaining no-human-code system.
