from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time

from dateutil.rrule import rrulestr

from cal_mirror.model import OP_DELETE, Event, Link, PlannedWrite, is_mirror, source_key

MAX_OCCURRENCES = 5000


@dataclass(frozen=True)
class CleanupPlan:
    deletes: tuple[PlannedWrite, ...]
    counts: Mapping[str, int]
    sources: frozenset[str]
    kept_unbounded: int = 0


def _instant(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, time(0), tzinfo=UTC)


def _year(start: datetime | date) -> int:
    if isinstance(start, datetime):
        return start.astimezone(UTC).year
    return start.year


def _owned(event: Event, owner_email: str) -> bool:
    owner = owner_email.strip().casefold()
    if not owner:
        return False
    return owner in (
        event.organizer_email.strip().casefold(),
        event.creator_email.strip().casefold(),
    )


def _limit(cutoff: date) -> datetime:
    return datetime.combine(cutoff, time(0), tzinfo=UTC)


_COMMON_PARTS = frozenset({"FREQ", "INTERVAL", "COUNT", "UNTIL", "WKST"})
_WEEKDAYS = frozenset({"MO", "TU", "WE", "TH", "FR", "SA", "SU"})
_ALLOWED_FREQ = frozenset({"DAILY", "WEEKLY", "MONTHLY", "YEARLY"})
_EXTRA_PARTS = {
    "DAILY": frozenset(),
    "WEEKLY": frozenset({"BYDAY"}),
    "MONTHLY": frozenset({"BYDAY", "BYMONTHDAY", "BYMONTH"}),
    "YEARLY": frozenset({"BYDAY", "BYMONTHDAY", "BYMONTH"}),
}


def _prop_name(line: str) -> str:
    head = line.split(":", 1)[0]
    return head.split(";", 1)[0].strip().upper()


def _prop_value(line: str) -> str:
    if ":" not in line:
        return ""
    return line.split(":", 1)[1]


def _csv_count(value: str) -> int:
    if value.strip() == "":
        return 0
    return len(value.split(","))


def _rrule_over_budget(line: str) -> bool:
    freq: str | None = None
    parts: list[tuple[str, str]] = []
    for piece in _prop_value(line).split(";"):
        if piece.strip() == "":
            continue
        key, sep, raw = piece.partition("=")
        if sep != "=":
            return True
        key = key.strip().upper()
        raw = raw.strip()
        parts.append((key, raw))
        if key == "FREQ":
            candidate = raw.upper()
            if freq is not None and freq != candidate:
                return True
            freq = candidate
    if freq not in _ALLOWED_FREQ:
        if freq not in {"SECONDLY", "MINUTELY", "HOURLY"}:
            raise ValueError
        return True
    allowed = _COMMON_PARTS | _EXTRA_PARTS[freq]
    for key, raw in parts:
        if key not in allowed:
            return True
        if key == "COUNT":
            try:
                count = int(raw)
            except ValueError:
                return True
            if count > MAX_OCCURRENCES:
                return True
        if key == "INTERVAL":
            try:
                interval = int(raw)
            except ValueError:
                return True
            if interval < 1:
                return True
        if key == "BYDAY" and freq == "WEEKLY":
            days = [day.strip().upper() for day in raw.split(",")]
            if not days or any(day not in _WEEKDAYS for day in days):
                return True
    return False


def _exceeds_budget(recurrence: Sequence[str]) -> bool:
    if any(line.split() != [line.strip()] for line in recurrence):
        return True
    rrule_lines: list[str] = []
    dated = 0
    for line in recurrence:
        line = line.strip()
        name = _prop_name(line)
        if name == "EXRULE":
            return True
        if name == "RRULE":
            rrule_lines.append(line)
        elif name in {"RDATE", "EXDATE"}:
            dated += _csv_count(_prop_value(line))
    if len(rrule_lines) > 1 or dated > MAX_OCCURRENCES:
        return True
    return len(rrule_lines) == 1 and _rrule_over_budget(rrule_lines[0])


def bounded_occurrences(
    recurrence: Sequence[str],
    dtstart: datetime,
    stop: datetime | None = None,
) -> tuple[datetime, ...] | None:
    if _exceeds_budget(recurrence):
        return None
    rules = rrulestr("\n".join(recurrence), dtstart=dtstart, forceset=True)
    found: list[datetime] = []
    for occurrence in rules:
        if stop is not None and occurrence >= stop:
            break
        if len(found) == MAX_OCCURRENCES:
            return None
        found.append(occurrence)
    return tuple(found)


