"""OWNER-ONLY: regenerate `frozen_inputs.json` (ARCH-007). Agents must never run this.

Run `python -m tests.archtest.update_frozen_inputs` only after the owner reviewed and accepted
a change to `tests/legacy/*.txt`, `requirements.txt` or `constraints.txt`.
"""

import json

from tests.archtest._common import REPO_ROOT
from tests.archtest.frozen_inputs import MANIFEST_PATH, compute_manifest


def main() -> None:
    manifest = compute_manifest(REPO_ROOT)
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(manifest)} hashes to {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
