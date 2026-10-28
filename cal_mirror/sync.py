"""One sync run: read changes, write mirrors, check meetings, alert."""

import base64
import hashlib
import json
import logging
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import quote

from cal_mirror.alerts import step_alert
from cal_mirror.checks import notice_text, plan_notices, summary_text
from cal_mirror.model import (
    FAIL_API,
    FAIL_DB,
    FAIL_TOKEN,
    KIND_BLOCKED,
    KIND_COPY,
    MARKER_KIND,
    MARKER_SOURCE,
    OP_CREATE,
    OP_DELETE,
    OP_MOVE,
    OP_UPDATE,
    CalendarInfo,
    Event,
    Failure,
    Link,
    PlacementEntry,
    PlannedWrite,
    Rule,
    Settings,
    Status,
    source_key,
)
from cal_mirror.plan_blocked import plan_blocked
from cal_mirror.plan_copy import plan_copies
from cal_mirror.ports import (
    Calendar,
    CalendarAuthError,
    CalendarError,
    Clock,
    Notifier,
    NotifyError,
    Store,
    StoreError,
    SyncTokenExpired,
)
from cal_mirror.rules import choose_calendar, guest_domains

log = logging.getLogger("cal_mirror.sync")

_RECENT = "recent_notices"
_FIRST = "first_start_done"
_RECENT_LIMIT = 50


@dataclass(frozen=True)
class RunResult:
    writes: int = 0
    notices: int = 0
    failure: str = ""


class _Run:
    def __init__(self) -> None:
        self.writes = 0
        self.notices = 0
        self.failure = ""
        self.settings = Settings()
        self.early = False
        self.unowned: list[str] = []
        self.hold: set[str] = set()


def mirror_event_id(source: str, kind: str) -> str:
    """Stable Google event id for one source and kind."""
    text = f"{source}|{kind}"
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return base64.b32hexencode(digest).decode("ascii").rstrip("=").lower()


def _mark_unowned(run: _Run, calendar_id: str) -> None:
    if calendar_id not in run.unowned:
        run.unowned.append(calendar_id)
    if run.failure == "":
        run.failure = FAIL_API


def run_once(store: Store, calendar: Calendar, notifier: Notifier, clock: Clock) -> RunResult:
    """Read every selected calendar once and apply the planners. Does not raise port errors."""
    now = clock.now()
    run = _Run()
    try:
        _execute(store, calendar, notifier, now, run)
    except CalendarAuthError as exc:
        log.error("%s: %s", CalendarAuthError.__name__, exc)
        run.failure = FAIL_TOKEN
    except CalendarError as exc:
        log.error("%s", type(exc).__name__)
        run.failure = FAIL_API
    except StoreError:
        log.error("%s", StoreError.__name__)
        run.failure = FAIL_DB
    if run.early:
        return RunResult()
    _finish(store, notifier, now, run)
    log.info("writes=%s notices=%s", run.writes, run.notices)
    return RunResult(writes=run.writes, notices=run.notices, failure=run.failure)


def _execute(
    store: Store,
    calendar: Calendar,
    notifier: Notifier,
    now: datetime,
    run: _Run,
) -> None:
    listed = calendar.list_calendars()
    store.set_calendars(listed)
    run.settings = store.get_settings()
    if run.settings.main_calendar == "":
        run.early = True
        return
    owned = {item.id for item in listed if item.access_role == "owner"}
    selected = _selected(run.settings)
    for cid in selected:
        if cid not in owned:
            _mark_unowned(run, cid)
    first = store.get_meta(_FIRST) != "1"
    changes, pending, failed = _read_all(store, calendar, selected, owned, now, first, run)
    blocked, copies = _link_maps(store)
    remembered: dict[str, Rule | None] = {}
    rules = store.list_rules()

    def place(event: Event) -> str:
        chosen, rule = choose_calendar(event, rules, run.settings.default_calendar)
        remembered[source_key(event)] = rule
        return chosen

    events = {source_key(event): event for batch in changes.values() for event in batch}
    for masters in (True, False):
        for cid in selected:
            if cid in failed or cid not in changes:
                continue
            phase = _phase(changes[cid], masters=masters)
            try:
                _apply_phase(
                    store,
                    calendar,
                    run,
                    phase,
                    blocked,
                    copies,
                    place,
                    remembered,
                    events,
                    now,
                    owned,
                    listed,
                )
            except CalendarAuthError:
                raise
            except CalendarError as exc:
                log.error("%s", type(exc).__name__)
                run.failure = FAIL_API
                failed.add(cid)
    main = run.settings.main_calendar
    if not first and main in changes and main not in failed:
        _notices(store, notifier, run, changes[main], events, now, first)
    elif first and run.failure == "":
        _notices(store, notifier, run, changes.get(main, []), events, now, first)
    for cid, token in pending.items():
        if cid not in failed and cid not in run.hold:
            store.set_sync_token(cid, token)


