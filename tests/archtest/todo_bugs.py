"""ARCH-012: `TODO(BUG-NNN)` comments in `crc/` and the open critical/high rows of BUGS.md agree."""

import re
from pathlib import Path

from tests.archtest._common import SKIP_DIRS, relpath, violation

RULE = "ARCH-012"
BUGS_PATH = "docs/refactor/BUGS.md"
_ROW_START = re.compile(r"^\|\s*(BUG-\d{3,})\s*\|")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_WORD = "TO" + "DO"
_TODO = re.compile(rf"\b{_WORD}\b")
_TODO_BUG = re.compile(rf"\b{_WORD}\(BUG-(\d{{3,}})\)")
_COLUMNS = 8
_TRACKED_RISKS = frozenset({"critical", "high"})
_TRACKED_STATUSES = ("open", "partly fixed")
_REMEDIATION_UNKNOWN = (
    f"A {_WORD}(BUG-NNN) names a bug that is still open or partly fixed. If the bug is fixed, "
    "remove the TODO and mark the row `fixed in <PR or commit>` in docs/refactor/BUGS.md (the "
    "todo-to-issue workflow then closes the issue). If the ID is a typo, correct it."
)
_REMEDIATION_NO_ID = (
    "Every TODO in crc/ is tracked by a bug row. Write the row first (next free BUG-NNN in "
    f"docs/refactor/BUGS.md, current crc/ file:line), then write `# {_WORD}(BUG-NNN): <title under "
    "100 chars>` followed by `#  labels: bug, risk-<risk>, crc`. Do not leave an untracked TODO."
)
_REMEDIATION_MISSING = (
    "Every open or partly fixed critical or high row needs exactly one TODO(BUG-NNN) comment in "
    "crc/, directly above the function where the behaviour lives (one TODO at the shared "
    "decision point or the first cleaner, with `see also` lines naming the others):\n"
    f"    # {_WORD}(BUG-NNN): <title under 100 chars>\n"
    "    #  labels: bug, risk-<critical|high>, crc"
)
_REMEDIATION_DUPLICATE = (
    "A bug has exactly one TODO(BUG-NNN) in crc/ (one GitHub issue per bug). Keep the one at the "
    "shared decision point or the first cleaner and turn the others into `see also` lines."
)


def tracked_bug_rows(root: Path) -> tuple[set[str], set[str]]:
    """`(open_or_partly_fixed, required)` bug IDs: all such rows, and the critical/high ones."""
    open_ids: set[str] = set()
    required: set[str] = set()
    for line in (root / BUGS_PATH).read_text(encoding="utf-8").splitlines():
        match = _ROW_START.match(line)
        if not match:
            continue
        cells = [cell.strip() for cell in _CELL_SPLIT.split(line.strip().strip("|"))]
        if len(cells) < _COLUMNS:
            continue
        risk, status = cells[-3].lower(), cells[-1].lower()
        if status.startswith(_TRACKED_STATUSES):
            open_ids.add(match.group(1))
            if risk in _TRACKED_RISKS:
                required.add(match.group(1))
    return open_ids, required


def _scanned(root: Path) -> list[Path]:
    base = root / "crc"
    if not base.is_dir():
        return []
    return sorted(
        path
        for path in base.rglob("*")
        if path.suffix in {".py", ".md"}
        and path.is_file()
        and not SKIP_DIRS - {"tests"} & set(path.relative_to(root).parts[:-1])
    )


def check_todo_bugs(root: Path) -> list[str]:
    open_ids, required = tracked_bug_rows(root)
    problems: list[str] = []
    seen: dict[str, list[str]] = {}
    for path in _scanned(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if not _TODO.search(line):
                continue
            where = f"{relpath(root, path)}:{number}"
            ids = [f"BUG-{m.group(1)}" for m in _TODO_BUG.finditer(line)]
            if not ids:
                problems.append(
                    violation(RULE, f"{where} has a TODO without a BUG id", _REMEDIATION_NO_ID)
                )
            for bug in ids:
                seen.setdefault(bug, []).append(where)
                if bug not in open_ids:
                    problems.append(
                        violation(
                            RULE,
                            f"{where} has TODO({bug}), but {bug} is not an open or partly "
                            f"fixed row in {BUGS_PATH}",
                            _REMEDIATION_UNKNOWN,
                        )
                    )
    for bug, places in sorted(seen.items()):
        if len(places) > 1 and bug in open_ids:
            problems.append(
                violation(
                    RULE,
                    f"TODO({bug}) appears {len(places)} times ({', '.join(places)})",
                    _REMEDIATION_DUPLICATE,
                )
            )
    problems.extend(
        violation(
            RULE,
            f"{bug} is open or partly fixed (critical or high) in {BUGS_PATH} but has no "
            f"TODO({bug}) in crc/",
            _REMEDIATION_MISSING,
        )
        for bug in sorted(required - seen.keys())
    )
    return problems
