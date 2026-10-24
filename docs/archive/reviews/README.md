# docs/refactor/reviews

## Purpose

One review per rung, written by a separate review run with `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`:
`R0a.md` to `R4c.md`, the lane rungs (`AWS-*.md`, `AZ-*.md`, `GCP-*.md`, `OCI-*.md`, `CLI-*.md`), the shared-helper rungs (`PL-*.md`), the subpackage rungs (`ST-*.md`) and `FINAL.md`. Each states the scope reviewed, the
findings, what the reviewer fixed and the verdict.

## Public API

- `<rung>.md`: the review of one rung, named exactly like its row in `docs/refactor/STATUS.md`.

## Safety Invariants

- A review only approves when `make check` is green, the test count did not drop and no safety test
  was weakened (checklist section 1).
- Reviews are history: do not rewrite an earlier review; add a new one for a re-review.

## Extension Rules

1. Create `<rung>.md` for the rung just implemented; link it from the `STATUS.md` row.
2. Record bugs found during review in `docs/refactor/BUGS.md` too.
