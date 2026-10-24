"""The `RoutedCall` value shared by the per-cloud `ROUTED_CALLS` tables of the CLI parity tests."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RoutedCall:
    """The oracle `CRC` call of a routed kind, and which of its arguments reach the cleaner.

    `keys` are the `RunContext.options` keys, in the order of the oracle call arguments. A
    `None` key is an argument the legacy adapter never used (the AWS IP adapter dropped the
    regexes, the AWS disk adapter the Slack owner flags); the oracle event loses that position.
    `stop` is the oracle method of a stop run, which takes the first four keys.
    """

    method: str
    keys: tuple[str | None, ...]
    stop: str | None = None
