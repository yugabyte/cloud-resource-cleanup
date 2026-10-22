"""ARCH-005: `plan` methods must be read-only."""

import ast
from pathlib import Path

from tests.archtest._common import non_legacy_files, parse_module, relpath, violation

RULE = "ARCH-005"
MUTATING_WORDS = (
    "delete",
    "terminate",
    "cancel",
    "stop",
    "deallocate",
    "release",
    "schedule_key_deletion",
    "begin_delete",
    "begin_deallocate",
    "begin_power_off",
    "create_tags",
    "delete_tags",
    "update_tags",
)
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_NORMALISED = tuple(word.replace("_", "").lower() for word in MUTATING_WORDS)


def is_mutating(name: str) -> bool:
    """Case-insensitive match that ignores underscores (`CreateTags` == `create_tags`)."""
    flat = name.replace("_", "").lower()
    return any(word in flat for word in _NORMALISED)


def _calls(node: ast.AST) -> list[ast.Call]:
    return [child for child in ast.walk(node) if isinstance(child, ast.Call)]


def _scan(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    module_funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    class_methods: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    seen: set[str],
) -> list[tuple[int, str, str]]:
    """Mutating calls in `func` and in the same-module functions it calls directly."""
    hits: list[tuple[int, str, str]] = []
    for call in _calls(func):
        target = call.func
        if isinstance(target, ast.Attribute):
            if is_mutating(target.attr):
                hits.append((call.lineno, target.attr, func.name))
            owner = target.value
            if isinstance(owner, ast.Name) and owner.id in {"self", "cls"}:
                callee = class_methods.get(target.attr)
                if callee is not None and f"m:{callee.name}" not in seen:
                    seen.add(f"m:{callee.name}")
                    hits.extend(_scan(callee, module_funcs, class_methods, seen))
        elif isinstance(target, ast.Name):
            callee = module_funcs.get(target.id)
            if callee is not None and f"f:{callee.name}" not in seen:
                seen.add(f"f:{callee.name}")
                hits.extend(_scan(callee, module_funcs, class_methods, seen))
    return hits


def find_plan_mutations(tree: ast.Module) -> list[tuple[int, str, str]]:
    """Return `(line, attribute, function)` for mutating calls reachable from `plan`."""
    module_funcs = {n.name: n for n in tree.body if isinstance(n, _FUNCTIONS)}
    hits: list[tuple[int, str, str]] = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        methods = {n.name: n for n in cls.body if isinstance(n, _FUNCTIONS)}
        plan = methods.get("plan")
        if plan is not None:
            hits.extend(_scan(plan, module_funcs, methods, {"m:plan"}))
    return sorted(set(hits))


def check_plan_readonly(root: Path, legacy: frozenset[str]) -> list[str]:
    problems: list[str] = []
    for path in non_legacy_files(root, legacy, "crc"):
        for line, attr, func in find_plan_mutations(parse_module(path)):
            problems.append(
                violation(
                    RULE,
                    f"{relpath(root, path)}:{line} `plan()` reaches mutating call "
                    f"`.{attr}(...)` in `{func}`",
                    "`plan()` backs --dry_run and must only list, describe and filter. Move "
                    "this call into `apply()` (or a helper that only `apply()` calls), keep the "
                    "order required by CLEANUP_SAFETY.md, and add a test that `plan()` makes "
                    "no mutating client call.",
                )
            )
    return problems
