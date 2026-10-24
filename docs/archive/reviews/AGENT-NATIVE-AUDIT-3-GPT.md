# Independent Agent-Native Repository Audit 3

**Repository:** `cloud-resource-cleanup`  
**Audited commit:** `f8ed6dd0713741697d01b4d92df8a43eb237e17f`  
**Audit date:** 2026-10-24  
**Prior audits:** 8.2/10 and 7.0/10 at `0e2bf38`  
**Verdict:** **8.4/10 — genuinely agent-friendly and mostly agent-native, but not yet
self-sustaining without human engineering judgment**

## Executive answer

Yes, this now really does look designed for coding agents. The evidence is not the volume of
agent prose; it is the executable harness: strict lint/type/import boundaries, twelve
architecture checks with actionable failures, a network-denied offline subprocess smoke, a
47-command Jenkins contract, an additive CLI compatibility mechanism, all 46 safety IDs mapped
to hash-locked tests, and no remaining adapter source files. A newcomer without Jenkins access
can reproduce the production command shapes locally.

The claim should not be inflated to “agents can maintain it forever with no human engineering.”
Live docs already contain several semantic contradictions that link and symbol checks cannot
see; `BUGS.md` is a 25,000-word shadow issue tracker; frozen-oracle parity still dominates the
44,597-test suite; resource-kind metadata is repeated across many files; changing a locked
safety contract still ends at “owner only”; and there are no repository-level agent evals or
run-quality telemetry. This is a strong agent-maintained repository, not an autonomous software
factory.

## 1. Day-one walkthrough and context cost

I started at `AGENTS.md` and `README.md`, then followed the links rather than searching the whole
tree. The useful path was:

1. `AGENTS.md`: commands, safety boundaries, map, workflows.
2. `README.md`: human CLI surface and examples.
3. `ARCHITECTURE.md` and `CLEANUP_SAFETY.md`: engine contract and destructive invariants.
4. `.cursor/skills/crc-maintain/SKILL.md`: implementation loop.
5. The owning cloud README, kind README, route, cleaner, and tests.
6. `tests/archtest/README.md` when a mechanical rule or locked test matters.
7. A targeted `BUG-NNN` search; reading all of `BUGS.md` is counterproductive.

For a small change to an existing AWS cleaner, the eight orientation documents above are about
11,200 words, roughly 15,000 tokens. The model cleaner, client/init, route and two test files add
about 3,300 code-words, roughly another 6,000–8,000 tokens. A realistic safe starting set is
therefore **14 files and about 21,000–23,000 tokens**. Adding a new resource kind also requires
the 1,900-word CLI README and 2,100-word core README plus compatibility tables, taking the
working set to roughly **16–18 files and 27,000–30,000 tokens**. This is manageable, but not
small.

Where I got lost or paid unnecessary context tax:

- `AGENTS.md` is a good 116-line map, but it is almost at its 120-line mechanical ceiling and
  repeats workflow text also present in the skill.
- The 261-line root README is useful to operators, not agents. Old YugabyteDB branding, badges,
  community links and product marketing obscure CRC-specific onboarding.
- The “one hop away” design becomes several large hops: `CLEANUP_SAFETY.md` is 270 lines, while
  the AWS, CLI and core READMEs are about 1,900, 1,900 and 2,100 words respectively. They repeat
  behavior rather than consistently pointing to one authority.
- `BUGS.md` has 403 rows, 455 lines and 25,000 words. Its old rows intentionally preserve
  `main` locations, so it is an archive and an active queue in the same table. Search works;
  browsing does not.
- `PLAN.md` and `STATUS.md` are correctly archived. They are migration history, intentionally
  stale, and a day-one agent should not read them unless a bug row explicitly sends it there.
- Broad repository search is polluted by more than 100 archived review/history documents.
  Archiving was the right move, but agents must scope searches away from `docs/archive`.

## 2. Current guidance used

The 2025–2026 guidance is notably consistent:

