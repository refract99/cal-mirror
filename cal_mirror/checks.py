"""Pure meeting checks: failures, notice planning, and message text.

No store, network, clock, environment, file access, or logging. The worker
sends notices and saves the returned state.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from cal_mirror.model import (
    CHECK_HOURS,
    CHECK_NO_LINK,
    Event,
    Notice,
    Settings,
    is_meeting,
    source_key,
)

_HOST_SPLIT = re.compile("[/?#:'\"\\s<]+")
_CALL_HOSTS = frozenset({"meet.google.com", "zoom.us", "teams.microsoft.com", "teams.live.com"})
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_CHECK_WORDS = {
    CHECK_NO_LINK: "no call link",
    CHECK_HOURS: "outside hours",
}


def _is_call_host(token: str) -> bool:
    host = token.lower()
    if host in _CALL_HOSTS:
        return True
    return len(host) > len(".zoom.us") and host.endswith(".zoom.us")


def _text_has_call_host(text: str) -> bool:
    return any(_is_call_host(token) for token in _HOST_SPLIT.split(text) if token)


def has_call_link(event: Event) -> bool:
    """True when the event has a video meeting link.

    A phone-only conference or dial-in numbers alone are not a call link.
    """
    if event.meeting_link != "":
        return True
    points = event.conference.get("entryPoints") or ()
    for entry in points:
        if isinstance(entry, Mapping) and entry.get("entryPointType") == "video":
            return True
    return _text_has_call_host(event.location) or _text_has_call_host(event.description)


def _outside_hours(event: Event, settings: Settings) -> bool:
    start = event.start
    end = event.start if event.end is None else event.end
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return False
    zone = ZoneInfo(settings.time_zone)
    local_start = start.astimezone(zone)
    local_end = end.astimezone(zone)
    allowed = settings.allowed_hours.get(local_start.weekday())
    if allowed is None:
        return True
    return (
        local_start.time() < allowed[0]
        or local_end.time() > allowed[1]
        or local_end.date() > local_start.date()
    )


def meeting_failures(event: Event, settings: Settings) -> tuple[str, ...]:
    """Failed checks for a meeting, in check order. Empty when it is not a meeting."""
    if not is_meeting(event, settings.owner_email):
        return ()
    failures: list[str] = []
    if settings.check_no_link and not has_call_link(event):
        failures.append(CHECK_NO_LINK)
    if settings.check_hours and _outside_hours(event, settings):
        failures.append(CHECK_HOURS)
    return tuple(failures)


def _fingerprint(event: Event) -> str:
    end = "" if event.end is None else event.end.isoformat()
    start = event.start.isoformat() if isinstance(event.start, datetime) else ""
    return f"{start}|{end}"


def plan_notices(
    changes: Sequence[Event],
    states: Mapping[str, Mapping[str, str]],
    settings: Settings,
    now: datetime,
) -> tuple[list[Notice], dict[str, dict[str, str]]]:
    """Plan one notice per new failure and the next state for each main-calendar source.

    A source that appears more than once is planned once, from its last occurrence,
    in that occurrence's position. Other calendars are skipped.
    """
    last: dict[str, Event] = {}
    order: list[str] = []
    for event in changes:
        if settings.main_calendar == "" or event.calendar_id != settings.main_calendar:
            continue
        key = source_key(event)
        if key in last:
            order.remove(key)
        order.append(key)
        last[key] = event

    notices: list[Notice] = []
    new_states: dict[str, dict[str, str]] = {}
    for key in order:
        event = last[key]
        old = states.get(key, {})
        if event.status == "cancelled":
            new_states[key] = {}
            continue
        start = event.start
        if not isinstance(start, datetime) or start <= now:
            new_states[key] = dict(old)
            continue
        fingerprint = _fingerprint(event)
        failing = set(meeting_failures(event, settings))
        new: dict[str, str] = {}
        for check in (CHECK_NO_LINK, CHECK_HOURS):
            if check not in failing:
                continue
            new[check] = fingerprint
            if old.get(check) != fingerprint:
                notices.append(Notice(source=key, check=check, start=start))
        new_states[key] = new
    return notices, new_states


def notice_text(notice: Notice, settings: Settings) -> str:
    """One meeting-check message. No title and no other event detail."""
    local = notice.start.astimezone(ZoneInfo(settings.time_zone))
    hour = local.hour % 12 or 12
    ampm = "AM" if local.hour < 12 else "PM"
    day = _DAYS[local.weekday()]
    words = _CHECK_WORDS[notice.check]
    return (
        f"{day} {local.date().isoformat()} {hour}:{local.minute:02d} {ampm} "
        f"{local.tzname()} meeting: {words}"
    )


def summary_text(notices: Sequence[Notice], settings: Settings) -> str:
    """One first-start summary. `settings` is unused; the worker passes it."""
    del settings
    sources = {notice.source for notice in notices}
    count = len(sources)
    if count == 0:
        return "cal-mirror first start: no future meetings fail checks"
    parts: list[str] = []
    no_link = sum(1 for notice in notices if notice.check == CHECK_NO_LINK)
    hours = sum(1 for notice in notices if notice.check == CHECK_HOURS)
    if no_link:
        parts.append(f"{no_link} no call link")
    if hours:
        parts.append(f"{hours} outside hours")
    noun = "meeting" if count == 1 else "meetings"
    verb = "fails" if count == 1 else "fail"
    text = f"cal-mirror first start: {count} future {noun} {verb} checks"
    if parts:
        text += f" ({', '.join(parts)})"
    return text
