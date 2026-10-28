"""Apply confirmed rule-change moves and archive cleanups. Dry runs change nothing."""

import dataclasses
import logging
from datetime import UTC, date, datetime, time
from pathlib import Path

from cal_mirror.alerts import step_alert
from cal_mirror.archive import ArchiveError, archived_sources, write_archive
from cal_mirror.cleanup import exceeds_budget, is_owned, plan_cleanup, verify_archive
from cal_mirror.model import (
    FAIL_ARCHIVE,
    KIND_COPY,
    REQ_ARCHIVE,
    REQ_CONFIRMED,
    REQ_DONE,
    REQ_FAILED,
    REQ_PENDING,
    REQ_READY,
    REQ_RULES,
    Event,
    Failure,
    Link,
    Request,
    Rule,
    Settings,
    is_mirror,
    source_key,
)
from cal_mirror.plan_move import plan_moves
from cal_mirror.ports import (
    Calendar,
    CalendarError,
    Clock,
    Notifier,
    NotifyError,
    Store,
    StoreError,
)
from cal_mirror.rules import choose_calendar

_LOG = logging.getLogger("cal_mirror.requests")
MAX_PAST_INSTANCES = 2000
_CAUGHT = (CalendarError, StoreError, ArchiveError, ValueError, KeyError, TypeError)


def process_requests(store: Store, calendar: Calendar, clock: Clock, notifier: Notifier) -> None:
    pending = sorted(store.list_requests(REQ_PENDING), key=lambda req: req.id)
    confirmed = sorted(store.list_requests(REQ_CONFIRMED), key=lambda req: req.id)
    for req in pending:
        handle_request(store, calendar, clock, notifier, req)
    for req in confirmed:
        handle_request(store, calendar, clock, notifier, req)


def handle_request(
    store: Store, calendar: Calendar, clock: Clock, notifier: Notifier, req: Request
) -> None:
    if req.state not in (REQ_PENDING, REQ_CONFIRMED):
        return
    now = clock.now()
    settings: Settings | None = None
    try:
        settings = store.get_settings()
        if req.kind == REQ_RULES:
            _handle_rules(store, calendar, req, now, settings)
        elif req.kind == REQ_ARCHIVE:
            _handle_archive(store, calendar, notifier, req, now, settings)
        else:
            _finish(store, req, REQ_FAILED, {"error": "unknown request kind"})
    except _CAUGHT as err:
        _finish(store, req, REQ_FAILED, {"error": _error_text(err)})
        if req.kind == REQ_ARCHIVE:
            _archive_alert(store, notifier, req, now, settings)


def _handle_rules(
    store: Store, calendar: Calendar, req: Request, now: datetime, settings: Settings
) -> None:
    proposed = [
        Rule(id=i + 1, kind=d["kind"], value=d["value"], calendar_id=d["calendar_id"])
        for i, d in enumerate(req.params["rules"])
    ]

    def place(event: Event) -> str:
        return choose_calendar(event, proposed, settings.default_calendar)[0]

    owned = _owned_ids(calendar)
    sources = [
        link.source.split("/", 1)[0] for link in store.list_links() if link.kind == KIND_COPY
    ]
    missing = _unowned(sources, owned)
    if missing:
        _finish(store, req, REQ_FAILED, {"error": _not_owned(missing, "moved")})
        return
    copies = _copy_pairs(store, calendar)
    writes = plan_moves(copies, now, place)
    write_ids: list[str] = []
    for write in writes:
        write_ids.append(write.calendar_id)
        write_ids.append(write.destination)
    missing = _unowned(write_ids, owned)
    if missing:
        _finish(store, req, REQ_FAILED, {"error": _not_owned(missing, "moved")})
        return
    if req.state == REQ_PENDING:
        _finish(store, req, REQ_READY, {"moves": len(writes)})
        return
    store.save_rules(proposed)
    for cid in (settings.main_calendar, *settings.secondary_calendars):
        if cid:
            store.set_sync_token(cid, None)
    by_source = {link.source: (link, src) for link, src in copies}
    applied = 0
    try:
        for write in writes:
            new_id = calendar.apply(write)
            link, src = by_source[write.source]
            matched = choose_calendar(src, proposed, settings.default_calendar)[1]
            rule_id = None if matched is None else matched.id
            store.put_link(
                dataclasses.replace(
                    link,
                    calendar_id=write.destination,
                    event_id=new_id,
                    rule_id=rule_id,
                )
            )
            prefix = link.source + "_"
            for inst in store.list_links():
                if inst.kind == KIND_COPY and inst.source.startswith(prefix):
                    store.put_link(dataclasses.replace(inst, calendar_id=write.destination))
            applied += 1
    except _CAUGHT as err:
        _finish(store, req, REQ_FAILED, {"error": _error_text(err), "moves": applied})
        return
    _finish(store, req, REQ_DONE, {"moves": applied})