- [AGENTS.md open convention](https://agents.md/): use one predictable root map, exact commands,
  security constraints, and nested guidance only where local rules differ.
- [OpenAI, Harness engineering](https://openai.com/index/harness-engineering/): encode testing,
  validation, review, feedback and recovery in the repository; the harness, not model heroics,
  enables end-to-end autonomy.
- [OpenAI, Codex customization](https://developers.openai.com/codex/concepts/customization):
  keep `AGENTS.md` small, route to detail, codify repeated mistakes, and pair prose with linters,
  type checks and automation.
- [Anthropic, Claude Code best practices](https://www.anthropic.com/engineering/claude-code-best-practices):
  context is scarce; always-loaded instructions should contain only non-obvious, broadly
  applicable facts, with focused tests during iteration.
- [Anthropic, Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills):
  use progressive disclosure, split large references, and evaluate skills on representative
  tasks rather than guessing what agents need.
- [Cursor rules documentation](https://docs.cursor.com/en/context/rules): keep persistent project
  guidance versioned, concise and scoped; use skills for reusable procedures.
- [Factory, AGENTS.md](https://docs.factory.com/harness/agents-md): provide exact setup, focused
  and full verification, generated-file rules, security boundaries and completion evidence.
- [Factory, reproducible agent environments](https://factory.com/articles/reproducible-coding-agent-environments):
  lock dependencies, preserve the starting revision and exact command results, and isolate
  mutable workspaces.

Against that guidance, CRC is strongest at mechanical architecture and offline behavioral
contracts. It is weakest at context economy, semantic document freshness, guardrail evolution,
and measurement of agent performance.

## 3. Entry docs: short, accurate and progressive?

**Mostly, not completely.** `AGENTS.md`, four short path-scoped Cursor rules and the
`crc-maintain` skill are a sound progressive structure. `CLAUDE.md` being a symlink to
`AGENTS.md` is vendor-neutral and avoids duplicate truth. The map names the cheapest focused
test, fast gate and full gate. It also says plainly that `--dry_run` is not permission to call
real cloud accounts.

Accuracy is the issue. The entry map is better than at `0e2bf38`, but facts drift beneath it:

- `AGENTS.md` says about 44,421 tests in 61 seconds. This audit collected 44,597 and took
  70.21 seconds after dependencies were installed.
- `ARCHITECTURE.md` says the otherwise-pure core has one exception where `is_old` reads the
  `MAX_AGE` environment variable. Current `crc/core/age.py` has no environment read; CLI installs
  a callback from `crc/cli/context.py`.
- Several package READMEs are longer than the architecture document and duplicate detailed
  safety behavior already authoritative in `CLEANUP_SAFETY.md`.

The docs are navigable, but they are not yet a reliably minimal system of record.

## 4. Mechanical enforcement versus prose

| Rule | Current enforcement | Assessment |
|---|---|---|
| Formatting, lint and types | Ruff check + format, strict mypy, SDK-specific overrides | Strong |
| Layering | Five import-linter contracts | Strong |
| Architecture | ARCH-001..012: ratchet, READMEs/API sync, bans, sizes, read-only plans, forbidden constructs, frozen inputs, test floor, bug refs, doc budgets/paths, TODO/bug sync | Unusually strong |
| Safety invariants | All 46 `SAF-NNN` IDs have hash-locked test mappings; no gaps | Strong, but expensive to evolve |
| CLI compatibility | Frozen oracle plus explicit `ADDED_FLAGS`, `ADDED_CHOICES`, `ADDED_RESOURCES` contract | Strong and now extensible |
| Production use | 47 Jenkins commands parse, validate, route and run against frozen expected outcomes | Strong |
| End-to-end safety | `make smoke-offline` runs real `crc.py` subprocesses with fake SDKs and denied sockets | Strong |
| Critical/high debt | ARCH-012 requires exactly one `TODO(BUG-NNN)` for each open/partial critical or high row; workflow creates/closes issues | Strong for highest risks |
| “Never call cloud APIs” | Smoke denies sockets; ordinary unit tests rely on correct mocking and have no suite-wide network deny | Partial |
| “Never weaken a test” | Count floor and selected hashes catch deletion/locked changes, not semantic weakening of every test | Partial |
| “Docs match code” | Links, paths, headings and API names are checked; behavioral prose is not | Partial; current drift proves it |
| Scope is one bug | Templates and prose only | Not mechanically knowable |
| Every destructive cleaner has a real gate | Prose and selected tests; open high bugs explicitly violate it | Partial |
| Guardrail changes | Owner-only updater/hash workflow | Safe, but not compatible with “no human writes code” as stated |

The repository has correctly moved many rules out of prose. It should resist adding more AST
rules merely because they are possible: semantic contracts and representative behavior tests pay
better than policing every coding preference.

## 5. Feedback loops

The checkout was not bootstrapped: there was no `.venv`, and the first command could not import
Ruff. The repository does contain a constrained install in `.cursor/environment.json`, but this
machine had not run it. After:

```text
python3 -m pip install --user -r requirements.txt -r requirements-dev.txt -c constraints.txt
```

the substantive `make check` was green:

```text
44,596 passed, 1 skipped in 51.62s
ARCH-008: 44,597 tests, floor 44,597
wall time: 70.21s
```

No cloud API was called. The full suite includes the network-denied offline smoke.

Seventy seconds is acceptable for a pre-commit gate and `make test-file`/`make check-fast` provide
the inner loop. The cost is still inflated by exhaustive frozen parity. ARCH failures are
excellent: ID, location and `REMEDIATION`. Ruff, mypy, import-linter and ordinary pytest failures
only provide their native output, accurately disclosed in `AGENTS.md`. The missing bootstrap is
the biggest day-one feedback defect: the canonical command failed before evaluating the repo.

## 6. Documentation drift spot checks

At least five current claims were checked against code/data:

1. **Stale:** `tests/archtest/README.md` says 30 of 46 SAF IDs are locked and lists 16 gaps.
   `tests/archtest/safety_lock.json` has 46 lock keys and `gaps: []`.
2. **Stale:** `tests/README.md` says five hash-locked tests build adapter classes and are the only
   legacy-ratchet files. No `*adapter*.py` exists under `crc/`; `pyproject.toml` has an empty Ruff
   exclusion, and safety mappings now target engine/current tests.
3. **Stale:** `crc/core/README.md` says `crc.core.selection` holds CLI expansion and opt-in gates.
   `crc/core/selection.py` explicitly says the gates live in `crc/cli/inputs.py` and
   `crc/cli/dispatch.py`; it now holds only constants and `Selection`.
4. **Stale:** `ARCHITECTURE.md` says `is_old` reads `MAX_AGE` from the environment.
   `crc/core/age.py` calls an injected source; only `crc/cli/context.py` imports `os` and reads it.
5. **Stale:** `AGENTS.md` claims 44,421 tests. The committed floor and this run are 44,597.
6. **True:** the production Jenkins contract contains exactly 47 commands, and
   `tests/test_jenkins_contract.py` checks parsing, validation, routing, offline execution,
   cleaner options, exit status and frozen outcomes.
7. **True:** every adapter class is gone. The only live “adapter” strings in scan are historical
   class names used to preserve `NotImplementedError` text; there are zero adapter files.
8. **True:** high-risk open bugs such as BUG-062, BUG-075, BUG-112, BUG-124 and BUG-142 each have
   one current `TODO(BUG-NNN)` at the implementing function, enforced by ARCH-012.

`PLAN.md` and `STATUS.md` are internally consistent as archived migration records, not current
plans. `BUGS.md` is consistent with its own rule that old locations remain historical, but that
rule makes most rows poor navigation for current code. Package READMEs are mixed: the earlier
fail-open claims were fixed, but the core/test/archtest examples above show semantic drift still
occurs despite green doc checks.

## 7. Over-engineering and what is still missing

Context/process that costs more than it returns:

- `BUGS.md`: 403 immutable rows and 25,000 words in one Markdown table. GitHub issues now exist
  for high/critical TODOs, so keeping all historical, fixed, low-risk and active work together is
  duplication.
- Thirty frozen source oracles and three slow CLI parity files preserve migration history at
  runtime. The Jenkins contract and additive compatibility tables now provide a route to retire
  much of this without losing external compatibility.
- Per-package README behavioral essays duplicate `CLEANUP_SAFETY.md`, implementation docstrings
  and tests. ARCH-002 makes their shape trustworthy, not their meaning.
- Supported-kind wiring remains distributed across parser choices, constants, pair tables,
  route tables, registry imports, labels, additive compatibility data, tests and docs.
- The test-count floor rewards accumulation and makes consolidation bureaucratic even when
  behavioral coverage improves.

Missing pieces common in mature agent-native repos:

1. Representative repo evals: “fix a safety bug,” “add a cleaner,” “change an optional flag,”
   and “review a destructive change,” with success rate, retries and context cost.
2. Agent-run observability: exact commands, failures/retries, check timing trends and flaky tests
   retained as structured evidence.
3. An agent-generated, reviewed workflow for changing safety hashes/oracles/guardrails. Requiring
   approval is sound; requiring a human to perform the edit is not.
4. A suite-wide network deny, not only the smoke hook.
5. Dependency/security scanning and lock provenance. Constraints make resolution reproducible,
   and the earlier unused direct dependencies are gone, but there is no vulnerability/license
   gate or automated proof that the constraints still describe a reviewed resolution.
6. Generated semantic reference sections for supported pairs, route/label coverage, safety-lock
   coverage and test counts.

Task/PR templates, issue creation, offline execution and Jenkins-without-Jenkins are now present;
those are no longer gaps.

## 8. Mental exercise: add an AWS NAT gateway cleaner

The code seam is good. I would create `crc/aws/nat_gateway/` with a typed client Protocol,
`Cleaner`, registry factory, README and focused tests. `plan()` would list and normalize;
`apply()` would perform ordered mutations; the route would inject options. Existing cleaners are
small and consistent enough to copy structurally without copying cloud policy.

The friction is metadata fan-out:

1. Add package/client/cleaner/README and registration.
2. Import it, build options and add the AWS route.
3. Add the resource in CLI constants and parser choices.
4. Add supported-cloud rules and decide whether `--resource all` includes it.
5. Add report label and `KIND_LABELS`.
6. Add an `ADDED_RESOURCES` compatibility entry and resource help.
7. Extend route/bridge/main parity tables and explicit cleaner tests.
8. Add safety text and locked tests for age/filter gates and destructive order.
9. Raise the test floor.

This is feasible for an agent now—the old immutable-parser dead end is fixed—but it is easy to
miss one of nine representations. A typed resource manifest that derives parser choices,
supported pairs, labels and structural tests would materially reduce errors. The guide should
also say to search `BUGS.md` by resource/ID, never read it wholesale.

## 9. Status of the 8.2/10 audit's top ten

| # | Recommendation | Status now | Evidence |
|---:|---|---|---|
| 1 | Fix post-engine doc lies and seven fail-open READMEs | **Partly done** | Deleted handler/adapter references and package fail-open claims are fixed, but `ARCHITECTURE.md` now has a stale `MAX_AGE` claim, so the broader documentation-freshness goal is not complete. |
| 2 | Extend ARCH-011 to all live docs and backticked paths | **Done** | Recursive `.md`/`.mdc` scan, archive exclusion, links, repo paths, globs and placeholders in `doc_links.py`, with self-tests. |
| 3 | Repoint safety locks, map all gaps, delete adapters/dead selection builder | **Done** | 46/46 locks, zero gaps, zero adapter files, no `build_selection`. |
| 4 | Permit additive CLI flags/resources while preserving the oracle | **Done** | One typed ADDED table plus tests proving additions pass and changed legacy flags fail. |
| 5 | Add the production Jenkins command manifest | **Done** | 47-command fixture and three contract layers in `test_jenkins_contract.py`. |
| 6 | Split/archive BUGS and unify location policy | **Partly done** | New rows use current paths and TODOs point to current code; the 403-row table remains unsplit and old locations remain intentionally stale. |
| 7 | Close ARCH-003 env/clock/print loopholes | **Partly done** | Core env and clock-reference holes are closed; cloud bans are broad. `crc/notify` still permits and uses `print`. |
| 8 | Pin deps, remove unused deps, format, type `crc.py` | **Partly done** | Constraints and SDK-independent mypy exist, unused direct dependencies were removed, and Ruff format is in lint/CI. `requirements.txt` itself is unpinned and `crc.py` remains excluded from mypy. |
| 9 | Add offline CLI smoke | **Done** | `make smoke-offline`, fake SDK hook, denied sockets, subprocess per production pair. |
| 10 | Prebuild the Cursor Cloud environment | **Open / deliberately deprioritized** | Repo install config exists, but this machine was unbootstrapped. Owner says Cloud Agents are not the long-term workflow, so this should not lower the cross-agent score much. |

Net: **5 done, 4 partly done, 1 open/deprioritized**.

## 10. Status of the 7.0/10 audit's top ten

| # | Recommendation | Status now | Evidence |
|---:|---|---|---|
| 1 | Reproducibly green environment and mypy SDK policy | **Partly done** | Constraints and overrides make a fresh constrained install green; the checkout itself lacked the documented environment and needed manual bootstrap. |
| 2 | Add explicit compatibility contract and reduce combinatorial parity | **Partly done** | Additive compatibility is solved; the exhaustive parity/oracle bulk remains. |
| 3 | Delete adapters, exports, dead helpers and adapter-only tests | **Done** | No adapter source remains; scan calls the AWS scan function directly; engine tests hold the locks. |
| 4 | Correct safety docs and generate repeated facts | **Partly done** | Earlier safety lies are fixed, but five current drift examples remain and repeated facts are still handwritten. |
| 5 | Add network-denied offline CLI simulator | **Done** | Subprocess smoke with fake providers and socket denial. |
| 6 | Import and test the Jenkins command matrix | **Done** | 47 commands frozen and runnable without Jenkins access. |
| 7 | Move active bugs to queryable files/issues | **Partly done** | TODO-to-issue covers critical/high rows; the flat Markdown register remains canonical for all 403 rows. |
| 8 | Define resource kinds once and derive consumers | **Open** | Resource metadata remains manually repeated. |
| 9 | Replace owner-edit dead ends with reviewed agent changes | **Open** | Locked-test/oracle updates still instruct agents to stop and ask the owner to perform them. |
| 10 | Add evals/observability/formatter/security gates | **Partly done** | Formatter is done; agent evals, run telemetry and dependency/security gates are absent. |

Net: **3 done, 5 partly done, 2 open**.

## 11. Score

**8.4/10.** CRC has crossed the line from “a repository with agent instructions” to “a
repository whose important architecture, compatibility and destructive-safety constraints are
substantially executable.” An unfamiliar local agent can install deterministically, run the
actual CLI offline, reproduce all 47 Jenkins invocations, make an additive CLI change, and receive
actionable structural failures. It is held below 9 by present-tense semantic doc drift, a large
migration/parity tax, repeated resource metadata, an owner-only guardrail evolution path, and no
measurement loop showing that agents actually complete representative maintenance tasks
reliably. The “no human writes code” goal is plausible only if human approval remains available
for destructive-contract judgment; removing human judgment entirely would be unsafe.

## 12. Top ten next changes, ranked by value/effort

| Rank | Change | Value | Effort |
|---:|---|:---:|:---:|
| 1 | Fix the five live doc contradictions above; generate SAF coverage, test count and resource tables from source instead of copying them | Very high | Low |
| 2 | Split `BUGS.md`: active current-location records or GitHub issues by package, fixed/historical rows in archive | Very high | Low–medium |
| 3 | Retire frozen parity suites kind by kind now that Jenkins and additive contracts exist; preserve explicit outcomes, not executable old source | High | Medium |
| 4 | Define each resource kind once in typed metadata and derive parser choices, supported pairs, labels and coverage checks | High | Medium |
| 5 | Add four repository agent evals (bug fix, cleaner, optional flag, review) and record success, retries, context and gate time | High | Medium |
| 6 | Let agents generate guardrail/hash updates with a machine-readable old/new diff and rationale, gated by approval rather than human editing | High | Low–medium |
| 7 | Resolve the remaining open high-risk missing-gate bugs before claiming autonomous destructive maintenance | Very high | Medium |
| 8 | Add a suite-wide socket/cloud-call deny and dependency/security audit to `make check`/CI | High | Low |
| 9 | Make bootstrap self-checking (`make doctor` or equivalent) and print the exact constrained install command when tools are absent | Medium | Low |
| 10 | Trim the root/cloud/core/CLI READMEs to maps; keep behavior in one safety authority plus executable tests | Medium | Medium |
