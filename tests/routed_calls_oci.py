"""Oracle `CRC` calls of the OCI kinds routed through the engine (read by `test_cli_main_parity`).

One entry per kind: the oracle method, the `RunContext.options` keys in oracle argument order
(`None` marks an argument the OCI adapter never read) and the oracle method of a stop run.
`spot_instances` is routed through the engine; the oracle skips it under `--cloud all`
as the route does.
"""

CALLS: dict[str, tuple[str, tuple[str | None, ...], str | None]] = {
    "spot_instances": (
        "delete_spot_instances",
        ("filter_tags", "exception_tags", "age", "custom_age_tag_key"),
        None,
    ),
    "disk": (
        "delete_disks",
        (
            "filter_tags",
            "exception_tags",
            "age",
            "custom_age_tag_key",
            None,
            None,
            None,
            None,
            None,
        ),
        None,
    ),
    "vm": (
        "delete_vm",
        ("filter_tags", "exception_tags", "age", "custom_age_tag_key", "instance_state"),
        "stop_vm",
    ),
}
