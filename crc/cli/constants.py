"""Supported clouds and resource kinds of the CLI (`--cloud all` and `--resource all` order)."""

from crc.core.selection import RESOURCES as _RESOURCES

CLOUDS = ["aws", "azure", "gcp", "oci"]
RESOURCES = list(_RESOURCES)
