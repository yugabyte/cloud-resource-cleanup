"""ARCH-009: every `BUG-NNN` mentioned in code, tests or package READMEs is a row in BUGS.md."""

import re
from pathlib import Path

from tests.archtest._common import SKIP_DIRS, relpath, violation

RULE = "ARCH-009"
BUGS_PATH = "docs/refactor/BUGS.md"
_REF = re.compile(r"\bBUG-(\d+)\b")
_ROW = re.compile(r"^\|\s*(BUG-\d{3,})\s*\|", re.MULTILINE)
_REMEDIATION = (
    "Bug IDs are never invented in code. Look the bug up in docs/refactor/BUGS.md and fix the "
    "ID if it is a typo. If it is a new bug or quirk, add a row to BUGS.md with the next free "
    "ID and the current crc/ file:line (AGENTS.md rule 5; old rows keep their main location) "
    "before referencing it. Do not remove the reference to make the check pass."
)


def known_bug_ids(root: Path) -> set[str]:
    return set(_ROW.findall((root / BUGS_PATH).read_text(encoding="utf-8")))


def _scanned(root: Path) -> list[Path]:
    files: list[Path] = []
    for top in ("crc", "tests"):
        base = root / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.suffix not in {".py", ".md"} or not path.is_file():
                continue
            if SKIP_DIRS - {"tests"} & set(path.relative_to(root).parts[:-1]):
                continue
            files.append(path)
    return sorted(files)


def check_bug_refs(root: Path) -> list[str]:
    known = known_bug_ids(root)
    problems: list[str] = []
    for path in _scanned(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            for ref in _REF.finditer(line):
                bug = f"BUG-{ref.group(1)}"
                if bug not in known:
                    problems.append(
                        violation(
                            RULE,
                            f"{relpath(root, path)}:{number} mentions {bug}, which has no row in "
                            f"{BUGS_PATH}",
                            _REMEDIATION,
                        )
                    )
    return problems
