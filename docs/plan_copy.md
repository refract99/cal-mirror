# plan_copy

`cal_mirror/plan_copy.py` plans the main-to-secondary copies. It is pure: no store, network,
clock, env or file access, no logging. The worker applies the writes and records the links.

## Inputs

`plan_copies(changes, links, settings, place)`

- `changes`: events seen on the calendars, in order.
- `links`: existing `Link`s keyed by `source_key` (`<calendar>/<event id>`).
- `settings`: only `main_calendar` is read. If it is empty the result is `[]`.
- `place(event) -> calendar_id`: chosen by the caller (rules, default). Called only for a create,
  once per create.

Only events on `main_calendar` count. Events carrying the app's marker (`is_mirror`, including
BLOCKED events on main) give nothing. If a `source_key` repeats, only its last occurrence is
planned. Output keeps input order.

## Details block

`details_block(event)` joins these lines with `\n` (no trailing newline), leaving out empty ones:

1. `Meeting link: <link>`
2. one `Dial-in: <entry>` per dial-in entry, verbatim
3. `Organizer: <email>`
4. `Participants:` then `- <email> (<response>)` per attendee, if there are any

No such data gives `""`. The copy's description is the block, a blank line, then the original
description; if only one is non-empty, that one; otherwise `""`.

`copy_body(event, calendar_id)` copies title, time, all-day flag, recurrence, location,
attachments, meeting link and dial-in; sets `transparency="opaque"`, no attendees, the conference
without `createRequest`, and the marker (`MARKER_SOURCE` = source key, `MARKER_KIND` = copy). `copy_body` copies the source event's `time_zone`.

## Decision table

wanted = busy (not cancelled, not free) and not declined by the owner.

| wanted | link | write |
| --- | --- | --- |
| yes | yes | `OP_UPDATE` on the linked event, reason `changed` |
| yes | no | `OP_CREATE` on `place(event)`, reason `new` |
| no | yes | `OP_DELETE` on the linked event, reason `cancelled`, `free` or `declined` |
| no | no | nothing |

An edit never changes the calendar, even if `place` would now choose another one.

## Recurring exceptions

For an event with `recurring_event_id`, its own link is used first. Without one, the series link
`<calendar>/<recurring_event_id>` is looked up. With it, the target is the series calendar and
event id `<series event id>_<suffix>` (suffix = text after the first `_` of the event id):
wanted gives `OP_UPDATE` (reason `instance changed`), not wanted gives `OP_DELETE`. `place` is not
called. `source` is the instance's own key. With neither link, wanted creates like a single
event; not wanted gives nothing.

## Never

- adds guests: the copy has none, so no notice is sent;
- creates a meeting: the copy reuses the original link and drops `createRequest`;
- changes its input (the conference mapping is copied);
- moves a copy, touches an event the app did not create, reads rules, or writes links.
