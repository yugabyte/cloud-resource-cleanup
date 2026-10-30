"""The GCP kinds of `ROUTED_CALLS` (tests/test_cli_main_parity.py), one table per cloud.

Built from `tests/helpers_cli_routes_gcp.GCP_ROUTED_CALLS`: the keys are the `RunContext.options`
keys the GCP cleaner reads, in the order of the oracle `CRC` call; a `None` key is an argument
the GCP cleaner never reads (the IP cleaner has no tag filters).
"""

from tests.helpers_cli_routes_gcp import GCP_ROUTED_CALLS
from tests.helpers_routed_calls import RoutedCall

ROUTED_CALLS: dict[str, RoutedCall] = {
    kind: RoutedCall(method, keys, stop)
    for (_cloud, kind), (method, keys, stop) in GCP_ROUTED_CALLS.items()
}
