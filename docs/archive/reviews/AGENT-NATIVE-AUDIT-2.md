# Independent Agent-Native Repository Audit #2 (`cloud-resource-cleanup`)

**Branch audited:** `fix/integration` (at commit `0e2bf38`)  
**Previous audit:** `origin/audit/agent-native:docs/refactor/reviews/AGENT-NATIVE-AUDIT.md` (`f59ef39`, score **6.5 / 10**)  
**Date:** 2026-10-24  
**Verdict Score:** **8.2 / 10**

---

## Executive Summary & Score Justification (8.2 / 10)

Since the first audit (`6.5 / 10` at `f59ef39`), CRC made a major leap from a mid-refactor construction site to a genuinely well-engineered agent-first codebase: every `(cloud, kind)` pair in `crc/cli` now executes through `crc.core.engine.run()`, the legacy CLI dispatch layer (`resource_ops.py`, `vm_ops.py`, `notify.py`, `adapter_args.py`, `report_calls.py`) and 11 of 20 cloud adapter classes were deleted, source files were split to `<= 250` lines / `<= 60` lines per function (`ARCH-004`), six new AST/integrity checks (`ARCH-006..011`) and `pytest-xdist` (`make check-fast` in `27.5s`, `make check` in `65.0s`) were added, `AGENTS.md` and `.cursor/skills/crc-maintain/` were rewritten for steady-state maintenance, and `183,345` words of refactor history (`106` files) were moved into `docs/archive/`. What keeps CRC at **8.2 / 10** instead of `9.5 / 10` is an unfinished last mile: (1) `AGENTS.md`, `ARCHITECTURE.md`, and `CLEANUP_SAFETY.md` were not updated after `WB-1` deleted the legacy CLI layer and still instruct agents about nonexistent files (`crc/cli/vm_ops.py`, `crc/cli/resource_ops.py`, `dispatch.HANDLERS`); (2) 7 package `README.md`s still falsely state that custom age tags "fail open (`BUG-004`)" when `get_retention_age` fails closed; (3) 9 legacy `Adapter` classes (`5` held hostage by `safety_lock.json`, `4` `VM` adapters held by `crc/cli/scan.py`) plus dead `crc/core/selection.py` (`build_selection`) remain in `crc/`; (4) the frozen-oracle parity suite (`tests/legacy/*.txt`) still blocks adding any new CLI flag or `--resource` choice despite `AGENTS.md:95-97` claiming otherwise; and (5) `docs/refactor/BUGS.md` (`24,902` words, `405` rows) and unpinned `requirements.txt` remain unaddressed.

---

## 0. Status of the First Audit's Top-10 Recommendations

