# Requests runner

`cal_mirror/requests_runner.py` is the worker half of a rule change and of an archive.
The web app only writes `Request` rows. This module counts, writes the archive, and,
after the owner confirms, moves future copies or deletes what the dry run listed.

`process_requests(store, calendar, clock, notifier)` takes `list_requests(pending)` and
then `list_requests(confirmed)`, each in ascending id, and calls `handle_request` on
each. `ready`, `done`, `failed` and `cancelled` are never read back and never written:
a cancelled change moves nothing and saves no rules. `now` is `clock.now()` once per
request that is actually handled.

`handle_request` dispatches on kind. An unknown kind becomes `failed` with
`result = {"error": "unknown request kind"}`. `CalendarError`, `StoreError`,
`ArchiveError`, `ValueError`, `KeyError` and `TypeError` become `failed` with
`result = {"error": <text>}`, where `<text>` is `str(err)`, or the exception class
name when that string is empty. Each write uses
`store.update_request(replace(req, state=..., result=...))`.

Logs (`logging.getLogger("cal_mirror.requests")`) carry the request id, kind, state
and counts (`moves`, `deleted`, `counts`). They do not carry event titles or the
error text.

## `rules`

Proposed rules are `Rule(id=i + 1, kind, value, calendar_id)` from `params["rules"]`
in list order. Copy pairs are every `copy` link, in link order, with its source
event: `source` splits on the first `/` into calendar id and event id, and
`calendar.get_event` loads it. A missing source is left out. Placement is
`choose_calendar(event, proposed, settings.default_calendar)`; `place` is the
calendar id only. A rules request checks ownership once before any other calendar call: each copy source, then each planned move's calendar and destination, must have `access_role` `owner` (a calendar missing from the list is not), or it ends `failed` with `{"error": "calendar not owned: <ids>; nothing was moved"}` and rules are not saved and nothing is moved.

| From | To | `result` | What changes |
|---|---|---|---|
| `pending` | `ready` | `{"moves": n}` | `n` is `count_moves`. Nothing is moved. `save_rules` is not called. |
| `confirmed` | `done` | `{"moves": n}` | A fresh read of the copies, the ownership check, `save_rules(proposed)`, then each `plan_moves` write. `n` is how many `calendar.apply` calls finished. |
| `confirmed` | `failed` | `{"error": <text>, "moves": n}` | A move failed. `n` is how many finished before it. Later moves are not applied. |

For each planned move the runner stores `calendar.apply`'s new id on the copy link
(`calendar_id` = destination, `rule_id` = the matched rule's id, or `None`) and sets
`calendar_id` to that destination on every other `copy` link whose source starts with
`<link.source>_` (instances of that series).

## `archive`

Params are `cutoff` (ISO date) and `destination`. The destination must be one of
`settings.archive_destinations`; otherwise the request is `failed` with
`{"error": "unknown archive destination"}` and nothing is written or deleted.

The read is every calendar with `access_role == "owner"`. Events are
`list_events(calendar)` with no `time_min` and no `time_max`, minus `cancelled`, so an
exception moved past the cutoff is included. A series master is left out of the cleanup
plan when one of its exceptions has no start, or starts or ends at or after midnight UTC
on the cutoff, and that series is not deleted. `write_archive` still receives every read
event and keeps only those that start before the cutoff, so the archive does not gain
later events. `plan_cleanup` sees the events that remain, the cutoff,
`settings.owner_email`, and links keyed by source. Calendars the owner does not own are not read, and a delete is not applied to a calendar he does not own. If any planned delete names a calendar that is not owned, the request fails with `{"error": "calendar not owned: <ids>; nothing was deleted"}` before a pending zip is written and before a confirmed delete is applied.

| From | To | `result` | What changes |
|---|---|---|---|
| `pending` | `ready` | `counts`, `check`, `paths`, `deletes`, `kept_unbounded` | `write_archive` then `verify_archive`. `counts` is `dict(plan.counts)` (`"<calendar>/<year>"` -> n). `check` is `"ok"` or `"failed"`. `paths` is the zip paths as strings. `deletes` is the sorted `[calendar_id, event_id]` pairs, mirror deletes included, stored whether `check` is `"ok"` or `"failed"`. `kept_unbounded` is `plan.kept_unbounded`, an int including `0`. The confirmed run ignores it. No delete. |
| `confirmed`/`pending` | `failed` | `{"error": "calendar not owned: <ids>; nothing was deleted"}` | A planned delete's calendar is not owned. No zip is written and no delete is applied. |
| `confirmed` | `done` | the stored result plus `deleted` | See the re-plan below. `deleted` is how many deletes finished. The stored `deletes` stay on the result. |
| `confirmed` | `failed` | `{"error": "the events to delete changed since the dry run; nothing was deleted"}` | The fresh delete list is not the stored `deletes` (or it is missing). No delete. |
| `confirmed` | `failed` | `{"error": "archive check failed"}` | Stored `check` is not `"ok"`, or `verify_archive` of the stored paths is false. No delete. |
| `confirmed` | `failed` | `{"error": <text>, "deleted": n}` | A delete raised. `n` is how many finished. Later deletes are not applied. |

Confirmed run, in order, and only when the stored `check` is `"ok"`:

1. Read and `plan_cleanup` again. Delete calendars are checked for ownership before the stored `deletes` are compared. The new list must equal the stored `deletes`
   exactly, so the deletes are the dry run's pairs.
2. `verify_archive(plan, archived_sources(stored paths))` must be true. The zips are
   the ones named in the dry-run result; they are not written again.
3. Apply each `plan.deletes` write with `calendar.apply`, then `store.delete_link`
   on that write's source.

A pending dry run with `check == "failed"` stays `ready` (so the page can show the
failed check) and still raises the archive alert. It does not delete.

## Archive alert

Every archive request that ends `failed`, and a pending dry run whose `check` is
`"failed"`, calls `step_alert(store.get_alert_state(), Failure(FAIL_ARCHIVE), now)`,
saves that state, and, when `step_alert` returns a message, `notifier.send("cal-mirror",
message, click=...)`. The click is `{web_url}/archive/{id}`, or `""` when `web_url`
is empty. A second failure while the alert is active gets no message. `NotifyError`
is logged as the class name only. The message is `step_alert`'s (`cal-mirror failure:
archive`); it is never the error text or an event detail.
