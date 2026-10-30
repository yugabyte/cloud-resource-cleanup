# Review a PR

Companion of [SKILL.md](SKILL.md). Reject on any failed item and say which one. Findings first,
with `file:line`; then the verdict.

## 1. Gates and scope

- [ ] `make check` is green on the PR head; CI is green.
- [ ] The PR does one thing and names its `BUG-NNN`, or says why none applies.
- [ ] No guardrail changed to make something pass: `pyproject.toml` limits and contracts,
      `Makefile`, `requirements*.txt`, `tests/archtest/*` data files, `tests/legacy/*.txt`, the
      test floor, `.github/`. These files need an explicit task from the owner.
- [ ] CLI contract intact: no flag, choice, default, env var or value form renamed, removed or
      re-typed. A new flag is optional and defaults to today's behaviour. The PR says which
      `cloud-resource-cleanup-triggers` jobs are affected.

## 2. Safety (`CLEANUP_SAFETY.md`)

- [ ] No deleted, skipped, xfailed or loosened test; assertions only grew.
- [ ] No age gate weakened; no fail-open parsing of age, tags or telemetry; incomplete telemetry
      keeps the resource.
- [ ] Breakers and listing or auth errors still raise (AWS disk opt-in, `describe_regions`,
      Azure spot list).
- [ ] Destructive call order and abort-on-failure behaviour intact (AWS spot: cancel before
      terminate).
- [ ] `--max_age` / `MAX_AGE` still never shortens a floor set by another flag.
- [ ] A changed destructive path updated `CLEANUP_SAFETY.md`; no `SAF-NNN` removed, weakened or
      renumbered.

## 3. Design

- [ ] `plan()` makes no mutating call; every mutation is in `apply()`.
- [ ] New behaviour is on the engine path (`crc/cli/routes/<cloud>.py` plus a registered
      cleaner); `crc/cli` gains no per-kind code.
- [ ] Policy and age decisions use `crc/core`; no SDK object or raw response dict crosses the
      cleaner boundary; SDK clients are injected.
- [ ] Errors are specific, carry the resource id, chain with `from e`; no silent broad catch; no
      added retry layer.

## 4. Tests and docs

- [ ] Every new behaviour has an offline test that fails without the change, including the
      fail-closed path and call order. The test count rose or stayed (floor raised if needed).
- [ ] Parity divergences are marked per case with a bug ID and assert the new outcome.
- [ ] Package README and `CLEANUP_SAFETY.md` match the code; no doc line the diff made false;
      new bugs found are rows in `docs/refactor/BUGS.md`.
