## Coding summary

Rung FIX-CORE-AGE (wave FIX-1): `--custom_age_tag_key` is now fail closed for a tag that is present but broken.

- **Bugs fixed.** BUG-004 (empty or unparseable custom age tag fell back to `--age`) and BUG-016 (a parsed
  custom age that is not finite, zero or negative was used as is). As a side effect the same code path
  now keeps instead of failing in the variants BUG-208 (Azure VM `{'days': 1e999}` raised `OverflowError`) and
  BUG-182 (float, string or list tag: `TypeError` or never old); the driver decides whether to mark those rows.
- **Bugs left.** None of the two. BUG-182's `True` / `{'days': True}` case (1 day) is unchanged (see New bugs).
- **How.** `crc/core/age.py`: `get_retention_age` (used by the 9 non-spot call sites: aws disk/kms/vm, azure disk/vm,
  gcp disk/vm, oci disk/vm) now validates with the same code as `validated_retention_age`
  (`_checked_retention_age`, same messages; `validated_retention_age` still logs at WARNING and raises
  `ValueError`, the spot modules are untouched). No key, no tags or tag absent: `None` (use `--age`) as before.
  Valid tag: the parsed literal as before (so calls, selections and logs equal today). Present but empty, whitespace,
  `None`/non-string value, unparseable, not an int/dict, unsupported key, not finite, zero or negative:
  the keep marker `KEEP_AGE` (`{"days": 1000000}`), the run log counts `skip("bad_age_tag")` and the cause is
  logged at DEBUG only. `is_old` returns False for `KEEP_AGE` before the `MAX_AGE` override, so the
  production `--max_age {"days": 21}` cannot shorten it. The other callers need no change because they all
  pass the value through `policy.effective_age` into `is_old` (AWS disk through `normalize_age` and
  `older_than_floor`, where 1,000,000 days is also never reached). `crc/service.py` only gets a docstring update
  (it delegates unchanged).
- **Production commands.** Every Jenkins command keeps its flags and its selection. Only a resource whose
  `max_retention_age` tag is present but broken changes: it is kept (before: judged by `--age`, or deleted on
  a zero/negative/inf tag). Resources without the tag and resources with a valid tag are unchanged.
- **Direction.** Only keeps more. A resource is never deleted, stopped or released that was kept before; no new
  SDK call, no new flag, no exit-code change (the `OverflowError` / `TypeError` stops become keeps).
- **Files changed.** `crc/core/age.py`, `crc/service.py` (docstring), `crc/core/README.md`, `crc/README.md`,
  `tests/test_core_age.py`, `tests/test_core_age_fix.py` (new), `tests/test_crc_service_parity.py`, and the
  quirk tables of `tests/test_{aws_disk,aws_kms,aws_vm,azure_disk,azure_vm,gcp_disk,gcp_vm,oci_disk,oci_vm}_parity.py`
  (marked `BUG_004_FIXED`: the new outcome is asserted, the other quirks still assert oracle and new). Hash-locked
  tests are not touched and all pass.
- **Parity note.** The module oracles in `tests/legacy/*.txt` (`class VM(Service)`) inherit the live
  `crc.service.Service`, so they cannot show the old custom-age behaviour; the old behaviour is pinned by
  `tests/test_crc_service_parity.py` against the frozen `crc_service.txt` (diverging cases are the index set
  `BUG_004_CASES`, asserted by `test_get_retention_age_bad_tag_diverges_and_keeps`: differs from the oracle,
  returns `KEEP_AGE`, only DEBUG logs). All other cases still equal the oracle. The `test_matches_main` case
  `custom-age-inf-raises` of `tests/test_azure_vm_parity.py` no longer has `fails_in_plan`.
- **Tests.** Before (182466d): 42444 collected, all passed. After: 42491 collected (+47), all pass; `make check` green.
- **Owner actions.** See below.

## Owner action

