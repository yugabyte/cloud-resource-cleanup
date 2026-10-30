"""The Azure kinds of `ROUTED_CALLS` (tests/test_cli_main_parity.py), one table per cloud.

`tests/test_cli_main_parity.py` imports `tests.helpers_routed_<cloud>.ROUTED_CALLS` when that
module exists, so a cloud adds its own file and leaves the shared table alone. The keys are
the `RunContext.options` keys of the Azure route in the order of the oracle `CRC` call; a `None`
key is an oracle argument the Azure adapter never used (the IP regexes), and the options the
oracle gets from the `CRC` constructor (`resource_group`, `notags`) are asserted by the test itself.
"""

from tests.helpers_routed_calls import RoutedCall

ROUTED_CALLS: dict[str, RoutedCall] = {
    "ip": RoutedCall("delete_ip", ("filter_tags", "exception_tags", None, None)),
    "nic": RoutedCall("delete_nic", ("name_regex", "exception_regex")),
    "disk": RoutedCall(
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
    ),
    "vm": RoutedCall(
        "delete_vm",
        ("filter_tags", "exception_tags", "age", "custom_age_tag_key", "instance_state"),
        stop="stop_vm",
    ),
    "spot_instances": RoutedCall(
        "delete_spot_instances",
        ("filter_tags", "exception_tags", "age", "custom_age_tag_key"),
    ),
}
