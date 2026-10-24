# Copyright (c) Yugabyte, Inc.

"""Helpers shared by the AWS cleaners of this package (structure only, no behaviour of their own).

They hold the pieces that the key pair, Elastic IP, KMS, snapshot and disk cleaners repeated:
the default EC2 region lister, the skipped-region log, the per-region grouping of a plan and
the summary lines `main` logs once all regions are done.
"""

import logging
from collections.abc import Sequence

from crc.aws import _regionlog
from crc.aws._base import get_all_regions
from crc.aws.connectivity import log_skipped_region
from crc.core.cleaner import Candidate

EC2_SERVICE_NAME = "ec2"
DEFAULT_REGION = "us-west-2"


def ec2_regions() -> Sequence[str]:
    """Regions of the EC2 service (describe_regions with the connectivity fallback)."""
    return get_all_regions(EC2_SERVICE_NAME, DEFAULT_REGION)


def skip_region(region: str, context: str, error: BaseException) -> None:
    """Count a region skipped for a connectivity error and log the WARNING naming `context`."""
    _regionlog.region_skipped(region, error)
    log_skipped_region(region, context, error)


def group_by_region(candidates: Sequence[Candidate]) -> dict[str, list[Candidate]]:
    """Candidates grouped by region, in the order of first appearance."""
    by_region: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_region.setdefault(candidate.region, []).append(candidate)
    return by_region


def log_deleted_summary(label: str, deleted: Sequence[str], *, dry_run: bool) -> None:
    """The summary lines `main` logs for `label` ("keypairs", "Elastic IPs", "KMS")."""
    items = list(deleted)
    if not dry_run:
        logging.warning("number of AWS %s deleted: %s", label, len(items))
        logging.warning("List of AWS %s deleted: %s", label, items)
    else:
        logging.warning(
            "List of AWS %s (Total: %s) which will be deleted: %s", label, len(items), items
        )