`CLEANUP_SAFETY.md` is CODEOWNERS: proposed replacement of the SAF-009 sentence ("Other modules that still call
`get_retention_age` alone **fail open** ...") in "Age gates":

> [SAF-009] Modules that use `get_retention_age` (AWS disk/kms/vm, Azure disk/vm, GCP disk/vm, OCI disk/vm) fail
> **closed** too (BUG-004, BUG-016): a custom age tag that is present but empty, unparseable, not finite, zero or
> negative makes `get_retention_age` return `KEEP_AGE`, which `is_old` never treats as old (`MAX_AGE` included), and
> the run log counts `bad_age_tag`. An absent tag still uses `--age` / detach_age.

Also in the Module map row for `crc/service.py`: add "`get_retention_age` returns `KEEP_AGE` for a bad tag".
The READMEs of the per-module packages (`crc/aws/vm/README.md:40`, `crc/oci/disk/README.md:32`,
`crc/oci/vm/README.md:38`) still say "fail-open custom age tag"; I left them to the module FIX rungs / the driver to
avoid merge conflicts.

## New bugs

- Double counting: for a kept bad-tag resource the run log counts `bad_age_tag` (in `get_retention_age`) and the
  module's own later skip (for example `too_young` in `crc/aws/disk/volumes.py:_old_enough`) when it records one;
  `SKIPPED` counts can exceed the number of resources. Exists on main: no (introduced by the keep marker). Fix: switch
  the callers to `validated_retention_age` in their own FIX rungs and drop the marker.
- `crc/aws/disk/volumes.py:_old_enough` (`invalid_age_tag` skip) can no longer see an invalid tag from
  `get_retention_age`; the branch stays for ages that pass validation but fail `normalize_age`. Exists on main: yes
  (dead path now).
- `True` / `{'days': True}` tags are accepted as 1 day by `require_positive_age` (BUG-024 family), so a `True` tag is a
  valid 1-day age. Main: yes (`crc/service.py:214-220`).
- Duplicate AWS tag keys: the first entry decides (empty first entry keeps; before, the first non-empty won,
  BUG-289). AWS tag keys are unique, so no real impact.

## Round 1 (Argon)

- **Findings.** None blocking or nit.
  - (1) **Bugs fixed:** Both `BUG-004` (present but empty/whitespace/`None`/non-string/unparseable custom age tag) and `BUG-016` (present custom age tag that parses to a non-finite, zero, negative, or otherwise invalid age) are fixed in `crc/core/age.py` (`get_retention_age` + `is_old`, delegated by `crc/service.py`). Shared validation in `_checked_retention_age` reuses `lookup_custom_age_tag` and `require_positive_age` with the same semantics as `validated_retention_age`, while keeping `validated_retention_age` (Spot modules) untouched (`WARNING` + `ValueError`). In `get_retention_age`, present-but-bad tags record `logger.current().skip("bad_age_tag")`, log at `DEBUG`, and return a fresh copy of `KEEP_AGE` (`{"days": 1_000_000}`); `is_old` returns `False` for `KEEP_AGE` before `_apply_max_age_override` so `MAX_AGE` cannot shorten it, and AWS disk's `older_than_floor` also keeps it. `tests/test_core_age_fix.py` and `tests/test_crc_service_parity.py::test_get_retention_age_bad_tag_diverges_and_keeps` fail on `182466d` and pass now.
  - (2) **Direction & parity:** Strictly keeps more (or turns mid-run `OverflowError` / `TypeError` on malformed tags into keeps); never deletes, stops, or releases anything kept before. All `tests/legacy/*.txt` oracle files are untouched. Diverging cases in `tests/test_crc_service_parity.py` (`BUG_004_CASES`) assert both inequality with the oracle and the exact new outcome (`KEEP_AGE`, `DEBUG`-only logs); all other cases assert equality with the oracle. Diverging quirk entries in the 9 caller parity tests are marked `BUG_004_FIXED` and assert the new kept outcome.
  - (3) **Production commands:** All 31 Jenkins commands keep working with identical flags and selection for resources without `max_retention_age` or with a valid positive tag; only resources with a present-but-broken `max_retention_age` tag change (kept, even under `--max_age '{"days": 21}'`).
  - (4) **Fail-closed before SDK mutation:** Age checks run during candidate selection (`plan()`) before any delete/stop SDK calls.
  - (5) **Protected files:** `tests/archtest/safety_lock.json`, the 5 hash-locked safety test files, `docs/refactor/PLAN.md`, `docs/refactor/STATUS.md`, `docs/refactor/BUGS.md`, and `CLEANUP_SAFETY.md` are untouched. Proposed `CLEANUP_SAFETY.md` (`SAF-009` and module map) updates are documented under `## Owner action`.
  - (6) **READMEs:** `crc/core/README.md` (`## Public API` and `## Safety Invariants`) and `crc/README.md` (`## Safety Invariants`) are updated and pass `ARCH-002`.
  - (7) **Unfixed bugs:** None (`BUG-004` and `BUG-016` are both fixed).
- **Gates (`make check`):**
  - `ruff check .`: passed
  - `mypy`: passed (`Success: no issues found in 229 source files`)
  - `lint-imports`: passed (`5 kept, 0 broken`)
  - `pytest -q` (including `tests/archtest`): `42491 passed` (up from 42444 on `182466d`, +47 tests)

VERDICT: APPROVE


## Round 2 (Opus)

- **Round 1 check.** Round 1 (Argon) had no findings and made no fixes, so there was nothing to re-check. All points below were checked again independently.
- **(1) Bugs fixed.** `get_retention_age` (`crc/core/age.py:249-284`) now shares `_checked_retention_age` with
  `validated_retention_age`. A present tag that is empty, whitespace-only, `None`, not a string, unparseable,
  not an int/dict, uses an unsupported key, is not finite, is zero or is negative returns a fresh copy of `KEEP_AGE`. The run log counts
  `skip("bad_age_tag")` and the cause is logged at DEBUG. `is_old` returns False for the marker before `MAX_AGE` is
  applied (`age.py:216-218`). I traced all 9 callers (aws disk/kms/vm, azure disk/vm, gcp disk/vm, oci disk/vm). Each one passes the value
  through `policy.effective_age` into `is_old`, except AWS disk, which uses `normalize_age`, then `aws_disk_creation_floor` and
  `is_older_than_floor`. There a 1,000,000-day timedelta is compared against `now - created`, so it never overflows and is never
  reached, and the disk is kept. Absent tag or no key: `None`, so `--age` / `--detach_age` apply as before. A valid tag returns the same
  literal and logs the same lines in the same order. I confirmed the new tests fail on 182466d: with `KEEP_AGE` defined
  locally, `tests/test_core_age_fix.py` gives 38 failed and 9 passed (the 9 that pass are the cases whose outcome is unchanged), and both new files fail at import because `KEEP_AGE` does not exist there.
- **(2) Direction and parity.** The change only keeps more. A bad tag was either judged by `--age`, deleted on a zero, negative or `0` tag
  (`is_old(0)` matched everything), or failed the run (`OverflowError`, `TypeError`); it is now kept. No new SDK call
  and no exit-code change on other inputs. `tests/legacy/` is untouched. In `tests/test_crc_service_parity.py`, the diverging cases are the
  explicit index set `BUG_004_CASES`; each one asserts that it differs from the oracle and returns `KEEP_AGE` with DEBUG-only logs, and every
  other case still asserts equality with the oracle. The module parity files skip the oracle assertion only for the cases named in
  `BUG_004_FIXED`, which is justified because those oracles inherit the live `Service`. Nothing is loosened globally.
- **(3) Production commands.** All 31 commands keep their flags. The selection is unchanged except for resources whose `max_retention_age` tag
  is present but bad; those are kept, even under `--max_age '{"days": 21}'`. The spot commands (`spot_instance_requests`,
  azure `spot_instances`) use `validated_retention_age`, whose result and WARNING logs are unchanged.
- **(4) Fail closed.** The fix keeps the resource instead of raising. The age gate runs inside `plan()` before any delete or stop call.
- **(5) Protected files.** `git diff 182466d..HEAD` touches none of `CLEANUP_SAFETY.md`, PLAN/STATUS/BUGS, `tests/archtest/**`,
  `tests/legacy/**`, the 5 locked tests, AGENTS.md, ARCHITECTURE.md or `.cursor/**`.
- **(6) READMEs.** `crc/core/README.md` (Public API and Safety Invariants) and `crc/README.md` are updated, and ARCH-002 passes.
- **(7) Unfixed bugs.** None.

### Findings

No BLOCKING findings.

- NIT (out of rung scope, for the driver or the module FIX rungs): these module READMEs still say the custom age tag
  "fails open (BUG-004)": `crc/gcp/README.md:55-56`, `crc/azu/README.md:56`, `crc/azu/vm/README.md:39`,
  `crc/aws/vm/README.md:40`, `crc/oci/disk/README.md:32`, `crc/oci/vm/README.md:38`. Proposed text: "custom age tag
  fails closed: a present but bad tag keeps the resource (`get_retention_age` returns `KEEP_AGE`, BUG-004/BUG-016)".
  Round 1's coder note already lists most of these.
- NIT (log text, bug-covered inputs only): `KEEP_AGE` is truthy, so for a bad tag every caller now logs
  `Updating age for <resource>` at INFO (for example `crc/gcp/vm.py:273`, `crc/aws/disk/volumes.py:170`), although the resource is
  kept. Before the fix, an empty or unparseable tag gave `None` and no such line. This is harmless but misleading. It will go away when the
  callers move to `validated_retention_age` in their own rungs, which is the same follow-up as the double-count bug above.
- NIT (equality marker): `is_old` detects the marker with `age == KEEP_AGE`, so a *valid* tag, or an `--age`, of exactly
  `{'days': 1000000}` now ignores a shorter `MAX_AGE` (kept; before, it was shortened to `--max_age`). This only keeps more and
  no real tag uses that value, so I left it.
- NIT: in `crc/aws/kms.py:245`, `get_retention_age` runs before the key state and description checks, so `bad_age_tag` is
  counted for keys that would have been ignored anyway (only the SKIPPED count changes, on bug-covered inputs). This is the same family
  as the double-count bug above.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` passed, then `make check` passed: ruff, mypy and lint-imports (5 contracts kept, 0 broken),
and pytest **42491 passed** (182466d: 42444).

ROUND 2 VERDICT: APPROVE
