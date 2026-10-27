import dataclasses
from datetime import UTC, datetime

from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar
from cal_mirror.model import (
    KIND_COPY,
    MARKER_KIND,
    MARKER_SOURCE,
    REQ_ARCHIVE,
    REQ_CONFIRMED,
    REQ_DONE,
    REQ_FAILED,
    REQ_READY,
    REQ_RULES,
    Link,
    Rule,
    Settings,
)
from cal_mirror.ports import FixedClock
from cal_mirror.requests_runner import process_requests
from cal_mirror.store_memory import MemoryStore

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
RULES = [{"kind": "title_word", "value": "Review", "calendar_id": "home@x"}]
PROPOSED = [Rule(id=1, kind="title_word", value="Review", calendar_id="home@x")]
CUTOFF = "2024-07-01"
MOVED = "calendar not owned: home@x; nothing was moved"
DELETED = "calendar not owned: work@x; nothing was deleted"


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send(self, title: str, message: str, click: str = "") -> None:
        self.messages.append((title, message, click))


class RecordingCalendar:
    """Delegates to GoogleCalendar and counts get_event calls."""

    def __init__(self, inner: GoogleCalendar) -> None:
        self._inner = inner
        self.get_events = 0

    def list_calendars(self):
        return self._inner.list_calendars()

    def list_changes(self, calendar_id, sync_token, time_min=None):
        return self._inner.list_changes(calendar_id, sync_token, time_min=time_min)

    def list_events(self, calendar_id, time_min=None, time_max=None):
        return self._inner.list_events(calendar_id, time_min, time_max)

    def get_event(self, calendar_id, event_id):
        self.get_events += 1
        return self._inner.get_event(calendar_id, event_id)

    def apply(self, write):
        return self._inner.apply(write)


def _updates_are_none(state: FakeCalendar) -> bool:
    return all(write["sendUpdates"] == "none" for write in state.writes)


def _world(
    tmp_path,
    calendars: tuple[str, ...] = ("main@x", "work@x", "home@x"),
    roles: dict[str, str] | None = None,
):
    store = MemoryStore()
    store.save_settings(
        Settings(
            main_calendar="main@x",
            secondary_calendars=("work@x", "home@x"),
            default_calendar="home@x",
            owner_email="steve@x",
            archive_destinations=(str(tmp_path),),
        )
    )
    state = FakeCalendar()
    access = roles or {}
    for calendar_id in calendars:
        state.add_calendar(calendar_id, access_role=access.get(calendar_id, "owner"))
    clock = FixedClock(NOW)
    notifier = RecordingNotifier()
    return store, state, GoogleCalendar(FakeService(state)), clock, notifier


def _timed(event_id: str, start: str, end: str, summary: str, organizer: str, **extra) -> dict:
    item = {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "organizer": {"email": organizer},
        "status": "confirmed",
    }
    item.update(extra)
    return item


def _seed_copies(store: MemoryStore, state: FakeCalendar) -> None:
    state.add_event(
        "main@x",
        _timed("fut", "2026-11-03T15:00:00Z", "2026-11-03T16:00:00Z", "Review Q4", "steve@x"),
    )
    state.add_event(
        "main@x",
        _timed("past", "2026-10-01T15:00:00Z", "2026-10-01T16:00:00Z", "Review notes", "steve@x"),
    )
    for source_id, copy_id, start, end in (
        ("fut", "copy-fut", "2026-11-03T15:00:00Z", "2026-11-03T16:00:00Z"),
        ("past", "copy-past", "2026-10-01T15:00:00Z", "2026-10-01T16:00:00Z"),
    ):
        state.add_event(
            "work@x",
            _timed(
                copy_id,
                start,
                end,
                "Review",
                "steve@x",
                extendedProperties={
                    "private": {MARKER_SOURCE: f"main@x/{source_id}", MARKER_KIND: KIND_COPY}
                },
            ),
        )
        store.put_link(
            Link(
                source=f"main@x/{source_id}", kind=KIND_COPY, calendar_id="work@x", event_id=copy_id
            )
        )


def _seed_archive(store: MemoryStore, state: FakeCalendar) -> None:
    state.add_event(
        "main@x",
        _timed("owned", "2024-03-01T15:00:00Z", "2024-03-01T16:00:00Z", "Mine", "steve@x"),
    )
    state.add_event(
        "work@x",
        _timed(
            "copy-owned",
            "2024-03-01T15:00:00Z",
            "2024-03-01T16:00:00Z",
            "Mine",
            "steve@x",
            extendedProperties={"private": {MARKER_SOURCE: "main@x/owned", MARKER_KIND: KIND_COPY}},
        ),
    )
    store.put_link(
        Link(source="main@x/owned", kind=KIND_COPY, calendar_id="work@x", event_id="copy-owned")
    )


