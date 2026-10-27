# Worker

`cal_mirror/worker.py` is the schedule loop. It does not plan mirrors or apply
requests itself: each pass calls `process_requests` (`cal_mirror.requests_runner`)
and, when the interval is due, `run_once` (`cal_mirror.sync`). No agent, no token
spend, and no email.

Run it with:

```sh
python -m cal_mirror.worker
```

`main` is the only place this module reads the environment. Importing it does not.

## Environment

Required (missing or `""` exits):

- `CAL_MIRROR_DSN`: Postgres DSN passed to `PgStore`. Exit text: `CAL_MIRROR_DSN is not set`.
- `CAL_MIRROR_GOOGLE_CLIENT`: path to the OAuth client JSON (`web` or `installed`).
  Exit text: `CAL_MIRROR_GOOGLE_CLIENT is not set`.
- `CAL_MIRROR_SECRET_KEY`: Fernet key. `cipher_from_env` builds the box.
  A missing or invalid key exits with that function's `ValueError` text
  (`CAL_MIRROR_SECRET_KEY is not set` or `invalid secret key`).

An unreadable, non-UTF-8, invalid-JSON or non-object client file exits with
`CAL_MIRROR_GOOGLE_CLIENT is unreadable: <ClassName>` (or `not an object`), never the file content.

Also redacted when set, but not required: `PGPASSWORD`.

SIGINT and SIGTERM set a `threading.Event`. The loop checks it at the start of
each pass and sleeps with `Event.wait`, so a signal wakes the sleep.

## Loop

`REQUEST_SECONDS` is 15. Every pass that is not stopped:

1. `store.get_settings()`.
2. Build a notifier: `NullNotifier` when `ntfy_topic` is `""`, otherwise
   `NtfyNotifier(ntfy_server, ntfy_topic)`.
3. `make_calendar()`. No Google token yet (`None` or `""`) sets
   `token_ok=False` on the current status and skips both steps. Other status
   fields stay as they were.
4. Otherwise `process_requests` runs every pass. `run_once` runs when no sync
   has started yet, or when `now >= last_start + max(1, sync_interval_minutes)`
   minutes. `last_start` is the clock time when that `run_once` started.
   The interval is read again each pass, so a change in the web app applies on
   the next pass without a restart. The default in settings is 5 minutes.
5. Sleep 15 seconds.

`StoreError`, `CalendarError`, `NotifyError` and `ValueError` are caught by
name. The log line is `<step> failed: <ClassName>` where step is `settings`,
`calendar`, `requests` or `sync`. The exception text is not logged. The loop
then sleeps and continues.

## Database alert

One owner holds an open database alert (memory when the store cannot record it,
else the persisted alert state), and memory hands it to the store once the store
is writable. The recovery is sent once, on the first `run_once` that returns
without failure `database`, never on a settings read alone.

A `RunResult` with failure `database` from `run_once` counts as a database failure and clears only when a later `run_once` returns without it.

`serve` gives `run_once` a notifier that holds back its database failure notice while an alert is already open and notes its database failure and recovery notices, so the owner moves to the store at once and no second failure or recovery is sent.

`NullNotifier.send` logs `ntfy topic not set: message dropped` and does not send.

## What logs redact

`configure_logging` attaches a `RedactingFilter` to a stream handler on the
`cal_mirror` logger at INFO, format
`%(asctime)s %(levelname)s %(name)s %(message)s`. The secret list is a callable,
so values learned after startup are covered. It holds:

- `CAL_MIRROR_SECRET_KEY`
- `PGPASSWORD` when set
- the OAuth client secret
- `token` and `refresh_token` from the last Google token JSON read

Those strings are replaced with `[redacted]`. Log lines do not include the
exception text, so a token that appears only inside an error message is not
written either.
