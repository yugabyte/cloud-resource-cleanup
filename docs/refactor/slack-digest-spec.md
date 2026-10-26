# Slack digest: one summary per Jenkins run (spec)

Status: implemented in CRC (writer, poster); the triggers job change is pending rollout. Owner decision (2026-10-26): replace the per-command Slack messages of the
`cloud-resource-cleanups` Jenkins job with one digest per run and channel. This file is the
shared contract between the writer (part A), the poster (part B) and the triggers job (part C).

## Why

Each run starts about 59 `python crc.py` processes (in three `parallel {}` blocks) and each one
posts "Deleted the following `0` aws VM(s): `[]`" style messages: about 59 messages per run, most
of them empty, ids shown as a raw Python list inside one inline-code span. Slack's app-design
guide asks for a digest instead of one message per automated event.

## Switch

- Environment variable `CRC_SLACK_DIGEST_DIR` (absolute directory path). Unset or empty: today's
  behaviour, byte for byte (every Slack parity test stays green unchanged). No new CLI flag; the
  frozen Jenkins command fixture and flag-parity tables do not change.
- Set: the channel messages `SlackNotifier` would post from `report.slack_messages()` are not
  posted; one JSON record per process is written into that directory instead.
- Not affected, still posted directly as today: owner pings (`report.by_owner`, GCP disk
  `--slack_notify_users`), the `--scan_tag` path (`notify_cleanup`), Influx writes.
- Fail safe: if the record cannot be written (directory missing and not creatable, disk error,
  serialisation error), log one WARNING naming the error and post the messages directly as today.
  A lost notification is worse than an ugly one. `SlackNotifier.fail_open` stays False.
- A record is written also when the report is empty (the digest counts commands that found
  nothing; that is how it shows "nothing to clean" for liveness).

## Record (version 1)

One file per process: `<dir>/<UTC yyyymmddTHHMMSS>-<pid>-<uuid4 hex>.json`, written to a temp
file in the same directory and moved into place with `os.replace` (atomic; the parallel stages
never share a file). UTF-8, `json.dumps(..., sort_keys=True)`.

```json
{
  "version": 1,
  "written_at": "2026-10-26T07:31:02Z",
  "channel": "yba-crc-cleanup-notifications",
  "cloud": "aws",
  "kind": "vm",
  "operation": "delete",
  "dry_run": false,
  "resource_suffix": "ONLY_YBA",
  "stage": "AWS VMs 6 Hours old",
  "build_url": "https://jenkins.dev.yugabyte.com/job/cloud-resource-cleanups/job/cloud-resource-cleanups/16654/",
  "build_number": "16654",
  "job_name": "cloud-resource-cleanups/cloud-resource-cleanups",
  "sections": [{"label": "VM", "ids": ["yb-itest-jenk-is8701-...-n2", "..."]}],
  "failures": [{"id": "i-0abc", "error": "OperationNotPermitted: ..."}],
  "by_owner": null
}
```

- `channel`: the `--slack_channel` value as given (no `#` added or removed).
- `cloud`: report cloud (`aws`, `azure`, `gcp`, `oci`). `kind`: the `--resource` value.
- `operation`: `delete` or `stop`, as the report has it (lower case).
- `resource_suffix`: the `--influxdb` `resource_suffix`, else null.
- `stage`, `build_url`, `build_number`, `job_name`: Jenkins env `STAGE_NAME`, `BUILD_URL`,
  `BUILD_NUMBER`, `JOB_NAME`; null when unset. Read in `crc/cli`, passed down; `crc/notify` reads
  no environment.
- `sections`: every section of the report in order, `ids` = `slack_ids` when set, else `ids`
  (what Slack shows today), including empty sections.
- `failures`: `report.failures` (`id`, `error`), may be empty.
- `by_owner`: `{owner: [ids]}` when the report is owner-grouped, else null (pings still posted).

Readers ignore unknown keys and skip (with one WARNING) a file whose `version` is not 1 or that
does not parse; one bad file never stops the digest.

## Poster

