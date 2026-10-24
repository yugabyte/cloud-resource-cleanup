"""ARCH-005: `plan` methods must be read-only, also through calls into sibling modules."""

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


_Func = ast.FunctionDef | ast.AsyncFunctionDef
_Hit = tuple[Path, int, str, str]


def _dotted(node: ast.expr) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return ".".join(reversed([*parts, node.id])) if isinstance(node, ast.Name) else None


class _Module:
    """A parsed module with its top-level functions and the sibling modules it imports."""

    def __init__(self, path: Path, root: Path) -> None:
        self.path = path
        self.root = root
        self.tree = parse_module(path)
        self.funcs: dict[str, _Func] = {
            n.name: n for n in self.tree.body if isinstance(n, _FUNCTIONS)
        }
        self.func_imports: dict[str, tuple[Path, str]] = {}
        self.module_imports: dict[str, Path] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom):
                self._from_import(node)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target = self._sibling(alias.name.split("."), 0)
                    if target is not None:
                        self.module_imports[alias.asname or alias.name] = target

    def _sibling(self, parts: list[str], level: int) -> Path | None:
        """The `.py` file of a module in this module's own package directory, if any."""
        package_dir = self.path.parent
        if level:
            base = package_dir
            for _ in range(level - 1):
                base = base.parent
            candidate = base.joinpath(*parts)
        else:
            candidate = self.root.joinpath(*parts)
        if candidate.parent != package_dir:
            return None
        for option in (candidate.with_suffix(".py"), candidate / "__init__.py"):
            if option.is_file() and option != self.path:
                return option
        return None

    def _from_import(self, node: ast.ImportFrom) -> None:
        parts = node.module.split(".") if node.module else []
        target = self._sibling(parts, node.level) if parts else None
        for alias in node.names:
            name = alias.asname or alias.name
            if target is not None:
                self.func_imports[name] = (target, alias.name)
                continue
            as_module = self._sibling([*parts, alias.name], node.level)
            if as_module is not None:
                self.module_imports[name] = as_module


def _load(cache: dict[Path, _Module], path: Path, root: Path) -> _Module:
    if path not in cache:
        cache[path] = _Module(path, root)
    return cache[path]


def _callee(
    call: ast.Call, module: _Module, cache: dict[Path, _Module]
) -> tuple[_Module, _Func] | None:
    """The sibling-module function a call resolves to, if any."""
    target = call.func
    if isinstance(target, ast.Name) and target.id in module.func_imports:
        path, attr = module.func_imports[target.id]
        sibling = _load(cache, path, module.root)
        func = sibling.funcs.get(attr)
        return (sibling, func) if func is not None else None
    if isinstance(target, ast.Attribute):
        mod = _dotted(target.value)
        if mod is not None and mod in module.module_imports:
            sibling = _load(cache, module.module_imports[mod], module.root)
            func = sibling.funcs.get(target.attr)
            return (sibling, func) if func is not None else None
    return None


def _local_functions(
    func: _Func,
    module_funcs: dict[str, _Func],
    class_methods: dict[str, _Func],
) -> list[_Func]:
    """`func` and every same-module function or `self`/`cls` method it reaches transitively."""
    reached: list[_Func] = []
    seen: set[str] = set()

    def visit(current: _Func, kind: str) -> None:
        key = f"{kind}:{current.name}"
        if key in seen:
            return
        seen.add(key)
        reached.append(current)
        for call in _calls(current):
            target = call.func
            if isinstance(target, ast.Attribute):
                owner = target.value
                if isinstance(owner, ast.Name) and owner.id in {"self", "cls"}:
                    callee = class_methods.get(target.attr)
                    if callee is not None:
                        visit(callee, "m")
            elif isinstance(target, ast.Name):
                callee = module_funcs.get(target.id)
                if callee is not None:
                    visit(callee, "f")

    visit(func, "m" if func.name in class_methods else "f")
    return reached


def _scan_siblings(
    func: _Func,
    module: _Module,
    class_methods: dict[str, _Func],
    cache: dict[Path, _Module],
    seen: set[tuple[Path, str]],
) -> list[_Hit]:
    """Mutating calls in sibling-module functions reachable from `func`."""
    hits: list[_Hit] = []
    for local_fn in _local_functions(func, module.funcs, class_methods):
        for call in _calls(local_fn):
            resolved = _callee(call, module, cache)
            if resolved is None:
                continue
            sibling, callee = resolved
            key = (sibling.path, callee.name)
            if key in seen:
                continue
            seen.add(key)
            for line, attr, name in _scan(callee, sibling.funcs, {}, {f"f:{callee.name}"}):
                hits.append((sibling.path, line, attr, name))
            hits.extend(_scan_siblings(callee, sibling, {}, cache, seen))
    return hits


def find_plan_mutations_in_siblings(path: Path, root: Path) -> list[_Hit]:
    """Mutating calls in sibling modules reachable from a `plan` method of `path`.

    Follows `from .x import f`, `from . import x` (`x.f()`), the absolute forms of both that
    name a module of the same package directory, and `import pkg.x [as x]`. Calls inside `path`
    itself are the job of `find_plan_mutations`.
    """
    module = _Module(path, root)
    cache: dict[Path, _Module] = {path: module}
    hits: list[_Hit] = []
    for cls in (n for n in ast.walk(module.tree) if isinstance(n, ast.ClassDef)):
        methods = {n.name: n for n in cls.body if isinstance(n, _FUNCTIONS)}
        plan = methods.get("plan")
        if plan is not None:
            hits.extend(_scan_siblings(plan, module, methods, cache, set()))
    return sorted(set(hits))


def check_plan_readonly_siblings(root: Path, legacy: frozenset[str]) -> list[str]:
    problems: list[str] = []
    for path in non_legacy_files(root, legacy, "crc"):
        for hit_path, line, attr, func in find_plan_mutations_in_siblings(path, root):
            problems.append(
                violation(
                    RULE,
                    f"{relpath(root, hit_path)}:{line} `.{attr}(...)` in `{func}` is reached from "
                    f"`plan()` of {relpath(root, path)} through a sibling-module call",
                    "`plan()` backs --dry_run and must only list, describe and filter, also "
                    "through helpers imported from sibling modules. Move the mutating call into "
                    "a function that only `apply()` calls (the helper may be split in a read "
                    "part and a write part), keep the order required by CLEANUP_SAFETY.md, and "
                    "add a test that `plan()` makes no mutating client call.",
                )
            )
    return problems
