# Independent Agent-Native Repository Audit (`cloud-resource-cleanup`)

**Branch audited:** `fix/integration` (at commit `f59ef39`)  
**Date:** 2026-10-23  
**Verdict Score:** **6.5 / 10**

---

## Executive Summary & Score Justification (6.5 / 10)

CRC has genuinely strong **2026-grade mechanical guardrails**: AST-level architectural tests (`tests/archtest/ARCH-001..005` and `SAF-LOCK`) that emit actionable `REMEDIATION:` instructions and test themselves via `tmp_path`, `import-linter` layer contracts, `mypy --strict` across 251 files, strict `ruff` complexity/size limits (`C901 <= 10`, `PLR0915 <= 40`, `<= 400` lines/file), and Protocol-typed offline SDK fakes. However, the repository does **not** yet look like a self-sustaining "agent-native" production repo; it looks like a **refactor construction site frozen mid-scaffolding**. Specifically: (1) the target architecture advertised in `ARCHITECTURE.md` (`crc.py -> crc.core.engine -> @register` cleaners) is **not wired in production**—`crc/cli` still calls legacy adapter classes (`VM`, `Disk`, `KeyPairs`, `SpotVM`) while `crc/core/engine.py` and `crc/core/registry.py` are only exercised by tests; (2) 179,911 words (~240k tokens) of refactor-process prose across 96 Markdown files (`BUGS.md` alone is 23,280 words, plus 69 review files) dwarf the 12,900-line Python codebase; (3) `CODEOWNERS` and `SAF-LOCK` restrictions prevented agents during the 19 `FIX-*` rungs from updating `CLEANUP_SAFETY.md` and parent `README.md`s, causing immediate documentation drift on safety-critical invariants; and (4) the frozen-oracle parity harness (`tests/legacy/*.txt`) and `make check`'s 43,010-test / 173.5-second runtime actively penalize adding new cleaners or flags.

---

## 1. Day-One Agent Onboarding Walkthrough

### Token and File Tax Before Making a Safe Edit

Following the entry path (`AGENTS.md` -> linked docs + `.cursor/rules/*.mdc` + `.cursor/skills/crc-python-refactor/`) before touching a single cleaner requires reading:

