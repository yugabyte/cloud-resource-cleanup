# R0a review

Scope reviewed: `git diff c7c3159..HEAD` (commit `73fe1a5`, R0a tooling and docs)
against `docs/refactor/PLAN.md`, `AGENTS.md`, `ARCHITECTURE.md`,
`CLEANUP_SAFETY.md` and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

## Scope and behaviour

- Every R0a deliverable exists: `pyproject.toml`, `requirements-dev.txt`,
  `.cursor/environment.json`, `AGENTS.md` (70 lines), `CLAUDE.md` (symlink to
  `AGENTS.md`), `ARCHITECTURE.md`, `.github/CODEOWNERS`, `.cursor/rules/*.mdc`,
  `.cursor/skills/crc-python-refactor/` (plus `.claude/skills` symlink),
  Makefile `check`.
- No behaviour change: nothing under `crc/`, `crc.py` or `tests/` changed.
  `pytest.ini` was removed and its config (`pythonpath`, `testpaths`) moved to
  `[tool.pytest.ini_options]` unchanged. `requirements.txt` unchanged.
- No R0b work started (`tests/archtest/` absent, no CI workflow).

## BLOCKING (fixed in this run)

1. Legacy ratchet exempted whole trees (`pyproject.toml`): ruff
   `extend-exclude` listed `"crc"` and `"tests"`, and the mypy override used
   `module = ["crc", "crc.*"]`. Every new module under `crc/core`, `crc/notify`
   and every new test would never be linted or type-checked. Fixed: both lists
   now name each pre-existing file / module explicitly (`crc.py`, 35 `crc/**`
   modules, 6 `tests/*.py` files). The comment states no directories or globs
   and shrink-only. Verified with a throwaway `crc/core/_probe.py` and
   `tests/test_probe.py`: ruff and mypy both reported errors on them (removed
   afterwards).
2. mypy guard and `crc.py` / `crc/` name clash (`pyproject.toml`, `Makefile`):
   `[tool.mypy]` now sets `files = ["crc", "tests"]` and
   `exclude = ['^crc\.py$']`; `make check` runs bare `mypy`. `crc.py` is left
   out of mypy (clashes with the `crc` package as module `crc`); the CLI rung
   must resolve it. Documented in `pyproject.toml` and `ARCHITECTURE.md`
   ("Legacy ratchet"). `AGENTS.md` and `REVIEW_CHECKLIST.md` updated from
   `mypy crc` to `mypy`.
3. `.cursor/environment.json` install required `python3.11`, which is not on
   the image (only 3.12, without `ensurepip`), so the install failed. Fixed:
   uses a `python3.11` venv when available, otherwise
   `python3 -m pip install --user`. `Makefile` now runs every tool as
   `$(PYTHON) -m ...` (`.venv/bin/python` when present, else `python3`) so
   `make check` works without `~/.local/bin` on `PATH`.
4. The skill and checklist asked refactor rungs to change behaviour
   (`.cursor/skills/crc-python-refactor/SKILL.md` 4.3 and the `--max_age` line;
   `REVIEW_CHECKLIST.md` section 2). They required every custom age tag to fail
   closed and `--max_age` to never shorten a floor. On main (`CLEANUP_SAFETY.md`
   "Age gates"), only the `validated_retention_age` callers skip on bad custom
   age tags; other modules fail open, and `MAX_AGE` can shorten a floor inside
   `is_old`. Following the skill would have changed behaviour in a refactor
   rung, or made reviewers reject a faithful refactor. Fixed: the wording now
   says to keep today's behaviour, that new modules must fail closed, and that
   fixes belong to the later behaviour-fix rungs in PLAN.md. The circuit
   breaker, `describe_regions` auth, Azure SpotVM list-error and CloudWatch
   rules were already correct.

## NIT (left)

- `requirements-dev.txt` uses `>=` lower bounds, not pins. Tool upgrades can
  turn `make check` red without a code change. Consider pinning in R0b with CI.
- `pyproject.toml`: the core layer and "core forbids SDKs" import-linter
  contracts are a commented TODO until `crc/core` exists (R1/R2). This is
  acceptable, but the rung that creates `crc/core` must enable them.
- mypy strict will report `import-untyped` for `boto3`/`azure`/`oci` once a
  cloud module leaves the ratchet. Stubs or per-SDK `ignore_missing_imports`
  are needed then and must not be added to the ratchet list.
- `.github/CODEOWNERS` covers all guardrail files, including `/tests/archtest/`
  ahead of R0b. It only takes effect with branch protection requiring code
  owner review.
- The import-linter `root_package = "crc"` does not see `crc.py`. That is fine
  until the CLI rung adds a contract for it.
- Skill: no `async`, `pydantic`, `httpx` or new frameworks are recommended; all
  are listed as forbidden. Cursor rules are short and correctly globbed.
  `ARCHITECTURE.md` layout and dependency rules match PLAN.md.

## Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check` on
Python 3.12.3 (target `py311`):

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 41 source files |
| `lint-imports` | 1 kept, 0 broken ("Cloud packages are independent") |
| `pytest -q` | 110 passed |

Test count: 110 on `c7c3159`, 110 after R0a and after the review fixes.

VERDICT: APPROVE
