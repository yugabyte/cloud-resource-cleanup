"""OWNER ONLY: regenerate the AST hashes in safety_lock.json.

Run from the repository root after reviewing a deliberate change to a locked test:

    python tests/archtest/update_safety_lock.py
    python tests/archtest/update_safety_lock.py --map SAF-003 tests/test_x.py::Class::test_y

The lock file is covered by CODEOWNERS. Agents must not run this to make a failing
SAF-LOCK check pass.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.archtest._common import REPO_ROOT
from tests.archtest.safety_lock import LOCK_PATH, find_test, hash_function


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--map", nargs=2, action="append", metavar=("SAF_ID", "NODE_ID"), default=[]
    )
    args = parser.parse_args(argv)

    data = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    locks: dict[str, list[dict[str, str]]] = data["locks"]
    for saf_id, node_id in args.map:
        locks.setdefault(saf_id, []).append({"test": node_id, "sha256": ""})
        data["gaps"] = [gap for gap in data.get("gaps", []) if gap != saf_id]
    for saf_id, entries in locks.items():
        for entry in entries:
            found = find_test(REPO_ROOT, entry["test"])
            if isinstance(found, str):
                print(f"{saf_id}: {found}", file=sys.stderr)
                return 1
            entry["sha256"] = hash_function(found[0])
    data["locks"] = dict(sorted(locks.items()))
    data["gaps"] = sorted(data.get("gaps", []))
    LOCK_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"updated {LOCK_PATH.name}: {len(locks)} locked IDs, {len(data['gaps'])} gaps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
