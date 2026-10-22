# Review ST-AWS-VPC

## Coding summary

Rung ST-AWS-VPC (structure): `crc/aws/vpc.py` and `crc/aws/vpc_api.py` converted into the subpackage `crc/aws/vpc/`, pure move, no behaviour change.

- **Moves (`git mv`).** `crc/aws/vpc.py` -> `crc/aws/vpc/cleaner.py` (393 lines, unchanged except the import line), `crc/aws/vpc_api.py` -> `crc/aws/vpc/client.py` (unchanged). New `crc/aws/vpc/__init__.py` re-exports every public name (`VPC`, `VpcCleaner`, `VpcRuntime`, `build_vpc_cleaner` and all Protocols), so `from crc.aws.vpc import ...` in `crc.py`, `crc/cli/resource_ops.py` and the tests works unchanged. New `crc/aws/vpc/README.md` with the four ARCH-002 sections; the VPC section of `crc/aws/README.md` is trimmed to a pointer.
- **Import sites.** Only the internal import `crc.aws.vpc_api` -> `crc.aws.vpc.client` in `cleaner.py` changed. No other file imported `crc.aws.vpc_api`; no test import line changed; no shim module needed (none of the five hash-locked tests imports the VPC module).
- **monkeypatch targets.** None retargeted: the VPC tests patch `boto3.client`, `boto3.resource`, `boto3.session.Session` and `crc.service.init_logging`, all still looked up where they were.
- **Tests.** Before: 42213 passed. After: 42213 passed (same). `make check` steps (ruff, mypy, lint-imports, pytest incl. archtests) green.
- **Open issues.** `tests/archtest/legacy_baseline.txt` still lists `crc/aws/vpc.py` (stale since AWS-1, guardrail file, not edited; it only ever shrinks). `docs/refactor/FILTER_VARIANTS.md` and `crc/README.md` mention `crc/aws/vpc.py` / `vpc_api.py` paths (not edited; to update: `crc/aws/vpc/cleaner.py`, `crc/aws/vpc/client.py`). `docs/refactor/bugs/pending/AWS-1.md` / `BUGS.md` main file:line refs are of `main` and stay valid.
- **Bugs.** None new: `docs/refactor/bugs/pending/ST-AWS-VPC.md`.

## Round 1 (Argon)

Scope reviewed: `git diff -M d109ee5..HEAD` (`crc/aws/README.md`, `crc/aws/vpc/README.md`, `crc/aws/vpc/__init__.py`, `crc/aws/vpc/cleaner.py`, `crc/aws/vpc/client.py`, `docs/refactor/bugs/pending/ST-AWS-VPC.md`, `docs/refactor/reviews/ST-AWS-VPC.md`) against `AGENTS.md` and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### Verification

1. **Pure move (`git diff -M d109ee5..HEAD`):**
   - `crc/aws/vpc.py` -> `crc/aws/vpc/cleaner.py` (99% similarity, 393 lines): only line 21 changed (`from crc.aws.vpc_api import Ec2Client, Ec2Resource, Vpc` -> `from crc.aws.vpc.client import Ec2Client, Ec2Resource, Vpc`). Zero logic changes.
   - `crc/aws/vpc_api.py` -> `crc/aws/vpc/client.py` (100% similarity, 184 lines): zero changes.
   - `crc/aws/vpc/__init__.py` (48 lines): docstring, re-exports of all 4 public definitions from `cleaner.py` (`VPC`, `VpcCleaner`, `VpcRuntime`, `build_vpc_cleaner`) and all 14 `Protocol`s from `client.py`, plus `__all__`. Importing `crc.aws.vpc` imports `crc.aws.vpc.cleaner`, so `@register("aws", "vpc")` still runs on package import.
2. **Import resolution and test invariance:**
   - Every caller import (`from crc.aws.vpc import VPC` in `crc.py`, `crc/cli/resource_ops.py`, and `tests/legacy/crc_main.txt`; `from crc.aws.vpc import VpcCleaner` in `tests/test_aws_vpc.py`; `from crc.aws.vpc import VPC, VpcCleaner, VpcRuntime, build_vpc_cleaner` in `tests/test_aws_vpc_parity.py`) resolves unchanged via `crc/aws/vpc/__init__.py`.
   - No caller or test in the repository imported `crc.aws.vpc_api`; the five hash-locked safety test files (`SAF-LOCK`) and `tests/legacy/**` are untouched; no test files needed edits or monkeypatch retargets (`tests/test_aws_vpc_parity.py` patches `boto3.client`, `boto3.resource`, `boto3.session.Session`, and `crc.service.init_logging`).
3. **READMEs and ARCH-002 sync:**
   - `crc/aws/vpc/README.md` contains all four required `ARCH-002` sections (`## Purpose`, `## Public API`, `## Safety Invariants`, `## Extension Rules`) and syncs all 18 exported public names.
   - `crc/aws/README.md` touches only the VPC entries and adds `### vpc`, without rewriting any other module's section.