def _handle_archive(
    store: Store,
    calendar: Calendar,
    notifier: Notifier,
    req: Request,
    now: datetime,
    settings: Settings,
) -> None:
    cutoff = date.fromisoformat(req.params["cutoff"])
    destination = req.params["destination"]
    if destination not in settings.archive_destinations:
        _fail_archive(store, notifier, req, now, settings, {"error": "unknown archive destination"})
        return
    if req.state == REQ_CONFIRMED and req.result.get("check") != "ok":
        _fail_archive(store, notifier, req, now, settings, {"error": "archive check failed"})
        return
    owned = _owned_ids(calendar)
    events, plan = _archive_plan(store, calendar, cutoff, settings, owned)
    missing = _unowned([write.calendar_id for write in plan.deletes], owned)
    if missing:
        _fail_archive(
            store,
            notifier,
            req,
            now,
            settings,
            {"error": _not_owned(missing, "deleted")},
        )
        return
    if req.state == REQ_PENDING:
        paths = write_archive(events, Path(destination), cutoff)
        ok = verify_archive(plan, archived_sources(paths))
        _finish(
            store,
            req,
            REQ_READY,
            {
                "counts": dict(plan.counts),
                "check": "ok" if ok else "failed",
                "paths": [str(path) for path in paths],
                "deletes": _delete_pairs(plan),
                "kept_unbounded": plan.kept_unbounded,
            },
        )
        if not ok:
            _archive_alert(store, notifier, req, now, settings)
        return
    fresh = _delete_pairs(plan)
    raw_deletes = req.result.get("deletes")
    stored = [list(pair) for pair in raw_deletes] if isinstance(raw_deletes, list) else None
    if stored is None or fresh != stored:
        _fail_archive(
            store,
            notifier,
            req,
            now,
            settings,
            {"error": "the events to delete changed since the dry run; nothing was deleted"},
        )
        return
    stored_paths = [Path(path) for path in req.result["paths"]]
    if not verify_archive(plan, archived_sources(stored_paths)):
        _fail_archive(store, notifier, req, now, settings, {"error": "archive check failed"})
        return
    deleted = 0
    try:
        for write in plan.deletes:
            calendar.apply(write)
            store.delete_link(write.source)
            deleted += 1
    except _CAUGHT as err:
        _fail_archive(
            store,
            notifier,
            req,
            now,
            settings,
            {"error": _error_text(err), "deleted": deleted},
        )
        return
    _finish(store, req, REQ_DONE, {**dict(req.result), "deleted": deleted})


def _delete_pairs(plan) -> list[list[str]]:
    return sorted([write.calendar_id, write.event_id] for write in plan.deletes)


def _owned_ids(calendar: Calendar) -> set[str]:
    return {item.id for item in calendar.list_calendars() if item.access_role == "owner"}


def _unowned(ids: list[str], owned: set[str]) -> list[str]:
    missing: list[str] = []
    seen: set[str] = set()
    for calendar_id in ids:
        if calendar_id in owned or calendar_id in seen:
            continue
        seen.add(calendar_id)
        missing.append(calendar_id)
    return missing


def _not_owned(ids: list[str], done: str) -> str:
    return "calendar not owned: " + ", ".join(ids) + "; nothing was " + done