def _series_end_state(event: Event, cutoff: date) -> tuple[bool, bool]:
    start = event.start
    if start is None or not event.recurrence:
        return False, False
    for line in event.recurrence:
        upper = line.upper()
        if upper.startswith("RRULE:") and "UNTIL=" not in upper and "COUNT=" not in upper:
            return False, False
    timed = isinstance(start, datetime)
    if isinstance(start, datetime):
        dtstart = start.astimezone(UTC)
    else:
        dtstart = datetime.combine(start, time(0))
    try:
        found = bounded_occurrences(event.recurrence, dtstart)
        if found is None:
            return False, True
        if not found:
            return False, False
        last = found[-1]
        end = event.end if event.end is not None else start
        duration = _instant(end) - _instant(start)
        if timed:
            return last + duration <= _limit(cutoff), False
        return last.date() + duration <= cutoff, False
    except (ValueError, TypeError):
        return False, False


def series_ends_before(event: Event, cutoff: date) -> bool:
    ends, _unbounded = _series_end_state(event, cutoff)
    return ends


def _exception_keeps(master: Event, peers: Sequence[Event], cutoff: date) -> bool:
    limit = _limit(cutoff)
    for event in peers:
        if event.recurring_event_id != master.id or event.status == "cancelled":
            continue
        if event.start is None:
            return True
        end = event.end if event.end is not None else event.start
        if _instant(event.start) >= limit or _instant(end) >= limit:
            return True
    return False


def _classify(event: Event, cutoff: date, owner_email: str, peers: Sequence[Event]) -> str:
    if event.status == "cancelled" or is_mirror(event) or not _owned(event, owner_email):
        return "keep"
    if event.recurring_event_id != "":
        return "keep"
    if event.recurrence:
        if _exception_keeps(event, peers, cutoff):
            return "keep"
        ends, unbounded = _series_end_state(event, cutoff)
        if unbounded:
            return "unbounded"
        return "delete" if ends else "keep"
    if event.start is None:
        return "keep"
    limit = _limit(cutoff)
    end = event.end if event.end is not None else event.start
    ended = _instant(event.start) < limit and _instant(end) <= limit
    return "delete" if ended else "keep"


def _is_deleted(
    event: Event,
    cutoff: date,
    owner_email: str,
    peers: Sequence[Event] = (),
) -> bool:
    return _classify(event, cutoff, owner_email, peers) == "delete"


def plan_cleanup(
    events: Mapping[str, Sequence[Event]],
    cutoff: date,
    owner_email: str,
    links: Mapping[str, Link],
) -> CleanupPlan:
    doomed: list[Event] = []
    kept_unbounded = 0
    for calendar_events in events.values():
        for event in calendar_events:
            if _is_deleted(event, cutoff, owner_email, calendar_events):
                doomed.append(event)
            elif _classify(event, cutoff, owner_email, calendar_events) == "unbounded":
                kept_unbounded += 1
    doomed.sort(key=source_key)
    deletes: list[PlannedWrite] = []
    years: list[int] = []
    seen: set[tuple[str, str]] = set()
    sources: set[str] = set()

    def add(write: PlannedWrite, year: int) -> None:
        pair = (write.calendar_id, write.event_id)
        if pair in seen:
            return
        seen.add(pair)
        deletes.append(write)
        years.append(year)

    for event in doomed:
        key = source_key(event)
        year = _year(event.start)
        sources.add(key)
        add(
            PlannedWrite(
                op=OP_DELETE,
                calendar_id=event.calendar_id,
                event_id=event.id,
                source=key,
                reason="cleanup",
            ),
            year,
        )
        link = links.get(key)
        if link is not None:
            add(
                PlannedWrite(
                    op=OP_DELETE,
                    calendar_id=link.calendar_id,
                    event_id=link.event_id,
                    source=link.source,
                    reason="cleanup mirror",
                ),
                year,
            )
    tally: dict[str, int] = {}
    for write, year in zip(deletes, years, strict=True):
        name = f"{write.calendar_id}/{year}"
        tally[name] = tally.get(name, 0) + 1
    counts = dict(sorted(tally.items()))
    return CleanupPlan(
        deletes=tuple(deletes),
        counts=counts,
        sources=frozenset(sources),
        kept_unbounded=kept_unbounded,
    )


def verify_archive(plan: CleanupPlan, archived: set[str]) -> bool:
    targets = {f"{write.calendar_id}/{write.event_id}" for write in plan.deletes}
    return (
        targets <= archived
        and len(targets & archived) == len(plan.deletes)
        and sum(plan.counts.values()) == len(plan.deletes)
        and plan.sources <= archived
    )
