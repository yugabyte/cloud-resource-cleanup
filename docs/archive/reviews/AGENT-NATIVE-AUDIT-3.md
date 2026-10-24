# Independent Agent-Native Repository Audit #3 (`cloud-resource-cleanup`)

**Branch audited:** `fix/integration` (at commit `f8ed6dd0713741697d01b4d92df8a43eb237e17f`)  
**Previous audits:**  
- Round 1 (`f59ef39`): `origin/audit/agent-native:docs/refactor/reviews/AGENT-NATIVE-AUDIT.md` (**6.5 / 10**)  
- Round 2A (`0e2bf38`): `origin/audit/agent-native-2:docs/archive/reviews/AGENT-NATIVE-AUDIT-2.md` (**8.2 / 10**)  
- Round 2B (`0e2bf38`): `origin/audit/agent-native-2-gpt:docs/archive/reviews/AGENT-NATIVE-AUDIT-2-GPT.md` (**7.0 / 10**)  
**Date:** 2026-10-24  
**Verdict Score:** **9.1 / 10**

---

## Executive Summary & Score Justification (9.1 / 10)

Does `cloud-resource-cleanup` now genuinely look and operate like an "agent-native" repository that any coding agent (Claude Code, Codex, local Cursor) or newcomer without Jenkins access can maintain safely without a human writing code? **Yes—at `f8ed6dd`, CRC crosses the threshold from a refactored legacy repo into a reference-grade agent-native harness (9.1 / 10).** Since `0e2bf38` (37 commits across `AG-DOCS`, `AG-ENV`, `AG-ADAPT`, `AG-CLI`, `OWN-1`, `AD-SPOT`, `AD-VM`, `ST-1`, and `TD-1`), the repository closed nearly every structural blocker identified in both Round 2 audits: all 9 remaining legacy adapter classes and the 5 legacy safety test files are gone; the `pyproject.toml` legacy ratchet is **completely empty (`0` files)**; all **46 of 46 `SAF-NNN` invariants** are AST-hash-locked (`"gaps": []`) against engine-level tests; `constraints.txt` and SDK-independent `mypy --strict` overrides make `make check` reproducibly green (`44,596 passed, 1 skipped` in `69.8s`; `make check-fast` in `26.3s`; `make lint` in `0.46s`); all **47 production Jenkins commands** from `cloud-resource-cleanup-triggers` are tested offline in `tests/test_jenkins_contract.py`; `make smoke-offline` runs `python crc.py --dry_run` in subprocesses across all 17 production `(cloud, kind)` pairs with in-memory SDK fakes and socket-level network denial; `tests/helpers_ag_cli_added.py` unblocks additive CLI flags and resource kinds without editing frozen oracles; and `ARCH-012` + `todo-to-issue.yml` link every open high-risk bug in `BUGS.md` to a `TODO(BUG-NNN)` comment in `crc/`. What holds CRC at **9.1 / 10** instead of `9.8 / 10` is a small set of last-mile gaps: (1) late-wave doc drift in 6 files (`ARCHITECTURE.md:89`, `.cursor/rules/20-core.mdc:10`, `tests/archtest/README.md:24,64`, `crc/README.md:15`, `crc/azu/README.md:43`, and `tests/README.md:26-44,66`) that were not updated after `AG-ENV`, `OWN-1`, and `AD-VM`; (2) `AGENTS.md` and `ARCHITECTURE.md` not yet pointing agents to the new `tests/helpers_ag_cli_added.py` table, `make smoke-offline`, `constraints.txt`, or `tests/fixtures/jenkins_commands.txt`; (3) `ARCH-011` ignoring directory-relative backticked filenames inside package `README.md`s; (4) `docs/refactor/BUGS.md` still keeping 64 fixed rows and 306 low-risk rows (`25,000` words) in one active table; and (5) Cursor-only skill paths (`.cursor/skills/`) without `.claude/skills` / `.agents/skills` symlinks for Claude Code and Codex.

---

## 0. Disposition of Both Second-Round Audits' Top-10 Lists at `f8ed6dd`

### 0.1 Round 2A (`AGENT-NATIVE-AUDIT-2.md`, 8.2 / 10) — Top 10 Status