def _selected(settings: Settings) -> list[str]:
    chosen: list[str] = []
    for cid in (settings.main_calendar, *settings.secondary_calendars):
        if cid != "" and cid not in chosen:
            chosen.append(cid)
    return chosen


def _read_all(
    store: Store,
    calendar: Calendar,
    selected: list[str],
    owned: set[str],
    now: datetime,
    first: bool,
    run: _Run,
) -> tuple[dict[str, list[Event]], dict[str, str], set[str]]:
    changes: dict[str, list[Event]] = {}
    pending: dict[str, str] = {}
    failed: set[str] = set()
    for cid in selected:
        if cid not in owned:
            continue
        try:
            found, token = _read_one(store, calendar, cid, now, first)
        except CalendarAuthError:
            raise
        except CalendarError as exc:
            log.error("%s", type(exc).__name__)
            run.failure = FAIL_API
            failed.add(cid)
            continue
        changes[cid] = found
        pending[cid] = token
    return changes, pending, failed


def _read_one(
    store: Store,
    calendar: Calendar,
    cid: str,
    now: datetime,
    first: bool,
) -> tuple[list[Event], str]:
    stored = store.get_sync_token(cid)
    if first or not stored:
        return calendar.list_changes(cid, None, time_min=now)
    try:
        return calendar.list_changes(cid, stored)
    except SyncTokenExpired:
        log.info("%s", SyncTokenExpired.__name__)
        store.set_sync_token(cid, None)
        return calendar.list_changes(cid, None, time_min=now)


def _link_maps(store: Store) -> tuple[dict[str, Link], dict[str, Link]]:
    blocked: dict[str, Link] = {}
    copies: dict[str, Link] = {}
    for link in store.list_links():
        if link.kind == KIND_BLOCKED:
            blocked[link.source] = link
        elif link.kind == KIND_COPY:
            copies[link.source] = link
    return blocked, copies


def _phase(events: list[Event], *, masters: bool) -> list[Event]:
    if masters:
        return [event for event in events if event.recurring_event_id == ""]
    return [event for event in events if event.recurring_event_id != ""]


def _lost_copy(
    calendar: Calendar,
    listed: list[CalendarInfo],
    write: PlannedWrite,
    mid: str,
) -> str | None:
    """Owned calendar, other than the create target, that still has this copy."""
    for item in listed:
        if item.access_role != "owner" or item.id == write.calendar_id:
            continue
        found = calendar.get_event(item.id, mid)
        if found is None or found.status == "cancelled":
            continue
        private = found.private
        if private.get(MARKER_SOURCE) != write.source or private.get(MARKER_KIND) != KIND_COPY:
            continue
        return item.id
    return None


def _apply_phase(
    store: Store,
    calendar: Calendar,
    run: _Run,
    phase: list[Event],
    blocked: dict[str, Link],
    copies: dict[str, Link],
    place,
    remembered: dict[str, Rule | None],
    events: dict[str, Event],
    now: datetime,
    owned: set[str],
    listed: list[CalendarInfo],
) -> None:
    planned = [(KIND_BLOCKED, item) for item in plan_blocked(phase, blocked, run.settings, now)]
    planned += [(KIND_COPY, item) for item in plan_copies(phase, copies, run.settings, place)]
    for kind, write in planned:
        if kind == KIND_COPY and write.op == OP_CREATE and write.calendar_id == "":
            continue
        if write.calendar_id not in owned:
            _mark_unowned(run, write.calendar_id)
            run.hold.add(write.source.split("/", 1)[0])
            continue
        if write.op == OP_CREATE:
            mid = mirror_event_id(write.source, kind)
            write = replace(write, event_id=mid)
            if kind == KIND_COPY:
                origin = _lost_copy(calendar, listed, write, mid)
                if origin is not None:
                    calendar.apply(
                        PlannedWrite(
                            OP_MOVE,
                            origin,
                            event_id=mid,
                            destination=write.calendar_id,
                            source=write.source,
                            reason="recover",
                        )
                    )
                    run.writes += 1
        new_id = calendar.apply(write)
        run.writes += 1
        _after_write(store, kind, write, new_id, blocked, copies, remembered, events, now)


def _after_write(
    store: Store,
    kind: str,
    write: PlannedWrite,
    new_id: str,
    blocked: dict[str, Link],
    copies: dict[str, Link],
    remembered: dict[str, Rule | None],
    events: dict[str, Event],
    now: datetime,
) -> None:
    links = blocked if kind == KIND_BLOCKED else copies
    if write.op == OP_DELETE:
        store.delete_link(write.source)
        links.pop(write.source, None)
        return
    if write.op == OP_CREATE:
        rule = remembered.get(write.source)
        rule_id = None if rule is None else rule.id
        link = _link(write, kind, new_id, rule_id)
        store.put_link(link)
        links[link.source] = link
        if kind == KIND_COPY and rule is None:
            _placement(store, events[write.source], write.calendar_id, now)
        return
    if write.op == OP_UPDATE:
        link = _link(write, kind, write.event_id, _kept_rule(write, links, events))
        store.put_link(link)
        links[link.source] = link


