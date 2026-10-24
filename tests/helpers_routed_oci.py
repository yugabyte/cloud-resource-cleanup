"""The OCI kinds of `ROUTED_CALLS` (tests/test_cli_main_parity.py), one table per cloud.

Built from `tests/routed_calls_oci.CALLS`: the oracle method, the `RunContext.options` keys in
oracle argument order (`None` marks an argument the OCI adapter never read) and the stop method.
"""

from tests.helpers_routed_calls import RoutedCall
from tests.routed_calls_oci import CALLS

ROUTED_CALLS: dict[str, RoutedCall] = {
    kind: RoutedCall(method, keys, stop) for kind, (method, keys, stop) in CALLS.items()
}
