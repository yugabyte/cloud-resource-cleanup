# R1a review

Scope reviewed: `git diff 5e4e11b..599b136` (commit `599b136`, R1a age handling
in `crc.core.age` and core import-linter contracts) against `5e4e11b:crc/service.py`,
`docs/refactor/PLAN.md`, `AGENTS.md`, `ARCHITECTURE.md`, `CLEANUP_SAFETY.md`
and `.cursor/skills/crc-python-refactor/REVIEW_CHECKLIST.md`.

## Scope and behaviour

- Behaviour parity (`crc/core/age.py` vs `5e4e11b:crc/service.py`):
  `AGE_KEYS`, `require_positive_age`, `lookup_custom_age_tag`,
  `validated_retention_age`, `get_min_age`, `is_old`, `parse_literal` and
  `get_retention_age` match `5e4e11b:crc/service.py` across every branch,
  return value, exception type, exception message, exception cause (`from e`),
  log level, log message and the `MAX_AGE` environment variable override.
- `crc/service.py` retains `logs_dir`, `logs_file`, `AGE_KEYS` and every
  existing method with identical signatures (`__init__`,
  `require_positive_age`, `_lookup_custom_age_tag`, `validated_retention_age`,
  `get_min_age`, `is_old`, `_parse_literal`, `get_retention_age`), delegating
  all age logic to `crc.core.age`.
- No other `crc/**` module, `crc.py`, `requirements.txt`, `requirements-dev.txt`
  or pre-existing `tests/**` file was modified.
- `pyproject.toml`: neither the ruff `extend-exclude` list nor the mypy override
  list was extended. The two new import-linter contracts
  (`"Core never imports cloud packages or services"` and
  `"Core never imports cloud or telemetry SDKs"` with
  `include_external_packages = true`) are active and verified by adding a
  throwaway `import boto3` in `crc/core/age.py`, confirming `lint-imports`
  failed with `crc.core.age -> boto3`, and reverting.
- `crc/core/README.md` passes `ARCH-002` with all required sections (`Purpose`,
  `Public API`, `Safety Invariants`, `Extension Rules`), accurate symbol sync
  against `crc/core/__init__.__all__` and `crc/core/age.py`, and accurate
  `SAF-006`/`SAF-008`/`SAF-009`/`SAF-010` descriptions.

## BLOCKING (fixed in this run)

1. Missing test coverage for the `if not (set(age) & AGE_KEYS):` branch in
   `require_positive_age` (`crc/core/age.py:52-53`, `tests/test_core_age.py`):
   ported verbatim from `crc/service.py:52-53`, this guard raises
   `ValueError(f"{label} must include days and/or hours")` when `age` is a
   truthy `dict` subclass with no keys (so `not age` is `False` and
   `set(age) - AGE_KEYS` is empty), and had 0 hits under `test_core_age.py`.
   Fixed: added `_TruthyEmptyDict` and the parametrized case
   `(_TruthyEmptyDict(), "age must include days and/or hours")` to
   `test_require_positive_age_rejects` in `tests/test_core_age.py`. Every
   executable line and branch in `crc/core/age.py` is now exercised and
   asserted.

## NIT (left)

- `crc.core.age.is_old` (`crc/core/age.py:138`) still reads `os.getenv("MAX_AGE")`
  directly to preserve exact parity with `Service.is_old`. Moving environment
  reads to the CLI boundary belongs to the later CLI / behaviour-fix rungs.
- `crc/core/age.py:227` types `get_retention_age` as `-> Any` because
  `ast.literal_eval` can return any literal (`dict`, `int`, etc.), while
  `crc/service.py:92` keeps the legacy `-> Optional[str]` annotation for
  compatibility until `Service` is retired.

## Gates

`pip install -r requirements.txt -r requirements-dev.txt` then `make check`:

| Gate | Result |
|------|--------|
| `ruff check .` | All checks passed |
| `mypy` (`crc`, `tests`) | Success: no issues found in 59 source files |
| `lint-imports` | 3 kept, 0 broken ("Cloud packages are independent", "Core never imports cloud packages or services", "Core never imports cloud or telemetry SDKs") |
| `pytest -q` | 348 passed (190 pre-R1a + 158 `test_core_age.py`) |

Test count: 190 on `5e4e11b`, 347 on `599b136`, 348 after the review fix.

VERDICT: APPROVE