def _archive_plan(
    store: Store, calendar: Calendar, cutoff: date, settings: Settings, owned: set[str]
):
    events = {
        calendar_id: [
            event for event in calendar.list_events(calendar_id) if event.status != "cancelled"
        ]
        for calendar_id in owned
    }
    links = {link.source: link for link in store.list_links()}
    limit = datetime.combine(cutoff, time(0), tzinfo=UTC)
    planning: dict[str, list] = {}
    for calendar_id, calendar_events in events.items():
        master_ids = {event.id for event in calendar_events if event.recurrence}
        held: set[str] = set()
        for event in calendar_events:
            master_id = event.recurring_event_id
            if master_id not in master_ids:
                continue
            if event.start is None:
                held.add(master_id)
                continue
            end = event.end if event.end is not None else event.start
            start_at = (
                event.start
                if isinstance(event.start, datetime)
                else datetime.combine(event.start, time(0), tzinfo=UTC)
            )
            end_at = (
                end if isinstance(end, datetime) else datetime.combine(end, time(0), tzinfo=UTC)
            )
            if start_at >= limit or end_at >= limit:
                held.add(master_id)
        planning[calendar_id] = [event for event in calendar_events if event.id not in held]
    plan = plan_cleanup(planning, cutoff, settings.owner_email, links)
    past, too_many = _past_instances(calendar, events, plan.sources, limit, settings.owner_email)
    if too_many:
        plan = dataclasses.replace(plan, kept_unbounded=plan.kept_unbounded + too_many)
    if not past:
        return events, plan
    for calendar_id, found in past.items():
        by_id = {event.id: event for event in events[calendar_id]}
        by_id.update({event.id: event for event in found})
        events[calendar_id] = list(by_id.values())
    every = [event for found in past.values() for event in found]
    cleaned = plan_cleanup(planning, cutoff, settings.owner_email, links, every)
    return events, dataclasses.replace(cleaned, kept_unbounded=cleaned.kept_unbounded + too_many)


def _past_instances(
    calendar: Calendar,
    events: dict[str, list],
    doomed: frozenset[str],
    limit: datetime,
    owner_email: str,
) -> tuple[dict[str, list], int]:
    """Ended occurrences of series the owner owns whose master stays on the calendar.

    A series with more than MAX_PAST_INSTANCES ended occurrences is left alone and counted.
    """
    found: dict[str, list] = {}
    too_many = 0
    for calendar_id, calendar_events in events.items():
        for master in calendar_events:
            if not master.recurrence or master.status == "cancelled" or is_mirror(master):
                continue
            if not is_owned(master, owner_email) or source_key(master) in doomed:
                continue
            if not isinstance(master.start, (datetime, date)):
                continue
            starts = (
                master.start
                if isinstance(master.start, datetime)
                else datetime.combine(master.start, time(0), tzinfo=UTC)
            )
            if starts >= limit:
                continue
            ended = []
            for item in calendar.list_instances(calendar_id, master.id, limit):
                if item.status == "cancelled" or item.start is None:
                    continue
                end = item.end if item.end is not None else item.start
                end_at = (
                    end if isinstance(end, datetime) else datetime.combine(end, time(0), tzinfo=UTC)
                )
                if end_at <= limit:
                    ended.append(item)
            if len(ended) > MAX_PAST_INSTANCES:
                too_many += 0 if exceeds_budget(master.recurrence) else 1
            elif ended:
                found.setdefault(calendar_id, []).extend(ended)
    return found, too_many


def _copy_pairs(store: Store, calendar: Calendar) -> list[tuple[Link, Event]]:
    pairs: list[tuple[Link, Event]] = []
    for link in store.list_links():
        if link.kind != KIND_COPY:
            continue
        calendar_id, event_id = link.source.split("/", 1)
        source = calendar.get_event(calendar_id, event_id)
        if source is None:
            continue
        pairs.append((link, source))
    return pairs


def _fail_archive(
    store: Store,
    notifier: Notifier,
    req: Request,
    now: datetime,
    settings: Settings,
    result: dict,
) -> None:
    _finish(store, req, REQ_FAILED, result)
    _archive_alert(store, notifier, req, now, settings)


def _archive_alert(
    store: Store,
    notifier: Notifier,
    req: Request,
    now: datetime,
    settings: Settings | None,
) -> None:
    state, message = step_alert(store.get_alert_state(), Failure(FAIL_ARCHIVE), now)
    store.set_alert_state(state)
    if message is None:
        return
    web = "" if settings is None else settings.web_url
    click = "" if web == "" else f"{web}/archive/{req.id}"
    try:
        notifier.send("cal-mirror", message, click=click)
    except NotifyError as err:
        _LOG.error("%s", type(err).__name__)


def _finish(store: Store, req: Request, state: str, result: dict) -> None:
    store.update_request(dataclasses.replace(req, state=state, result=result))
    counts: dict[str, object] = {}
    if "moves" in result:
        counts["moves"] = result["moves"]
    if "deleted" in result:
        counts["deleted"] = result["deleted"]
    if "counts" in result:
        counts["counts"] = dict(result["counts"])
    _LOG.info("request %s %s %s %s", req.id, req.kind, state, counts)


def _error_text(err: BaseException) -> str:
    text = str(err)
    if text == "":
        return type(err).__name__
    return text
