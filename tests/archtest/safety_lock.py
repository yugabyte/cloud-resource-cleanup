"""SAF-LOCK: safety tests are pinned to SAF IDs by a hash of their normalised AST."""

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from tests.archtest._common import REPO_ROOT, violation

RULE = "SAF-LOCK"
LOCK_PATH = Path(__file__).resolve().parent / "safety_lock.json"
DOC_PATH = REPO_ROOT / "CLEANUP_SAFETY.md"
_SAF_TAG = re.compile(r"\[(SAF-\d{3})\]")
_SKIP_NAMES = frozenset(
    {"skip", "skipif", "skipunless", "xfail", "expectedfailure", "importorskip"}
)
_SKIP_CALLS = frozenset({"skip", "xfail", "importorskip", "skiptest"})
_IGNORED_FIELDS = frozenset({"type_params", "kind"})
_UPDATE_HINT = (
    "If you did not intend to change this test, restore it. Only the repository owner "
    "may regenerate hashes with `python tests/archtest/update_safety_lock.py` after "
    "reviewing the change; never weaken, skip or delete a safety test."
)


def normalised_dump(node: object) -> str:
    """Stable AST text: no positions, no fields that differ between Python versions."""
    if isinstance(node, ast.Constant):
        return f"Constant({node.value!r})"
    if isinstance(node, ast.AST):
        parts = [
            f"{name}={normalised_dump(value)}"
            for name, value in ast.iter_fields(node)
            if name not in _IGNORED_FIELDS and value is not None and value != []
        ]
        return f"{type(node).__name__}({', '.join(parts)})"
    if isinstance(node, list):
        return "[" + ", ".join(normalised_dump(item) for item in node) + "]"
    return repr(node)


def hash_function(node: ast.AST) -> str:
    return hashlib.sha256(normalised_dump(node).encode("utf-8")).hexdigest()


def _name_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _skips(node: ast.AST) -> list[str]:
    """Names of skip/xfail markers, decorators and calls found anywhere under `node`."""
    found: list[str] = []
    for child in ast.walk(node):
        name = _name_of(child)
        if name is not None and name.lower() in _SKIP_NAMES:
            found.append(name)
        elif isinstance(child, ast.Call):
            callee = _name_of(child.func)
            if callee is not None and callee.lower() in _SKIP_CALLS:
                found.append(callee)
    return found


def find_test(
    root: Path, node_id: str
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, list[str]] | str:
    """Resolve `path::Class::test` to its function node and skip markers, or an error text."""
    path_part, *names = node_id.split("::")
    file = root / path_part
    if not file.is_file() or not names:
        return f"file or test name missing in `{node_id}`"
    tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    skips = [
        name
        for stmt in tree.body
        if isinstance(stmt, ast.Assign) and any(_name_of(t) == "pytestmark" for t in stmt.targets)
        for name in _skips(stmt)
    ]
    scope: list[ast.stmt] = tree.body
    for index, name in enumerate(names):
        is_last = index == len(names) - 1
        wanted = (ast.FunctionDef, ast.AsyncFunctionDef) if is_last else (ast.ClassDef,)
        match = next((s for s in scope if isinstance(s, wanted) and s.name == name), None)
        if match is None:
            return f"`{name}` not found in `{node_id}`"
        if isinstance(match, ast.ClassDef):
            skips.extend(n for d in match.decorator_list for n in _skips(d))
            scope = match.body
        else:
            return match, [*skips, *_skips(match)]
    return f"`{node_id}` does not name a test function"


def _load_lock(lock_path: Path) -> dict[str, Any] | str:
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"cannot read {lock_path.name}: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("locks"), dict):
        return f"{lock_path.name} must be an object with `locks` and `gaps`"
    if not isinstance(data.get("gaps", []), list):
        return f"{lock_path.name} `gaps` must be a list"
    return data


def _check_entry(root: Path, saf_id: str, entry: object) -> str | None:
    if not isinstance(entry, dict) or not isinstance(entry.get("test"), str):
        return f"{saf_id} has a malformed lock entry: {entry!r}"
    node_id = entry["test"]
    found = find_test(root, node_id)
    if isinstance(found, str):
        return f"{saf_id} locked test is missing: {found}"
    func, skips = found
    if skips:
        return f"{saf_id} locked test `{node_id}` is skipped or xfailed ({sorted(set(skips))})"
    if hash_function(func) != entry.get("sha256"):
        return f"{saf_id} locked test `{node_id}` changed (AST hash mismatch)"
    return None


def check_safety_lock(
    root: Path, lock_path: Path = LOCK_PATH, doc_path: Path = DOC_PATH
) -> list[str]:
    data = _load_lock(lock_path)
    if isinstance(data, str):
        return [violation(RULE, data, _UPDATE_HINT)]
    tags = _SAF_TAG.findall(doc_path.read_text(encoding="utf-8"))
    locks: dict[str, Any] = data["locks"]
    gaps = {str(item) for item in data.get("gaps", [])}
    problems = [
        violation(
            RULE,
            f"{saf_id} appears more than once in {doc_path.name}",
            "Give every invariant one unique, stable ID. Never reuse or renumber IDs.",
        )
        for saf_id in sorted({t for t in tags if tags.count(t) > 1})
    ]
    unknown = sorted((set(locks) | gaps) - set(tags))
    unlisted = sorted(set(tags) - set(locks) - gaps)
    overlap = sorted(set(locks) & gaps)
    for label, ids in (("not tagged in the doc", unknown), ("neither locked nor a gap", unlisted)):
        if ids:
            problems.append(
                violation(
                    RULE,
                    f"SAF IDs {label}: {ids}",
                    "Keep `[SAF-NNN]` tags in CLEANUP_SAFETY.md and safety_lock.json in sync: "
                    "map an ID to existing tests under `locks`, or list it under `gaps` and in "
                    "tests/archtest/README.md. Do not invent tests to fill a gap.",
                )
            )
    if overlap:
        problems.append(
            violation(RULE, f"SAF IDs both locked and listed as gaps: {overlap}", _UPDATE_HINT)
        )
    for saf_id, entries in sorted(locks.items()):
        for entry in entries if isinstance(entries, list) and entries else [None]:
            message = _check_entry(root, saf_id, entry)
            if message is not None:
                problems.append(violation(RULE, message, _UPDATE_HINT))
    return problems
