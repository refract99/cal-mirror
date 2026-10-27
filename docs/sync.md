# Sync

`run_once(store, calendar, notifier, clock)` in `cal_mirror/sync.py` is one pass of the mirror. It returns a frozen `RunResult` (`writes`, `notices`, `failure`). `failure` is `""` on success, otherwise `token`, `google_api`, or `database`. `writes` counts Google applies that returned. `notices` counts meeting-check notices from the planners, not ntfy messages. The clock is read once.

`CalendarError`, `StoreError`, and `NotifyError` are caught by name. A `NotifyError` is logged as the class name and the run continues. Nothing else from those errors is logged: no event title, description, token, or Google error text. The info log is the write count and the notice count.

An empty `main_calendar` returns `RunResult()` after the calendar list is stored. Status and the alert state are left as they were, and no calendar is read.

## Read

Selected calendars are the main calendar, then each secondary, skipping blanks and repeats. Any other calendar is not read. A selected calendar is read only when `list_calendars` just returned it with access role owner; otherwise it is not read, its token is left untouched, and nothing is planned from it.

First start means meta `first_start_done` is not `"1"`. For each selected calendar, a first start or a missing token uses `list_changes(cid, None, time_min=now)`. Otherwise the stored token is used. `SyncTokenExpired` clears that token and retries with `time_min=now`. The new token is kept pending. It is not stored yet.

## Two phases

Links already in the store are split into a blocked map and a copy map, keyed by source. Rules are loaded once. `place` calls `choose_calendar` and remembers the matched rule (or none) for that source.

Changes are applied in two phases. Phase A is events whose `recurring_event_id` is empty. Phase B is every exception. In each phase, selected calendars run in order: `plan_blocked` and `plan_copies` see only that phase's changes for that calendar, the writes are applied, and the link maps are updated before the next calendar or phase. An exception whose series is created in the same run therefore patches `<mirror series id>_<suffix>` instead of inserting a second mirror.

## Writes

Each `PlannedWrite` goes through `calendar.apply`. A copy create whose calendar id is empty (no rule and no default) is skipped and not counted. Every create that is applied uses `mirror_event_id(source, kind)` as the Google event id, which depends only on the source and kind, so a retry that lost the link updates that same event. A copy create first moves a lost copy of that id from another owned calendar, then creates.

After a create, the link stores the new event id, the body start, and the remembered rule id (`None` when `place` did not match). A copy create with no rule also appends one placement: the source summary, organizer, and guest domains, at the run's `now`. An update keeps the rule id from the source link, or from the series link for an exception, and stores `event_id` from the write. A delete drops the link.

## Tokens

Tokens of calendars whose read and writes all succeeded are saved only after those writes. A `CalendarError` that is not an auth error stops the remaining writes for that source calendar and its token is not saved, so the next run reads those changes again. Creates that already have a link come back as updates. `CalendarAuthError` anywhere stops the run immediately and saves no token. Tokens are saved after the post-phase meeting checks, and a `StoreError` in those checks saves none.

## Notices

Main-calendar changes from both phases go to `plan_notices`. Every returned state is saved. Each notice is also prepended to meta `recent_notices` (JSON, newest first, at most 50) as `source`, `check`, `start` (ISO), and `title` (the event summary). The title is not put in the ntfy message.

First start sends one message, `summary_text`, even when there are zero notices, with click `<web_url>/status` (or `""` when `web_url` is empty). It does not send one message per notice. `first_start_done` is set to `"1"` only after that send, and only when the run succeeded. Later runs send `notice_text` per notice. The click is `<web_url>/status?notice=<quoted source>`, or `""` when `web_url` is empty.

When it is not first start, main was read, and none of its writes raised `CalendarError`, those checks run and notices are sent before any token is saved, even if another calendar failed; first start still sends its summary and sets `first_start_done` only when the whole run succeeds.

## Status and alerts

Success stores `last_good_sync=now`, `failure=""`, `token_ok=True`, and counts of blocked and copy links after the run. Failure stores the previous status with `failure` set to the kind, and `token_ok=False` only for a token failure.

`step_alert` then runs with `Failure(kind)` or `None`. A message is sent with the status click. One alert when a failure starts, one when it clears. A `StoreError` while reading or saving the alert state is logged and nothing is sent. When a selected calendar or a planned write's calendar is not owned, the run fails as `google_api` and the stored status text is `calendar not owned: ` plus those ids, unless a token or database failure in the same run keeps its own kind and text.
