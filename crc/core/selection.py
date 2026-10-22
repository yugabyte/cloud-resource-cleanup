"""Selection rules of `crc.py main()`: which `(cloud, kind)` cleaners a run executes.

Pure and typed. Encodes the `--cloud all` / `--resource all` expansion and every opt-in
gate with the errors and warning texts of `crc.py`, so the wiring rung only calls
`build_selection()`. Nothing here reads the environment or logs.
"""

from dataclasses import dataclass

from crc.core.cleaner import Operation

CLOUDS: tuple[str, ...] = ("aws", "azure", "gcp", "oci")
RESOURCES: tuple[str, ...] = ("disk", "ip", "keypair", "vm", "kms", "nic", "snapshot")
EXTRA_RESOURCES: tuple[str, ...] = ("vpc", "spot_instance_requests", "spot_instances")

_AWS_DISK_OPT_IN_MESSAGE = (
    "AWS EBS volume cleanup requires '--cloud aws --resource disk'. "
    "Refusing to continue so --cloud all / --resource all does not "
    "skip EBS and then delete other AWS resources, and so "
    "--cloud all --resource disk does not reach other clouds' disks."
)
_SPOT_REQUESTS_WARNING = (
    "spot_instance_requests is AWS-only; skipped for {cloud} (use --resource spot_instances)"
)
_AWS_SPOT_INSTANCES_WARNING = (
    "For AWS use --resource spot_instance_requests "
    "(cancel request before terminate). Skipping spot_instances."
)
_SPOT_INSTANCES_ALL_WARNING = (
    "GCP/OCI spot_instances require explicit --cloud {cloud} "
    "(not --cloud all); skipped. Azure spot_instances still runs "
    "under --cloud all for backward compatibility."
)


@dataclass(frozen=True, slots=True)
class SelectionOptions:
    """CLI values that `main()` validates together with the cloud and resource."""

    kms_key_description: str | None = None
    kms_user: str | None = None
    slack_notify_users: bool = False
    slack_user_label: str | None = None


@dataclass(frozen=True, slots=True)
class Selection:
    """Ordered `(cloud, kind)` pairs to run and the warnings of the pairs that were skipped."""

    items: tuple[tuple[str, str], ...]
    warnings: tuple[str, ...] = ()


def _validate(
    cloud: str, resource: str, operation: Operation, options: SelectionOptions
) -> None:
    if cloud != "all" and cloud not in CLOUDS:
        raise ValueError(f"unknown cloud {cloud!r}; expected one of {[*CLOUDS, 'all']}")
    if resource != "all" and resource not in RESOURCES + EXTRA_RESOURCES:
        known = [*RESOURCES, *EXTRA_RESOURCES, "all"]
        raise ValueError(f"unknown resource {resource!r}; expected one of {known}")
    if operation == Operation.STOP and resource != "vm":
        raise ValueError("Stop is supported only for vm resource")
    if resource == "all" and cloud != "all":
        raise ValueError(
            "All Resources cleanup is supported only with all Clouds. "
            "Format: --cloud all --resources all"
        )
    if options.slack_notify_users and not options.slack_user_label:
        raise ValueError("--slack_user_label is mandatory when passing --slack_notify_user")
    if resource in ("kms", "all"):
        if not options.kms_key_description:
            raise ValueError("Key description string is required for deleting KMS keys.")
        if not options.kms_user:
            raise ValueError("Jenkins user ARN is reuired for deleting associated KMS keys.")


def _skip_warning(cloud: str, kind: str, cloud_arg: str) -> str | None:
    """Warning text when `crc.py` skips `(cloud, kind)`; None when the pair runs."""
    if kind == "spot_instance_requests" and cloud != "aws":
        return _SPOT_REQUESTS_WARNING.format(cloud=cloud)
    if kind == "spot_instances":
        if cloud == "aws":
            return _AWS_SPOT_INSTANCES_WARNING
        if cloud in ("gcp", "oci") and cloud_arg == "all":
            return _SPOT_INSTANCES_ALL_WARNING.format(cloud=cloud)
    return None


def build_selection(
    cloud: str,
    resource: str,
    operation: Operation = Operation.DELETE,
    options: SelectionOptions | None = None,
) -> Selection:
    """Expand `--cloud` / `--resource` into ordered pairs, applying the opt-in gates.

    Raises `ValueError` with the messages of `crc.py main()`. The AWS EBS circuit breaker
    raises as soon as the `(aws, disk)` pair is reached, so no other AWS resource is selected.
    """
    opts = options if options is not None else SelectionOptions()
    _validate(cloud, resource, operation, opts)
    aws_disk_opt_in = cloud == "aws" and resource == "disk"
    clouds = CLOUDS if cloud == "all" else (cloud,)
    kinds = RESOURCES if resource == "all" else (resource,)
    items: list[tuple[str, str]] = []
    warnings: list[str] = []
    for each_cloud in clouds:
        for kind in kinds:
            if kind == "disk" and each_cloud == "aws" and not aws_disk_opt_in:
                raise ValueError(_AWS_DISK_OPT_IN_MESSAGE)
            warning = _skip_warning(each_cloud, kind, cloud)
            if warning is None:
                items.append((each_cloud, kind))
            else:
                warnings.append(warning)
    return Selection(tuple(items), tuple(warnings))