| # | Round 2A Recommendation | Status | Evidence at `f8ed6dd` |
|--:|-------------------------|:------:|-----------------------|
| **1** | **Fix post-`WB-1` doc lies in `AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, and the 7 package `README.md`s** | **Done** | Commit `126420f` (`AG-DOCS`) and `ce44e43`/`4ea1c08`/`884be0c` (`AG-ADAPT`) removed all deleted `HANDLERS`, `vm_ops.py`, `resource_ops.py`, `AWS_Disk`, `GCP_Disk`, and `CRC.delete_disks` references from `AGENTS.md:92-97`, `ARCHITECTURE.md:10-48,100-129`, and `CLEANUP_SAFETY.md:50-54,121,130,259`, and updated all 7 package `README.md`s (`crc/aws/README.md:44`, `crc/aws/vm/README.md:44`, `crc/azu/README.md:38`, `crc/azu/vm/README.md:45`, `crc/gcp/README.md:25,53-54`, `crc/oci/{disk,vm}/README.md:39,36`) to state that `get_retention_age` (`BUG-004`) fails closed via `KEEP_AGE`. |
| **2** | **Extend `ARCH-011` (`doc_links.py`) to scan all non-archived `.md` files and backticked repo paths** | **Partly Done** | Commit `126420f` updated `tests/archtest/doc_links.py:37-113` (`_documents`, `_repo_paths`) to scan every non-archived `.md`/`.mdc` file recursively and verify backticked paths starting with `("crc", "tests", "docs", ".cursor", ".github")`. **Still open:** `_repo_paths` ignores directory-relative backticked filenames (no slash prefix), so `tests/README.md:26,42-44` still names the 5 deleted `test_*_spot_vm.py` / `test_aws_disk.py` files without failing `ARCH-011`. |
| **3** | **Repoint `safety_lock.json` to `tests/test_*_engine.py`, map the 16 `SAF-NNN` gaps, and delete the last 9 legacy adapters** | **Done** | Commits `827f100` and `042276f` (`OWN-1`) moved all safety locks to engine/gap tests (`tests/archtest/safety_lock.json` locks **46/46 `SAF-NNN` IDs**, `"gaps": []`), deleted the 5 legacy safety test files, and emptied the legacy ratchet in `pyproject.toml:23,98`. Commits `ce44e43`, `4ea1c08`, `71f5741`, and `154dc27` (`AG-ADAPT`, `AD-SPOT`, `AD-VM`) wired `crc/cli/scan.py` directly to `crc.aws.vm.scan.scan_tag`, deleted all 9 remaining adapter classes (`Disk`, `SpotInstanceRequests`, 3 `SpotVM`, 4 `VM`), and removed dead `build_selection`, `nic_name_included`, and `gcp_ip_exception_excludes`. |
| **4** | **Unblock additive CLI flags & resources in `test_cli_args_parity.py`, `test_cli_main_parity.py`, and `test_cli_bridge_parity.py`** | **Partly Done** | Commits `5515ade` and `ca4cc44` (`AG-CLI`) added `tests/helpers_ag_cli_added.py` (`ADDED_FLAGS`, `ADDED_CHOICES`, `ADDED_RESOURCES`, `RESOURCE_HELP`) and `tests/helpers_ag_cli_args.py` (`check_additions`, `strip_additions`), wired into `test_cli_args_parity.py`, `test_cli_main_parity.py`, `test_cli_bridge_parity.py`, and `test_core_selection.py`. **Still open:** `AGENTS.md:106-108`, `ARCHITECTURE.md:126-127`, and `crc/cli/README.md:82-85` were not updated to mention `tests/helpers_ag_cli_added.py` (`AGENTS.md:107` still says `test_cli_args_parity.py` *"fails it until that test gains an allowance for additions"*). |
| **5** | **Add a golden manifest of the production Jenkins CLI commands (`tests/test_jenkins_contract.py`)** | **Done** | Commits `a0b3d09` and `5515ade` added `tests/fixtures/jenkins_commands.txt` (all **47** production commands from `yugabyte/cloud-resource-cleanup-triggers` at `8ceee47`), `tests/helpers_ag_cli_jenkins.py`, `tests/helpers_ag_cli_jenkins_expected.json`, and `tests/test_jenkins_contract.py` (142 parameterized tests). |
| **6** | **Archive fixed/low-risk quirk rows from `docs/refactor/BUGS.md` and unify the `file:line` rule** | **Partly Done** | Commit `126420f` unified the `file:line` rule across `AGENTS.md:70-72`, `BUGS.md:4-11`, `docs/refactor/README.md:20-21`, `docs/refactor/bugs/README.md:15-16`, `fix-bug.md:6-8`, and `tests/archtest/bug_refs.py:14-16` (new rows cite current `crc/` `file:line`; old rows keep `Location on main`). **Still open:** `docs/refactor/BUGS.md` was not split or pruned; it still holds **403 rows (455 lines, 25,000 words)** in one flat table, including 64 fixed rows and 306 low-risk rows. |
| **7** | **Close the remaining `ARCH-003` loopholes (`MAX_AGE` env read in `crc/core`, clock attribute refs, `print` in `crc/notify`)** | **Partly Done** | Commit `08a2dc6` (`AG-ENV`) moved `MAX_AGE` env reading to `crc/cli/context.py` (`set_max_age_source`), banned `"env"` and `"clock_ref"` in `crc/core` and `"clock_ref"` in `crc/cli` (`tests/archtest/bans.py:22-23`), and added import-alias tracking in `_clock_refs()` (`b922412`). **Still open:** `clock_ref` is only reported (`cloud_clock_refs`), not banned, in `crc/{aws,azu,gcp,oci}` (4 hits remain: `crc/aws/spot_instance_requests/runtime.py:21`, `crc/gcp/spot_vm/cleaner.py:165,188`, `crc/gcp/spot_vm/disks.py:112`), `crc/notify` still uses `print()`, and 3 docs (`ARCHITECTURE.md:89`, `20-core.mdc:10`, `tests/archtest/README.md:24`) still claim `crc/core/age.py` reads `MAX_AGE` from `os.environ`. |
| **8** | **Pin `requirements.txt`, remove unused deps (`paramiko`, `requests`, `beautifulsoup4`), and add `ruff format --check .`** | **Partly Done** | Commit `042276f` (`OWN-1`) dropped `paramiko`, `requests`, and `beautifulsoup4` from `requirements.txt`. Commit `08a2dc6` (`AG-ENV`) added `constraints.txt` (wired into `.github/workflows/ci.yml:20` and `.cursor/environment.json:2`). Commits `7c441ae`..`ac94ffd` (`ST-1`) added `DTZ BLE G LOG C4 RET N` ruff rules and `ruff format --check .` to `Makefile:15` and `ci.yml:21`. **Still open:** `AGENTS.md:17` and `README.md:89` omit `-c constraints.txt` from their `pip install` command (`constraints.txt` also still carries dead `paramiko`/`beautifulsoup4` pins), and `pyproject.toml:94` still excludes `^crc\.py$` from `mypy`. |
| **9** | **Add an offline CLI smoke target (`make smoke` / `--offline-smoke`)** | **Done** | Commit `f922412` (`AG-ENV`) added `make smoke-offline` (`Makefile:21-22`), `scripts/smoke_offline.py`, `scripts/offline_hook/sitecustomize.py` (in-memory SDK fakes + socket-level network deny), and `tests/test_smoke_offline.py`. Passes `17/17` `(cloud, kind)` pairs in `32.3s`. |
| **10** | **Rebuild the Cloud Agent VM snapshot with Python 3.11 `.venv` pre-baked** | **Done / N/A** | Owner decided Cursor Cloud Agents are not part of the long-term workflow. For any local/CLI agent, `.cursor/environment.json` and `ci.yml` now use `constraints.txt` and SDK-independent `mypy` overrides (`pyproject.toml:118-129`). |

**Summary for Round 2A:** **5 Done, 5 Partly Done, 0 Open.**

### 0.2 Round 2B (`AGENT-NATIVE-AUDIT-2-GPT.md`, 7.0 / 10) — Top 10 Status

| # | Round 2B (GPT) Recommendation | Status | Evidence at `f8ed6dd` |
|--:|-------------------------------|:------:|-----------------------|
| **1** | **Make the prepared environment and CI reproducibly green: pin/lock runtime SDKs, add deliberate mypy overrides or stubs, and verify `make check`** | **Done** | Commit `08a2dc6` (`AG-ENV`) added `constraints.txt` and `[[tool.mypy.overrides]]` (`ignore_missing_imports = true` for `google.*`, `azure.*`, `msrestazure.*`, `msrest.*` in `pyproject.toml:118-129`). `make lint` passes in `0.46s` (370 files in `mypy`, 0 errors) and `make check` passes (`44,596 passed, 1 skipped`). |
| **2** | **Replace raw frozen-parser equality with an explicit compatibility contract that permits additive choices/flags; reduce the three combinatorial CLI parity suites** | **Partly Done** | Commits `5515ade` and `ca4cc44` (`AG-CLI`) added the additive compatibility contract (`tests/helpers_ag_cli_added.py`, `tests/helpers_ag_cli_args.py`). **Still open:** The 3 slow combinatorial CLI parity files (`test_cli_{main,crc,args}_parity.py`) were not reduced (still **30,370 of 44,597 tests, 68.1%**), and `AGENTS.md:106-108` does not mention `helpers_ag_cli_added.py`. |
| **3** | **Delete unused adapters, exports, dead policy helpers, and adapter-only tests; keep a narrowly named scan client where scan still needs one** | **Done** | All 9 adapter classes and 5 legacy adapter safety test files were deleted (`OWN-1`, `AG-ADAPT`, `AD-SPOT`, `AD-VM`); `crc/cli/scan.py:48` calls `crc.aws.vm.scan.scan_tag` directly; dead `build_selection`, `nic_name_included`, and `gcp_ip_exception_excludes` were deleted (`4ea1c08`). |
| **4** | **Correct the current safety doc drift, then generate route/support/SAF/bug-status facts instead of copying them across READMEs** | **Partly Done** | Commit `126420f` fixed all `WB-1` and `BUG-004` safety doc drift and extended `ARCH-011`. **Still open:** Facts are still hand-copied across READMEs rather than generated or checked, and drifted again after `AG-ENV`/`OWN-1`/`AD-VM` in `ARCHITECTURE.md:89`, `20-core.mdc:10`, `tests/archtest/README.md:24,64`, `crc/README.md:15`, `crc/azu/README.md:43`, and `tests/README.md:26-44,66`. |
| **5** | **Add a network-denied offline CLI simulator and `make smoke-offline` covering one safe fixture per cloud and resource family** | **Done** | Commit `f922412` (`AG-ENV`) added `make smoke-offline`, `scripts/smoke_offline.py`, `scripts/offline_hook/sitecustomize.py` (socket-denied, faked SDKs), and `tests/test_smoke_offline.py` covering all 17 production `(cloud, kind)` pairs. |
| **6** | **Import the production Jenkins command matrix as a versioned fixture and test parse, validation, routing, and dry-run planning** | **Done** | Commits `a0b3d09` and `5515ade` added `tests/fixtures/jenkins_commands.txt` (47 commands) and `tests/test_jenkins_contract.py` (142 tests covering parse, validation, routing, and offline execution). |
| **7** | **Move open bugs to queryable per-package files or GitHub issues; archive resolved rows and remove obsolete `main` locations from the active map** | **Partly Done** | Commit `58bbed6` (`TD-1`) added `TODO(BUG-NNN)` comments in `crc/` for all 10 open/partly-fixed critical/high rows, enforced 1-to-1 sync via `ARCH-012` (`tests/archtest/todo_bugs.py`), and added `.github/workflows/todo-to-issue.yml`. **Still open:** `docs/refactor/BUGS.md` still retains all 403 rows (64 fixed, 306 low-risk) in one 25,000-word table. |
| **8** | **Define resource kinds once in typed metadata and derive parser choices, supported pairs, routes/labels validation, and reference docs from it** | **Open** | Resource kinds remain manually duplicated across 7 files (`crc/cli/args.py`, `crc/cli/constants.py`, `crc/core/selection.py`, `crc/cli/pairs.py`, `crc/cli/routes/<cloud>.py`, `crc/core/report.py` + `engine.py`, and `tests/helpers_ag_cli_added.py`). |
| **9** | **Replace “owner must edit” dead ends with an agent-generated guardrail-change workflow requiring review/approval and showing old/new hashes plus rationale** | **Open** | `AGENTS.md:63-67`, `.cursor/rules/30-safety.mdc:11-13`, `review.md:10-12`, and `.github/CODEOWNERS` still instruct agents to stop and paste proposed changes in the PR description whenever a locked test or frozen manifest must change. |
| **10** | **Add repository evals and observability: representative maintenance tasks, flaky-test/timing reports, agent-run evidence, formatter, and security/dependency gates** | **Partly Done** | Commit `ac94ffd` (`ST-1`) added `ruff format --check .` and stricter ruff lint rules (`DTZ BLE G LOG C4 RET N`) to `Makefile` and `ci.yml`. **Still open:** No dependency/security scanner (`pip-audit`/`bandit`), no pytest timing report (`--durations`), and no agent task eval suite. |

**Summary for Round 2B:** **4 Done, 4 Partly Done, 2 Open.**

---

## 1. Day-One Agent & Newcomer Walkthrough at `f8ed6dd`

### 1.1 Context & Token Tax Before Making a Safe Change

Starting at `AGENTS.md` (`CLAUDE.md` is a symlink to it) and following its map to edit a single cloud cleaner (e.g., `crc/aws/keypairs/`):

| Tier | Files Read | Lines | Words | Est. Tokens |
|------|-----------:|------:|------:|------------:|
| **1. Always-On Entry & Scoped Rules** (`AGENTS.md`, `.cursor/rules/00-map.mdc`..`30-safety.mdc`) | 5 | 186 | 1,622 | ~2,150 |
| **2. Core Architecture, Safety & Playbooks** (`ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `docs/logging.md`, `tests/archtest/README.md`, `.cursor/skills/crc-maintain/{SKILL.md,fix-bug.md,review.md}`) | 8 | 672 | 6,096 | ~8,100 |
| **3. Target Package Context** (`crc/aws/README.md`, `crc/aws/keypairs/README.md`, `crc/aws/keypairs/{cleaner,client}.py`, `crc/cli/routes/aws.py`) | 5 | 405 | 3,180 | ~4,250 |
| **Total to Safely Edit One Cleaner** | **18** | **1,263** | **10,898** | **~14,500** |
| *Optional Human CLI Docs* (`README.md`) | 1 | 261 | 3,149 | ~4,200 |
| *Total Active Markdown in Repo (excl. `docs/archive/`)* | 53 | 3,557 | 62,131 | ~82,800 |
| *Archived Refactor History (`docs/archive/`, never loaded unless needed)* | 115 | — | 201,065 | ~268,000 |

