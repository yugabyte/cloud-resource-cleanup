---
name: crc-python-refactor
description: Implementer rules for the CRC Python 3.11 refactor: Cleaner protocol, registry decorators, pure policy, strict typing, CLEANUP_SAFETY.md error handling, and offline pytest mocking. Use when refactoring or adding code under crc/ or tests/.
---

# crc-python-refactor (implementer)

Behaviour changes only in a behaviour-fix rung, and only for the bugs it names:
follow `BEHAVIOUR_FIX.md`. Every other change keeps behaviour, CLI flags, env vars
and JSON flag values the same.
Read `AGENTS.md`, `ARCHITECTURE.md` and `CLEANUP_SAFETY.md` first. Run
`make check` before committing. Reviewers use `REVIEW_CHECKLIST.md`.

Forbidden: `async`/`await`, `pydantic`, `httpx`, `tenacity`, `@singleton`,
`Result[T, E]` wrappers, new entries in `requirements.txt`, live cloud calls.

## 1. Protocols, DTOs and registry decorators

- 1.1 Define the `Cleaner` interface with `typing.Protocol`: `plan()` is
  read-only, `apply()` performs mutations. Use `@runtime_checkable` only when an
  `isinstance` check is needed at runtime.
- 1.2 Use `@dataclass(frozen=True, slots=True)` with
  `field(default_factory=...)` for resource descriptors, policies and plans.
  Never pass raw SDK response dicts or models across module boundaries.
- 1.3 Type registration decorators with `Callable`, `TypeVar` and
  `functools.wraps`, e.g. `@register("aws", "disk")` from `crc.core.registry`. No
  singletons, no hidden mutable state beyond the registry, no import-time cloud
  calls.
- 1.4 Inject SDK clients, policy objects and loggers through constructors or
  factories so tests pass mocks without patching module globals.

## 2. Strict type safety

- 2.1 Annotate every function, method and attribute; write `-> None` explicitly.
- 2.2 Use `T | None`, `list[str]`, `dict[str, list[str]]` and
  `collections.abc` types. No bare `list`/`dict`. Confine `Any` to untyped SDK
  boundaries and narrow immediately.
- 2.3 Narrow optionals with explicit guards (`if value is None:`). Do not use
  `# type: ignore` to hide a possible `None`.
- 2.4 Keep `strict = true` in `pyproject.toml`. Overrides are only the legacy
  ratchet, which may only shrink. When you refactor a module, remove it from the
  ruff `extend-exclude` and mypy override lists and make it pass.
- Ruff limits: complexity 10, 40 statements, 6 arguments, line length 100,
  rules `E,F,W,I,B,UP,FURB,SIM,PERF,C901,PLR,RUF`. Split functions, do not
  raise the limits.

## 3. Pure policy and layer contracts

- 3.1 Tag, age and regex filtering lives in `crc/core/policy.py` and
  `crc/core/age.py` as pure functions on DTOs: no SDK calls, no env reads, no
  hidden clock. Cleaners fetch and normalise; policy decides.
- 3.2 Parse and validate CLI values (`--age`, `--detach_age`, `--max_age`,
  `--cloud`, `--resource`, regexes) at the boundary, before any cloud call.
  Keep JSON-style values like `'{"days": 1}'` accepted.
- 3.3 Declare `__all__` in package `__init__.py`. Respect import-linter:
  cloud packages never import each other; `crc.core` never imports a cloud
  package or SDK. Do not edit contracts to make code pass.

## 4. Error handling and CLEANUP_SAFETY.md

- 4.1 Raise specific exceptions (`ValueError`, `TypeError`, `RuntimeError`) with
  context (what, resource id, region, why). Chain with `raise ... from e`.
- 4.2 Circuit breakers and list/auth failures must raise: the `aws` + `disk`
  opt-in breaker, `describe_regions` auth errors, Azure `SpotVM` list errors.
  Never wrap them in catch-all batch loops or a `BatchResult`.
- 4.3 Fail closed: if CloudWatch data is incomplete, keep the volume; where a
  module uses `Service.validated_retention_age` (spot modules), a custom age
  tag that is empty, malformed, zero or overflows skips the resource. Modules
  that fail open on main (`get_retention_age` alone) keep that behaviour in a
  refactor rung; making them fail closed is a later behaviour-fix rung. New
  modules must fail closed.
- 4.4 Isolate per-resource errors only where `CLEANUP_SAFETY.md` says so (per
  volume in AWS disk delete, per VM in Azure SpotVM delete): catch specific SDK
  errors, log the id, set the error flag, continue. Never `except Exception: pass`.
- 4.5 One retry layer: do not add `@retry` over `botocore` or Azure/OCI SDK
  retries. Preserve destructive order, e.g. AWS spot: tag
  `DeleteOnTermination=false` volumes, cancel request, terminate instance, wait
  for volume `available`, delete volumes. Abort termination if cancel fails and
  clear `crc_leftover_of` / `crc_leftover_tag_*` tags on cancel/terminate failure.
- Keep today's `--max_age` / `MAX_AGE` semantics (on main it can shorten a floor
  inside `is_old`, see `CLEANUP_SAFETY.md`); never add a new place where it
  shortens a floor set by another flag.

## 5. Unit tests and SDK mocking

- 5.1 Tests are offline `pytest` with `unittest.mock` (`Mock`, `MagicMock`,
  `patch`, `call`). Mock `boto3`, Azure, GCP and OCI clients, paginators, pollers
  and `ClientError`. No credentials.
- 5.2 Use `@pytest.mark.parametrize` for tag/age/regex matrices and
  `pytest.raises(Exc, match=...)` for breakers and invalid input. Cover the
  fail-closed and error paths, and assert call order with `mock_calls`.
- 5.3 Use function-scoped fixtures, `monkeypatch` for env vars, `tmp_path` for
  files. No `autouse` session fixtures or shared mutable state.
- Never delete, skip or weaken an existing test. The test count must not drop.
- `plan()` tests assert that no mutating method on the client was called.
