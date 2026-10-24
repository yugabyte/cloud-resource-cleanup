"""ARCH-008: the number of collected tests may never drop below the committed floor.

Run as `python -m tests.archtest.test_count check` (part of `make check`, after pytest) or
`python -m tests.archtest.test_count raise` (`make raise-test-floor`, raises the floor only).
"""

import re
import subprocess
import sys
from pathlib import Path

from tests.archtest._common import REPO_ROOT, violation

RULE = "ARCH-008"
FLOOR_PATH = Path(__file__).resolve().parent / "test_count_floor.txt"
_REMEDIATION = (
    "Tests may only be added or strengthened (AGENTS.md hard rule 1). A lower count means a "
    "test was deleted, skipped at collection or lost to a collection error. Restore it, fix "
    "the import or fixture that stops it from being collected, and add tests until the count "
    "is back at or above the floor. Never edit `test_count_floor.txt` to lower it; it only "
    "rises through `make raise-test-floor`."
)
_NODE_LINES = re.compile(r"^\S+\.py::", re.MULTILINE)


def parse_collected(output: str) -> int | None:
    """Count the test ids of `pytest --collect-only -q` output (None if there are none)."""
    count = len(_NODE_LINES.findall(output))
    return count or None


def collected_count(root: Path = REPO_ROOT) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    count = parse_collected(result.stdout)
    if result.returncode != 0 or count is None:
        raise SystemExit(f"{RULE}: pytest --collect-only failed:\n{result.stdout}{result.stderr}")
    return count


def read_floor(path: Path = FLOOR_PATH) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def check_floor(count: int, floor: int) -> list[str]:
    if count >= floor:
        return []
    return [
        violation(
            RULE, f"{count} tests collected, below the committed floor of {floor}", _REMEDIATION
        )
    ]


def raise_floor(count: int, path: Path = FLOOR_PATH) -> bool:
    """Write `count` as the new floor only if it is higher than the current one."""
    if count <= read_floor(path):
        return False
    path.write_text(f"{count}\n", encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else "check"
    count = collected_count()
    if mode == "raise":
        changed = raise_floor(count)
        print(f"{RULE}: floor {'raised to ' + str(count) if changed else 'unchanged'}")
        return 0
    problems = check_floor(count, read_floor())
    print("\n\n".join(problems) if problems else f"{RULE}: {count} tests, floor {read_floor()}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