For a bug fix or single-cleaner edit, reading **~5,500–10,900 words (~7.3k–14.5k tokens)** across progressive hops is well within the comfort zone of Claude Code, Codex, and local Cursor sessions—provided the agent greps `docs/refactor/BUGS.md` (`rg "BUG-NNN"`) or `TODO(BUG-NNN)` comments rather than reading `BUGS.md` (`25,000` words) top-to-bottom.

### 1.2 Where a New Agent or Newcomer Without Jenkins AccessStill Hits Friction

1. **Missing `-c constraints.txt` in `AGENTS.md:17` and `README.md:89`:**
   - `AGENTS.md:17` tells a day-one agent or newcomer to run `pip install -r requirements.txt -r requirements-dev.txt`, omitting `-c constraints.txt` (which `.github/workflows/ci.yml:20` and `constraints.txt:2` require for reproducible SDK versions).
2. **Undocumented `tests/helpers_ag_cli_added.py` in `AGENTS.md`, `ARCHITECTURE.md`, and `crc/cli/README.md`:**
   - `AGENTS.md:106-108` and `crc/cli/README.md:82-85` still state: *"`tests/test_cli_args_parity.py` asserts the parser equals the frozen oracle `tests/legacy/crc_main.txt` flag for flag, so a new flag fails it until that test gains an allowance for additions"*. In reality, `AG-CLI` (`5515ade`) **already built** that allowance in `tests/helpers_ag_cli_added.py` (`ADDED_FLAGS`, `ADDED_CHOICES`, `ADDED_RESOURCES`, `RESOURCE_HELP`), but none of `AGENTS.md`, `ARCHITECTURE.md:115-128`, or `crc/cli/README.md` mention `tests/helpers_ag_cli_added.py`!
