# DOCS-1 review

## Coding summary

Lane DOCS-1 (agent-native top-level docs), branch `docs/agent-native`. `make check` green
(43095 tests, floor 43078).

### Files rewritten (lines before -> after)

| File | Before | After |
|------|-------:|------:|
| `AGENTS.md` (`CLAUDE.md` symlink kept) | 85 | 105 |
| `ARCHITECTURE.md` | 120 | 125 |
| `CLEANUP_SAFETY.md` (2 wording edits only: "this branch", "ENG-0"; no `SAF-NNN` touched) | 267 | 267 |
| `docs/README.md` | 23 | 25 |
| `docs/refactor/README.md` | 36 | 22 |
| `docs/logging.md` (was `docs/refactor/LOGGING.md`, history, bug table and rung names dropped) | 129 | 76 |
| `.cursor/rules/00-map.mdc`, `10-cloud-packages.mdc`, `20-core.mdc`, `30-safety.mdc` | 14, 25, 21, 16 | 15, 17, 18, 17 |
| `.cursor/skills/crc-python-refactor/SKILL.md` -> `.cursor/skills/crc-maintain/SKILL.md` (implement) | 95 | 52 |
| `.cursor/skills/crc-maintain/review.md` (replaces `REVIEW_CHECKLIST.md`, 67) | - | 46 |
| `.cursor/skills/crc-maintain/fix-bug.md` (replaces `BEHAVIOUR_FIX.md`, 77) | - | 28 |
| `.github/CODEOWNERS` | 11 | 11 (PLAN.md line replaced by `/tests/legacy/`) |
| New: `.github/PULL_REQUEST_TEMPLATE.md`, `.github/ISSUE_TEMPLATE/bug_report.md`, `cleaner_request.md`, `docs/archive/README.md` | - | new |
| Deleted: `.cursor/rules/README.md`, `.cursor/skills/.../README.md` (non-code dirs no longer need one) | 20, 28 | - |
| `.claude/skills/crc-python-refactor` symlink -> `crc-maintain` (outside the listed paths, but the old link would dangle) | | |

### Files archived (`git mv`, history kept, content unchanged)

- `docs/refactor/PLAN.md`, `STATUS.md`, `SKILL_SELECTION.md`, `FILTER_VARIANTS.md` -> `docs/archive/`
- `docs/refactor/reviews/` (all reviews and its README) -> `docs/archive/reviews/`
- `docs/refactor/LOGGING.md` -> `docs/logging.md` (rewritten, see above)
- `docs/refactor/BUGS.md` and `docs/refactor/bugs/` untouched. Still true from the archive and carried
  over: the Jenkins triggers contract, "never edit oracles", "locked tests go to the owner"
  (`AGENTS.md`), the legacy-path note and region ordering (`ARCHITECTURE.md`).

### Checks added or changed

- ARCH-010 `tests/archtest/doc_budget.py` + `test_arch010_doc_budget.py`: AGENTS.md <= 120 lines, each
  `.cursor/rules/*.mdc` <= 40, each `.cursor/skills/*/SKILL.md` <= 150. Self-tests: fires over each
  limit, passes at the limit, other files not budgeted.
- ARCH-011 `tests/archtest/doc_links.py` + `test_arch011_doc_links.py`: relative markdown links in
  AGENTS.md, ARCHITECTURE.md, CLEANUP_SAFETY.md, `docs/*.md`, `.cursor/**` (`.md`, `.mdc`) resolve.
  Self-tests: missing target, relative resolution, `.cursor` files, anchors/external/code spans
  ignored, root-relative links.
- ARCH-002 relaxed in `readmes.py`: only directories with Python code beneath them need the four
  headings (`docs/`, `.cursor/`, `tests/legacy/` no longer do; `README_EXEMPT_DIRS` is now the root
  only). Messages now read "contains Python code". `test_arch002_readmes.py` was updated to match
  (it asserted `docs/` needed a README, which the relaxation reverses); new self-tests cover
  non-code directories and a code directory under `docs/`. `tests/archtest/README.md` updated.
- Test floor `test_count_floor.txt` not raised (43078, actual 43095): guardrail data file, left for the
  integrator (`make raise-test-floor`) to avoid merge conflicts with parallel lanes.

## Links to fix

