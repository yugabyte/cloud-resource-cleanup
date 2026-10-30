"""Protected and managed-resource policy: yb_task, Kubernetes volumes, GKE and OKE nodes."""

import re
from collections.abc import Iterable, Mapping
from typing import Any

from crc.core.policy_tags import Tags, TagSpec

__all__ = [
    "SENSITIVE_YB_TASK",
    "aws_disk_has_sensitive_yb_task",
    "aws_disk_skips_kubernetes",
    "aws_disk_yb_task",
    "aws_filter_wants_kubernetes",
    "aws_is_kubernetes_volume",
    "aws_spot_has_sensitive_yb_task",
    "gcp_is_managed_or_gke_node",
    "is_sensitive_yb_task",
    "oci_is_managed_or_oke_node",
]


SENSITIVE_YB_TASK = frozenset({"infrastructure", "infra", "ipsec", "vpn", "prod", "production"})


_YB_TASK_KEYS = ("yb_task", "yb-task")


_YB_TASK_SPLIT = re.compile(r"[-_\s/]+")


_K8S_PREFIXES = ("kubernetes.io/", "ebs.csi.aws.com/")


_K8S_KEYS = ("KubernetesCluster", "CSIVolumeName")


_GKE_NODE_LABEL = "goog-gke-node"


_OKE_MARKERS = ("oke", "instancepool", "instance_pool", "instance-pool")


def _folded_key(key: str) -> str:
    return key.casefold().replace("-", "_")


def is_sensitive_yb_task(value: str) -> bool:
    """True for sensitive yb_task values (infra, prod, vpn, ...).

    Case-insensitive; matches the whole value, any token split on ``-``, ``_``, whitespace
    or ``/``, or a ``<sensitive>-`` / ``<sensitive>_`` prefix. Empty gives False.
    """
    folded = value.casefold().strip()
    if not folded:
        return False
    if folded in SENSITIVE_YB_TASK:
        return True
    if any(token in SENSITIVE_YB_TASK for token in _YB_TASK_SPLIT.split(folded)):
        return True
    return any(folded.startswith((s + "-", s + "_")) for s in SENSITIVE_YB_TASK)


def aws_disk_yb_task(tags: Tags) -> str:
    """AWS disk lookup: first key equal to yb_task ignoring case and ``-``/``_``; stripped value."""
    for key, value in tags.items():
        if _folded_key(key) == "yb_task":
            return value.strip()
    return ""


def aws_disk_has_sensitive_yb_task(tags: Tags) -> bool:
    """AWS disk: only the first folded yb_task key is judged."""
    return is_sensitive_yb_task(aws_disk_yb_task(tags))


def aws_spot_has_sensitive_yb_task(tags: Tags) -> bool:
    """AWS spot: exact (case sensitive) keys ``yb_task`` and ``yb-task``; either sensitive."""
    return any(key in tags and is_sensitive_yb_task(tags[key]) for key in _YB_TASK_KEYS)


def aws_is_kubernetes_volume(tags: Tags) -> bool:
    """AWS disk: a ``kubernetes.io/`` or ``ebs.csi.aws.com/`` key, or a known cluster key."""
    return any(key.startswith(_K8S_PREFIXES) or key in _K8S_KEYS for key in tags)


def aws_filter_wants_kubernetes(filter_tags: TagSpec) -> bool:
    """AWS disk: filter_tags explicitly names a Kubernetes key (opt-in to delete CSI volumes)."""
    return any(key.startswith(_K8S_PREFIXES) or key in _K8S_KEYS for key in filter_tags)


def aws_disk_skips_kubernetes(tags: Tags, filter_tags: TagSpec) -> bool:
    """AWS disk: skip Kubernetes volumes unless filter_tags names a Kubernetes key."""
    return aws_is_kubernetes_volume(tags) and not aws_filter_wants_kubernetes(filter_tags)


def gcp_is_managed_or_gke_node(labels: Tags, metadata_items: Iterable[tuple[str, str]]) -> bool:
    """GCP Spot VM: ``goog-gke-node`` label, or ``created-by`` metadata naming an
    ``instanceGroupManagers`` resource (managed instance group member)."""
    if _GKE_NODE_LABEL in labels:
        return True
    return any(
        key == "created-by" and "instanceGroupManagers" in value for key, value in metadata_items
    )


def _looks_oke(text: str) -> bool:
    folded = text.casefold()
    return any(marker in folded for marker in _OKE_MARKERS)


def oci_is_managed_or_oke_node(
    freeform_tags: Mapping[str, Any] | None,
    defined_tags: Mapping[str, Any] | None,
    metadata: object,
) -> bool:
    """OCI Spot VM: ``oke`` / instance-pool markers (case-insensitive substring) in any
    ``key:value`` of freeform tags, ``namespace:key:value`` of defined tags (non-dict
    namespaces ignored), or instance metadata (only when it is a dict)."""
    if any(_looks_oke(f"{k}:{v}") for k, v in (freeform_tags or {}).items()):
        return True
    for namespace, entries in (defined_tags or {}).items():
        if isinstance(entries, dict) and any(
            _looks_oke(f"{namespace}:{k}:{v}") for k, v in entries.items()
        ):
            return True
    return isinstance(metadata, dict) and any(_looks_oke(f"{k}:{v}") for k, v in metadata.items())