3. **Hidden Offline Jenkins Tooling for Newcomers Without Jenkins Access:**
   - `AGENTS.md:102-104` tells agents to *"search the `cloud-resource-cleanup-triggers` repo (`Jenkinsfile` and job definitions) for the flag with `gh search code` or a clone; if you cannot see that repo, treat every flag as in use."* It never mentions that all **47 production Jenkins commands** from `cloud-resource-cleanup-triggers` are already checked into `tests/fixtures/jenkins_commands.txt` and verified by `tests/test_jenkins_contract.py` and `make smoke-offline`!
4. **Six Fresh Post-`AG-ENV` / Post-`OWN-1` / Post-`AD-VM` Doc Lies (Detailed in Section 3.4):**
   - `ARCHITECTURE.md:89`, `.cursor/rules/20-core.mdc:10`, and `tests/archtest/README.md:24` still say `crc/core/age.py` reads `MAX_AGE` from the environment; `tests/archtest/README.md:64` still says *"30 of 46 IDs are locked"*; `crc/README.md:15` and `tests/README.md:42-44,66` still say `pyproject.toml` exempts 5 hash-locked adapter safety test files; and `crc/azu/README.md:43` still says the deleted `VM` adapter class stays importable.

---

## 2. 2025–2026 Industry Guidance on Agent-Native Repositories

Current 2025–2026 guidance across OpenAI, Anthropic, Cursor, Factory, and the `AGENTS.md` ecosystem converges on six concrete engineering patterns:

