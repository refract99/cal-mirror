# Status page

`cal_mirror/web/page_status.py` exposes `router` with one route, `GET /status`. It needs a
signed-in user (`current_user`; otherwise 303 to `/signin`) and reads only the store:
`get_settings()`, `get_status()`, `list_placements(50)` and `get_meta("recent_notices")`. It
never touches Google or calendar events. Every time is shown in `settings.time_zone`, for
example `Tue 2026-11-03 7:30 AM CST`.

Sections:

- **Last good sync**: the time of the last good sync, or `never`.
- **Current failure**: the failure text, or `none`.
- **Google token**: `Google token: ok`, or a link to `/google` reading
  `Google token rejected: reconnect`.
- **Counts**: one `key: value` line per count, sorted by key; `No counts yet.` if empty.
- **Default placements**: the 50 newest, as the store returns them: time, title, organizer,
  guest domains, calendar and a `Make a rule` link to `/rules?from_placement=<id>`;
  `No default placements yet.` if empty.
- **Recent notices**: the worker's `recent_notices` meta (a JSON list of `source`, `check`,
  `start`, `title`) with title, start time and check (`no call link`, `outside hours`, or the
  raw value). Missing, invalid or non-list data reads as empty (`No recent notices.`); items
  that are not objects are skipped; a start that does not parse is shown as given.

## The `notice` query

`GET /status?notice=<source>` adds a `Notice` section (`id="notice"`) before everything else,
listing each recent notice whose `source` equals the query, or
`That notice is no longer in the recent list.` when none does. Without the query the section
is absent.

The link comes from the meeting-check notification the worker sends:
`<web_url>/status?notice=<url-quoted source>`, where the source is the event's source key
(`<calendar id>/<event id>`).