4. **Lane and guardrail compliance:**
   - `docs/refactor/BUGS.md`, `docs/refactor/STATUS.md`, `docs/refactor/FILTER_VARIANTS.md`, `crc/README.md`, `tests/README.md`, `tests/legacy/**`, `crc.py`, `crc/cli/**`, `crc/core/**`, `pyproject.toml`, `.github/CODEOWNERS` guardrail files, and all other modules are untouched.

### BLOCKING (fixed)

None.

### NIT (left)

- `crc.aws.vpc_api` is not shimmed (moved to `crc.aws.vpc.client`), and module-level type aliases / private helpers (`TagSpec`, `ClientFactory`, `ResourceFactory`, `RegionLister`, `_boto3_client`, `_boto3_resource`, `_all_regions`) now live on `crc.aws.vpc.cleaner` rather than directly on `crc.aws.vpc`; no caller or test in the repository imports `crc.aws.vpc_api` or accesses those attributes on `crc.aws.vpc`.
- Shared docs (`docs/refactor/FILTER_VARIANTS.md`, `crc/README.md`) still mention `crc/aws/vpc.py` / `vpc_api.py` (outside lane scope; noted in the coding summary for integration).

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 197 source files |
| `lint-imports` | Contracts: 5 kept, 0 broken |
| `pytest -q` | 42213 passed |

Test count: 42213 before ST-AWS-VPC (`d109ee5`), 42213 after (+0, unchanged).

VERDICT: APPROVE

## Round 2 (Opus)

Scope: `git diff -M d109ee5..HEAD` (round 1 commit `5070d08` included), checked against `AGENTS.md`, `ARCHITECTURE.md`, `docs/refactor/BUGS.md` and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

### Round 1 fixes assessment

Round 1 made no code or test change (review file only), so no fix can have changed behaviour or weakened a test. Its verification is correct: `crc/aws/vpc/cleaner.py` differs from `crc/aws/vpc.py` at `d109ee5` only in the `client` import (line 21), `crc/aws/vpc/client.py` is a 100% rename, and `crc/aws/vpc/__init__.py` re-exports all 18 public names, so `@register("aws", "vpc")` still runs on `import crc.aws.vpc` (asserted by `tests/test_aws_vpc_parity.py:1049-1052`).

### Independent checks

- Behaviour parity: logging goes through the root `logging.*` functions (no `getLogger(__name__)`, no `__file__` / `__module__` use), so the move changes no logger name or log line. No packaging config (`pyproject.toml` has no setuptools package list) needs the new subpackage.
- monkeypatch targets: the VPC tests patch only `boto3.*` and `crc.service.init_logging`; no string target names `crc.aws.vpc.*`.
- Ratchet: `tests/archtest/legacy_baseline.txt:14` still lists `crc/aws/vpc.py`; `tests/archtest/ratchet.py` only requires ruff `extend-exclude` to be a subset of the baseline, so the stale entry is harmless. The new files are not excluded from ruff or mypy.
- Safety: no safety test touched; `plan()` / `apply()` split and the VPC rows of `CLEANUP_SAFETY.md` are unchanged because the code is unchanged.
- Citations: `git show main:crc/aws/vpc.py` confirms `BUG-083` (`:86-92`) and `BUG-012` (`:94`, `:160`) cite the right lines.

### BLOCKING (fixed)

- `docs/refactor/bugs/pending/ST-AWS-VPC.md` pointed to `docs/refactor/bugs/pending/AWS-1.md`, which was merged into `BUGS.md` and deleted in `5f82f96`. Now it cites `BUGS.md` only.

### NIT (left / fixed in passing)

- Fixed: the VPC quirk lines in `crc/aws/vpc/README.md` (Safety Invariants) and `crc/aws/README.md` (VPC bullet) said "the AWS-1 entries"; they now name `BUG-083` to `BUG-095` (AWS-1).
- Left: the coding summary's open issue says `crc/README.md` mentions `vpc.py` / `vpc_api.py`; it does not. `docs/refactor/FILTER_VARIANTS.md:39`, `:58`, `:195` cite `vpc.py` with `main` line numbers, which stay valid as `main` citations; updating them is optional integration work.
- Left (as round 1): `crc.aws.vpc_api` has no shim and the private helpers (`_boto3_client`, `_all_regions`, the type aliases) are on `crc.aws.vpc.cleaner`, not on `crc.aws.vpc`; nothing imports them.

### Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`: `ruff check .` all checks passed; `mypy` no issues in 197 source files; `lint-imports` 5 kept, 0 broken; `pytest -q` 42213 passed. After the README edits `pytest tests/archtest tests/test_aws_vpc.py tests/test_aws_vpc_parity.py` passed (394).

Test count: 42213 (unchanged from `d109ee5`).

New bugs: none (`docs/refactor/bugs/pending/ST-AWS-VPC.md`).

ROUND 2 VERDICT: APPROVE
