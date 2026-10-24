"""Name, regex, substring and state policy of the cleaners."""

import re
from collections.abc import Iterable, Sequence

__all__ = [
    "compile_patterns",
    "exception_regex_match_matches",
    "exception_regex_search_matches",
    "filter_substring_matches",
    "name_regex_search_matches",
    "nic_name_excluded",
    "state_in",
    "state_is_substring_of_any",
    "status_contains_any_allowed",
]


def name_regex_search_matches(patterns: Sequence[str], text: str | None) -> bool:
    """name_regex, ``re.search`` any: empty list matches. AWS disk and keypair. Case sensitive.

    ``None`` is searched as ``""``. An invalid pattern raises ``re.error``.
    """
    return not patterns or any(re.search(p, text or "") for p in patterns)


def exception_regex_search_matches(patterns: Sequence[str], text: str | None) -> bool:
    """exception_regex, ``re.search`` any: empty list gives False. AWS disk and keypair."""
    return any(re.search(p, text or "") for p in patterns)


def exception_regex_match_matches(patterns: Sequence[str], text: str) -> bool:
    """exception_regex, ``re.match`` (anchored at start) any: GCP disk. Empty gives False."""
    return any(re.match(p, text) for p in patterns)


def filter_substring_matches(patterns: Sequence[str], text: str) -> bool:
    """GCP IP filter_regex: plain substring, any. Empty list matches. Not a regex."""
    return not patterns or any(p in text for p in patterns)


def nic_name_excluded(patterns: Sequence[str], text: str) -> bool:
    """Azure NIC exception_regex: plain substring, any. Empty list gives False."""
    return any(p in text for p in patterns)


def compile_patterns(patterns: Sequence[str], label: str) -> tuple[re.Pattern[str], ...]:
    """Compile regexes up front; ``ValueError`` naming ``label`` on a bad pattern (AWS disk)."""
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern))
        except re.error as e:
            raise ValueError(f"Invalid {label} pattern {pattern!r}: {e}") from e
    return tuple(compiled)


def state_in(state: str | None, allowed: Iterable[str]) -> bool:
    """Exact state membership (GCP and OCI VMs, Azure Spot power states, GCP IP, OCI disk)."""
    return state in tuple(allowed)


def state_is_substring_of_any(state: str, allowed: Iterable[str]) -> bool:
    """Azure disk: the disk state must be a substring of an allowed state string."""
    return any(state in entry for entry in allowed)


def status_contains_any_allowed(status: str, allowed: Iterable[str]) -> bool:
    """Azure VM: an allowed state must be a substring of the instance_view display status."""
    return any(entry in status for entry in allowed)