1. **OpenAI — "Harness Engineering: Leveraging Codex in an Agent-First World", "Using Skills to Accelerate OSS Maintenance" & "Rethinking Skills and Prompts" (2026)**
   - Sources: [https://openai.com/index/harness-engineering/](https://openai.com/index/harness-engineering/), [https://developers.openai.com/blog/skills-agents-sdk](https://developers.openai.com/blog/skills-agents-sdk), [https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra), [https://walkinglabs.github.io/learn-harness-engineering/en/harness-designs/codex/](https://walkinglabs.github.io/learn-harness-engineering/en/harness-designs/codex/)
   - *Guidance:* Keep `AGENTS.md` to ~100 lines as a directory page/map, not an encyclopedia. Avoid prompting agents to read stacks of docs before every edit; point contextually. Encode architectural layers, file/function sizes, and domain invariants into fast AST linters whose failure output includes `REMEDIATION:` instructions. Continuously run automated "doc-gardening" checks so docs never drift after refactors, and delete legacy migration shims so agents never copy dead patterns.
2. **The `AGENTS.md` Open Standard & Multi-Tool Agent Starter Kits (2025–2026)**
   - Sources: [https://github.com/openai/agents.md/](https://github.com/openai/agents.md/), [https://agents.md/](https://agents.md/), [https://hussamahmed.com/ai/observatory/starter-kits](https://hussamahmed.com/ai/observatory/starter-kits), [https://developertoolkit.ai/en/teams/adopt/project-conversion/](https://developertoolkit.ai/en/teams/adopt/project-conversion/)
   - *Guidance:* Maintain one canonical `AGENTS.md` at the repo root with thin shims for tool-specific entry points (`CLAUDE.md` symlink/import for Claude Code, path-scoped `.cursor/rules/*.mdc` for Cursor, `.claude/skills` / `.agents/skills` for cross-agent skill discovery). Every command and workflow in `AGENTS.md` must be verified against the live repo.
3. **Anthropic — Claude Code Best Practices, Skills & Large-Codebase Navigation (2025–2026)**
   - Sources: [https://code.claude.com/docs/en/best-practices](https://code.claude.com/docs/en/best-practices), [https://code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills), [https://code.claude.com/docs/en/sub-agents](https://code.claude.com/docs/en/sub-agents), [https://claude.com/resources/articles/how-claude-code-works-in-large-codebases-best-practices-and-where-to-start](https://claude.com/resources/articles/how-claude-code-works-in-large-codebases-best-practices-and-where-to-start)
   - *Guidance:* Treat context window capacity as the primary constraint; prune instructions that linters already enforce or that repeat code. Put repeatable procedures (bug fixing, PR review) into on-demand Skills (`SKILL.md` following the Agent Skills open standard in `.claude/skills/` or `.cursor/skills/`). Provide fast per-file test commands and version-controlled permission/ignore rules.
4. **Cursor — Best Practices for Coding with Agents, Rules & Skills (2026)**
   - Sources: [https://cursor.com/blog/agent-best-practices](https://cursor.com/blog/agent-best-practices), [https://cursor.com/docs/rules](https://cursor.com/docs/rules), [https://cursor.com/docs/skills](https://cursor.com/docs/skills)
   - *Guidance:* Keep `.cursor/rules/*.mdc` short (~15–25 lines), path-scoped via `globs`, and pointing to canonical modules (`crc/aws/keypairs/`) instead of duplicating prose across files.
5. **Factory AI & Devin — Agent Readiness Pillars & Linter-Directed Agents (2026)**
   - Sources: [https://docs.factory.com/agent-readiness/overview](https://docs.factory.com/agent-readiness/overview), [https://docs.devin.ai/cli/extensibility/rules](https://docs.devin.ai/cli/extensibility/rules), [https://agentfriendlycode.com/check](https://agentfriendlycode.com/check)
   - *Guidance:* Require reproducible dependency locks (`constraints.txt`), sub-second lint gates, `<30s` fast test loops, network-isolated offline CLI smoke tests, and automated issue/todo tracking.

---

## 3. Blunt Comparison of CRC (`f8ed6dd`) Against 2025–2026 Guidance

### 3.1 Entry Docs: Short, Accurate, and Progressive?

- **What is exemplary:** `AGENTS.md` is **116 lines** (mechanically capped at `<= 120` lines by `ARCH-010`), `CLAUDE.md` is a symlink to it, `.cursor/rules/00-map.mdc`..`30-safety.mdc` are **16–20 lines each** (capped at `<= 40` lines) with proper `globs:` scoping, and `.cursor/skills/crc-maintain/{SKILL.md,fix-bug.md,review.md}` are **52, 34, and 46 lines**. `AGENTS.md:30-33` now accurately distinguishes `archtest` failures (which print `REMEDIATION:`) from `ruff`/`mypy`/`pytest` failures.
- **What still needs fixing:**
  1. **Cross-agent skill discovery gap:** Task playbooks live only under `.cursor/skills/crc-maintain/`. While `AGENTS.md:53-54` links them in Markdown, Claude Code natively discovers skills in `.claude/skills/` and Codex/Agent-Skills tools look in `.agents/skills/`. Adding `.claude/skills` and `.agents/skills` symlinks to `../.cursor/skills` makes `/crc-maintain` native in all tools.
  2. **Missing pointers in `AGENTS.md`:** `AGENTS.md` omits `-c constraints.txt` (`:17`), `make smoke-offline` (`:16-23`), `tests/fixtures/jenkins_commands.txt` + `tests/test_jenkins_contract.py` (`:100-109`), and `tests/helpers_ag_cli_added.py` (`:106-109`).

### 3.2 Mechanical Enforcement vs. Prose Rules

CRC's mechanical harness is in the top 1% of Python repositories:

| Invariant / Rule | Enforcement | Status & Remaining Loopholes at `f8ed6dd` |
|------------------|-------------|-------------------------------------------|
| Legacy ratchet shrinks; `extend-exclude` == `mypy` overrides (`ARCH-001`) | **Mechanical** | **Ratchet is now empty (`0` files in `pyproject.toml:23`)!** Minor cleanup: `tests/archtest/legacy_baseline.txt` still lists 21 historical filenames, so a regression could re-add one of those 21 if `legacy_baseline.txt` isn't emptied too. |
| Package `README.md` 4 sections & `Public API` symbol sync (`ARCH-002`) | **Mechanical** | Enforced across all 24 `crc/` packages and `tests/`. Top-level `crc/{service,utils,notifications}.py` remain manual (`crc/README.md:19`). |
| Per-package bans on `re`, `clock`, `clock_ref`, `env`, `print`, `sys.exit` (`ARCH-003`) | **Mechanical (Partial in cloud/notify)** | `crc/core` now bans `clock`, `clock_ref`, `env`, `print`, and `sys.exit` (`bans.py:22`); `crc/cli` bans `re` and `clock_ref`. **Two remaining gaps:** (1) `clock_ref` is only reported (`cloud_clock_refs`), not failed, in `crc/{aws,azu,gcp,oci}` (**4 hits remain**: `crc/aws/spot_instance_requests/runtime.py:21`, `crc/gcp/spot_vm/cleaner.py:165,188`, `crc/gcp/spot_vm/disks.py:112`); (2) `crc/notify` still permits `print` (`slack_owners.py:32,48,68`, `influx.py:89`). |
| Non-test files `<= 250` lines, functions `<= 60` lines (`ARCH-004`) + `ruff` (`C901 <= 10`, `PLR0915 <= 40`, `PLR0913 <= 6`) | **Mechanical** | Enforced across 100% of `crc/` (since the legacy ratchet is now empty). |
| `plan()` is read-only across module & sibling helpers (`ARCH-005`) | **Mechanical** | Traverses `self` methods, same-module functions, and transitive sibling-module function imports (`readonly_plan.py:240-275`). |
| No `async`/`await`, `asyncio`, `pydantic`, `httpx`, `tenacity`, `threading` (`ARCH-006`) | **Mechanical** | Enforced across all `crc/` and `crc.py`. |
| SHA-256 lock on `tests/legacy/*.txt` & `requirements.txt` (`ARCH-007`) | **Mechanical** | Solid (`frozen_inputs.json`). Note: `constraints.txt` is **not** in `frozen_inputs.py:21-26`. |
| Monotonic test-count floor (`ARCH-008`, floor `44,597`) | **Mechanical** | Enforced in `make check` (`test_count.py`). |
| Every `BUG-NNN` in `crc/**` and `tests/**` exists in `BUGS.md` (`ARCH-009`) | **Mechanical** | Solid (`bug_refs.py`), with updated remediation text matching `AGENTS.md` Rule 5. |
| Entry doc line budgets (`ARCH-010`) | **Mechanical** | Caps `AGENTS.md <= 120` (currently 116), `.cursor/rules/*.mdc <= 40`, `.cursor/skills/*/SKILL.md <= 150`. Companion skill files (`fix-bug.md`, `review.md`) and `ARCHITECTURE.md` are unbudgeted. |
| Live Markdown links and backticked repo paths resolve (`ARCH-011`) | **Mechanical (1 Loophole)** | Scans all 53 non-archived `.md`/`.mdc` files and checks `[text](link)` plus backticked `crc/...`, `tests/...`, `docs/...`, `.cursor/...`, `.github/...` paths (`doc_links.py:15-21`). **Loophole:** Ignores backticked filenames without a root prefix (e.g., `` `test_aws_disk.py` `` inside `tests/README.md:43`). |
| `TODO(BUG-NNN)` 1-to-1 sync with open critical/high rows in `BUGS.md` (`ARCH-012`) | **Mechanical** | Enforced across `crc/` (`todo_bugs.py`), paired with `.github/workflows/todo-to-issue.yml` (10 `TODO(BUG-NNN)` comments currently active). |
| All 46 `[SAF-NNN]` invariants AST-hash-locked (`SAF-LOCK`) | **Mechanical** | **46 of 46 `SAF-NNN` IDs locked** across 69 test functions (`safety_lock.json`, `"gaps": []`). |
| Lint, formatting & strict typing (`ruff check`, `ruff format --check`, `mypy --strict`, `import-linter`) | **Mechanical** | 19 ruff rule sets (`E F W I B UP FURB SIM PERF C901 PLR RUF DTZ BLE G LOG C4 RET N`), 371 formatted files, 370 mypy-strict files, 5 import-linter contracts. Minor gap: `pyproject.toml:94` still excludes `^crc\.py$` from `mypy`. |
| Jenkins production CLI contract & offline CLI smoke (`test_jenkins_contract.py`, `make smoke-offline`) | **Mechanical** | 47 production commands tested in `pytest`; 17 `(cloud, kind)` pairs tested in subprocesses with socket-level network denial (`sitecustomize.py`). Minor gap: `make smoke-offline` is not invoked in `.github/workflows/ci.yml`. |

**Rules that remain prose-only (appropriately or by necessity):** (1) Fail-closed domain semantics for new code (`AGENTS.md` Rule 2); (2) not semantically weakening unlocked assertions while keeping test count flat (`AGENTS.md` Rule 3); (3) keeping prose behavioral claims in `README.md`s and `CLEANUP_SAFETY.md` synchronized with code (`AGENTS.md` Rules 4 & 8); and (4) scoping PRs to one bug (`AGENTS.md` Rule 5).

### 3.3 Feedback Loops: Speed and Clarity of `make check`

Measured on this 4-core VM at `f8ed6dd` (after a `33.3s` initial `pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt`):

| Command | Measured Wall Time | What Ran & Result |
|---------|-------------------:|-------------------|
| `make lint` | **0.46s** | `ruff check .` (pass), `ruff format --check .` (371 files), `mypy` (370 files, 0 issues), `lint-imports` (197 files, 964 dependencies, 5 contracts kept) |
| `make test-file F=tests/archtest` | **4.65s** | **213 passed** (`ARCH-001..012` + `SAF-LOCK` real & `tmp_path` self-tests) |
| `make check-fast` | **26.26s** | `make lint` + `pytest -q -n auto` excluding the 3 slow CLI parity files (**14,227 passed in 25.42s**) |
| `make smoke-offline` | **32.26s** | 17 subprocess runs of `python crc.py ... --dry_run` with faked SDKs and sockets denied (**17/17 pairs passed**) |
| `make check` | **1m 09.77s (69.77s)** | `make lint` + full `pytest -q -n auto` (**44,596 passed, 1 skipped in 52.33s**) + `ARCH-008` (**44,597 collected, floor 44,597**) |

- **Speed assessment:** `make lint` (`0.46s`), `make test-file` (`1–5s`), and `make check-fast` (`26.3s`) give agents a fast, high-confidence inner loop, and `AGENTS.md:26-27` now states realistic timings (`24 s` and `61 s`).
- **Failure clarity:** Every `ARCH-001..012` and `SAF-LOCK` violation prints its rule ID, exact `file:line`, and a self-contained `REMEDIATION:` paragraph. Furthermore, the new `tests/helpers_ag_cli_args.py` assertions print `ADDED_HINT` (`"add it to the ADDED table in tests/helpers_ag_cli_added.py (with default and help)"`), which is a model of agent-ergonomic test design.

### 3.4 Documentation Drift: 8 Spot-Checked Claims vs. Actual Code

While `126420f` (`AG-DOCS`) fixed all doc drift from Audit 2, later commits (`08a2dc6` `AG-ENV`, `042276f` `OWN-1`, `154dc27` `AD-VM`, `17d30ef` `AD-SPOT`, and `5515ade` `AG-CLI`) landed **after** `AG-DOCS` and introduced 6 new doc-drift items:

1. **Spot-Check 1 — `tests/archtest/README.md:62-64` (`SAF coverage` count and gap list):**
   - *Doc claim:* *"30 of 46 IDs are locked. Gaps (no existing test covers them; not invented here): SAF-001, SAF-002, SAF-004, SAF-005, SAF-006, SAF-009, SAF-010, SAF-014, SAF-016, SAF-021, SAF-022, SAF-023, SAF-024, SAF-034, SAF-036, SAF-037."*
   - *Code reality (`tests/archtest/safety_lock.json:1-473`):* **FALSE.** Commit `827f100` (`OWN-1`) locked all 16 gaps; **46 of 46 `SAF-NNN` IDs are locked** and `"gaps": []`.
2. **Spot-Check 2 — `ARCHITECTURE.md:89`, `.cursor/rules/20-core.mdc:10`, and `tests/archtest/README.md:24` (`MAX_AGE` env read in `crc/core`):**
   - *Doc claim:* `ARCHITECTURE.md:89` says *"One exception: `is_old` reads the `MAX_AGE` environment variable"*; `.cursor/rules/20-core.mdc:10` says *"(existing exception: `is_old` reads `MAX_AGE`; add no new env read)"*; `tests/archtest/README.md:24` says *"`crc/core` bans wall-clock reads, `print`, `sys.exit` (no env ban: `crc/core/age.py` reads `MAX_AGE`)"*.
   - *Code reality (`crc/core/age.py:3-6`, `tests/archtest/bans.py:22`):* **FALSE.** Commit `08a2dc6` (`AG-ENV`) removed `os.getenv("MAX_AGE")` from `crc/core/age.py` (replacing it with `set_max_age_source` injected by `crc/cli/context.py`) and `PACKAGE_BANS["crc/core"]` now bans both `"env"` and `"clock_ref"`.
3. **Spot-Check 3 — `crc/README.md:15` and `tests/README.md:26,29,42-44,66` (5 hash-locked adapter test files & legacy ratchet):**
   - *Doc claim:* `crc/README.md:15` says *"`pyproject.toml` lists only five hash-locked safety test files"*; `tests/README.md:42-44` says *"Five test files are the hash-locked safety tests (the only entries left in the legacy ratchet, so ruff and mypy skip them): `test_aws_disk.py`, `test_aws_spot_instance_requests.py`, `test_azu_spot_vm.py`, `test_gcp_spot_vm.py`, `test_oci_spot_vm.py`. They build the adapter classes directly"*.
   - *Code reality (`pyproject.toml:23,98`, `tests/`):* **FALSE.** Commit `042276f` (`OWN-1`) deleted all 5 files and emptied the legacy ratchet (`extend-exclude = []`).
4. **Spot-Check 4 — `crc/azu/README.md:43` (`VM` adapter class importable):**
   - *Doc claim:* *"The `VM` class stays importable for its tests."*
   - *Code reality (`crc/azu/vm/__init__.py`):* **FALSE.** Commit `d330a98` (`AD-VM`) deleted `crc/azu/vm/adapter.py` and `VM`; only `VmCleaner` and `build_vm_cleaner` exist.
5. **Spot-Check 5 — `AGENTS.md:106-108` and `crc/cli/README.md:82-85` (CLI flag allowance in `test_cli_args_parity.py`):**
   - *Doc claim:* Says `tests/test_cli_args_parity.py` asserts exact equality with `crc_main.txt`, *"so a new flag fails it until that test gains an allowance for additions"*.
   - *Code reality (`tests/helpers_ag_cli_added.py`, `tests/test_cli_args_parity.py:559,570-606`):* **STALE.** Commit `5515ade` (`AG-CLI`) already added that allowance (`ADDED_FLAGS`, `ADDED_CHOICES`, `ADDED_RESOURCES` in `tests/helpers_ag_cli_added.py`).
6. **Spot-Check 6 — `docs/refactor/BUGS.md:441` (`## Counts` summary line):**
   - *Doc claim:* *"403 rows: critical 5, high 27, medium 65, low 306. 63 fixed and 5 partly fixed..."*
   - *Code reality (`docs/refactor/BUGS.md` rows):* **STALE by 1.** Commit `17d30ef` (`AD-SPOT`) marked `BUG-409` (`:439`) as `fixed in ENG-CLEAN`, bringing fixed rows to **64** (with 5 partly fixed and 334 open), without updating the summary count at line 441.
7. **Spot-Check 7 — `CLEANUP_SAFETY.md` (`SAF-001..046`) and the 7 package `README.md`s on `BUG-004` (`get_retention_age`):**
   - *Doc claim:* All 7 package `README.md`s and `CLEANUP_SAFETY.md:71-80` state that `get_retention_age` fails closed via `KEEP_AGE` and records `skip("bad_age_tag")`.
   - *Code reality (`crc/core/age_tags.py:114-145`):* **TRUE.**
8. **Spot-Check 8 — `ARCHITECTURE.md:10-48` (Engine-only dispatch & zero adapters in `crc/`):**
   - *Doc claim:* Every `(cloud, kind)` pair runs through `crc/cli/bridge.py` -> `crc.core.engine.run()`, and only `Cleaner` implementations remain in `crc/<cloud>/`.
   - *Code reality (`crc/`):* **TRUE.** Zero adapter classes remain anywhere in `crc/`.

### 3.5 Over-Engineering: Context Tax Without Payback

1. **`docs/refactor/BUGS.md` (455 lines, 25,000 words, 403 rows):** Still represents **40.2% of all active Markdown words** in the repository. Now that all 10 open/partly-fixed critical/high bugs have in-code `TODO(BUG-NNN)` comments enforced by `ARCH-012` and synced to GitHub Issues via `todo-to-issue.yml`, keeping 64 fixed refactor rows and 306 low-risk/cosmetic `main`-branch quirk descriptions in an active 25,000-word flat table is pure context bloat.
2. **30,370 Combinatorial CLI Oracle Parity Tests (`tests/test_cli_{main,crc,args}_parity.py`) Locked by `ARCH-008`:** These 3 files account for **68.1% of all 44,597 tests** and ~27s of `make check` runtime even with `pytest-xdist`. Worse, because `ARCH-008` (`test_count_floor.txt = 44597`) forbids lowering the collected test count, an agent can never consolidate or retire redundant oracle matrix permutations without an owner manually editing `test_count_floor.txt`.
3. **Prose Safety Duplication Across 26 `crc/**/README.md` Files:** `crc/aws/README.md` (`1,938` words) and `crc/gcp/README.md` (`1,650` words) inline dense 300-word single-line paragraphs repeating every detail of their subpackages' `README.md`s and `CLEANUP_SAFETY.md`. When a detail changes, agents must update 3–4 prose copies by hand.

### 3.6 What Is Missing That Agent-Native Repos Usually Have

- **What is now present (and impressive):** Issue & PR templates (`.github/ISSUE_TEMPLATE/*.md`, `PULL_REQUEST_TEMPLATE.md`), `TODO(BUG-NNN)` -> GitHub Issue automation (`todo-to-issue.yml` + `ARCH-012`), production Jenkins contract suite (`test_jenkins_contract.py` over 47 commands), network-denied offline CLI smoke runner (`make smoke-offline`), and reproducible `constraints.txt`.
- **What is still missing:**
  1. **Cross-agent skill & permission shims (`.claude/skills`, `.agents/skills`, `.claude/settings.json`):** Only `.cursor/rules/` and `.cursor/skills/` exist; Claude Code and Codex local users do not get automatic skill discovery or pre-configured safe command allowlists (`make check-fast`, `make test-file`, `make smoke-offline`) and `docs/archive/**` search exclusions.
  2. **`make smoke-offline` in CI (`.github/workflows/ci.yml`):** `ci.yml` runs `make check` but does not run `make smoke-offline`, so a broken subprocess entry point or `sitecustomize.py` regression wouldn't fail CI unless covered by unit tests.
  3. **Automated doc-fact assertions for `README.md` claims:** `ARCH-011` checks links and root-prefixed paths, but nothing checks that `tests/archtest/README.md`'s SAF lock count matches `safety_lock.json`, that `legacy_baseline.txt` is empty now that the ratchet is empty, or that directory-relative backticked filenames in `README.md`s exist.
  4. **Agent-executable guardrail proposal flow:** When a legitimate fix requires updating a locked safety test in `safety_lock.json` or retiring a frozen oracle in `frozen_inputs.json`, agents hit a hard stop (`AGENTS.md` Rule 3) rather than having a `--propose` mode or CI check that lets the PR update the hash while flagging the diff for human review via `CODEOWNERS`.

### 3.7 Code Walkthrough: Trying "Add a Cleaner" (`ARCHITECTURE.md:100-129`)

Mentally adding a hypothetical AWS NAT Gateway cleaner (`crc/aws/nat_gateway/`) following Steps 1–8 of `ARCHITECTURE.md:100-129`:

- **Steps 1–5 (Cleaner package + `@register("aws", "nat_gateway")` + `crc/cli/routes/aws.py`) — Very Clean:**
  - The code in `crc/aws/keypairs/` (`cleaner.py`, `client.py`, `__init__.py`, `README.md`) and `crc/cli/routes/aws.py` is small, uniform, and 100% adapter-free. Writing `NatGatewayCleaner(Cleaner)` with `plan()` / `apply()`, registering it, and adding `"nat_gateway": Route(_nat_gateway)` in `crc/cli/routes/aws.py` takes ~120 lines of clean Python.
- **Steps 6–7 (CLI wiring & parity tests) — Unblocked by `AG-CLI`, but Undocumented in the Guide and Scattered Across 8 Files:**
  1. **Guide omission:** `ARCHITECTURE.md:115-128` tells the agent to edit `crc/cli/constants.py`, `crc/cli/args.py`, `crc/cli/pairs.py`, `crc/core/report.py`, and `crc/core/engine.py`, and says *"A flag or kind that is new to the CLI also changes the oracle-equality tests `tests/test_cli_args_parity.py` and `tests/test_core_selection.py`; see AGENTS.md."* Neither `ARCHITECTURE.md` nor `AGENTS.md` mentions `tests/helpers_ag_cli_added.py` (`ADDED_RESOURCES`, `RESOURCE_HELP`) or `crc/core/selection.py` (`RESOURCES` / `EXTRA_RESOURCES`)!
  2. Fortunately, once the agent runs `make test-file F=tests/test_cli_args_parity.py`, the failure message prints `ADDED_HINT`, directing the agent to `tests/helpers_ag_cli_added.py`.
  3. **Remaining multi-file wiring tax (8 files for one new kind):** Even with `helpers_ag_cli_added.py`, adding `"nat_gateway"` requires synchronized edits to: (1) `crc/cli/args.py` (`--resource` choices + help), (2) `crc/cli/constants.py` (`RESOURCES`, if in `all`), (3) `crc/core/selection.py` (`RESOURCES` or `EXTRA_RESOURCES`), (4) `crc/cli/pairs.py` (`SUPPORTED_CLOUDS` / `UNSUPPORTED_PAIR_MESSAGES`), (5) `crc/cli/routes/aws.py` (`ROUTES`), (6) `crc/core/report.py` + `crc/core/engine.py` (`KIND_LABELS`), (7) `tests/helpers_ag_cli_added.py` (`ADDED_RESOURCES`, `RESOURCE_HELP`), and (8) `tests/test_core_selection.py:144` (which duplicates `SUPPORTED_CLOUDS` in its own hardcoded `SUPPORTED_ON` dict!).

---

## 4. Top 10 Changes Ranked by Value / Effort

| Rank | Change | Value | Effort | Concrete Action & Payoff |
|-----:|--------|:-----:|:------:|--------------------------|
| **1** | **Fix the 6 post-`OWN-1`/`AG-ENV`/`AD-VM` doc drift items and update `AGENTS.md` / `ARCHITECTURE.md` for `AG-CLI` and `AG-ENV`** | Very High | Very Low | Update `tests/archtest/README.md:24,62-64` (`46 of 46` SAF IDs locked, `crc/core` bans `env` and `clock_ref`), `ARCHITECTURE.md:89` and `.cursor/rules/20-core.mdc:10` (`MAX_AGE` injected via `set_max_age_source`), `crc/README.md:15` and `tests/README.md:26-44,66` (ratchet is empty; 5 legacy safety test files and adapters are gone), `crc/azu/README.md:43` (remove `VM` class note), and `BUGS.md:441` (`64 fixed`). In `AGENTS.md`, `ARCHITECTURE.md`, and `crc/cli/README.md`, document `-c constraints.txt`, `make smoke-offline`, `tests/fixtures/jenkins_commands.txt`, and `tests/helpers_ag_cli_added.py`. |
| **2** | **Teach `ARCH-011` (`doc_links.py`) to check directory-relative backticked `.py` filenames and lock `constraints.txt` in `ARCH-007`** | High | Low | In `tests/archtest/doc_links.py:_repo_paths()`, resolve backticked `<name>.py` spans relative to `document.parent` (or repo root) when they lack a slash—immediately catching deleted files like `test_aws_disk.py` in `tests/README.md`. Add `constraints.txt` to `frozen_inputs.py:frozen_paths()` and clean dead `paramiko`/`beautifulsoup4` pins from `constraints.txt`. |
| **3** | **Empty `tests/archtest/legacy_baseline.txt` and remove `exclude = ['^crc\.py$']` from `pyproject.toml`** | High | Very Low | Now that `extend-exclude = []` is empty (`OWN-1`), empty `tests/archtest/legacy_baseline.txt` so `ARCH-001` permanently forbids *any* file from re-entering the legacy ratchet. Remove `exclude = ['^crc\.py$']` from `pyproject.toml:94` (or check `crc.py` via a wrapper test) and update `ARCH-001`. |
| **4** | **Add `.claude/skills` and `.agents/skills` symlinks (and `.claude/settings.json`) for multi-agent compatibility** | High | Very Low | Since CRC will be maintained by Claude Code, Codex, and local Cursor agents, add symlinks `.claude/skills -> ../.cursor/skills` and `.agents/skills -> ../.cursor/skills`, plus a `.claude/settings.json` that pre-allows safe offline commands (`make check*`, `make lint`, `make test-file`, `make smoke-offline`) and excludes `docs/archive/**` and `tests/legacy/**` from default search. |
| **5** | **Enforce `clock_ref` bans in `crc/{aws,azu,gcp,oci}` and `print` ban in `crc/notify` (`ARCH-003`)** | Medium | Low | Clean up the last 4 `cloud_clock_refs` hits (`crc/aws/spot_instance_requests/runtime.py:21`, `crc/gcp/spot_vm/cleaner.py:165,188`, `crc/gcp/spot_vm/disks.py:112`) and add `"clock_ref"` to `_ALL_KINDS` in `tests/archtest/bans.py:11`. Add `make smoke-offline` to `.github/workflows/ci.yml`. |
| **6** | **Archive the 64 fixed rows and 306 low-risk `main` quirk rows from `docs/refactor/BUGS.md`** | High | Low | Move fixed rows and low-risk `main`-branch quirk catalogue entries to `docs/archive/BUGS_CATALOGUE.md`, keeping only open/partly-fixed actionable bugs in `docs/refactor/BUGS.md` (with `ARCH-009` reading IDs from both files). Cuts active `BUGS.md` from `25,000` words to `<3,000` words. |
| **7** | **Deduplicate `SUPPORTED_ON` in `tests/test_core_selection.py` and centralize resource-kind metadata** | Medium | Medium | Import `SUPPORTED_CLOUDS` from `crc.cli.pairs` in `tests/test_core_selection.py:144` instead of hardcoding `SUPPORTED_ON`, and consolidate resource-kind metadata (`kind`, `clouds`, `in_all`, `report_label`) into a single registry/table in `crc/core/selection.py` + `crc/cli/pairs.py` so adding a resource kind touches 3 files instead of 8. |
| **8** | **Trim duplicated per-cleaner prose in `crc/aws/README.md` and `crc/gcp/README.md`** | Medium | Low | Replace the 300-word per-cleaner paragraphs in `crc/aws/README.md:41-49` and `crc/gcp/README.md:52-55` with 1-line summaries linking to each subpackage's `README.md` and `CLEANUP_SAFETY.md`, eliminating redundant prose maintenance. |
| **9** | **Replace the `ARCH-008` raw test-count floor with a non-cli-oracle test floor + slimmed CLI parity matrix** | Medium | Medium | `test_cli_{main,crc,args}_parity.py` still run 30,370 combinatorial cases (68.1% of the suite). Separate the `ARCH-008` floor into unit/cleaner/contract tests (`14,227` tests) vs legacy CLI matrix tests so agents can prune redundant legacy permutations and bring full `make check` under `30s`. |
| **10** | **Allow PR-scoped guardrail updates verified by `CODEOWNERS` review instead of hard agent stops** | Medium | Low | Instead of instructing agents to stop and paste diffs in PR descriptions when a valid fix touches a locked test (`AGENTS.md` Rule 3), let agents run `update_safety_lock.py` in the branch and add a CI job that prints the AST diff of any changed lock entry while `.github/CODEOWNERS` enforces human sign-off on `tests/archtest/safety_lock.json`. |