Entry: `python crc_digest.py --dir <dir>` (thin root script like `crc.py`), token from env
`SLACK_BOT_TOKEN`. The Slack `WebClient` is created in `crc/cli` (as `crc/cli/clients.py` does);
reading, grouping and Block Kit building live in `crc/notify` with the client injected. Uses
`slack_sdk.models.blocks` (already pinned, slack-sdk 3.45.0); no new dependency. Reuses
`send_slack_message` for retries.

Per channel found in the records, one top-level message, then thread replies (`thread_ts` of the
top message):

1. Top message
   - `header`: `CRC cleanup · #<build_number>` (no build number: `CRC cleanup`).
   - Summary `section` (mrkdwn): one line per record group that found something, ordered by
     cloud then stage, e.g. `🗑️ *AWS VMs 6 Hours old* · aws vm · 34 deleted`,
     `🔍 *Dry Run: iTest AWS VMs with Autoclean=False label* · 7 would be deleted`,
     `⏸️ … · 3 stopped`, plus ` · ⚠️ 1 failed` when failures exist. Label = `stage`, else
     `<cloud> <kind>` plus `(<resource_suffix>)`.
   - A last line `✅ <n> other checks found nothing` (or `✅ Nothing to clean (<n> checks)` when
     every record is empty).
   - `context`: `<build_url|Build #N> · <job_name> · <n> commands · <UTC time>`.
   - Fallback `text`: `CRC cleanup #N: 41 deleted, 7 would be deleted, 1 failed`.
   A record with more than one non-empty section shows a count per section label,
   e.g. `· azure vm · 7 VM, 7 NIC deleted`.
2. One thread reply per non-empty section (and per record with failures): a small header line
   (`*AWS VMs 6 Hours old* · aws VM · 34 deleted`) then the ids one per line as a bullet list,
   chunked with `slack_chunks._chunk_lines_into_messages` limits (20 ids, 2800 chars per message,
   `Part i/n` when split). Failures listed as `• \`id\` — error` (error cut to 200 chars).
3. Owner-grouped records: summary line counts them and says `owners pinged separately`; no
   thread reply duplicating the ping.

Block Kit limits to respect: at most 50 blocks per message, section text at most 3000 chars,
header plain_text at most 150 chars. If the summary has more lines than fit in one section, split
into several sections in the same message.

Leftovers: when `BUILD_NUMBER` is set, records of another build (left by a run whose post step
never ran) are dropped with one warning each, so two runs are never merged.

Errors: a failed post of one channel or one reply is logged (with the channel) and the poster
goes on; exit status 1 if any top-level post failed, 0 otherwise. Missing or empty directory:
log `no digest records in <dir>` and exit 0. Missing `SLACK_BOT_TOKEN`: exit 2 with a clear
message. `--dry_run` prints a JSON list `[{channel, top: {text, blocks}, replies: [...]}]` to stdout and
posts nothing; each `top` object pastes into Block Kit Builder. Channels are posted as
`SlackPoster` does: a plain name gets a leading `#`. The context time is the latest
`written_at` of the channel's records, which is each command's start time.

## Triggers job (part C)

- `environment { CRC_SLACK_DIGEST_DIR = "${WORKSPACE}/crc-slack-digest" }`.
- In `post { always { ... } }`, before `cleanWs()`: if the clone stage produced
  `cloud-resource-cleanup/env/bin/python` and the digest directory exists, run
  `env/bin/python crc_digest.py --dir "$CRC_SLACK_DIGEST_DIR"`; otherwise `echo` a loud
  `CRC SLACK DIGEST NOT POSTED: <reason>` line. Do not hide a poster failure with `|| true`
  silently: print the line, and let the step mark the build UNSTABLE rather than FAILED
  (`catchError(buildResult: 'SUCCESS', stageResult: 'UNSTABLE')` or equivalent).

Rollout order: CRC (A+B) on `refactor/python-agentic` first; then the triggers env line. Old CRC
with the variable set ignores it (still posts per command); new CRC without the post step would
write records nobody posts.
