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
    Link,
    Settings,
)
from cal_mirror.ports import FixedClock
from cal_mirror.requests_runner import process_requests
from cal_mirror.store_memory import MemoryStore

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
CUTOFF = "2024-07-01"


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send(self, title: str, message: str, click: str = "") -> None:
        self.messages.append((title, message, click))


def _world(tmp_path, calendars: tuple[str, ...] = ("main@x",)):
    store = MemoryStore()
    store.save_settings(
        Settings(
            main_calendar="main@x",
            secondary_calendars=("work@x",),
            default_calendar="work@x",
            owner_email="steve@x",
            archive_destinations=(str(tmp_path),),
        )
    )
    state = FakeCalendar()
    for calendar_id in calendars:
        state.add_calendar(calendar_id)
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


def _seed_archive(state: FakeCalendar) -> None:
    state.add_event(
        "main@x",
        _timed("owned", "2024-03-01T15:00:00Z", "2024-03-01T16:00:00Z", "Mine", "steve@x"),
    )
    state.add_event(
        "main@x",
        _timed(
            "invited",
            "2024-03-02T15:00:00Z",
            "2024-03-02T16:00:00Z",
            "Theirs",
            "boss@corp.com",
            creator={"email": "boss@corp.com"},
        ),
    )
    state.add_event(
        "main@x",
        _timed(
            "weekly",
            "2024-01-01T15:00:00Z",
            "2024-01-01T16:00:00Z",
            "Standup",
            "steve@x",
            recurrence=["RRULE:FREQ=WEEKLY"],
        ),
    )


def _archive(store: MemoryStore, tmp_path) -> int:
    return store.add_request(REQ_ARCHIVE, {"cutoff": CUTOFF, "destination": str(tmp_path)})


def _run(store, calendar, clock, notifier) -> None:
    process_requests(store, calendar, clock, notifier)


def _confirm(store: MemoryStore, req_id: int) -> None:
    ready = store.get_request(req_id)
    assert ready is not None
    store.update_request(dataclasses.replace(ready, state=REQ_CONFIRMED))


def _delete_writes(state: FakeCalendar) -> list[tuple[str, str]]:
    return [
        (write["calendarId"], write["eventId"])
        for write in state.writes
        if write["method"] == "delete"
    ]


def test_archive_pending_stores_the_owned_delete(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path)
    _seed_archive(state)
    req_id = _archive(store, tmp_path)
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_READY
    deletes = req.result["deletes"]
    assert deletes[0] == ["main@x", "owned"]
    assert all(d[1].startswith("weekly_") for d in deletes[1:]) and len(deletes) == 27
    assert req.result["counts"] == {"main@x/2024": 27}
    assert _delete_writes(state) == []
    assert all(write["sendUpdates"] == "none" for write in state.writes)


def test_archive_confirm_deletes_source_and_linked_copy(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path, ("main@x", "work@x"))
    _seed_archive(state)
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
    req_id = _archive(store, tmp_path)
    _run(store, calendar, clock, notifier)
    ready = store.get_request(req_id)
    assert ready is not None
    whole = [d for d in ready.result["deletes"] if not d[1].startswith("weekly_")]
    assert whole == [["main@x", "owned"], ["work@x", "copy-owned"]]
    _confirm(store, req_id)
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_DONE
    assert req.result["deleted"] == 28
    whole = [w for w in _delete_writes(state) if not w[1].startswith("weekly_")]
    assert whole == [("main@x", "owned"), ("work@x", "copy-owned")]
    assert all(write["sendUpdates"] == "none" for write in state.writes)


def test_archive_confirm_rejects_a_different_event_with_the_same_count(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path)
    _seed_archive(state)
    req_id = _archive(store, tmp_path)
    _run(store, calendar, clock, notifier)
    state.cancel_event("main@x", "owned")
    state.edit_event(
        "main@x",
        "weekly",
        recurrence=["RRULE:FREQ=WEEKLY;UNTIL=20240301T000000Z"],
    )
    _confirm(store, req_id)
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    weekly = calendar.get_event("main@x", "weekly")
    assert req is not None
    assert req.state == REQ_FAILED
    assert req.result == {
        "error": "the events to delete changed since the dry run; nothing was deleted"
    }
    assert _delete_writes(state) == []
    assert weekly is not None and weekly.status == "confirmed"
    assert [message for _title, message, _click in notifier.messages] == [
        "cal-mirror failure: archive"
    ]
    assert all(write["sendUpdates"] == "none" for write in state.writes)


def test_archive_confirm_rejects_a_stored_result_without_deletes(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path)
    _seed_archive(state)
    req_id = _archive(store, tmp_path)
    _run(store, calendar, clock, notifier)
    ready = store.get_request(req_id)
    assert ready is not None
    stored = dict(ready.result)
    del stored["deletes"]
    store.update_request(dataclasses.replace(ready, state=REQ_CONFIRMED, result=stored))
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_FAILED
    assert req.result == {
        "error": "the events to delete changed since the dry run; nothing was deleted"
    }
    assert _delete_writes(state) == []
    assert all(write["sendUpdates"] == "none" for write in state.writes)


def test_archive_confirm_unchanged_deletes_the_owned_event(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path)
    _seed_archive(state)
    req_id = _archive(store, tmp_path)
    _run(store, calendar, clock, notifier)
    _confirm(store, req_id)
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_DONE
    assert req.result["deleted"] == 27
    writes = _delete_writes(state)
    assert writes[0] == ("main@x", "owned")
    assert all(w[1].startswith("weekly_") for w in writes[1:])
    assert all(write["sendUpdates"] == "none" for write in state.writes)


def test_archive_series_exception_moved_past_cutoff_is_not_deleted(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path)
    state.add_event(
        "main@x",
        _timed(
            "ended",
            "2024-01-01T15:00:00Z",
            "2024-01-01T16:00:00Z",
            "Ended",
            "steve@x",
            recurrence=["RRULE:FREQ=WEEKLY;UNTIL=20240601T150000Z"],
        ),
    )
    state.add_exception(
        "main@x",
        "ended",
        "2024-05-27T15:00:00Z",
        start={"dateTime": "2024-08-05T15:00:00Z"},
        end={"dateTime": "2024-08-05T16:00:00Z"},
    )
    req_id = _archive(store, tmp_path)
    _run(store, calendar, clock, notifier)
    ready = store.get_request(req_id)
    assert ready is not None
    assert ready.state == REQ_READY
    assert len(ready.result["deletes"]) == 21
    assert all(d[1].startswith("ended_") for d in ready.result["deletes"])
    _confirm(store, req_id)
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    series = calendar.get_event("main@x", "ended")
    assert req is not None
    assert req.state == REQ_DONE
    assert req.result["deleted"] == 21
    assert all(w[1].startswith("ended_") for w in _delete_writes(state))
    assert series is not None and series.status == "confirmed"
    assert all(write["sendUpdates"] == "none" for write in state.writes)