Files this lane may not edit still point at moved docs. Mapping: `docs/refactor/PLAN.md`, `STATUS.md`,
`FILTER_VARIANTS.md`, `SKILL_SELECTION.md`, `reviews/` -> `docs/archive/...`;
`docs/refactor/LOGGING.md` -> `docs/logging.md`; `.cursor/skills/crc-python-refactor/` ->
`.cursor/skills/crc-maintain/` (`BEHAVIOUR_FIX.md` -> `fix-bug.md`, `REVIEW_CHECKLIST.md` -> `review.md`).
Most package READMEs only need the "see `docs/refactor/PLAN.md`" lines removed (rung history).

- `crc/aws/README.md:7,9,51,71` (`docs/refactor/PLAN.md`, `FILTER_VARIANTS.md`, `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`)
- `crc/aws/disk/README.md:59` (`docs/refactor/PLAN.md`)
- `crc/aws/elastic_ips/README.md:30` (`docs/refactor/PLAN.md`)
- `crc/aws/keypairs/README.md:27` (`docs/refactor/PLAN.md`)
- `crc/aws/kms/README.md:38` (`docs/refactor/PLAN.md`)
- `crc/aws/snapshot/README.md:35` (`docs/refactor/PLAN.md`)
- `crc/aws/vm/README.md:55` (`docs/refactor/PLAN.md`)
- `crc/aws/vpc/README.md:42` (`docs/refactor/PLAN.md`)
- `crc/azu/README.md:7,9,37,51` (`docs/refactor/PLAN.md`, `FILTER_VARIANTS.md`, `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`)
- `crc/azu/disk/README.md:33` (`docs/refactor/PLAN.md`)
- `crc/azu/ip/README.md:29` (`docs/refactor/PLAN.md`)
- `crc/azu/nic/README.md:32` (`docs/refactor/PLAN.md`)
- `crc/azu/vm/README.md:46` (`FILTER_VARIANTS.md`)
- `crc/core/README.md:10,17,141,159,167` (`docs/refactor/FILTER_VARIANTS.md`, `PLAN.md`, `LOGGING.md`, `reviews/FIX-CORE-AGE.md`)
- `crc/core/logger.py:3` (`docs/refactor/LOGGING.md`)
- `crc/core/policy.py:4` (`docs/refactor/FILTER_VARIANTS.md`)
- `crc/gcp/README.md:7,9,56,69,74` (`docs/refactor/PLAN.md`, `FILTER_VARIANTS.md`, `LOGGING.md`, `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`)
- `crc/gcp/spot_vm/README.md:5,58` (`docs/refactor/reviews/FIX-GCP-SPOT.md`, `docs/refactor/PLAN.md`)
- `crc/oci/README.md:7,9,33,39,45` (`docs/refactor/PLAN.md`, `FILTER_VARIANTS.md`, `LOGGING.md`, `.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`)
- `crc/oci/disk/README.md:42,47` (`docs/refactor/FILTER_VARIANTS.md`, `PLAN.md`)
- `crc/oci/spot_vm/README.md:71` (`docs/refactor/PLAN.md`)
- `crc/oci/vm/README.md:40,50` (`docs/refactor/FILTER_VARIANTS.md`, `PLAN.md`)
- `tests/README.md:19,65` (`docs/refactor/LOGGING.md`, `.cursor/skills/crc-python-refactor/SKILL.md`)
- `tests/legacy/README.md:65` (`.cursor/skills/crc-python-refactor/BEHAVIOUR_FIX.md`)
- `tests/helpers_log_fix.py:6`, `tests/log_fix_aws_compute.py:6`, `tests/log_fix_azure.py:4`, `tests/log_fix_gcp.py:5`, `tests/log_fix_oci.py:4` (`docs/refactor/LOGGING.md`)
- `tests/test_aws_kms_parity.py:697`, `tests/test_gcp_ip_parity.py:37` (`BEHAVIOUR_FIX.md`)
- `docs/refactor/BUGS.md` (4 rows cite archived reviews or PLAN rung names; must stay as is, per owner).

## New bugs

No code bugs fixed. Doc lies and stale wording noticed (not fixed; owners of those files):