def _run(store, calendar, clock, notifier) -> None:
    process_requests(store, calendar, clock, notifier)


def _confirm(store: MemoryStore, req_id: int) -> None:
    ready = store.get_request(req_id)
    assert ready is not None
    store.update_request(dataclasses.replace(ready, state=REQ_CONFIRMED))


def test_rules_confirmed_refuses_writer_destination(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path, roles={"home@x": "writer"})
    _seed_copies(store, state)
    req_id = store.add_request(REQ_RULES, {"rules": RULES})
    _confirm(store, req_id)
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    future = store.get_link("main@x/fut")
    assert req is not None
    assert req.state == REQ_FAILED
    assert req.result == {"error": MOVED}
    assert "move" not in [write["method"] for write in state.writes]
    assert future is not None and future.calendar_id == "work@x"
    assert store.list_rules() == []
    assert notifier.messages == []
    assert _updates_are_none(state)


def test_rules_pending_refuses_writer_destination(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path, roles={"home@x": "writer"})
    _seed_copies(store, state)
    req_id = store.add_request(REQ_RULES, {"rules": RULES})
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_FAILED
    assert req.result == {"error": MOVED}
    assert state.writes == []
    assert store.list_rules() == []
    assert _updates_are_none(state)


def test_rules_confirmed_refuses_missing_destination(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path, ("main@x", "work@x"))
    _seed_copies(store, state)
    req_id = store.add_request(REQ_RULES, {"rules": RULES})
    _confirm(store, req_id)
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_FAILED
    assert req.result == {"error": MOVED}
    assert store.list_rules() == []
    assert _updates_are_none(state)


def test_rules_confirmed_refuses_writer_copy_calendar(tmp_path):
    store, state, calendar, clock, notifier = _world(
        tmp_path, roles={"work@x": "writer", "home@x": "owner"}
    )
    _seed_copies(store, state)
    req_id = store.add_request(REQ_RULES, {"rules": RULES})
    _confirm(store, req_id)
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_FAILED
    assert req.result == {"error": "calendar not owned: work@x; nothing was moved"}
    assert "move" not in [write["method"] for write in state.writes]
    assert store.list_rules() == []
    assert _updates_are_none(state)


def test_rules_confirmed_refuses_writer_source_without_reading(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path, roles={"main@x": "writer"})
    _seed_copies(store, state)
    watched = RecordingCalendar(calendar)
    req_id = store.add_request(REQ_RULES, {"rules": RULES})
    _confirm(store, req_id)
    _run(store, watched, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_FAILED
    assert req.result == {"error": "calendar not owned: main@x; nothing was moved"}
    assert watched.get_events == 0
    assert store.list_rules() == []
    assert _updates_are_none(state)


def test_rules_confirmed_moves_when_all_owned(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path)
    _seed_copies(store, state)
    req_id = store.add_request(REQ_RULES, {"rules": RULES})
    _confirm(store, req_id)
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_DONE
    assert req.result == {"moves": 1}
    assert store.list_rules() == PROPOSED
    assert _updates_are_none(state)


def test_archive_pending_refuses_writer_mirror(tmp_path):
    store, state, calendar, clock, notifier = _world(
        tmp_path, ("main@x", "work@x"), roles={"work@x": "writer"}
    )
    _seed_archive(store, state)
    req_id = store.add_request(REQ_ARCHIVE, {"cutoff": CUTOFF, "destination": str(tmp_path)})
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_FAILED
    assert req.result == {"error": DELETED}
    assert list(tmp_path.glob("*.zip")) == []
    assert state.writes == []
    assert [message for _title, message, _click in notifier.messages] == [
        "cal-mirror failure: archive"
    ]
    assert _updates_are_none(state)


def test_archive_confirmed_refuses_mirror_after_ownership_drops(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path, ("main@x", "work@x"))
    _seed_archive(store, state)
    req_id = store.add_request(REQ_ARCHIVE, {"cutoff": CUTOFF, "destination": str(tmp_path)})
    _run(store, calendar, clock, notifier)
    ready = store.get_request(req_id)
    assert ready is not None
    assert ready.state == REQ_READY
    assert ready.result["check"] == "ok"
    state.calendars["work@x"]["accessRole"] = "writer"
    _confirm(store, req_id)
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    link = store.get_link("main@x/owned")
    deletes = [write for write in state.writes if write["method"] == "delete"]
    assert req is not None
    assert req.state == REQ_FAILED
    assert req.result == {"error": DELETED}
    assert deletes == []
    assert link is not None
    assert _updates_are_none(state)