def _link(write: PlannedWrite, kind: str, event_id: str, rule_id: int | None) -> Link:
    start = None if write.body is None else write.body.start
    return Link(
        source=write.source,
        kind=kind,
        calendar_id=write.calendar_id,
        event_id=event_id,
        start=start,
        rule_id=rule_id,
    )


def _kept_rule(write: PlannedWrite, links: dict[str, Link], events: dict[str, Event]) -> int | None:
    current = links.get(write.source)
    if current is not None:
        return current.rule_id
    source = events.get(write.source)
    if source is None or source.recurring_event_id == "":
        return None
    series = links.get(f"{source.calendar_id}/{source.recurring_event_id}")
    if series is None:
        return None
    return series.rule_id


def _placement(store: Store, source: Event, calendar_id: str, now: datetime) -> None:
    store.add_placement(
        PlacementEntry(
            id=0,
            at=now,
            source=source_key(source),
            calendar_id=calendar_id,
            title=source.summary,
            organizer_email=source.organizer_email,
            guest_domains=guest_domains(source),
        )
    )


def _notices(
    store: Store,
    notifier: Notifier,
    run: _Run,
    main_changes: list[Event],
    events: dict[str, Event],
    now: datetime,
    first: bool,
) -> None:
    states = {
        source_key(event): store.get_notice_state(source_key(event)) for event in main_changes
    }
    notices, new_states = plan_notices(main_changes, states, run.settings, now)
    for key, state in new_states.items():
        store.set_notice_state(key, state)
    run.notices = len(notices)
    _prepend_recent(store, notices, events)
    if first:
        _send(notifier, summary_text(notices, run.settings), _status_click(run.settings))
        store.set_meta(_FIRST, "1")
        return
    for notice in notices:
        text = notice_text(notice, run.settings)
        _send(notifier, text, _notice_click(run.settings, notice.source))


def _prepend_recent(store: Store, notices, events: dict[str, Event]) -> None:
    if not notices:
        return
    raw = store.get_meta(_RECENT)
    items: list = []
    if raw:
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            loaded = []
        if isinstance(loaded, list):
            items = loaded
    for notice in notices:
        source = events.get(notice.source)
        items.insert(
            0,
            {
                "source": notice.source,
                "check": notice.check,
                "start": notice.start.isoformat(),
                "title": "" if source is None else source.summary,
            },
        )
    del items[_RECENT_LIMIT:]
    store.set_meta(_RECENT, json.dumps(items))


def _status_click(settings: Settings) -> str:
    web = settings.web_url.rstrip("/")
    if web == "":
        return ""
    return f"{web}/status"


def _notice_click(settings: Settings, source: str) -> str:
    web = settings.web_url.rstrip("/")
    if web == "":
        return ""
    return f"{web}/status?notice={quote(source, safe='')}"


def _send(notifier: Notifier, message: str, click: str) -> None:
    try:
        notifier.send("cal-mirror", message, click=click)
    except NotifyError:
        log.error("%s", NotifyError.__name__)


def _finish(store: Store, notifier: Notifier, now: datetime, run: _Run) -> None:
    if run.failure == "":
        try:
            _status_ok(store, now)
        except StoreError:
            log.error("%s", StoreError.__name__)
            run.failure = FAIL_DB
    if run.failure != "":
        try:
            detail = ""
            if run.failure == FAIL_API and run.unowned:
                detail = "calendar not owned: " + ", ".join(run.unowned)
            _status_bad(store, run.failure, detail)
        except StoreError:
            log.error("%s", StoreError.__name__)
    _alert(store, notifier, run, now)


def _status_ok(store: Store, now: datetime) -> None:
    links = store.list_links()
    blocked = sum(1 for link in links if link.kind == KIND_BLOCKED)
    copies = sum(1 for link in links if link.kind == KIND_COPY)
    store.set_status(
        Status(
            last_good_sync=now,
            failure="",
            token_ok=True,
            counts={"blocked": blocked, "copies": copies},
        )
    )


def _status_bad(store: Store, failure: str, detail: str) -> None:
    prev = store.get_status()
    token_ok = False if failure == FAIL_TOKEN else prev.token_ok
    store.set_status(replace(prev, failure=detail or failure, token_ok=token_ok))


def _alert(store: Store, notifier: Notifier, run: _Run, now: datetime) -> None:
    try:
        state = store.get_alert_state()
        failure = None if run.failure == "" else Failure(run.failure)
        new_state, message = step_alert(state, failure, now)
        store.set_alert_state(new_state)
    except StoreError:
        log.error("%s", StoreError.__name__)
        return
    if message is not None:
        _send(notifier, message, _status_click(run.settings))
