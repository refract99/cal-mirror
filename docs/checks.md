# Meeting checks

Pure functions in `cal_mirror/checks.py`. They do not read a store or a clock, and they do not send notices. The worker does that.

A meeting is a busy, timed event with at least one guest other than the owner (`is_meeting`). Only meetings on the main calendar are planned. Each check can be turned off in settings.

## No call link

`CHECK_NO_LINK` (`no_call_link`) fails when the meeting has no call link.

`has_call_link` is true when any of these hold:

- `meeting_link` is non-empty
- `conference.entryPoints` has an entry with `entryPointType` `video`
- `location` or `description` (plain text or HTML) contains a URL or bare host, with or without `http://` or `https://`, whose host is `meet.google.com`, `zoom.us`, any `*.zoom.us`, `teams.microsoft.com`, or `teams.live.com` (case-insensitive)

The host ends at `/`, `?`, `#`, `:`, a quote, whitespace, `<`, or the end of the text. A phone-only conference, or dial-in numbers alone, is not a call link.

## Outside hours

`CHECK_HOURS` (`outside_hours`) fails when the meeting starts or ends outside the allowed hours for its weekday, in `settings.time_zone` (`zoneinfo`).

Weekday keys are Monday `0` through Sunday `6`. A missing weekday allows no hours. Both bounds are inclusive. The end is the start when `end` is missing. The meeting is outside hours when the allowed window is missing, the local start is before the open, the local end is after the close, or the local end falls on a later date than the local start.

Failure order is `no_call_link`, then `outside_hours`.

## State and fingerprint

`plan_notices` takes changes, the saved state, settings, and `now`. State is keyed by `source_key` (`calendar_id/id`). Each value maps a failing check to a fingerprint of the event's own start and end:

`{start.isoformat()}|{end.isoformat()}`

A missing end is an empty string after the `|`. Times are not converted for the fingerprint.

- Events whose calendar is not `main_calendar`, and every event when `main_calendar` is empty, are skipped and left out of both results.
- A source that appears more than once is planned once, from its last occurrence, in that occurrence's place in the input.
- Cancelled: new state `{}`, no notice.
- Start is not a datetime, or start is at or before `now`: keep the previous state, no notice. Only future meetings are checked.
- Otherwise each failing check is stored with the new fingerprint. A notice is produced only when that check's saved fingerprint differs (first failure, or a later change such as a new time). A check that passes, is turned off, or does not apply is omitted, which clears a failure that was fixed.

Notices keep that input order, then check order. The returned dict is the new state of every planned source. The worker saves each one. One notice per event and failed check.

## Message text

`notice_text` is the ntfy message. It is local time of the notice start, with a fixed English day name (`Mon` through `Sun`) and `AM`/`PM`, not the process locale. The hour has no leading zero (`12:05 AM`, `12:00 PM`). The zone abbreviation comes from `tzname()`.

`Tue 2026-11-03 7:30 AM CST meeting: no call link`

`Tue 2026-11-03 12:00 PM CST meeting: outside hours`

The message never contains the title or any other event detail. The web link is not in the text; the worker passes it as the notifier click.

`summary_text` is the single first-start message, not one notice per meeting. `n` is the number of distinct sources.

- `n == 0`: `cal-mirror first start: no future meetings fail checks`
- otherwise: `cal-mirror first start: {n} future meeting(s) fail(s) checks ({a} no call link, {b} outside hours)`
- one meeting uses `meeting fails`; a check counted 0 is left out of the parentheses
