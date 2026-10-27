# Cleanup

`cal_mirror/cleanup.py` is a pure planner. It lists what a cleanup would delete and checks
that list against the archive. It reads no store, network, clock or environment. The worker
applies the deletes only after the owner confirms and the archive check passes.

## Cutoff instant

The cutoff `C` is midnight UTC at the start of the cutoff date. A `date` counts as midnight
UTC of that day; a `datetime` is itself. The year of an event is its start year (UTC for a
`datetime`).

## Delete rules

`plan_cleanup(events, cutoff, owner_email, links)` deletes a source event when all hold:

- its status is not `cancelled`;
- it is not a mirror event the app wrote;
- it is owned: `owner_email` (stripped, case-insensitive, non-empty) equals the organizer or
  the creator email. An empty owner owns nothing, so the plan is empty;
- it is not an exception of a series (`recurring_event_id` is empty);
- it is a single event that starts before `C` and ends at or before `C`, or a series master
  whose series ended before the cutoff.

## Keep rules

Everything else is kept: events the owner was invited to, mirror events, cancelled events, every
series that runs past the cutoff, and every exception on its own. An exception of a deleted
series is not a separate delete, and its own link makes no delete either.

## Series rule

`series_ends_before(event, cutoff)` is False when the event has no start or no recurrence, or
when an `RRULE:` line has neither `UNTIL=` nor `COUNT=`. Otherwise the recurrence (EXDATE and
RDATE included) is expanded with `dateutil`, and the series ends before the cutoff when the last
occurrence plus the event duration is at or before `C` (all-day: the last date plus the duration
is at or before the cutoff date). A parse error or a series with no occurrence is kept.
An owned, non-cancelled, non-mirror master is kept with its instances, and makes no delete, when
any non-cancelled exception in the same calendar list has no start, a start at or after `C`, or an
end (its start, when end is missing) at or after `C`; that check runs before bounded evaluation, so
the master is not counted in `kept_unbounded`.
The budget is checked before any occurrence is generated: an `EXRULE`, more than one `RRULE`, a
`FREQ` outside daily, weekly, monthly and yearly, a part outside the allowed set, a `COUNT` above
`MAX_OCCURRENCES` (5000), more than 5000 `RDATE` and `EXDATE` values together, an `INTERVAL` below
1, or any recurrence line that is blank or holds whitespace inside it (dateutil splits on all whitespace) keeps the series and adds one to `kept_unbounded`.
The walk then examines at most `MAX_OCCURRENCES` occurrences, lazily and never as a full `list` of
the set, and one further occurrence is kept the same way (`series_ends_before` is False).

## Mirror deletes

For each deleted source, in order of source key, the plan holds a delete with reason `cleanup`,
then, when `links` has a link for that source key, a delete of the mirror event with reason
`cleanup mirror`. A `(calendar_id, event_id)` pair appears once; later duplicates are dropped.

## Counts

`counts` maps `"<calendar_id>/<year>"` to the number of deletes, keys sorted, no zero entries.
A mirror delete counts under the mirror's calendar and its source event's year. The sum equals
`len(deletes)`. `sources` holds the source keys of deleted source events, never mirror keys.

## Archive check

`verify_archive(plan, archived)` is True only when every delete target `<calendar_id>/<event_id>` is in `archived`, mirrors by their own calendar and event id, and every key in `plan.sources` is in `archived`.
The archived targets and `sum(counts)` equal `len(deletes)`.
A mirror missing from the archive refuses the cleanup.
Extra archived keys are fine, and an empty plan verifies True.
