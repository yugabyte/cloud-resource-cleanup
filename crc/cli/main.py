"""`main()`: parse the flags, check them, build the clients and run the cleanup."""

from crc.cli.args import get_argparser
from crc.cli.clients import (
    build_influxdb_client,
    build_slack_client,
    reset_max_age,
    set_max_age,
)
from crc.cli.context import RunContext
from crc.cli.dispatch import run_dispatch
from crc.cli.inputs import (
    check_flag_combinations,
    check_tokens,
    read_options,
    read_tokens,
    resolve_targets,
    validate_values,
)
from crc.cli.scan import run_scan


def main() -> None:
    """
    Main function to perform resource cleanup operations on the specified cloud(s) and resource(s).

    :return: None
    """
    options = read_options(get_argparser())
    tokens = read_tokens()

    check_tokens(options, tokens)
    check_flag_combinations(options)
    targets = resolve_targets(options)
    validate_values(options)

    slack_client = build_slack_client(options, tokens)
    influxdb_client = build_influxdb_client(options, tokens)
    context = RunContext(
        dry_run=options.dry_run,
        notags=options.notags,
        slack_client=slack_client,
        influxdb_client=influxdb_client,
        project_id=options.project_id,
        resource_group=options.resource_group,
        slack_channel=options.slack_channel,
        influxdb=options.influxdb,
    )

    set_max_age(options.max_age)

    if options.scan_tag:
        run_scan(context, options, targets)

    run_dispatch(context, options, targets)

    reset_max_age(options.max_age)
