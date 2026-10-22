# REVIEW_CHECKLIST.md (reviewer for crc-python-refactor)

Review the diff against the rung scope in `docs/refactor/PLAN.md`. Reject on any
failed item and say which one. Write the review to `docs/refactor/reviews/<rung>.md`.

## 1. Gates and scope

- [ ] `make check` is green: `ruff check .`, `mypy`, `lint-imports`, `pytest -q`.
- [ ] Test count is not lower than before the rung (compare with STATUS.md).
- [ ] Only the current rung's scope changed; no other rung started.
- [ ] No behaviour change, except in a behaviour-fix rung for the `BUG-NNN` rows
      its PLAN.md entry names, done as `BEHAVIOUR_FIX.md` says (oracles untouched,
      divergence marked per case with the bug ID and asserting the new outcome,
      every other case still equal to `main`). CLI flags, env vars and JSON flag
      values (`'{"days": 1}'`) still work.
- [ ] `requirements.txt` unchanged unless the rung says so.

## 2. CLEANUP_SAFETY.md parity

- [ ] No weakened, deleted, skipped or xfail'd safety test.
- [ ] No age gate weakened; no fail-open tag or age parsing introduced.
- [ ] Custom age tag that is empty, malformed, zero or overflowing still skips
      where it skips today (modules using `Service.validated_retention_age`);
      modules that fail open on main keep doing so until their behaviour-fix rung.
- [ ] Opt-in circuit breaker (`aws` + `disk`), `describe_regions` auth errors and
      Azure `SpotVM` list errors still raise, not swallowed.
- [ ] Incomplete CloudWatch data still keeps the volume.
- [ ] AWS spot order intact: tag volumes, cancel request, terminate, wait
      `available`, delete volumes; cancel failure aborts termination; tags
      cleared on failure.
- [ ] `--max_age` / `MAX_AGE` semantics unchanged (on main it can still shorten
      a floor inside `is_old`); no new place where it shortens a floor.

## 3. Architecture

- [ ] `plan()` is strictly read-only; every mutation is in `apply()`.
- [ ] `crc.core` has no cloud package or SDK import; cloud packages do not import
      each other; import-linter contracts untouched and passing.
- [ ] Policy and age code is pure (no I/O, env or hidden clock).
- [ ] No SDK types or raw response dicts leak across module boundaries.
- [ ] Registration uses the decorator; no singletons or import-time cloud calls.
- [ ] Dependencies injected through constructors.

## 4. Errors and dependencies

- [ ] No silent `except Exception: pass`; specific exceptions, ids logged,
      `raise ... from e`.
- [ ] Per-resource isolation only where `CLEANUP_SAFETY.md` allows it.
- [ ] No double retry over SDK retries.
- [ ] No `async`, `pydantic`, `httpx`, `tenacity`, `Result` wrapper or other
      unapproved dependency.

## 5. Typing, lint, tests

- [ ] All signatures and collections fully typed; no bare `list`/`dict`; `Any`
      only at SDK edges; no unjustified `# type: ignore`.
- [ ] Refactored modules removed from the ruff `extend-exclude` and mypy
      override lists; nothing added to them (the list only shrinks).
- [ ] Ruff limits not raised (complexity 10, statements 40, args 6).
- [ ] Tests are offline, cover fail-closed and error paths, assert SDK call
      order, use function-scoped fixtures.
- [ ] Guardrail files (CODEOWNERS list) untouched unless the rung allows it.

## 6. Docs

- [ ] `docs/refactor/STATUS.md` updated; package README and `CLEANUP_SAFETY.md`
      updated when behaviour documentation changed.
