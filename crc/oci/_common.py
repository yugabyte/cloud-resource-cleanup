# Copyright (c) Yugabyte, Inc.

"""Helpers shared by the OCI cleaners."""

from collections.abc import Iterable

from crc.core.cleaner import Candidate


def group_by_region(candidates: Iterable[Candidate]) -> dict[str, list[Candidate]]:
    """Candidates keyed by region, in first-seen order."""
    by_region: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        by_region.setdefault(candidate.region, []).append(candidate)
    return by_region