- `crc/core/engine.py:1` says "nothing is wired yet" (production routes through it; `crc/cli/bridge.py`).
- `crc/core/engine.py:35-36` comment calls `vpc` label "a placeholder for the wiring rung".
- `crc/core/selection.py:1-5` says "the wiring rung only calls `build_selection()`"; production selection is
  `crc/cli/dispatch.py` `validate_targets`, so `selection.py` is not what `main()` uses.
- `crc/aws/keypairs/__init__.py:6` docstring references rung name `ENG-0`.
- `crc/cli/routes/azu.py:1` says "Empty until its lane migrates them".
- `crc/cli/README.md` Extension Rules 1 ("only in a rung that allows a new flag") and the rung / FIX-* wording
  throughout its Safety Invariants.
- `tests/archtest/bug_refs.py:15` remediation cites "AGENTS.md hard rule 11" and "main-branch file:line";
  the rule is now `AGENTS.md` rule 5 and locations are current `file:line`.
- `tests/archtest/frozen_inputs.py:13-14` remediation cites "AGENTS.md hard rules 5 and 7"; in `AGENTS.md`
  `tests/legacy/*.txt` is now rule 3 and `requirements*.txt` is rule 7.
- `docs/refactor/bugs/README.md` and `docs/refactor/bugs/pending/README.md` still describe refactor lanes and
  behaviour-fix rungs (files kept as is, per the contract).
- Parity tests against `tests/legacy/crc_main.txt` make a new optional flag fail
  (`tests/test_cli_args_parity.py` `DEFAULTS_EXPECTED` and the oracle comparison); `AGENTS.md` tells agents to
  extend the expected tables, which the engine/test lanes should confirm is workable.

## Round 1 (Argon)

### Findings

- The rewrite of `AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`, `.cursor/rules/*.mdc`,
  `.cursor/skills/crc-maintain/*`, `docs/logging.md`, `docs/README.md`, `docs/refactor/README.md`,
  `docs/archive/README.md`, `.github/CODEOWNERS`, `.github/PULL_REQUEST_TEMPLATE.md` and
  `.github/ISSUE_TEMPLATE/*.md`, plus the new `ARCH-010` and `ARCH-011` checks and the `ARCH-002`
  relaxation, meets the DOCS-1 contract and the guidance in `AGENT-NATIVE-AUDIT.md` (sections 2,
  3.1, 3.5, 3.6 and 4).
- Two minor cleanups applied in Round 1:
  1. Replaced the remaining prose phrase "refactor history" in `AGENTS.md:43`, `docs/README.md:12`
     and `docs/refactor/README.md:5` with "Archived history" / "Historical documents" so
     `grep -rni "rung\|refactor\|R0a\|R4"` on active docs matches only the preserved
     `docs/refactor/BUGS.md` and `docs/refactor/bugs/` paths.
  2. Expanded `## Links to fix` and `## New bugs` above with the exact `file:line` inventory across
     `crc/**/README.md`, `crc/core/*.py` and `tests/**` for the follow-up docs lane.

### Gates and checks

1. **New-agent onboarding & code spot-checks (12 verified):** Reading `AGENTS.md` (105 lines) plus
   at most two linked files (`ARCHITECTURE.md` or `.cursor/skills/crc-maintain/fix-bug.md` +
   `CLEANUP_SAFETY.md`) gives complete, accurate instructions to fix a bug, add/change a cleaner on
   the engine path, change a CLI flag under the `cloud-resource-cleanup-triggers` contract, or
   review a PR. Spot-checked against code:
   - `CLAUDE.md -> AGENTS.md` and `.claude/skills/crc-maintain -> ../../.cursor/skills/crc-maintain` symlinks (`AGENTS.md:3`).
   - `crc.py` re-export shim (`AGENTS.md:33`, `crc.py:1-102`).
   - `Makefile` targets `check`, `check-fast`, `lint`, `test-file`, `raise-test-floor` (`AGENTS.md:17-22`, `Makefile:1-26`).
   - Engine routing in `crc/cli/dispatch.py:186-189`, `crc/cli/bridge.py:25-77`, and `crc/cli/routes/aws.py:1-20` (`AGENTS.md:81-86`, `ARCHITECTURE.md:36-47`).
   - Legacy `HANDLERS` fallback in `crc/cli/dispatch.py:146-156` (`AGENTS.md:84-86`, `ARCHITECTURE.md:44-47`).
   - `crc/core/engine.py:34-121` `run()`, `KIND_LABELS`, `Notifier.fail_open` (`ARCHITECTURE.md:50-55`).
   - `crc/core/registry.py:12-83` `@register`, `DEFAULT_REGISTRY`, `DuplicateCleanerError`, `UnknownCleanerError` (`ARCHITECTURE.md:57-61`).
   - `crc/core/cleaner.py:18-143` `Cleaner` protocol and frozen dataclasses (`ARCHITECTURE.md:65-73`).
   - `crc/aws/keypairs/` model cleaner (`cleaner.py`, `client.py`, `__init__.py`) (`ARCHITECTURE.md:101-116`).
   - `crc/cli/args.py` / `args_run.py` `_add_*_args` groups and `tests/test_cli_args_parity.py` (`AGENTS.md:95-97`).
   - `crc/cli/scan.py:131-152` read-only `--scan_tag` with `sys.exit(0)` (`ARCHITECTURE.md:20-21`, `CLEANUP_SAFETY.md:42-43`).
   - `crc/core/logger.py:16-26,115-199` run-log line formats and `_OPTION_KEYS` order (`docs/logging.md:31-67`).
