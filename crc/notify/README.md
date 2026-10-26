# crc.notify

## Purpose

The Slack and InfluxDB senders of CRC. They take a `crc.core.report.Report` (or, for the
`--scan_tag` summary, plain item dicts) and send the run results: texts,
blocks, channel handling, retries, user lookups and Influx points. The clients (a
`slack_sdk.WebClient`, an `influxdb_client.InfluxDBClient`) are injected through the
constructors and never created at import time. `SlackNotifier` and `InfluxNotifier` implement
the `crc.core.engine.Notifier` protocol (`name`, `fail_open`, `send(report, context)`).
`crc/cli/bridge.py` builds the notifiers of a routed pair (Slack first, then Influx);
`crc/notifications.py` re-exports the Slack helpers.

## Public API

Slack (module slack.py):

- `SlackNotifier`: sends a report to the channel, pings owners, looks up users and groups, sends the scan-tag summary; not fail open.
- `SlackPoster`: the Slack client and channel with the post, user lookup and owner-ping calls (module slack_owners.py); `SlackNotifier` subclasses it. The message splitting is in slack_chunks.py.
- `notify_cleanup()`: the scan-tag summary, split into as many Slack messages as needed, a failed part is logged and skipped.
- `send_slack_message()`: chat_postMessage with up to three attempts on a Slack API error.
- `human_readable_timedelta()`: uptime text such as 3d 04h 12m 05s, a dash when unknown.

Slack digest (spec in [docs/refactor/slack-digest-spec.md](../../docs/refactor/slack-digest-spec.md)):

- `DigestTarget`: the record directory and the Jenkins run facts a `SlackNotifier` writes into its record (module digest_record.py).
- `build_record()`: the version 1 record of a report, a plain dict; pure.
- `record_name()`: the record file name, UTC time, pid and a random hex.
- `write_record()`: writes a record atomically (temp file, then a rename) and creates the directory.
- `DigestRecord`: one parsed version 1 record (module digest_load.py).
- `load_records()`: the records of a directory; a file that does not parse or is not version 1 is skipped with one warning.
- `DigestMessage`: the fallback text and blocks of one Slack message (module digest_blocks.py).
- `build_top_message()`: the summary message of one channel: header, one line per stage that found something, a line for the checks that found nothing, build link.
- `build_replies()`: the thread replies of one channel: the ids of each non-empty section and the failures, as bullets, split into parts.
- `build_messages()`: top message then replies, per channel (module digest_post.py).
- `post_digest()`: posts each channel's top message and its replies in the thread; returns the number of channels whose top message failed.

InfluxDB (module influx.py):

- `InfluxNotifier`: writes one point per report section; every write is caught and printed; fail open.
- `InfluxSettings`: protocol of the bucket and the resource suffix, read at write time.
- `InfluxConfig`: plain frozen settings for callers that are not the CLI run context.

## Safety Invariants

- Behaviour equals the frozen oracle: tests compare the frozen `main` `CRC` methods
  (`tests/legacy/crc_main.txt`, via `load_legacy_crc()`) with the notifiers on the same fake clients.
- Quirks are kept and covered by tests, not fixed here (`docs/refactor/BUGS.md`): the `##name`
  channel prefix (`BUG-034`), `ping_on_slack` wording (`BUG-029`), the missing Influx settings
  without `influxdb_conn` (`BUG-030`), disks that are neither list nor dict send nothing
  (`BUG-031`), Slack and Influx are written in dry run (`BUG-032`), bare `except` in the lookups
  (`BUG-033`), the duplicate `notify_deleted_nic_via_slack` of the original code (`BUG-028`), the
  string `"not_found"` iterated as user groups (`BUG-041`), `conversations_open` failure aborting
  owner pings (`BUG-042`), and `ping_on_slack` fetching user groups before checking an empty dict or
  `None` owner list (`BUG-043`, `BUG-044`).
- A failed `chat_postMessage` is printed and swallowed per message; an error from
  `conversations_open` propagates, so `SlackNotifier.fail_open` is False. Influx failures are
  swallowed per point (`InfluxNotifier.fail_open` is True).
- With a digest target (`CRC_SLACK_DIGEST_DIR` set, read in `crc/cli`) `SlackNotifier` writes a
  record instead of posting the channel messages; owner pings are still posted. If the record
  cannot be written it logs one warning and posts the messages directly. Without a target the
  behaviour is unchanged. The digest poster only reads records and posts; a failed post of one
  channel or reply is logged and the other channels are still posted.
- This package imports `crc.core` only: never `crc.aws`, `crc.azu`, `crc.gcp`, `crc.oci` or
  `crc.service`, and `crc.core` never imports it (import-linter contracts in `pyproject.toml`).
- Log messages and printed messages are part of the behaviour; keep them identical.

## Extension Rules

1. Keep the text and the points in `crc.core.report`; this package only sends them.
2. Inject clients; never create a client, read the environment or call a network API at import.
3. Declare every public name in `__all__` of `__init__.py` and list it under Public API.
4. Add tests with fake clients that assert the exact calls (channel, text, blocks, link_names,
   Influx tags and fields), and extend the parity test when a path changes.
5. Record every new bug or quirk in `docs/refactor/BUGS.md` (a new row cites the current `file:line` in `crc/`).
