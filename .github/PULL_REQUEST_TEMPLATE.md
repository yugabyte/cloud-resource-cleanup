## What and why

<!-- One or two sentences. Name the bug ID (BUG-NNN) if this fixes a row of docs/refactor/BUGS.md. -->

## Kind of change

- [ ] Bug fix (`BUG-NNN`: )
- [ ] New or changed cleaner (engine path: registered cleaner plus `crc/cli/routes/<cloud>.py`)
- [ ] CLI flag change (existing flags unchanged; new flag optional)
- [ ] Docs, tests or tooling only

## Safety and contract checklist

- [ ] `make check` is green; `make raise-test-floor` run if tests were added
- [ ] Fails closed on uncertain age, tags or telemetry; destructive call order unchanged
- [ ] `CLEANUP_SAFETY.md` updated if destructive behaviour changed; no `SAF-NNN` removed or weakened
- [ ] No test deleted, skipped or loosened; no locked or guardrail file edited (`tests/legacy`, `tests/archtest` data, `pyproject.toml`, `Makefile`, `requirements*.txt`)
- [ ] Offline tests only; no real cloud call or credential
- [ ] Package `README.md` and docs match the code
- [ ] New bugs noticed are rows in `docs/refactor/BUGS.md`

## CLI contract (`cloud-resource-cleanup-triggers`)

<!-- Which Jenkins jobs call the changed flags? "None, additive" or "no flag change" is a valid answer. -->

## Verification

<!-- Commands run and result, for example: make check -> green, N tests. -->
