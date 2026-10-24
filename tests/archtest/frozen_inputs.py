"""ARCH-007: SHA-256 manifest of the frozen oracles and the locked dependency files."""

import hashlib
import json
from pathlib import Path

from tests.archtest._common import relpath, violation

RULE = "ARCH-007"
MANIFEST_PATH = Path(__file__).resolve().parent / "frozen_inputs.json"
_REMEDIATION = (
    "These inputs are frozen: `tests/legacy/*.txt` are the main-branch oracles the parity "
    "tests compare against, and `requirements.txt` and `constraints.txt` may not change (AGENTS.md "
    "rules 3 and 7). Restore the file with `git checkout -- <path>` and change the code or the "
    "test instead. Never run `tests/archtest/update_frozen_inputs.py` and never edit "
    "`frozen_inputs.json`: both are owner-only. If a frozen input must change, stop and hand "
    "the owner the proposed change."
)


def frozen_paths(root: Path) -> list[Path]:
    paths = sorted((root / "tests" / "legacy").glob("*.txt"))
    for name in ("requirements.txt", "constraints.txt"):
        if (root / name).is_file():
            paths.append(root / name)
    return paths


def compute_manifest(root: Path) -> dict[str, str]:
    return {
        relpath(root, path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in frozen_paths(root)
    }


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in data.items()}


def check_frozen_inputs(root: Path, manifest: dict[str, str]) -> list[str]:
    actual = compute_manifest(root)
    problems: list[str] = []
    for rel in sorted(set(manifest) | set(actual)):
        if rel not in actual:
            problem = f"{rel} is in the frozen manifest but missing from the repository"
        elif rel not in manifest:
            problem = f"{rel} is not in the frozen manifest (new frozen input)"
        elif manifest[rel] != actual[rel]:
            problem = f"{rel} changed: SHA-256 {actual[rel]} != frozen {manifest[rel]}"
        else:
            continue
        problems.append(violation(RULE, problem, _REMEDIATION))
    return problems
