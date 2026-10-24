"""ARCH-011: relative links and backticked repo paths in the live markdown files resolve."""

import re
from pathlib import Path

from tests.archtest._common import relpath, violation

RULE = "ARCH-011"
ARCHIVE = "docs/archive"
BUG_REGISTER = "docs/refactor/BUGS.md"
SKIPPED_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}
    | {".ruff_cache", ".tox", ".idea"}
)
PATH_ROOTS = ("crc", "tests", "docs", ".cursor", ".github")
_FENCE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.MULTILINE | re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`\n]*)`")
_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#)", re.IGNORECASE)
_ROOTS = "|".join(re.escape(name) for name in PATH_ROOTS)
_REPO_PATH = re.compile(r"^(?:" + _ROOTS + r")/[\w.\-/*?\[\]<>]+$")
_BARE_FILE = re.compile(r"^[\w.\-]+\.(?:py|md|txt|json|yml)$")
_REGISTER_ROW = re.compile(r"^\|\s*(?:BUG-\d+|NEW)\s*\|")
_SUFFIX = re.compile(r"(?:::[\w\[\].\-]+|:\d+(?:-\d+)?|#[\w\-]+)+$")
_PLACEHOLDER = re.compile(r"<[^<>/]+>")
_GLOB_CHARS = re.compile(r"[*?\[]")
_FILE_NAME = re.compile(r"\.[A-Za-z0-9]+$")
# Bare filename -> reason it may stay unresolved. Add an entry only with a written reason.
BARE_NAME_ALLOWLIST: dict[str, str] = {}
_REMEDIATION = (
    "Fix the link target or the backticked path, or update it after a file move, rename or "
    "deletion. Linked documents and named files are how an agent finds detail one hop away, so "
    "a dead reference sends it to guess. Do not delete the reference if the document still "
    "needs it; name the file that exists now. A path that is deliberately historical (a file "
    "on `main` only) is written without its directory prefix or as a `main:<path>` reference."
)
_BARE_HINT = (
    " A bare filename is looked up next to the document, then at the repository root: write "
    "the path from the root when the file lives elsewhere, or name a file that exists. Only an "
    "entry in `BARE_NAME_ALLOWLIST` with a written reason may stay unresolved."
)


def _documents(root: Path) -> list[Path]:
    """Every live markdown file: all `.md` and `.mdc` files except the archive and caches."""
    archive = root / ARCHIVE
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        rel_parts = path.relative_to(root).parts
        if path.suffix not in {".md", ".mdc"} or path.is_symlink() or not path.is_file():
            continue
        if SKIPPED_DIRS.intersection(rel_parts) or archive in path.parents:
            continue
        if any((root.joinpath(*rel_parts[:n])).is_symlink() for n in range(1, len(rel_parts))):
            continue
        found.append(path)
    return found


def _blank_fences(text: str) -> str:
    return _FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def _links(text: str) -> list[tuple[int, str]]:
    """`(line number, target)` of each link, ignoring fenced and inline code."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(_blank_fences(text).splitlines(), start=1):
        plain = _INLINE_CODE.sub("", line)
        found += [(number, match.group(1)) for match in _LINK.finditer(plain)]
    return found


def _bare_names(text: str, is_register: bool) -> list[tuple[int, str]]:
    """`(line number, filename)` of each backticked name without a directory part.

    Same line rules as `_repo_paths`: fenced code and bug register rows are skipped.
    """
    found: list[tuple[int, str]] = []
    for number, line in enumerate(_blank_fences(text).splitlines(), start=1):
        if is_register and _REGISTER_ROW.match(line):
            continue
        for match in _INLINE_CODE.finditer(line):
            span = _SUFFIX.sub("", match.group(1).strip())
            if _BARE_FILE.match(span) and span not in BARE_NAME_ALLOWLIST:
                found.append((number, span))
    return found


def _repo_paths(text: str, is_register: bool) -> list[tuple[int, str]]:
    """`(line number, path)` of each backticked repo path, ignoring fenced code.

    The rows of the bug register are skipped: they are written against `main` and are never
    edited, so their titles and locations name files that no longer exist.
    """
    found: list[tuple[int, str]] = []
    for number, line in enumerate(_blank_fences(text).splitlines(), start=1):
        if is_register and _REGISTER_ROW.match(line):
            continue
        for match in _INLINE_CODE.finditer(line):
            span = _SUFFIX.sub("", match.group(1).strip())
            if _REPO_PATH.match(span) and (
                span.endswith("/") or _FILE_NAME.search(span) or _GLOB_CHARS.search(span)
            ):
                found.append((number, span))
    return found


def _resolves(root: Path, document: Path, target: str) -> bool:
    path_part = target.split("#", 1)[0].split("?", 1)[0]
    if not path_part:
        return True
    base = root if path_part.startswith("/") else document.parent
    return (base / path_part.lstrip("/")).exists()


def _path_exists(root: Path, span: str) -> bool:
    """True when the path exists; a glob or `<placeholder>` must match at least one path."""
    pattern = _PLACEHOLDER.sub("*", span).rstrip("/")
    if not _GLOB_CHARS.search(pattern):
        return (root / pattern).exists()
    return next(root.glob(pattern), None) is not None


def _document_problems(root: Path, document: Path) -> list[str]:
    rel = relpath(root, document)
    text = document.read_text(encoding="utf-8")
    problems: list[str] = []
    for number, target in _links(text):
        if _EXTERNAL.match(target) or _resolves(root, document, target):
            continue
        message = f"{rel}:{number} links to {target}, which does not exist"
        problems.append(violation(RULE, message, _REMEDIATION))
    for number, span in _repo_paths(text, rel == BUG_REGISTER):
        if not _path_exists(root, span):
            message = f"{rel}:{number} names `{span}`, which does not exist"
            problems.append(violation(RULE, message, _REMEDIATION))
    for number, name in _bare_names(text, rel == BUG_REGISTER):
        if not ((document.parent / name).exists() or (root / name).exists()):
            message = f"{rel}:{number} names `{name}`, found in neither its directory nor the root"
            problems.append(violation(RULE, message, _REMEDIATION + _BARE_HINT))
    return problems


def check_doc_links(root: Path) -> list[str]:
    """Violations for dead links, backticked repo paths and bare filenames that do not exist."""
    return [p for document in _documents(root) for p in _document_problems(root, document)]
