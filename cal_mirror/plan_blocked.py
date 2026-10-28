from collections.abc import Mapping, Sequence
from datetime import datetime

from cal_mirror.model import (
    BLOCKED_TITLE,
    KIND_BLOCKED,
    MARKER_KIND,
    MARKER_SOURCE,
    OP_CREATE,
    OP_DELETE,
    OP_UPDATE,
    Event,
    Link,
    PlannedWrite,
    Settings,
    declined_by_self,
    is_busy,
    is_mirror,
    source_key,
)
from cal_mirror.plan_move import is_future


def blocked_body(event: Event, main_calendar: str) -> Event:
    return Event(
        calendar_id=main_calendar,
        id="",
        summary=BLOCKED_TITLE,
        start=event.start,
        end=event.end,
        all_day=event.all_day,
        recurrence=event.recurrence,
        transparency="opaque",
        visibility="private",
        private={MARKER_SOURCE: source_key(event), MARKER_KIND: KIND_BLOCKED},
        time_zone=event.time_zone,
    )


def _delete_reason(event: Event) -> str:
    if event.status == "cancelled":
        return "cancelled"
    if event.transparency == "transparent":
        return "free"
    return "declined"


def _plan_one(event: Event, links: Mapping[str, Link], main: str) -> PlannedWrite | None:
    key = source_key(event)
    wanted = is_busy(event) and not declined_by_self(event)
    link = links.get(key)
    if link is not None:
        if wanted:
            return PlannedWrite(
                op=OP_UPDATE,
                calendar_id=main,
                event_id=link.event_id,
                body=blocked_body(event, main),
                source=key,
                reason="changed",
            )
        return PlannedWrite(
            op=OP_DELETE,
            calendar_id=main,
            event_id=link.event_id,
            source=key,
            reason=_delete_reason(event),
        )
    if event.recurring_event_id != "" and "_" in event.id:
        series = links.get(f"{event.calendar_id}/{event.recurring_event_id}")
        if series is not None:
            target = f"{series.event_id}_{event.id.split('_', 1)[1]}"
            if wanted:
                return PlannedWrite(
                    op=OP_UPDATE,
                    calendar_id=main,
                    event_id=target,
                    body=blocked_body(event, main),
                    source=key,
                    reason="instance changed",
                )
            return PlannedWrite(
                op=OP_DELETE,
                calendar_id=main,
                event_id=target,
                source=key,
                reason=_delete_reason(event),
            )
    if wanted:
        return PlannedWrite(
            op=OP_CREATE,
            calendar_id=main,
            body=blocked_body(event, main),
            source=key,
            reason="new",
        )
    return None


def plan_blocked(
    changes: Sequence[Event],
    links: Mapping[str, Link],
    settings: Settings,
    now: datetime | None = None,
) -> list[PlannedWrite]:
    """Plan BLOCKED events on main; with `now`, none is created for a past event."""
    main = settings.main_calendar
    if main == "":
        return []
    last = {source_key(event): index for index, event in enumerate(changes)}
    writes: list[PlannedWrite] = []
    for index, event in enumerate(changes):
        if last[source_key(event)] != index:
            continue
        if event.calendar_id not in settings.secondary_calendars or event.calendar_id == main:
            continue
        if is_mirror(event):
            continue
        write = _plan_one(event, links, main)
        if write is not None and write.op == OP_CREATE and now is not None:
            if not is_future(event, now):
                continue
        if write is not None:
            writes.append(write)
    return writes