2. **No refactor or rung history in active docs:** `grep -rni "rung\|refactor\|R0a\|R4" AGENTS.md ARCHITECTURE.md CLEANUP_SAFETY.md .cursor docs/*.md` matches only `docs/refactor/BUGS.md` and `docs/refactor/bugs/` path references.
3. **Safety rules and bug register:** No `SAF-001`..`SAF-046` rule weakened, dropped or renumbered (`test_safety_lock.py` 16/16 green); `docs/refactor/BUGS.md` and `docs/refactor/bugs/` have zero diff.
4. **New & updated mechanical checks:** `ARCH-010` (`doc_budget.py`, `test_arch010_doc_budget.py`), `ARCH-011` (`doc_links.py`, `test_arch011_doc_links.py`) and relaxed `ARCH-002` (`readmes.py`, `test_arch002_readmes.py`) pass with `tmp_path` self-tests and `REMEDIATION:` paragraphs.
5. **Scope, `git mv` and links:** Only allowed files changed (plus `.claude/skills/crc-maintain` symlink target update so it does not dangle); `git mv` (`R100`) used for archived files; all relative links in active docs resolve (`ARCH-011` green).
6. **Full `make check`:** `43095 passed in 52.59s` (total `make check` wall time `58.6s`; `ARCH-008: 43095 tests, floor 43078`).

VERDICT: APPROVE

## Round 2 (Opus)

### Round 1 fixes checked

- The "refactor history" wording that Round 1 removed is gone. The Round 1 `## Links to fix` and
  `## New bugs` inventories are accurate as far as I sampled (`crc/core/engine.py:1,33`,
  `crc/core/selection.py:1-5`, `crc/cli/routes/__init__.py`).
- Round 1 missed two doc lies (below). Its "Only allowed files changed" is true in spirit only:
  `.claude/skills/crc-maintain` (symlink retarget, needed so the link does not dangle) and the
  existing test `tests/archtest/test_arch002_readmes.py` (needed because ARCH-002 was relaxed) are
  outside the listed paths. Both are justified; I accept them.

### Findings and fixes (Round 2)

1. Doc lie, fixed: `ARCHITECTURE.md` layer table said `crc/cli` is the "only layer that reads env,
   prints, exits", and `.cursor/rules/20-core.mdc` said the same. False today: `crc/core/age.py:49`
   reads `MAX_AGE`, `crc/notify/slack_owners.py:32,48,68` prints, `crc/azu/_base.py:27-31` reads
   `os.environ`. ARCH-003 is the real limit. Reworded both to "the layer that reads env, prints and
   exits (other layers: ARCH-003)" / "new env reads, prints and exits go in `crc/cli`".
2. Doc lie, fixed: `ARCHITECTURE.md` "Core policy and age" said `crc/core/age*.py` is pure, with no I/O.
   `is_old` reads `MAX_AGE`, which `crc/cli/clients.py:33-37` `set_max_age` exports. Added that one
   exception to `ARCHITECTURE.md` and `20-core.mdc`.
