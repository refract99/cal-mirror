# plan_move

`cal_mirror/plan_move.py` is the pure planner for "rule changes move future copies". It reads no
store, network, clock or environment, and it does not import `cal_mirror.rules`: the new placement
comes in as `place`.

## Takes and returns

- `plan_moves(copies, now, place) -> list[PlannedWrite]`. `copies` is a sequence of
  `(Link, Event)` pairs: a copy link and its SOURCE event on main (rules match the source). `now`
  is a timezone-aware datetime. `place(source) -> str` returns the calendar id the source belongs
  in under the new rules.
- For each pair, in input order, nothing is planned when: the link kind is not `copy`; the source
  has a `recurring_event_id` (an exception instance moves with its series); the source is
  `cancelled`; the source is not future; or `place` returns the link's current calendar or `""`.
  `place` is called only for pairs that pass the earlier skips.
- Otherwise the planner emits `PlannedWrite(op="move", calendar_id=<link calendar>,
  event_id=<link event>, destination=<place result>, source=<link source>)`.
- `count_moves(copies, now, place)` is `len(plan_moves(copies, now, place))`; it is the number
  shown in the warning before the owner confirms.
- `is_future(event, now) -> bool` is the future test below.

## Future rule

- `start` or `end` is `None`: not future.
- Single timed event: future when `end > now` (an event in progress counts).
- Single all-day event: future when `end > now` as a UTC date. `end` is exclusive, so an all-day
  event covering today counts.
- Recurring event: future when the series has an occurrence starting strictly after `now`
  (`dateutil.rrule.rrulestr(...).after(now)` is not `None`). All-day series use a naive
  `dtstart` and a naive UTC `now`, because dateutil rejects a date-form `UNTIL` with an aware
  `dtstart`. A series with no `UNTIL` and no `COUNT` is always future. A recurrence that dateutil
  cannot read (`ValueError`) counts as past, so nothing moves on it.

## Series choice

A future series moves as a whole: "past copies stay" applies to past single events. A series with
any occurrence after `now` moves, past instances included. A series whose last occurrence has
already started stays where it is.
