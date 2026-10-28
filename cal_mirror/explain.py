"""Plain-words verdict on what happened to one main-calendar event, for the Status page."""

import json
from collections.abc import Mapping, Sequence
from datetime import datetime

from cal_mirror.model import Event, Link, Rule, Settings, declined_by_self, is_busy, source_key
from cal_mirror.rules import match_rule

KEY = "event_decisions"
KEEP = 60


def verdict(
    event: Event,
    link: Link | None,
    rules: Sequence[Rule],
    settings: Settings,
    names: Mapping[str, str],
) -> str:
    def name(calendar_id: str) -> str:
        return names.get(calendar_id, calendar_id)

    if link is not None:
        return f"copied to {name(link.calendar_id)}"
    if event.status == "cancelled":
        return "not copied: cancelled"
    if not is_busy(event):
        return "not copied: shown as Free, not Busy"
    if declined_by_self(event):
        return "not copied: you declined it"
    rule = match_rule(event, rules)
    if rule is not None:
        return f"matches rule {rule.kind} {rule.value}: copy to {name(rule.calendar_id)} pending"
    if settings.default_calendar:
        return f"default: copy to {name(settings.default_calendar)} pending"
    return f"not copied: no rule matched (organizer {event.organizer_email or 'unknown'})"


def merge(
    raw: str | None,
    events: Sequence[Event],
    verdicts: Mapping[str, str],
    now: datetime,
) -> str:
    try:
        loaded = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        loaded = []
    rows = {r["key"]: r for r in loaded if isinstance(r, dict) and "key" in r}
    for event in events:
        key = source_key(event)
        if key in verdicts and event.start is not None:
            rows[key] = {
                "key": key,
                "title": event.summary,
                "start": event.start.isoformat(),
                "verdict": verdicts[key],
            }
    cutoff = now.date().isoformat()
    kept = [r for r in rows.values() if str(r.get("start", ""))[:10] >= cutoff]
    kept.sort(key=lambda r: str(r.get("start", "")))
    return json.dumps(kept[:KEEP])
