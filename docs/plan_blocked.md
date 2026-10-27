# plan_blocked

`cal_mirror/plan_blocked.py` plans the BLOCKED events that mirror busy events from secondary
calendars onto the main calendar. It is pure: it takes values and returns a list of
`PlannedWrite`. The worker applies the writes and records the links.

## Inputs

- `changes`: changed `Event`s, in order.
- `links`: `Mapping[str, Link]` keyed by source key (`<calendar>/<event id>`).
- `settings`: `main_calendar` and `secondary_calendars`.

A change counts only when its calendar is in `secondary_calendars` and is not the main calendar.
An empty `main_calendar` gives no writes. An event carrying the mirror marker gives nothing,
whatever `links` holds. If a source key repeats, only its last occurrence is planned. Output
keeps input order.

`blocked_body(event, main_calendar)` builds the body: summary `BLOCKED`, same start, end,
all-day flag and recurrence, opaque, private, the source and kind markers, and nothing else
(no description, location, guests, links or conference data). `blocked_body` copies the source event's `time_zone`.

## Decision table

An event is wanted when it is busy (not transparent, not cancelled) and not declined by the owner.

| Wanted | Link | Write | Reason |
|--------|------|-------|--------|
| yes | yes | update `link.event_id` | `changed` |
| yes | no | create | `new` |
| no | yes | delete `link.event_id` | `cancelled`, else `free`, else `declined` |
| no | no | nothing | |

An update is planned even when nothing differs; the worker passes only real changes.

## Recurring exceptions

For an instance (`recurring_event_id` set), its own link is used first, as above. Without one,
the series link `<calendar>/<recurring_event_id>` is looked up. With it, the target is
`<series mirror id>_<suffix>`, where suffix is the text after the first `_` of the instance id.
Wanted gives an update (reason `instance changed`); not wanted gives a delete with the reason
above. `source` is the instance's own key. With neither link, wanted gives a create and not
wanted gives nothing.

## Never

It never touches a store, network, clock, environment or file, never logs, never adds guests,
and never plans a write for events it did not mirror (no link, not wanted).
