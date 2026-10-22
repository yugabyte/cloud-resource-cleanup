# crc.notify

## Purpose

The Slack and InfluxDB senders of CRC. They take a `crc.core.report.Report` (or, for the
`--scan_tag` summary, plain item dicts) and send exactly what `crc.py` sent before: same texts,
blocks, channel handling, retries, user lookups and Influx points. The clients (a
`slack_sdk.WebClient`, an `influxdb_client.InfluxDBClient`) are injected through the
constructors and never created at import time. `SlackNotifier` and `InfluxNotifier` implement
the `crc.core.engine.Notifier` protocol (`name`, `fail_open`, `send(report, context)`).
`crc.py` delegates its `notify_*` methods, `ping_on_slack`, `get_msg` and `write_influxdb` here;
`crc/notifications.py` re-exports the moved Slack helpers.

## Public API

Slack (module slack.py):

- `SlackNotifier`: sends a report to the channel, pings owners, looks up users and groups, sends the scan-tag summary; not fail open.
- `notify_cleanup()`: the scan-tag summary, split into as many Slack messages as needed, a failed part is logged and skipped.
- `send_slack_message()`: chat_postMessage with up to three attempts on a Slack API error.
- `human_readable_timedelta()`: uptime text such as 3d 04h 12m 05s, a dash when unknown.

InfluxDB (module influx.py):

- `InfluxNotifier`: writes one point per report section; every write is caught and printed; fail open.
- `InfluxSettings`: protocol of the bucket and the resource suffix, read at write time.
- `InfluxConfig`: plain frozen settings for callers that are not the CRC class of crc.py.

## Safety Invariants

- Behaviour equals `crc.py` on `main`: tests compare a frozen copy of the old methods
  (`tests/legacy_crc_notify_reference.txt`) with `crc.CRC` and the notifiers on the same fake clients.
- Quirks are kept and covered by tests, not fixed here (`docs/refactor/BUGS.md`): the `##name`
  channel prefix (`BUG-034`), `ping_on_slack` wording (`BUG-029`), the missing Influx settings
  without `influxdb_conn` (`BUG-030`), disks that are neither list nor dict send nothing
  (`BUG-031`), Slack and Influx are written in dry run (`BUG-032`), bare `except` in the lookups
  (`BUG-033`), the duplicate `notify_deleted_nic_via_slack` in `crc.py` (`BUG-028`), the
  string `"not_found"` iterated as user groups (`BUG-041`), `conversations_open` failure aborting
  owner pings (`BUG-042`), and `ping_on_slack` fetching user groups before checking an empty dict or
  `None` owner list (`BUG-043`, `BUG-044`).
- A failed `chat_postMessage` is printed and swallowed per message; an error from
  `conversations_open` propagates, so `SlackNotifier.fail_open` is False. Influx failures are
  swallowed per point (`InfluxNotifier.fail_open` is True).
- This package imports `crc.core` only: never `crc.aws`, `crc.azu`, `crc.gcp`, `crc.oci` or
  `crc.service`, and `crc.core` never imports it (import-linter contracts in `pyproject.toml`).
- Log messages and printed messages are part of the behaviour; keep them identical.

## Extension Rules

1. Keep the text and the points in `crc.core.report`; this package only sends them.
2. Inject clients; never create a client, read the environment or call a network API at import.
3. Declare every public name in `__all__` of `__init__.py` and list it under Public API.
4. Add tests with fake clients that assert the exact calls (channel, text, blocks, link_names,
   Influx tags and fields), and extend the parity test when a path changes.
5. Record every new bug or quirk in `docs/refactor/BUGS.md` with its `main` file:line.