3. Inaccuracy, fixed: `ARCHITECTURE.md` named the route fields `Route(options_builder, ...)`. The
   field is `options` (`crc/cli/routes/__init__.py`).
4. Wave history, fixed: in `tests/archtest/README.md`, ARCH-003 said "its env ban waits for Wave B".
   It now says "no env ban: `crc/core/age.py` reads `MAX_AGE`".
5. Not fixed, minor: ARCH-011 recognises only inline links `[t](target)`. It does not check
   reference-style links (`[t]: target`), `<angle>` targets or targets that contain `)`. None are
   used in the scoped docs today. Extend the check if one is added.
6. Not fixed, informational: `make check` here took 76 s wall time (pytest 55.5 s, with `-n auto`).
   `AGENTS.md` says "about 40 s", which matches the contract's figure. The time depends on the
   machine.

### Gates

1. New-agent read: with `AGENTS.md` plus `ARCHITECTURE.md` (add a cleaner) or `fix-bug.md` (fix a
   bug), an agent knows which files to touch and which checks to run. I checked 12 statements
   against the code. These were true:
   - `Makefile` targets and `SLOW_PARITY` (three files)
   - `bridge.py` `route_for` / `run_routed` / `build_notifiers` (Slack then Influx, `notify=False`)
   - `dispatch.py:186-189` route before `HANDLERS`
   - `routes/aws.py` imports `crc.aws.keypairs` with `noqa: F401`
   - `engine.run`: plan, then apply unless dry run, `Report.from_plan` / `from_result`, `fail_open`, `KIND_LABELS`
   - `registry` `DEFAULT_REGISTRY`, `DuplicateCleanerError`, `UnknownCleanerError`
   - `Operation` delete/stop
   - `RESOURCES` in `constants.py`
   - pair rules in `pairs.py`
   - labels in `report.py`
   - `_add_*_args` in `args.py` / `args_run.py`
   - `DEFAULTS_EXPECTED` and the oracle in `test_cli_args_parity.py`
   - `scan.py` forces dry run and exits 0
   - `main()` calls `set_max_age`
   - `literal_eval` flags
   - the import-linter contracts

   Three statements were false or inaccurate; they are findings 1 to 3 above.
2. `grep -rni "rung\|refactor\|R0a\|R4"` over the active docs matches only the preserved
   `docs/refactor/BUGS.md` and `docs/refactor/bugs/` paths.
3. SAF IDs before and after are identical (diff of `grep -o SAF-[0-9]*`). The only `CLEANUP_SAFETY.md`
   edits are the two wording changes. `docs/refactor/BUGS.md` and `docs/refactor/bugs/` have no diff.
4. ARCH-010, ARCH-011 and the ARCH-002 relaxation each have a rule ID, a `REMEDIATION:` paragraph and
   `tmp_path` self-tests:
   - ARCH-010 tests over the limit, at the limit, and files with no budget.
   - ARCH-011 tests a missing target, resolution relative to the document, `.cursor`, anchors,
     external links, code, fences and root-relative links.
   - ARCH-002 tests directories without code and code under `docs/`.

   The checks are not brittle: the limits are constants pinned by a test, and the link check uses
   the filesystem rather than a list. The 38 archtest tests for these checks pass. Doc sizes:
   `AGENTS.md` 105/120, rule files 15-19/40, `SKILL.md` 52/150.
5. Scope: allowed files only, plus the two justified extras above. The archive moves are git
   renames (`R100`). `CLAUDE.md` is still a symlink to `AGENTS.md`. All links resolve (ARCH-011 green).
6. `make check` (after `pip install -r requirements.txt -r requirements-dev.txt`) passes:
   `43095 passed in 55.52s`, 76 s wall time, ARCH-008 43095 against a floor of 43078. It is green
   again after the Round 2 edits: 43095 tests, 56 s wall time.

## New bugs (Round 2)

- `crc/azu/_base.py:27-31` reads `os.environ` in a cloud package (legacy adapter base; ARCH-003 exempts
  it through the ratchet). Move the env reads to `crc/cli` when Azure kinds move to the engine.
- `crc/notify/slack_owners.py:32,48,68` uses `print` instead of the logger. ARCH-003 does not ban
  `print` in `crc/notify`.

ROUND 2 VERDICT: APPROVE
