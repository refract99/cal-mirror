from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, time

from dateutil.rrule import rrulestr

from cal_mirror.model import KIND_COPY, OP_MOVE, Event, Link, PlannedWrite


def _series_is_future(event: Event, now: datetime) -> bool:
    start = event.start
    if start is None:
        return False
    rule = "\n".join(event.recurrence)
    try:
        if isinstance(start, datetime):
            return rrulestr(rule, dtstart=start, forceset=True).after(now) is not None
        naive_now = now.astimezone(UTC).replace(tzinfo=None)
        dtstart = datetime.combine(start, time())
        return rrulestr(rule, dtstart=dtstart, forceset=True).after(naive_now) is not None
    except ValueError:
        return False


def is_future(event: Event, now: datetime) -> bool:
    if event.start is None or event.end is None:
        return False
    if event.recurrence:
        return _series_is_future(event, now)
    if isinstance(event.end, datetime):
        return event.end > now
    return isinstance(event.end, date) and event.end > now.astimezone(UTC).date()


def plan_moves(
    copies: Sequence[tuple[Link, Event]],
    now: datetime,
    place: Callable[[Event], str],
) -> list[PlannedWrite]:
    writes: list[PlannedWrite] = []
    for link, source in copies:
        if link.kind != KIND_COPY:
            continue
        if source.recurring_event_id or source.status == "cancelled":
            continue
        if not is_future(source, now):
            continue
        dest = place(source)
        if dest == link.calendar_id or dest == "":
            continue
        writes.append(
            PlannedWrite(
                op=OP_MOVE,
                calendar_id=link.calendar_id,
                event_id=link.event_id,
                destination=dest,
                source=link.source,
            )
        )
    return writes


def count_moves(
    copies: Sequence[tuple[Link, Event]],
    now: datetime,
    place: Callable[[Event], str],
) -> int:
    return len(plan_moves(copies, now, place))