| # | First-Audit Recommendation | Status | Evidence on `fix/integration` (`0e2bf38`) |
|--:|----------------------------|:------:|-------------------------------------------|
| **1** | **Add `make check-fast` + `pytest-xdist` and pre-bake `.venv` in `.cursor/environment.json`** | **Partly Done** | `requirements-dev.txt:2` pins `pytest-xdist==3.8.0`; `Makefile:6-26` adds `make check-fast` (`27.49s`, `14,057` tests), `make lint`, `make test-file`, and `make raise-test-floor`, cutting `make check` from `173.5s` to `64.96s` (`44,415` tests). **Still open:** `Makefile` does not run `ruff format --check .`, and this Cloud Agent VM still booted without `.venv` or pre-installed packages (`python3` 3.12.3, requiring a `38.74s` cold `pip install`). |
| **2** | **Apply queued `## Owner action` updates to `CLEANUP_SAFETY.md` and the 6 drifted `README.md`s** | **Partly Done** | `W2-SAFETY-DOC` updated `CLEANUP_SAFETY.md` (`SAF-009` fail-closed `KEEP_AGE` at `:71-79`, `SAF-025` AWS disk floor at `:136-141`, `CLI validation` at `:22-35`, `Per-resource keep rules` at `:222-239`) and fixed root `README.md` (`logs/crc.log` at `:125`, Example 6 `--resource vpc` at `:187`). **Still open:** **7 package `README.md`s still falsely claim custom age tags "fail open (`BUG-004`)"** (`crc/aws/README.md:44`, `crc/aws/vm/README.md:46`, `crc/azu/README.md:38`, `crc/azu/vm/README.md:47`, `crc/gcp/README.md:25,55`, `crc/oci/disk/README.md:39`, `crc/oci/vm/README.md:39`), and `CLEANUP_SAFETY.md:50-53,120,129,258` still cites deleted classes/files (`AWS_Disk`, `CRC.delete_disks`, `GCP_Disk`, `resource_ops.py`, `vm_ops.py`). |
| **3** | **Add a golden manifest of the 31 production Jenkins CLI commands (`tests/test_jenkins_contract.py`)** | **Open** | No Jenkins trigger command manifest or contract test exists under `tests/`. `BUG-355` (`docs/refactor/BUGS.md:61`, Azure `spot_instances` breaking Jenkins build 16581) remains `open`. `AGENTS.md:92-94` only tells agents to run `gh search code` against `cloud-resource-cleanup-triggers` if accessible. |
| **4** | **Close `ARCH-003` and `ARCH-005` loopholes + add `ARCH-006` (test-count & `requirements.txt` lock)** | **Partly Done** | `W2-ENFORCE` and `DOCS-1` extended `ARCH-003` (`bans.py:18-23`) to `crc/core`, `crc/cli`, and `crc/notify`, extended `ARCH-005` (`readonly_plan.py:240-275`) to follow intra-package sibling helper imports transitively, and added `ARCH-006` (banned constructs/imports), `ARCH-007` (SHA-256 manifest of `tests/legacy/*.txt` + `requirements.txt`), `ARCH-008` (test count floor `44415`), `ARCH-009` (`BUG-NNN` existence), `ARCH-010` (doc line budgets), and `ARCH-011` (doc link resolution). **Still open:** `os.getenv("MAX_AGE")` remains in `crc/core/age.py:55` (so `ARCH-003` still allows `env` in `crc/core`), `ARCH-003` still misses `datetime.datetime.now` attribute references (`crc/azu/spot_vm/__init__.py:105`), and `ARCH-011` skips `crc/**/README.md`, `tests/**/README.md`, and `docs/refactor/*.md`. |
| **5** | **Finish the deferred `engine.run` wiring and delete the legacy `Adapter` classes** | **Partly Done** | All 20 cleanup `(cloud, kind)` pairs now route through `crc/cli/bridge.py` -> `crc.core.engine.run()`. `WB-1` deleted `crc/cli/{resource_ops,vm_ops,notify,adapter_args,report_calls,base}.py` and `dispatch.HANDLERS`; `WC-*` deleted 11 cloud adapters (`aws` `keypair`/`ip`/`kms`/`snapshot`/`vpc`, `azu` `ip`/`nic`/`disk`, `gcp` `ip`/`disk`, `oci` `disk`) and retargeted their parity tests to `engine.run`. **Still open:** **9 legacy `Adapter` classes remain in `crc/`** (`5` kept for `safety_lock.json`: `aws` `Disk`/`SpotInstanceRequests`, `azu`/`gcp`/`oci` `SpotVM`; `4` `VM` adapters kept for `crc/cli/scan.py` and `test_*_vm_parity.py`), `crc/core/selection.py` (`build_selection`) is still dead code in production, and `AGENTS.md:84-86` + `ARCHITECTURE.md:16,44-46,119-121` still describe the deleted `HANDLERS`/`vm_ops.py`/`resource_ops.py` path as active. |
| **6** | **Transition from frozen `main` parity oracles (`tests/legacy/*.txt`) to behavioral contract tests** | **Open** | All 30 `tests/legacy/*.txt` oracles (plus `tests/legacy_crc_notify_reference.txt`) remain active and are now SHA-256 locked by `ARCH-007`. The 3 slow CLI parity files still account for **30,358 of 44,415 tests (68.4%)**, and `tests/test_cli_args_parity.py:86-88` still enforces exact equality with `oracle.get_argparser`, blocking new CLI flags or `--resource` choices. |
| **7** | **Archive refactor history (`STATUS.md`, `reviews/*.md`, `SKILL_SELECTION.md`) and split `BUGS.md`** | **Partly Done** | `DOCS-1` moved `PLAN.md`, `STATUS.md`, `SKILL_SELECTION.md`, `FILTER_VARIANTS.md`, and all `102` review files to `docs/archive/` (`106` files, `183,345` words removed from active docs). **Still open:** `docs/refactor/BUGS.md` was **not** split or cleaned—it grew to **450 lines / 24,902 words (`405` rows)**, still sorted by risk with `main`-branch `file:line` citations and broken references to `docs/refactor/PLAN.md`, `FILTER_VARIANTS.md`, and `STATUS.md`. |
| **8** | **Rewrite `AGENTS.md` and `.cursor/skills/` for steady-state autonomous maintenance** | **Done** | `AGENTS.md` (104 lines), `.cursor/rules/*.mdc` (15–20 lines each), and `.cursor/skills/crc-maintain/{SKILL.md,fix-bug.md,review.md}` were rewritten around steady-state workflows (`fix a bug`, `add or change a cleaner`, `change a CLI flag`, `review a PR`), backed by `.github/PULL_REQUEST_TEMPLATE.md` and `.github/ISSUE_TEMPLATE/*.md`. (Needs a 5-line cleanup for post-`WB-1` `HANDLERS` references.) |
| **9** | **Map and lock the 16 open `SAF-NNN` gaps in `tests/archtest/safety_lock.json`** | **Open** | `tests/archtest/safety_lock.json` and `tests/archtest/README.md:62-64` still have the exact same **16 unlocked `gaps` out of 46 `SAF-NNN` IDs** (`35%`), the new `CLI validation` and `Per-resource keep rules` in `CLEANUP_SAFETY.md` have no `SAF-NNN` IDs, and the 30 locked IDs still point to the 5 ruff/mypy-exempt legacy adapter test files instead of `tests/test_*_engine.py`. |
| **10** | **Pin `requirements.txt`, remove unused deps (`paramiko`, `requests`, `beautifulsoup4`), and add offline CLI smoke mode** | **Open** | `requirements.txt` still has **0 version pins**, still installs unused `paramiko`, `requests`, and `beautifulsoup4`, and is now hash-locked by `ARCH-007`. No `make smoke` or `--offline-smoke` CLI mode exists. |

---

## 1. Day-One Agent Onboarding Walkthrough

### Token and File Tax Before Making a Safe Edit (Audit 1 vs. Audit 2)