| Layer | Files | Lines | Words | Est. Tokens |
|-------|------:|------:|------:|------------:|
| Entry & Rules (`AGENTS.md`, `.cursor/rules/*.mdc`) | 5 | 160 | 1,121 | ~1,500 |
| Project Skill (`SKILL.md`, `REVIEW_CHECKLIST.md`, `BEHAVIOUR_FIX.md`, `README.md`) | 4 | 267 | 2,102 | ~2,800 |
| Core Architecture & Safety (`ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `tests/archtest/README.md`) | 3 | 391 | 2,997 | ~4,000 |
| Refactor Control Docs (`PLAN.md`, `STATUS.md`, `BUGS.md`, `SKILL_SELECTION.md`) | 4 | 1,025 | 35,637 | ~48,000 |
| **Subtotal (Links in `AGENTS.md` Map + Rules/Skill)** | **16** | **1,843** | **41,857** | **~56,300** |
| Supporting Specs (`FILTER_VARIANTS.md`, `LOGGING.md`, `README.md`) | 3 | 562 | 6,603 | ~8,800 |
| Package & Test `README.md`s (21 files) + `docs/refactor/reviews/*.md` (69 files) | 90 | 10,143 | 131,451 | ~175,000 |
| **Total Markdown in Repo** | **109** | **12,548** | **179,911** | **~240,000** |

To safely modify one cloud module (e.g., `crc/azu/vm/`), a diligent day-one agent must read `AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `SKILL.md`, `BEHAVIOUR_FIX.md`, `PLAN.md`, `crc/README.md`, `crc/core/README.md`, `crc/azu/README.md`, `crc/azu/vm/README.md`, and grep `BUGS.md` and `FILTER_VARIANTS.md`—**12 to 14 files (~15,000–25,000 tokens)** before reading a single line of Python.

### Where a New Agent Gets Lost, Misled, or Hit by Contradictions

1. **The Split-Brain Architecture Trap (`engine.py` vs `crc/cli`):**
   - `ARCHITECTURE.md:10-25`, `PLAN.md:16-32`, and `.cursor/rules/10-cloud-packages.mdc` state that `crc.py -> crc.core.engine -> crc.core.*` selects cleaners registered via `@register(cloud, kind)` and runs `plan()` then `apply()`.
   - Only buried at `PLAN.md:70-76` and `crc/core/README.md:17` does the agent learn that **`crc/cli` never calls `crc.core.engine`, `crc.core.registry`, or `crc/core/selection`!** Every cloud resource maintains two parallel entry points: a `Cleaner` class (called by unit tests and `Cleaner` parity loops) and a legacy `Adapter` class (`VM`, `Disk`, `KeyPairs`, `SpotVM`, etc., called by `crc/cli/vm_ops.py` and `crc/cli/resource_ops.py`). Worse, their execution orders differ: `Cleaner.plan()` lists *all* regions before `apply()` mutates any region, whereas `Adapter.delete()` interleaves list-and-delete region by region (`crc/aws/keypairs.py:114-143` vs `260-279`).
2. **Stale Branch & Workflow Framing:**
   - `AGENTS.md:10,82`, `PLAN.md:3`, and `docs/refactor/README.md:5` instruct the agent to work on and push to `refactor/python-agentic`. In reality, work is on `fix/integration` (and per-lane branches `fix/*`, `logging/*`).
   - Every root instruction file (`AGENTS.md`, `.cursor/rules/00-map.mdc`, `SKILL.md`) is written as a temporary refactor-campaign script ("Stay inside the current rung", "Record every bug in `BUGS.md` with its `main` file:line; do not fix it outside a behaviour-fix rung") rather than a steady-state maintenance guide.
3. **Quadruple Redundancy:**
   - The exact same ~12 rules (ruff limits, no `async`/`pydantic`, `plan()` read-only, legacy ratchet, offline mocks) are repeated nearly verbatim across `AGENTS.md`, `ARCHITECTURE.md`, `PLAN.md`, `.cursor/rules/*.mdc`, `.cursor/skills/crc-python-refactor/SKILL.md`, `REVIEW_CHECKLIST.md`, and 21 package `README.md`s.
4. **Dead Weight Linked from `AGENTS.md`:**
   - `AGENTS.md:27` links `docs/refactor/SKILL_SELECTION.md` (3,767 words evaluating 5 third-party MCP Market skills that were rejected or merged on day 1). No coding or review agent ever needs this file in its map.

---

## 2. 2025–2026 Industry Guidance on Agent-Native Repositories

Current industry consensus on building codebases for autonomous coding agents converges on six principles:

1. **OpenAI — "Harness Engineering: Leveraging Codex in an Agent-First World" (2026)**
   - Source: [https://openai.com/index/harness-engineering/](https://openai.com/index/harness-engineering/)
   - *Key guidance:* Treat `AGENTS.md` as a short (~100-line) table of contents ("progressive disclosure") rather than an encyclopedia. Enforce strict layered architecture, boundary parsing, and "taste invariants" (structured logging, file sizes, naming) via custom linters and structural tests whose error messages inject `REMEDIATION:` instructions into agent context. Run recurring background "garbage collection" agents to eliminate doc drift, dead abstractions, and technical debt.
2. **The `AGENTS.md` Open Standard (Agentic AI Foundation / OpenAI / Harness, 2025–2026)**
   - Sources: [https://agents.md/](https://agents.md/), [https://github.com/agentsmd/agents.md/issues/135](https://github.com/agentsmd/agents.md/issues/135), [https://www.harness.io/blog/the-agent-native-repo-why-agents-md-is-the-new-standard](https://www.harness.io/blog/the-agent-native-repo-why-agents-md-is-the-new-standard)
   - *Key guidance:* Separate human onboarding (`README.md`) from agent instructions (`AGENTS.md`). Keep root `AGENTS.md` concise and operational (exact setup, build, lint, and single-test commands; architectural boundaries; common pitfalls). In multi-package repos, use nested `AGENTS.md` files for ancestor-scoped context loading rather than inventing custom prose schemas.
3. **Anthropic — Claude Code Best Practices & Agent Skills (2025–2026)**
   - Sources: [https://code.claude.com/docs/en/best-practices](https://code.claude.com/docs/en/best-practices), [https://www.anthropic.com/research/equipping-agents-for-the-real-world-with-agent-skills](https://www.anthropic.com/research/equipping-agents-for-the-real-world-with-agent-skills)
   - *Key guidance:* Context windows degrade as they fill. Prune `CLAUDE.md`/`AGENTS.md` ruthlessly—for every line, ask *"Would removing this cause the agent to make mistakes?"* Exclude anything inferable from code or already enforced by linters. Prefer running targeted single-file tests during iteration instead of the full test suite. Package occasional workflows and executable helper scripts inside on-demand Skills (`SKILL.md` + `scripts/`).
4. **Cursor — Rules & Agent Skills Documentation (2025–2026)**
   - Sources: [https://cursor.com/docs/rules](https://cursor.com/docs/rules), [https://cursor.com/docs/skills](https://cursor.com/docs/skills), [https://cursor.com/learn/customizing-agents](https://cursor.com/learn/customizing-agents)
   - *Key guidance:* `.cursor/rules/*.mdc` should be short, path-scoped via `globs`, and point to canonical files in the codebase rather than copying style guides. Skills (`.cursor/skills/<name>/SKILL.md`) are task-triggered workflows, not always-on rule dumps.
5. **Factory AI — "Agent Readiness" Framework & Linter-Directed Agents (2026) & Devin Docs**
   - Sources: [https://factory.com/news/agent-readiness](https://factory.com/news/agent-readiness), [https://factory.com/product/agent-readiness](https://factory.com/product/agent-readiness), [https://factory.com/articles/agents-md-guide](https://factory.com/articles/agents-md-guide), [https://factory.com/news/using-linters-to-direct-agents](https://factory.com/news/using-linters-to-direct-agents), [https://docs.devin.ai/cli/extensibility/rules](https://docs.devin.ai/cli/extensibility/rules)
   - *Key guidance:* Scores repos across 8 pillars (Style & Validation, Build, Testing, Documentation, Dev Environment, Code Quality, Observability, Security). Emphasizes sub-5-second local feedback loops, pre-built hermetic dev environments, offline E2E smoke verification, and encoding review lessons into lint rules instead of adding pages of prose warnings.

---

## 3. Blunt Comparison Against 2025–2026 Guidance

### 3.1 Entry Docs: Short, Accurate, and Progressive?

- **What works:** `AGENTS.md` is 85 lines (655 words), `CLAUDE.md` is a symlink to it, and `.cursor/rules/*.mdc` uses glob scoping (`10-cloud-packages.mdc`, `20-core.mdc`, `30-safety.mdc`).
- **Where progressive disclosure fails:**
  - Hop 1 from `AGENTS.md` dumps the agent into **monolithic, unindexed tables and historical logs**: `docs/refactor/BUGS.md` (23,280 words; 379 rows sorted by risk rather than module or ID, citing `main` line numbers that no longer exist in `crc/`) and `docs/refactor/STATUS.md` (7,301 words of chronological rung history and obsolete R0a–R4c open-issue lists).
  - `BEHAVIOUR_FIX.md:22-27` openly admits that `BUGS.md`'s `Location on main` column does not match the refactored tree and tells agents to grep package `README.md`s and `docs/refactor/reviews/*.md` to find the code.
  - The human `README.md` (228 lines) was left behind: it has typos (`esources` at line 129, `specfic` at line 144), states logs go to `./crc.log` (`README.md:116`) whereas `crc/utils.py:21` writes `logs/crc.log`, omits `--resource nic`, `--resource snapshot`, and `--scan_tag`, and its Example 6 (`README.md:174`, *"To Delete AWS VPCs"*) runs `--resource vm`!

### 3.2 Mechanical Enforcement vs. Prose Rules

CRC's `tests/archtest/` is the strongest part of the repository, closely matching OpenAI's "harness engineering" pattern (`violation(rule, problem, remediation)` + `tmp_path` self-tests). However, comparing prose rules against mechanical checks exposes clear gaps:

| Rule / Invariant | Where Stated | Enforcement Status | Gap / Bypass Found |
|------------------|--------------|--------------------|--------------------|
| Legacy ratchet only shrinks; ruff & mypy lists synced | `AGENTS.md` #8, `ARCHITECTURE.md` | **Mechanical** (`ARCH-001` in `ratchet.py`) | Solid. Only the 5 hash-locked test files remain. |
| Package `README.md` sections & `Public API` symbol sync | `ARCHITECTURE.md` | **Mechanical** (`ARCH-002` in `readmes.py`) | Skips `crc/` top-level modules (`crc/service.py`, `crc/utils.py`, `crc/notifications.py`). Over-enforces 4 code sections on non-code dirs (`docs/`, `.cursor/rules/`). |
| No `re`, wall clock, `os.environ`, `print`, `sys.exit` | `ARCHITECTURE.md`, `20-core.mdc`, `SKILL.md` 3.1 | **Partial** (`ARCH-003` in `bans.py`) | **2 loopholes:** (1) `ARCH-003` only scans `crc/{aws,azu,gcp,oci}`, **not `crc/core` or `crc/cli`**—so `crc/core/age.py:155` calls `os.getenv("MAX_AGE")` directly inside "pure" `crc/core`! (2) `bans.py` checks `ast.Call` for clocks, missing attribute references: every cloud adapter sets `clock: Clock = datetime.datetime.now` (`crc/aws/keypairs.py:66`, `crc/aws/vm/adapter.py:42`, etc.). |
| Non-test source files `<= 400` lines; functions `C901 <= 10`, `statements <= 40`, `args <= 6` | `ARCHITECTURE.md`, `SKILL.md` 2.4 | **Mechanical** (`ARCH-004` + `ruff`) | Solid, though `crc/cli/resource_ops.py:201,273` and `crc/notify/slack.py` bypass `PLR0913`/`PLR0917` via `# noqa` and `E722` via `# noqa: E722`. |
| `plan()` is strictly read-only | `ARCHITECTURE.md`, `SKILL.md` 1.1 | **Partial** (`ARCH-005` in `readonly_plan.py`) | **Blind across module boundaries:** `_scan()` in `readonly_plan.py:38-62` only follows `self.<method>()` and same-file functions. In subpackages (`crc/aws/vpc/`, `crc/oci/spot_vm/`, `crc/gcp/spot_vm/`), calls from `plan()` into sibling modules (`teardown.py`, `leftovers.py`, `disks.py`) are not traversed unless the call attribute name itself contains a banned word. |
| Safety tests locked to `[SAF-NNN]` invariants | `CLEANUP_SAFETY.md`, `AGENTS.md` #1, #12 | **Partial** (`SAF-LOCK` in `safety_lock.py`) | **16 of 46 `SAF-NNN` IDs (35%) are unlocked `gaps`** in `safety_lock.json:324-341` (`SAF-001, 002, 004, 005, 006, 009, 010, 014, 016, 021..024, 034, 036, 037`), even though tests for many were added in R1a/R1b. |
| Strict typing across all modules | `ARCHITECTURE.md`, `SKILL.md` 2.4 | **Partial** (`mypy --strict`) | `pyproject.toml:49` excludes `^crc\.py$` from `mypy`. |
| Code formatting | `SKILL_SELECTION.md:74` | **Prose only** | `Makefile` runs `ruff check .` but **not** `ruff format --check .`. |
| Test count must never decrease | `AGENTS.md:40`, `30-safety.mdc`, `REVIEW_CHECKLIST.md` | **Prose only** | No archtest or CI step checks collected test count against a floor. |
| Do not edit `requirements.txt`; no `async`, `pydantic`, `httpx`, `tenacity`, `@singleton` | `AGENTS.md` #7, `SKILL.md` | **Prose only** | No archtest hashes `requirements.txt` or bans `AsyncFunctionDef` / `asyncio` / forbidden imports outside `crc.core`. |
| Never edit `tests/legacy/*.txt` oracles | `AGENTS.md` #5, `BEHAVIOUR_FIX.md` | **Prose only** | Unlike `safety_lock.json`, `tests/legacy/*.txt` files have no SHA-256 manifest in `tests/archtest/`. |

### 3.3 Feedback Loops: Speed and Clarity of `make check`

- **Cold-start failure on Cloud Agent VM:** Running `make check` on a fresh checkout failed immediately (`Exit code 2: /usr/bin/python3: No module named ruff`) because `.venv` was absent and packages were not pre-installed. Installing `requirements.txt` and `requirements-dev.txt` took **35.3 seconds**. Furthermore, the VM runs **Python 3.12.3** while `.github/workflows/ci.yml` and `AGENTS.md` target **Python 3.11**.
- **Measured `make check` runtime and test count:**
  - **Total `make check` runtime:** **2m 53.5s (173.54s)**
  - `python3 -m ruff check .`: **0.024s**
  - `python3 -m mypy` (251 source files): **0.298s**
  - `lint-imports` (143 files, 826 dependencies, 5 contracts): **0.113s**
  - `python3 -m pytest -q`: **43,010 passed in 157.80s (2m 37.8s)** (plus ~15s collection/teardown).
- **Why `pytest` takes 2.6 minutes:** Three CLI parity test files generate **33,700 of the 43,010 tests (78.4%)** via exhaustive combinatorial parameterization against `tests/legacy/crc_main.txt`:
  - `tests/test_cli_main_parity.py`: **17,718 tests**
  - `tests/test_cli_crc_parity.py`: **11,936 tests**
  - `tests/test_cli_args_parity.py`: **4,046 tests**
  - All other 95 test files combined: **9,310 tests** (and `tests/archtest/` alone runs in **< 0.3s**).
- **Missing fast inner loop:** There is no `make check-fast` target, no `pytest-xdist` (`-n auto`) in `requirements-dev.txt`, and `AGENTS.md` only documents `make check`. Under a 25-minute agent time budget, running `make check` 3–4 times consumes 9–12 minutes of wall-clock time re-running 33,700 CLI matrix cases even when editing an isolated cloud module.
- **Failure message quality:** `tests/archtest/` failure messages are outstanding—every failure prints the rule ID, exact offending symbol or line, and a `REMEDIATION:` block telling the agent how to fix the root cause without weakening the test.

### 3.4 Documentation Drift: 8 Spot-Checked Claims vs. Actual Code

Because `CODEOWNERS` prevented agents in `FIX-*` rungs from editing `CLEANUP_SAFETY.md`, `PLAN.md`, `STATUS.md`, and parent `README.md`s (deferring edits to `## Owner action` sections scattered across `docs/refactor/reviews/FIX-*.md`), **serious documentation drift already exists on `fix/integration`**:

1. **Spot-Check 1 — `CLEANUP_SAFETY.md:53-56` (`[SAF-009]`) is FALSE on `fix/integration`:**
   - *Doc claim:* `[SAF-009] Other modules that still call get_retention_age alone fail open: parse errors become None and the resource is judged by --age / detach_age.`
   - *Code reality (`crc/core/age.py:249-280`):* Fixed in `FIX-CORE-AGE` (`BUG-004`/`BUG-016`). `get_retention_age()` now **fails closed**, returning `KEEP_AGE = {"days": 1_000_000}` and recording `skip("bad_age_tag")`.
2. **Spot-Check 2 — Six package `README.md`s contradict `crc/core/age.py` and `crc/core/README.md` on `BUG-004`:**
   - *Doc claim:* `crc/azu/README.md:56`, `crc/gcp/README.md:55-56`, `crc/aws/vm/README.md:40`, `crc/azu/vm/README.md:39`, `crc/oci/disk/README.md:32`, and `crc/oci/vm/README.md:38` all state that custom age tags *"fail open (`get_retention_age`, BUG-004)"*.
   - *Code reality:* All six modules call `crc.core.age.get_retention_age`, which is now fail-closed (`crc/core/README.md:31,153`).
3. **Spot-Check 3 — `CLEANUP_SAFETY.md:19-20` & `208` misdescribe CLI dispatch and module locations:**
   - *Doc claim:* Under `--cloud all --resource all`, *"the loop hits aws+disk first and the opt-in circuit breaker raises"*, and the Module Map lists `crc.py` as *"CLI wiring, opt-in gates, cloud/resource loop"*.
   - *Code reality (`crc/cli/main.py:55`, `crc/cli/dispatch.py:217-237`, `crc.py:1-104`):* `validate_targets()` in `crc/cli/dispatch.py` now validates all `(cloud, resource)` pairs in `main()` *before* `MAX_AGE` is set and *before* the dispatch loop starts; `crc.py` is just a re-export shim, and `crc/cli/` is missing from `CLEANUP_SAFETY.md`'s Module Map.
4. **Spot-Check 4 — `CLEANUP_SAFETY.md:114-118` (`[SAF-025]`) is outdated after `FIX-AWS-DISK`:**
   - *Doc claim:* On AWS disk, *"when `--age` is set [`_creation_age_floor`] returns `retention_age or self.age`."*
   - *Code reality (`crc/core/policy.py:aws_disk_creation_floor`, `crc/aws/disk/README.md:47`):* Fixed in `FIX-AWS-DISK` (`BUG-283`). When both `--age` and `--detach_age` are set, the CreateTime floor is now `(retention_age or self.age) + self.detach_age`.
5. **Spot-Check 5 — `docs/refactor/FILTER_VARIANTS.md` describes pre-fix bugs as current behavior across 5+ modules:**
   - *Doc claims:* Says `aws/elastic_ips.py` `notags` *"never skips"* (`:33`, fixed in `FIX-AWS-EIP`); `aws/kms.py` requires *"all (key, value) pairs"* and *"custom age tags never match"* (`:35`, fixed in `FIX-AWS-KMS`); `azu/nic.py` `name_regex` is *"plain substring (`in`)"* (`:66`, fixed to regex in `FIX-AZU-NIC`); `gcp/disk.py` *"notags is stored (:80) but never used ... name_regex stored (:81), never used"* (`:81`, fixed in `FIX-GCP-DISK`); `gcp/ip.py` `exception_regex` is *"inverted"* (`:82`, fixed in `FIX-GCP-IP`).
6. **Spot-Check 6 — `docs/refactor/STATUS.md:52-56` & `79-325` contradict the branch state:**
   - *Doc claims:* Rows `LOG-AWS1..LOG-OCI` say *"Not merged into `refactor/python-agentic`"* (all 5 are merged into `fix/integration`), and lines 79–325 retain 245 lines of obsolete "Open issues (R0a..R4c)" claiming e.g. *"Core layer import-linter contract is a commented TODO until `crc/core` exists"*.
7. **Spot-Check 7 — `crc/core/selection.py` vs `crc/cli/dispatch.py` split-brain:**
   - *Doc/code claim (`crc/core/selection.py:1-5`):* Claims to encode the selection rules and opt-in gates of `crc.py main()`.
   - *Code reality:* `FIX-CLI-ALL` and `FIX-CLI-ROUTE` updated `crc/cli/dispatch.py` (`validate_targets`, `SUPPORTED_CLOUDS`) to reject unsupported `(cloud, resource)` pairs up front, but left `crc/core/selection.py` (`build_selection`) un-updated on `main` semantics!
8. **Spot-Check 8 — `README.md:116` & `174` (User CLI docs) contradict code:**
   - *Doc claims:* Logs are written to `crc.log` in the script directory (`crc/utils.py:21` writes `logs/crc.log`), and Example 6 ("To Delete AWS VPCs") passes `--resource vm` instead of `--resource vpc`.

### 3.5 Over-Engineering: Context Tax Without Payback

1. **Dual-Implementation Purgatory (`Cleaner` + `Adapter` + `Oracle` + `Log Rewriter`):**
   Every cloud cleaner is implemented and tested twice: once as a `Cleaner` (`plan`/`apply` returning `Plan`/`Result` for `crc.core.engine`, which is **dead code in production**), and once via a stateful `Adapter` (`VM`, `Disk`, etc. called by `crc/cli`). On top of that, every behaviour fix requires updating: (a) the cleaner, (b) the adapter, (c) per-case `diverges="BUG-NNN"` tables in `tests/test_*_parity.py` against frozen `tests/legacy/*.txt` oracles, and (d) log-rewriter rules in `tests/log_fix_*.py`. This quadruples the surface area an agent must touch for a 5-line bug fix.
2. **`docs/refactor/BUGS.md` (426 lines, 23,280 words, 379 rows):**
   Storing 379 bugs (315 open, 64 fixed/partly fixed) in a single Markdown table sorted by risk rather than ID or module—with 100-word prose cells citing obsolete `main` line numbers—is an anti-pattern for LLM context windows.
3. **69 Review Essays in `docs/refactor/reviews/` (~113,000 words):**
   Keeping 69 two-round review transcripts in the active documentation tree—and using their `## Owner action` sections as a shadow queue for unapplied `CLEANUP_SAFETY.md` edits—guarantees that agents and humans lose track of the true safety specification.
4. **Over-Broad `ARCH-002` Directory README Rule:**
   Requiring `## Purpose`, `## Public API`, `## Safety Invariants`, and `## Extension Rules` in non-Python directories (`.cursor/rules/`, `.cursor/skills/crc-python-refactor/`, `docs/`, `docs/refactor/`, `docs/refactor/reviews/`) produces robotic filler headings.
5. **Self-Defeating `CODEOWNERS` + `SAF-LOCK` Freeze:**
   The owner wants CRC maintained by AI coding agents with *no human writing code*, yet `AGENTS.md` Rules 2 and 12 forbid agents from updating `CLEANUP_SAFETY.md`, `PLAN.md`, `safety_lock.json`, or the 5 hash-locked test files, forcing agents to halt and leave `## Owner action` notes whenever a bug fix improves a safety invariant.

### 3.6 What Is Missing That Agent-Native Repos Usually Have

1. **No Offline CLI Smoke / Dry-Run Simulator:** There is no `--offline-smoke` flag, local fixture mode, or `make smoke` target to run `python crc.py --cloud <c> --resource <r> --dry_run` end-to-end with canned SDK responses. Agents can only run `pytest`, never the CLI binary itself.
2. **Unpinned & Bloated `requirements.txt`:** Unlike `requirements-dev.txt` (which pins exact versions), `requirements.txt` has **zero version pins**, no lockfile, and installs `paramiko`, `requests`, and `beautifulsoup4`—none of which are imported anywhere in `crc/` or `tests/`.
3. **Broken Cross-Repo Contract with `cloud-resource-cleanup-triggers`:** `BUG-355` (`BUGS.md:61`) documents how PR #34 broke Jenkins build 16581 because `Jenkinsfile:354` in `cloud-resource-cleanup-triggers` called Azure `spot_instances` without `--age`. Despite reviews mentioning "31 production Jenkins commands", there is no machine-readable manifest of those 31 trigger invocations tested in this repo's CI.
4. **No Fast Inner-Loop Makefile Targets or Pre-Commit Hooks:** Missing `make lint` (<0.5s), `make test-unit` (excluding the 33,700 CLI matrix tests, ~8s), `pytest-xdist`, and `.pre-commit-config.yaml`.
5. **No Steady-State Agent Playbooks or Issue/PR Templates:** All skills and rules assume a refactor/fix "rung" workflow driven by `PLAN.md`. There are no `.github/ISSUE_TEMPLATE/` or `.github/PULL_REQUEST_TEMPLATE.md` files, and no skills for post-refactor tasks (e.g., adding a cleaner, updating Jenkins trigger contracts, or pruning resolved bugs).
6. **No Automated Drift / Garbage-Collection Check:** No test verifies that `BUGS.md` statuses match `README.md` claims, or that pending `## Owner action` items in `docs/refactor/reviews/` have been applied to `CLEANUP_SAFETY.md`.

### 3.7 Code Walkthrough: Trying "Add a Cleaner" (`ARCHITECTURE.md` Steps 1–10)

Mentally following `ARCHITECTURE.md:100-121` ("Add a resource cleaner") to add a hypothetical `aws` `nat_gateway` cleaner (`crc/aws/nat_gateway.py`) exposes severe friction:

- **Step 1–7 (`Cleaner` + `@register("aws", "nat_gateway")`) is a trap:** `ARCHITECTURE.md` claims *"The engine selects cleaners from the registry using the parsed `--cloud` / `--resource` values."* **If an agent follows Steps 1–10 literally, `python crc.py --cloud aws --resource nat_gateway` will not work at all**, because `crc/cli` does not call `engine.run()` or `registry.get()`!
- **The undocumented 8-file wiring tax required in reality:**
  1. Write both `NatGatewayCleaner` (`Cleaner` protocol) **and** a legacy `NatGateways(Service)` adapter in `crc/aws/nat_gateway.py`.
  2. Export `NatGateways` in `crc.py` (`__all__`).
  3. Add `"nat_gateway"` to `RESOURCES` or `EXTRA_RESOURCES` in `crc/cli/constants.py` and `crc/cli/args.py`.
  4. Add `delete_nat_gateways()` with manual Slack/Influx calls to `crc/cli/resource_ops.py`.
  5. Add `_nat_gateway` to `HANDLERS` and `SUPPORTED_CLOUDS` in `crc/cli/dispatch.py`.
  6. Add `"nat_gateway"` to `crc/core/selection.py`, `crc/core/engine.py` (`KIND_LABELS`), and `crc/core/report.py`.
  7. Sync backticked public symbol lists in `crc/aws/README.md`, `crc/cli/README.md`, and `crc/core/README.md` (`ARCH-002`).
  8. **Hit the Parity Wall:** Adding `"nat_gateway"` to `crc/cli/args.py` and `dispatch.py` immediately breaks `tests/test_cli_args_parity.py`, `tests/test_cli_crc_parity.py`, and `tests/test_cli_main_parity.py` because they assert exact parity with the frozen `tests/legacy/crc_main.txt` oracle—and `AGENTS.md` Rule 5 forbids editing `tests/legacy/*.txt`!

---

## 4. Top 10 Changes Ranked by Value / Effort

| Rank | Change | Value | Effort | Why & Concrete Action |
|-----:|--------|:-----:|:------:|-----------------------|
| **1** | **Add `make check-fast` + `pytest-xdist` and pre-bake `.venv` in `.cursor/environment.json`** | Very High | Low | Cuts inner-loop feedback from **173.5s** to **~3s**. Add `pytest-xdist` to `requirements-dev.txt`, add `make check-fast` (`ruff check`, `ruff format --check`, `mypy`, `lint-imports`, `pytest tests/archtest`), and ensure `.cursor/environment.json` pre-builds a Python 3.11 `.venv`. |
| **2** | **Apply the queued `## Owner action` updates to `CLEANUP_SAFETY.md` and the 6 drifted `README.md`s** | Very High | Low | Eliminates active safety doc lies (`SAF-009` claiming `get_retention_age` fails open when it fails closed; `SAF-025` AWS disk floor; 6 cloud READMEs still claiming `BUG-004` is open; `FILTER_VARIANTS.md` pre-fix claims). |
| **3** | **Add a golden manifest of the 31 production Jenkins CLI commands (`tests/test_jenkins_contract.py`)** | High | Low | Prevents another `BUG-355` production outage. Store the exact CLI flag invocations from `cloud-resource-cleanup-triggers` in a JSON/YAML fixture and assert in CI that every command parses, validates, and dry-runs cleanly against mocked SDKs. |
| **4** | **Close `ARCH-003` and `ARCH-005` mechanical loopholes + add `ARCH-006` (test-count & `requirements.txt` lock)** | High | Low | Extend `ARCH-003` to `crc/core` and `crc/notify` (after moving `os.getenv("MAX_AGE")` out of `crc/core/age.py:155` into `crc/cli`), flag `datetime.datetime.now` attribute defaults in cloud modules, make `ARCH-005` follow intra-package helper imports, and mechanically enforce test-count monotonicity and `requirements.txt` integrity. |
| **5** | **Finish the deferred `engine.run` wiring rung and delete the legacy `Adapter` classes** | Very High | Medium | Eliminates the #1 source of architectural confusion and double maintenance. Wire `crc/cli` through `crc.core.selection` + `crc.core.engine.run()`, rewrite/relock the 5 safety tests against the `Cleaner` classes, and retire the duplicate `Adapter` classes (`VM`, `Disk`, `KeyPairs`, etc.). |
| **6** | **Transition from frozen `main` parity oracles (`tests/legacy/*.txt`) to behavioral contract tests** | High | Medium | Once `fix/integration` merges to `main`, `tests/legacy/*.txt` and `tests/log_fix_*.py` become pure liability that block adding new CLI flags or cleaners. Replace the 33,700 combinatorial `crc_main.txt` parity cases with a concise (~300-case) behavioral specification suite. |
| **7** | **Archive refactor history (`STATUS.md`, `reviews/*.md`, `SKILL_SELECTION.md`) and split `BUGS.md`** | High | Low | Moves ~150,000 words of completed refactor history into `docs/archive/` out of the active agent map. Split the remaining open bugs in `BUGS.md` by cloud package with current `crc/` file paths instead of obsolete `main` line numbers. |
| **8** | **Rewrite `AGENTS.md` and `.cursor/skills/` for steady-state autonomous maintenance** | High | Low | Replace "refactor rung" rules (`refactor/python-agentic`, `BUGS.md` `main` file:line rules) with steady-state workflows: how to fix a bug, how to add a cleaner (matching actual CLI wiring), how to run fast targeted tests, and when to update `CLEANUP_SAFETY.md`. |
| **9** | **Map and lock the 16 open `SAF-NNN` gaps in `tests/archtest/safety_lock.json`** | Medium | Low | 16 of 46 `SAF-NNN` invariants (`SAF-001..006`, `009`, `010`, `014`, `016`, `021..024`, `034`, `036`, `037`) sit unlocked in `gaps` despite having unit tests proposed in `STATUS.md`. Map them with `update_safety_lock.py --map`. |
| **10** | **Pin `requirements.txt`, remove unused deps (`paramiko`, `requests`, `beautifulsoup4`), and add an offline CLI smoke mode** | Medium | Low | Remove the 3 unused libraries from `requirements.txt`, pin all SDK versions for reproducible agent/Jenkins runs, and add a `make smoke` command that exercises `python crc.py --dry_run` offline against stubbed clients. |
