from collections.abc import Callable, Mapping, Sequence

from cal_mirror.model import (
    KIND_COPY,
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


def details_block(event: Event) -> str:
    lines: list[str] = []
    if event.meeting_link:
        lines.append(f"Meeting link: {event.meeting_link}")
    lines.extend(f"Dial-in: {entry}" for entry in event.dial_in if entry)
    if event.organizer_email:
        lines.append(f"Organizer: {event.organizer_email}")
    if event.attendees:
        lines.append("Participants:")
        lines.extend(f"- {a.email} ({a.response})" for a in event.attendees)
    return "\n".join(lines)


def copy_body(event: Event, calendar_id: str) -> Event:
    block = details_block(event)
    if block and event.description:
        description = block + "\n\n" + event.description
    else:
        description = block or event.description
    conference = {k: v for k, v in event.conference.items() if k != "createRequest"}
    return Event(
        calendar_id=calendar_id,
        id="",
        summary=event.summary,
        start=event.start,
        end=event.end,
        all_day=event.all_day,
        recurrence=event.recurrence,
        location=event.location,
        attachments=event.attachments,
        transparency="opaque",
        attendees=(),
        meeting_link=event.meeting_link,
        dial_in=event.dial_in,
        conference=conference,
        description=description,
        private={MARKER_SOURCE: source_key(event), MARKER_KIND: KIND_COPY},
        time_zone=event.time_zone,
    )


def _delete_reason(event: Event) -> str:
    if event.status == "cancelled":
        return "cancelled"
    if event.transparency == "transparent":
        return "free"
    return "declined"


def _plan_one(
    event: Event,
    links: Mapping[str, Link],
    place: Callable[[Event], str],
) -> PlannedWrite | None:
    key = source_key(event)
    wanted = is_busy(event) and not declined_by_self(event)
    link = links.get(key)
    if link is not None:
        if wanted:
            return PlannedWrite(
                op=OP_UPDATE,
                calendar_id=link.calendar_id,
                event_id=link.event_id,
                body=copy_body(event, link.calendar_id),
                source=key,
                reason="changed",
            )
        return PlannedWrite(
            op=OP_DELETE,
            calendar_id=link.calendar_id,
            event_id=link.event_id,
            source=key,
            reason=_delete_reason(event),
        )
    if event.recurring_event_id:
        series = links.get(f"{event.calendar_id}/{event.recurring_event_id}")
        if series is not None:
            suffix = event.id.partition("_")[2]
            instance_id = f"{series.event_id}_{suffix}"
            if wanted:
                return PlannedWrite(
                    op=OP_UPDATE,
                    calendar_id=series.calendar_id,
                    event_id=instance_id,
                    body=copy_body(event, series.calendar_id),
                    source=key,
                    reason="instance changed",
                )
            return PlannedWrite(
                op=OP_DELETE,
                calendar_id=series.calendar_id,
                event_id=instance_id,
                source=key,
                reason=_delete_reason(event),
            )
    if not wanted:
        return None
    calendar_id = place(event)
    return PlannedWrite(
        op=OP_CREATE,
        calendar_id=calendar_id,
        event_id="",
        body=copy_body(event, calendar_id),
        source=key,
        reason="new",
    )


def plan_copies(
    changes: Sequence[Event],
    links: Mapping[str, Link],
    settings: Settings,
    place: Callable[[Event], str],
) -> list[PlannedWrite]:
    if not settings.main_calendar:
        return []
    last: dict[str, int] = {}
    for index, event in enumerate(changes):
        if event.calendar_id == settings.main_calendar and not is_mirror(event):
            last[source_key(event)] = index
    writes: list[PlannedWrite] = []
    for index in sorted(last.values()):
        write = _plan_one(changes[index], links, place)
        if write is not None:
            writes.append(write)
    return writes