| Layer | Audit 1 (`f59ef39`) | Audit 2 (`0e2bf38`) | Delta |
|-------|--------------------:|--------------------:|------:|
| Entry & Rules (`AGENTS.md`, `.cursor/rules/*.mdc`) | 5 files / 1,121 words (~1.5k tok) | 5 files / 1,196 words (~1.6k tok) | +5% (budgeted by `ARCH-010`) |
| Project Skill (`.cursor/skills/crc-maintain/*.md`) | 4 files / 2,102 words (~2.8k tok) | 3 files / 1,073 words (~1.4k tok) | **-49%** |
| Core Architecture, Safety & Logging (`ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `docs/logging.md`, `tests/archtest/README.md`) | 4 files / 4,900 words (~6.5k tok) | 4 files / 4,562 words (~6.1k tok) | -7% |
| Package & Test `README.md`s (`crc/**/README.md`, `tests/**/README.md`, `docs/**/README.md`) | 21 files / 18,451 words (~24.6k tok) | 34 files / 25,650 words (~34.2k tok) | +39% (more subpackages) |
| Active Bug Register (`docs/refactor/BUGS.md`) | 1 file / 23,280 words (~31.0k tok) | 1 file / 24,902 words (~33.2k tok) | +7% (**41.7% of all active docs**) |
| **Total Active Markdown in Repo (excl. `docs/archive/`)** | **109 files / 179,911 words (~240k tok)** | **49 files / 59,775 words (~80k tok)** | **-66.8% words** |
| Archived History (`docs/archive/**`, read only on demand) | 0 files | 106 files / 183,345 words (~244k tok) | Isolated from active map |

To safely edit a single cleaner today (e.g., `crc/azu/disk/`), an agent reads `AGENTS.md` (104 lines), `ARCHITECTURE.md` (125 lines), `CLEANUP_SAFETY.md` (270 lines), `.cursor/skills/crc-maintain/SKILL.md` (52 lines), `crc/azu/README.md` (52 lines), and `crc/azu/disk/README.md` (35 lines)—**6 files (~638 lines, ~5,200 tokens)**, down from 12–14 files (~20,000 tokens) in Audit 1.

### Where a Day-One Agent Still Gets Lost or Misled on `0e2bf38`

1. **Ghost Legacy CLI References in the Top-Level Entry Docs (`AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`):**
   - `AGENTS.md:84-86` tells the agent: *"Kinds still on the legacy adapter path (`crc/cli/dispatch.py` `HANDLERS`, `crc/cli/vm_ops.py`, `crc/cli/resource_ops.py`) are being moved to the engine; do not add new legacy handlers."*
   - `ARCHITECTURE.md:16,44-46,119-121` shows `HANDLERS[kind]` in the run-flow diagram and explains how to migrate kinds off `dispatch.HANDLERS`, `crc/cli/vm_ops.py`, and `crc/cli/resource_ops.py`.
   - `CLEANUP_SAFETY.md:50-53,120,129,258` cites `CRC.delete_disks`, `AWS_Disk.delete`, `GCP_Disk`, `resource_ops.py`, and `vm_ops.py`.
   - **Reality:** `WB-1` (`5c08d40`) deleted `crc/cli/vm_ops.py`, `crc/cli/resource_ops.py`, and `dispatch.HANDLERS`! Every pair in `crc/cli/dispatch.py` runs through `bridge.py` -> `engine.run`. Because `AGENTS.md`, `ARCHITECTURE.md`, and `CLEANUP_SAFETY.md` are `CODEOWNERS`-protected files, lanes `WB-1` and `WC-DOCS` were forbidden from updating them.
2. **Contradictory Instructions on `BUGS.md` Line Citations (`AGENTS.md` vs. `BUGS.md` / `WC-DOCS`):**
   - `AGENTS.md:61-62` and `.cursor/skills/crc-maintain/fix-bug.md:6` tell agents to add new bugs to `docs/refactor/BUGS.md` with *"current `file:line`"*.
   - Conversely, `docs/refactor/BUGS.md:11`, `docs/refactor/bugs/README.md:16`, `crc/README.md:45`, `crc/oci/README.md:50`, and `tests/archtest/bug_refs.py:15` (`ARCH-009` remediation text!) instruct agents to record bugs with *"the `main` file:line, verified with `git show main:<path>`"* (and `WC-DOCS` commit `ba721ea` explicitly reverted "current `file:line`" back to "`main` file:line"!).
3. **Half-Deleted Adapter Layer in `crc/<cloud>/`:**
   - 11 resource kinds have only a `Cleaner`. The other 9 resource kinds (`aws` `disk`/`spot_instance_requests`/`vm`, `azu` `spot_vm`/`vm`, `gcp` `spot_vm`/`vm`, `oci` `spot_vm`/`vm`) still have ~1,450 lines of `Adapter` classes (`Disk`, `SpotInstanceRequests`, `SpotVM`, `VM`) whose module docstrings (`crc/aws/disk/__init__.py:6`, `crc/azu/spot_vm/__init__.py:3`, `crc/gcp/spot_vm/adapter.py:3`, `crc/oci/spot_vm/adapter.py:3`, `crc/oci/vm/adapter.py:3`) falsely claim *"the legacy class `crc.py` uses"*. In reality, `crc.py` never imports any of them; 5 exist solely because `safety_lock.json` hasn't been repointed to `tests/test_*_engine.py`, and 4 (`VM`) exist for `crc/cli/scan.py`.

---

## 2. 2025–2026 Industry Guidance on Agent-Native Repositories

1. **OpenAI — "Harness Engineering: Leveraging Codex in an Agent-First World" & "Using Skills to Accelerate OSS Maintenance" (2026)**
   - URLs: [https://openai.com/index/harness-engineering/](https://openai.com/index/harness-engineering/), [https://developers.openai.com/blog/skills-agents-sdk](https://developers.openai.com/blog/skills-agents-sdk), [https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex](https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex)
   - *Guidance:* Keep `AGENTS.md` around ~100 lines as a map/table of contents with explicit workflow/skill triggers. Enforce layered architecture, file sizes, and taste invariants mechanically via custom linters whose failure messages inject `REMEDIATION:` instructions. Critically, **run automated "doc-gardening" / garbage-collection checks across all repository docs** so documentation never rots when code moves, and eliminate dead migration shims promptly so agents don't pattern-match against obsolete code.
2. **The `AGENTS.md` Open Standard (2025–2026)**
   - URLs: [https://github.com/openai/agents.md/](https://github.com/openai/agents.md/), [https://agent-ready.dev/how-to-write-an-effective-agents-md](https://agent-ready.dev/how-to-write-an-effective-agents-md), [https://devagentstack.com/blog/how-to-make-a-repo-agent-ready/](https://devagentstack.com/blog/how-to-make-a-repo-agent-ready/)
   - *Guidance:* Separate human CLI usage (`README.md`) from agent operational rules (`AGENTS.md`). Every command, file path, and workflow documented in `AGENTS.md` must be validated on a real representative task so an agent never hits a dead path or unworkable instruction.
3. **Anthropic — Claude Code Context Engineering & Agent Skills (2025–2026)**
   - URLs: [https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills), [https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/](https://claude.dev/blog/lessons-from-building-claude-code-how-we-use-skills/), [https://claudekit.io/en/guides/context-engineering-claude-5/](https://claudekit.io/en/guides/context-engineering-claude-5/)
   - *Guidance:* Over-constraining or feeding contradictory rules burns model reasoning tokens. Keep `CLAUDE.md`/`AGENTS.md` focused on high-signal codebase gotchas; structure Skills as a short hub (`SKILL.md`) with spoke playbooks (`fix-bug.md`, `review.md`) and executable helper scripts; never force agents to grep 25,000-word flat tables when structured/scoped files can be used.
4. **Cursor — Best Practices for Coding with Agents, Rules & Skills (2026)**
   - URLs: [https://cursor.com/blog/agent-best-practices](https://cursor.com/blog/agent-best-practices), [https://developertoolkit.ai/en/cto-scorecard-guide/shared-agent-rules/](https://developertoolkit.ai/en/cto-scorecard-guide/shared-agent-rules/)
   - *Guidance:* Keep `.cursor/rules/*.mdc` short (~15–25 lines), path-scoped via `globs`, and pointing to `AGENTS.md` and canonical modules (`crc/aws/keypairs/`) rather than duplicating rules across files.
5. **Factory AI — Agent Readiness & Evaluating Coding Agents on Your Codebase (2026)**
   - URLs: [https://factory.com/articles/evaluating-coding-agents-on-your-codebase](https://factory.com/articles/evaluating-coding-agents-on-your-codebase), [https://factory.com/news/using-linters-to-direct-agents](https://factory.com/news/using-linters-to-direct-agents)
   - *Guidance:* Ensure hermetic, reproducible environments (pinned dependencies, zero cold-install surprises), fast inner-loop verification (`<10–30s`), and runnable offline CLI/smoke checks for user-visible entry points.

---

## 3. Blunt Comparison Against 2025–2026 Guidance

### 3.1 Entry Docs: Short, Accurate, and Progressive?

- **What improved dramatically:** `AGENTS.md` (104 lines), `.cursor/rules/00-map.mdc`..`30-safety.mdc` (15–18 lines each), and `.cursor/skills/crc-maintain/{SKILL.md,fix-bug.md,review.md}` (52, 28, and 46 lines) are textbook progressive disclosure and are mechanically capped by `ARCH-010`. Refactor history (`PLAN.md`, `STATUS.md`, `SKILL_SELECTION.md`, `FILTER_VARIANTS.md`, and 102 reviews) is cleanly archived under `docs/archive/`.
- **What is still broken:**
  1. **Stale post-`WB-1` architecture claims** in `AGENTS.md:84-86`, `ARCHITECTURE.md:16,44-46,119-121`, and `CLEANUP_SAFETY.md:258` (pointing to deleted `crc/cli/vm_ops.py`, `crc/cli/resource_ops.py`, and `dispatch.HANDLERS`).
  2. **Unworkable CLI flag workflow in `AGENTS.md:95-97`:** Tells agents to *"extend its expected tables for a new flag instead of touching the oracle"* in `tests/test_cli_args_parity.py`, which fails in practice because `_compare()` (`test_cli_args_parity.py:86-88`) asserts exact equality against `oracle.get_argparser()` (`tests/legacy/crc_main.txt`) before `DEFAULTS_EXPECTED` is ever checked.

### 3.2 Mechanical Enforcement vs. Prose Rules

`tests/archtest/` (`ARCH-001..011` and `SAF-LOCK`) is top-tier harness engineering: every check is a pure function with `tmp_path` positive/negative self-tests and a `REMEDIATION:` block. However, specific mechanical blind spots remain:

| Rule / Invariant | Enforcement Status | Remaining Loophole / Defect on `0e2bf38` |
|------------------|--------------------|------------------------------------------|
| Ratchet shrinks; non-test files `<=250` lines, funcs `<=60` lines (`ARCH-001`, `ARCH-004`) | **Mechanical** | `tests/archtest/legacy_baseline.txt` still lists 17 deleted `.py` paths (`crc/aws/disk.py`, `crc/oci/disk.py`, etc.). `crc.py` (31 lines) is still excluded from `mypy` (`pyproject.toml:49`) even though `crc/cli` replaced the old `crc.py` clash. |
| Package `README.md` 4 sections & `Public API` symbol sync (`ARCH-002`) | **Mechanical** | Relaxed so non-Python dirs (`docs/`, `.cursor/`, `tests/legacy/`) no longer need artificial code headings. Still skips top-level `crc/{service,utils,notifications}.py`. |
| Per-package bans on `re`, clock, `env`, `print`, `sys.exit` (`ARCH-003`) | **Partial** | (1) `crc/core/age.py:55` still calls `os.getenv("MAX_AGE")`, so `PACKAGE_BANS["crc/core"]` (`bans.py:20`) still does not ban `env`. (2) `_expr_kind` (`bans.py:82-89`) only checks `ast.Call` for clocks, missing `datetime.datetime.now` passed as a default argument or attribute in the remaining adapters (`crc/azu/spot_vm/__init__.py:105`, `crc/aws/disk/__init__.py:38`). (3) `crc/notify/slack_owners.py:32,48,68` and `crc/notify/influx.py:89` use `print()` instead of `logging`. |
| `plan()` read-only across module & sibling helpers (`ARCH-005`) | **Mechanical** | `check_plan_readonly_siblings` now follows intra-package function imports transitively (`readonly_plan.py:240-275`). Only misses method calls on imported sibling helper classes (e.g. `self._cleaner.detach_guard...`). |
| No `async`/`pydantic`/`httpx`/`tenacity`/`threading` (`ARCH-006`) | **Mechanical** | Solid across all `crc/` and `crc.py`. |
| SHA-256 lock on `tests/legacy/*.txt` & `requirements.txt` (`ARCH-007`) | **Mechanical** | Solid, except its `_REMEDIATION` (`frozen_inputs.py:13-14`) cites *"AGENTS.md hard rules 5 and 7"* (now rules 3 and 7). |
| Monotonic test-count floor (`ARCH-008`, floor `44,415`) | **Mechanical** | Solid in `make check`, though `make check-fast` does not run it. |
| `BUG-NNN` refs exist in `BUGS.md` (`ARCH-009`) | **Mechanical** | Solid, except `_REMEDIATION` (`bug_refs.py:15`) cites *"AGENTS.md hard rule 11"* and *"main-branch file:line"*, contradicting `AGENTS.md` Rule 5 (*"current `file:line`"*). |
| Entry doc line budgets (`ARCH-010`) | **Mechanical** | Caps `AGENTS.md <= 120`, `.cursor/rules/*.mdc <= 40`, `.cursor/skills/*/SKILL.md <= 150`. Does not budget companion skill files (`fix-bug.md`, `review.md`), `ARCHITECTURE.md`, or `BUGS.md`. |
| Relative markdown links resolve (`ARCH-011`) | **Partial** | **Two blind spots in `doc_links.py:21-39`:** (1) `_documents()` only scans `AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `docs/*.md` (non-recursive!), and `.cursor/**`—skipping `docs/refactor/*.md`, all 29 `crc/**/README.md`s, `tests/**/README.md`, and `README.md`. (2) It only checks `[text](target)` links, ignoring backticked file paths (`crc/cli/vm_ops.py` in `AGENTS.md:85` and `ARCHITECTURE.md:45`, `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md` in `tests/legacy/README.md:65`, `docs/refactor/PLAN.md` in `BUGS.md:13,444`). |
| Safety tests locked to `[SAF-NNN]` (`SAF-LOCK`) | **Partial** | **16 of 46 `SAF-NNN` IDs (35%) remain unlocked `gaps`**, and all 30 locked IDs still point to the 5 legacy adapter test files instead of `tests/test_*_engine.py`. |
| Code formatting (`ruff format --check .`) | **Prose only** | Still missing from `Makefile` and `.github/workflows/ci.yml`. |

### 3.3 Feedback Loops: Speed and Clarity of `make check`

- **Cold-start VM gap remains:** `.cursor/environment.json` defines `install`, yet this VM booted without `.venv` or dev packages pre-installed (and runs Python `3.12.3` vs CI's `3.11`). Cold `pip install -r requirements.txt -r requirements-dev.txt` took **38.74s** and emitted 10 `PATH` warnings.
- **Measured warm runtimes and test counts on `0e2bf38`:**
  - `make lint` (`ruff check .`, `mypy` across 367 files, `lint-imports` across 204 files / 1,073 dependencies / 5 contracts): **~1.1s**
  - `make check-fast` (`lint` + `pytest -q -n auto` excluding the 3 slow CLI parity files): **14,057 passed in 26.35s** (total wall time **27.49s**; note `AGENTS.md:18` claims *"about 10 s"*).
  - `make check` (`lint` + full `pytest -q -n auto` + `ARCH-008` floor check): **44,415 passed in 57.60s**, `ARCH-008: 44415 tests, floor 44415` (total wall time **1m 04.96s**, down 62.6% from `2m 53.5s` in Audit 1; `AGENTS.md:19` claims *"about 40 s"*).
- **Failure clarity:** Excellent. Every `ARCH-001..011` and `SAF-LOCK` failure prints the rule ID, exact `file:line`, and a `REMEDIATION:` paragraph. (Only `bug_refs.py:15` and `frozen_inputs.py:13-14` have slightly stale `AGENTS.md` rule numbers in their remediation strings.)

### 3.4 Documentation Drift: 8 Spot-Checked Claims vs. Actual Code

1. **Spot-Check 1 — `AGENTS.md:84-86` & `ARCHITECTURE.md:16,44-46,119-121` (Legacy CLI handlers):**
   - *Doc claim:* Says kinds not in a route table run through `crc/cli/dispatch.py` `HANDLERS`, `crc/cli/vm_ops.py`, and `crc/cli/resource_ops.py`.
   - *Code reality:* **FALSE.** All three were deleted in `WB-1` (`5c08d40`). `crc/cli/dispatch.py` routes 100% of pairs through `crc/cli/bridge.py`.
2. **Spot-Check 2 — Seven package `README.md`s on `BUG-004` (`get_retention_age` fail-open vs. fail-closed):**
   - *Doc claim:* `crc/aws/README.md:44` (KMS), `crc/aws/vm/README.md:46`, `crc/azu/README.md:38` (Azure disks), `crc/azu/vm/README.md:47`, `crc/gcp/README.md:25,55`, `crc/oci/disk/README.md:39`, and `crc/oci/vm/README.md:39` all state that custom age tags/labels *"fail open (`get_retention_age`, BUG-004)"* or *"fall back to `--age`"*.
   - *Code reality (`crc/core/age_tags.py:114-145`, `CLEANUP_SAFETY.md:71-79`):* **FALSE.** `get_retention_age()` is fail-closed: a present but empty, unparseable, non-finite, zero, or negative custom age tag returns `KEEP_AGE = {"days": 1_000_000}` and records `skip("bad_age_tag")`.
3. **Spot-Check 3 — `CLEANUP_SAFETY.md:50-53,120,129,258` (Deleted classes and modules):**
   - *Doc claim:* Cites `AWS_Disk.delete`, `CRC.delete_disks` (`:51`), `SpotVM.delete` (`:52`), `AWS_Disk` (`:120`), `GCP_Disk` (`:129`), and `runner.py / resource_ops.py / vm_ops.py (CRC)` (`:258`).
   - *Code reality:* **FALSE.** `CRC.delete_disks`, `resource_ops.py`, `vm_ops.py`, and `GCP_Disk` (`crc/gcp/disk_adapter.py`) were deleted; production runs `DiskCleaner` and `AzureSpotVmCleaner` via `crc/cli/bridge.py` (`_ReportThenRaise`).
4. **Spot-Check 4 — `AGENTS.md:95-97` & `crc/cli/README.md:82-83` (Adding a CLI flag via `test_cli_args_parity.py`):**
   - *Doc claim:* Says to add a new flag to `_add_*_args` in `crc/cli/args.py` and *"extend its expected tables for a new flag instead of touching the oracle"* in `tests/test_cli_args_parity.py`.
   - *Code reality (`tests/test_cli_args_parity.py:80-89,434-455`):* **FALSE.** `test_get_argparser_matches_main` and `test_defaults_are_as_documented` call `_compare(oracle, argv, ...)`, which asserts `new == old` directly against `oracle.get_argparser()` (`tests/legacy/crc_main.txt`) with no divergence table or new-flag filter.
5. **Spot-Check 5 — `tests/legacy/README.md:17-44,65` & `docs/refactor/BUGS.md:11-28,444` (Stale paths):**
   - *Doc claim:* `tests/legacy/README.md:65` points to `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`; `BUGS.md:13,27,28,444` points to `docs/refactor/PLAN.md`, `docs/refactor/FILTER_VARIANTS.md`, and `docs/refactor/STATUS.md` and says work is on `refactor/python-agentic`.
   - *Code reality:* **FALSE.** Those files were moved to `.cursor/skills/crc-maintain/fix-bug.md` and `docs/archive/` in `DOCS-1`.
6. **Spot-Check 6 — `crc/cli/args.py:39-44,105,156-176` (`python crc.py --help` text vs. actual CLI behavior):**
   - *Doc/help claim:* `--resource` `help=` omits `'vpc'`; `--name_regex` `help=` says *"Only applies to AWS keypairs and GCP IPs"* (omitting AWS disks, GCP disks, and Azure NICs); `--detach_age` `help=` omits the `age + detach_age` floor (`SAF-025`).
   - *Code reality:* **STALE** (kept frozen only because `test_cli_args_parity.py` compares `--help` stdout byte-for-byte with `tests/legacy/crc_main.txt`).
7. **Spot-Check 7 — `ARCHITECTURE.md:20-31` & `crc/core/README.md:15-19` (Engine routing & layer contracts):**
   - *Doc claim:* All 20 `(cloud, kind)` cleaners register with `@register(cloud, kind)` and run through `crc/cli/bridge.py` -> `crc.core.engine.run() -> notifiers`, with 5 active `import-linter` contracts.
   - *Code reality:* **TRUE.** Verified in `crc/cli/routes/{aws,azu,gcp,oci}.py`, `crc/cli/bridge.py`, and `pyproject.toml:77-130`.
8. **Spot-Check 8 — `docs/logging.md:31-67` (Structured run-log contract):**
   - *Doc claim:* Every cleaner pass emits `RUN START`, per-region `REGION`, per-resource `ACTION`, sorted `SKIPPED`, `ERRORS`/`FAILED`, and `RESULT` lines through `crc/core/logger.py`, holding `RUN START` until `init_logging` attaches handlers (`BUG-363`/`BUG-364`).
   - *Code reality:* **TRUE.** Verified in `crc/core/logger.py`, `crc/cli/dispatch.py:43-56`, and `crc/utils.py`.

### 3.5 Over-Engineering: Context Tax Without Payback

1. **`docs/refactor/BUGS.md` (450 lines, 24,902 words, 405 rows):** Still accounts for **41.7% of all active Markdown words** in the repository. Over 330 rows are low-priority pre-refactor quirks (`"keeps what it should delete"` or `"cosmetic"`) mixed into the same flat table as critical safety bugs, sorted by risk rather than package or ID, and citing `main` line numbers from monolithic files that were split into subpackages.
2. **9 Zombie Adapter Classes + Dead `crc/core/selection.py` (~1,600 lines of Python in `crc/`):**
   - 5 adapters (`crc/aws/disk/__init__.py:Disk`, `crc/aws/spot_instance_requests/adapter.py:SpotInstanceRequests`, `crc/azu/spot_vm/__init__.py:SpotVM`, `crc/gcp/spot_vm/adapter.py:SpotVM`, `crc/oci/spot_vm/adapter.py:SpotVM`) are dead in production and kept *only* because `tests/archtest/safety_lock.json` still locks the 5 legacy `tests/test_<name>.py` files even though strict-checked `tests/test_<name>_engine.py` copies already exist!
   - 4 `VM` adapters (`crc/{aws,azu,gcp,oci}/vm/*adapter*.py`) are kept only because `crc/cli/scan.py` calls `_scan_vm(crc)` (even though only AWS implements `find_instances_with_tag`, and `crc/aws/vm/scan.py` already has the standalone function!) and because `tests/test_{aws,azure,gcp,oci}_vm_parity.py` were not included in Wave C (`WC-*`).
   - `crc/core/selection.py:build_selection()` (119 lines + 453-line `tests/test_core_selection.py`) is never called by `crc/cli` and still implements pre-`FIX-CLI-ALL` pair selection.
3. **30,358 Combinatorial CLI Oracle Parity Tests (`tests/test_cli_{main,crc,args}_parity.py`):** Executing `tests/legacy/crc_main.txt` in-process across 30,358 parameterized cases adds ~38 seconds to every `make check` and locks historical typos (`--resources`, `reuired` in `crc/cli/inputs.py:118,128`), stale `--help` strings (`crc/cli/args.py`), and dead `CRC` context quirks into a permanent straitjacket.

### 3.6 What Is Missing That Agent-Native Repos Usually Have

- **Fixed since Audit 1:** `.github/PULL_REQUEST_TEMPLATE.md` and `.github/ISSUE_TEMPLATE/{bug_report,cleaner_request}.md` now exist and align with `AGENTS.md` and `CLEANUP_SAFETY.md`.
- **Still missing:**
  1. **Machine-readable Jenkins trigger contract test (`tests/test_jenkins_contract.py`):** Still no fixture of the actual CLI invocations from `cloud-resource-cleanup-triggers` to prevent another `BUG-355` break.
  2. **Offline CLI smoke runner (`make smoke` / `--offline-smoke`):** Still no way to execute `python crc.py --cloud <c> --resource <r> --dry_run` from the shell with canned responses without live cloud credentials.
  3. **Pinned `requirements.txt` without unused packages:** `requirements.txt` is still unpinned and installs unused `paramiko`, `requests`, and `beautifulsoup4`.
  4. **Pre-baked Python 3.11 `.venv` in Cloud Agent snapshot:** Agents still hit a 39s cold install on Python 3.12.

### 3.7 Code Walkthrough: Trying "Add a Cleaner" (`ARCHITECTURE.md:100-126`)

Mentally adding a new `aws` `nat_gateway` cleaner (`crc/aws/nat_gateway/`) following Steps 1–9 of `ARCHITECTURE.md:100-126`:

- **Steps 1–5 (Cleaner + `@register` + `crc/cli/routes/aws.py`) — Smooth!** Unlike Audit 1 (where `crc/cli` ignored `engine.py`), routing `"nat_gateway": Route(_nat_gateway)` in `crc/cli/routes/aws.py` now wires it end-to-end through `bridge.py` -> `engine.run` -> Slack/Influx notifiers with **no legacy adapter class needed**.
- **Step 6 (Adding a new `--resource` choice or CLI flag) — Hits 4 Mechanical Walls:**
  1. Adding `"nat_gateway"` to `crc/cli/args.py` (`--resource` choices) and `crc/cli/constants.py` (`RESOURCES`) immediately breaks `tests/test_cli_args_parity.py` (`test_get_argparser_matches_main`), `tests/test_cli_main_parity.py`, and `tests/test_core_selection.py::test_all_expansion_order_follows_crc_py` because they assert exact equality against `tests/legacy/crc_main.txt`—which `ARCH-007` (`frozen_inputs.json`) and `AGENTS.md` Rule 3 forbid editing!
  2. `ARCHITECTURE.md` Step 6 forgets to mention `crc/core/selection.py` (`RESOURCES` / `EXTRA_RESOURCES`), which `tests/test_core_selection.py` tests.
  3. Adding `"nat_gateway"` to `crc/cli/routes/aws.py` breaks `tests/test_cli_bridge_parity.py:173` (`test_route_tables_route_the_aws_kinds`), which hardcodes the exact keys of `bridge.TABLES`.
- **Step 7 ("Moving a legacy kind to the engine") — Obsolete:** Tells the agent to delete `CRC` methods and `dispatch.HANDLERS` entries that were already deleted in `WB-1`.

---

## 4. Top 10 Changes Ranked by Value / Effort

| Rank | Change | Value | Effort | Why & Concrete Action |
|-----:|--------|:-----:|:------:|-----------------------|
| **1** | **Fix post-`WB-1` doc lies in `AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, and the 7 package `README.md`s** | Very High | Low | Remove deleted `HANDLERS` / `vm_ops.py` / `resource_ops.py` / `AWS_Disk` / `CRC.delete_disks` references from `AGENTS.md:84-86`, `ARCHITECTURE.md:16,44-46,119-121`, and `CLEANUP_SAFETY.md:50-53,120,129,258`. Fix the 7 package `README.md`s (`crc/aws/README.md:44`, `crc/aws/vm/README.md:46`, `crc/azu/README.md:38`, `crc/azu/vm/README.md:47`, `crc/gcp/README.md:25,55`, `crc/oci/{disk,vm}/README.md:39`) that falsely claim `get_retention_age` (`BUG-004`) fails open. |
| **2** | **Extend `ARCH-011` (`doc_links.py`) to scan all non-archived `.md` files and backticked repo paths** | High | Low | `ARCH-011` currently scans only 5 paths + `.cursor/**` and ignores `` `path/to/file.py` `` spans. Extending `_documents()` to `crc/**/README.md`, `tests/**/README.md`, `docs/refactor/**`, and `README.md` and checking backticked repo-relative paths would have caught every broken path in Spot-Checks 1, 3, and 5 automatically. |
| **3** | **Repoint `safety_lock.json` to `tests/test_*_engine.py`, map the 16 `SAF-NNN` gaps, and delete the last 9 legacy adapters** | Very High | Medium | Repoint the 30 locks in `safety_lock.json` from the 5 ruff/mypy-exempt `tests/test_<name>.py` files to `tests/test_<name>_engine.py` ( emptying the legacy ratchet in `pyproject.toml`!), map the 16 open `SAF-NNN` gaps, call `crc/aws/vm/scan.py` directly from `crc/cli/scan.py`, and delete the remaining 9 `Adapter` classes (`Disk`, `SpotInstanceRequests`, 3 `SpotVM`, 4 `VM`) plus dead `crc/core/selection.py:build_selection`. |
| **4** | **Unblock additive CLI flags & resources in `test_cli_args_parity.py`, `test_cli_main_parity.py`, and `test_cli_bridge_parity.py`** | High | Low | Make `AGENTS.md:95-97` actually true: add an explicit `ADDED_FLAGS` / `ADDED_RESOURCES` allowance in `tests/test_cli_args_parity.py:_compare()`, `tests/test_cli_main_parity.py`, and `tests/test_cli_bridge_parity.py` so agents can add optional flags or new resource kinds without breaking frozen `crc_main.txt` oracle assertions. |
| **5** | **Add a golden manifest of the 31 production Jenkins CLI commands (`tests/test_jenkins_contract.py`)** | High | Low | Still open from Audit 1 (#3). Commit the exact CLI invocations from `cloud-resource-cleanup-triggers` as a test fixture and assert every command parses, passes `validate_targets`, and dry-runs cleanly against `FakeCleaners`. |
| **6** | **Archive fixed/low-risk quirk rows from `docs/refactor/BUGS.md` and unify the `file:line` rule** | High | Low | Move fixed rows and catalogued `main` quirks to `docs/archive/BUGS_HISTORY.md`, leaving only active open bugs in `docs/refactor/BUGS.md` with **current `crc/` `file:line`** paths. Resolve the contradiction between `AGENTS.md:61` ("current `file:line`") and `BUGS.md:11` / `ARCH-009` ("`main` file:line"). |
| **7** | **Close the remaining `ARCH-003` loopholes (`MAX_AGE` env read in `crc/core`, clock attribute refs, `print` in `crc/notify`)** | Medium | Low | Pass `max_age` through `RunContext` (keep env fallback at CLI boundary) so `PACKAGE_BANS["crc/core"]` can ban `"env"`; check `ast.Attribute` for `datetime.now`/`utcnow` in `bans.py`; replace `print()` in `crc/notify/slack_owners.py` and `influx.py` with `logging` and ban `"print"` in `crc/notify`. |
| **8** | **Pin `requirements.txt`, remove unused deps (`paramiko`, `requests`, `beautifulsoup4`), and add `ruff format --check .`** | Medium | Low | Remove unused `paramiko`, `requests`, and `beautifulsoup4`, pin SDK versions in `requirements.txt`, regenerate `frozen_inputs.json` (`ARCH-007`), remove `exclude = ['^crc\.py$']` from `pyproject.toml` (no longer clashes now that `crc.py` is 31 lines), and add `ruff format --check .` to `make lint`. |
| **9** | **Add an offline CLI smoke target (`make smoke` / `--offline-smoke`)** | Medium | Low | Add a lightweight fixture/stub mode or `scripts/smoke_cli.py` invoked by `make smoke` that runs `python crc.py --cloud <c> --resource <r> --dry_run` across all 20 routed `(cloud, kind)` pairs in a subprocess and verifies exit code `0` and `RUN START` / `RESULT` log output. |
| **10** | **Rebuild the Cloud Agent VM snapshot with Python 3.11 `.venv` pre-baked** | Medium | Low | Trigger and activate a prebuilt environment snapshot from `.cursor/environment.json` so new Cloud Agent sessions start with `.venv/bin/python` (3.11) and all dependencies pre-installed, eliminating the `38.74s` cold `pip install` and Python 3.12 drift. |
