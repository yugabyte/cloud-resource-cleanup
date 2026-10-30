"""The oracle `CRC` call of each routed GCP pair, for `tests/test_cli_main_parity.py`.

Each value is `(method, keys, stop)` as `RoutedCall` takes them: `keys` are the
`RunContext.options` keys the GCP cleaner reads, in the order of the oracle call arguments (a
`None` key is an argument the GCP cleaner never reads: the IP cleaner has no tag filters).
"""

GCP_ROUTED_CALLS: dict[tuple[str, str], tuple[str, tuple[str | None, ...], str | None]] = {
    ("gcp", "spot_instances"): (
        "delete_spot_instances",
        ("filter_tags", "exception_tags", "age", "custom_age_tag_key"),
        None,
    ),
    ("gcp", "ip"): ("delete_ip", (None, None, "name_regex", "exception_regex"), None),
    ("gcp", "vm"): (
        "delete_vm",
        ("filter_tags", "exception_tags", "age", "custom_age_tag_key", "instance_state"),
        "stop_vm",
    ),
    ("gcp", "disk"): (
        "delete_disks",
        (
            "filter_tags",
            "exception_tags",
            "age",
            "custom_age_tag_key",
            "detach_age",
            "name_regex",
            "exception_regex",
            "slack_notify_users",
            "slack_user_label",
        ),
        None,
    ),
}
