"""The Slack digest switch and the Jenkins variables, read once at the CLI boundary."""

import os
from typing import Any

from crc.notify.digest_record import DigestTarget


def read_digest_target(kind: Any, resource_suffix: str | None) -> DigestTarget | None:
    """The digest target of one pair, or None when `CRC_SLACK_DIGEST_DIR` is unset or empty.

    `STAGE_NAME`, `BUILD_URL`, `BUILD_NUMBER` and `JOB_NAME` are None when unset.
    """
    directory = os.environ.get("CRC_SLACK_DIGEST_DIR")
    if not directory:
        return None
    return DigestTarget(
        directory=directory,
        kind=None if kind is None else str(kind),
        resource_suffix=resource_suffix or None,
        stage=os.environ.get("STAGE_NAME"),
        build_url=os.environ.get("BUILD_URL"),
        build_number=os.environ.get("BUILD_NUMBER"),
        job_name=os.environ.get("JOB_NAME"),
    )
