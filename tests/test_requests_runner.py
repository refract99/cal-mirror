import dataclasses
from datetime import UTC, datetime

from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar
from cal_mirror.model import (
    KIND_COPY,
    MARKER_KIND,
    MARKER_SOURCE,
    REQ_ARCHIVE,
    REQ_CANCELLED,
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


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send(self, title: str, message: str, click: str = "") -> None:
        self.messages.append((title, message, click))


def _updates_are_none(state: FakeCalendar) -> bool:
    return all(write["sendUpdates"] == "none" for write in state.writes)


def _world(tmp_path, calendars: tuple[str, ...] = ("main@x", "work@x", "home@x")):
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


def _run(store, calendar, clock, notifier) -> None:
    process_requests(store, calendar, clock, notifier)


def test_rules_pending_counts_one_future_move(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path)
    _seed_copies(store, state)
    req_id = store.add_request(REQ_RULES, {"rules": RULES})
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_READY
    assert req.result == {"moves": 1}
    assert [write["method"] for write in state.writes] == []
    assert store.list_rules() == []
    assert _updates_are_none(state)


def test_rules_ready_processed_again_changes_nothing(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path)
    _seed_copies(store, state)
    req_id = store.add_request(REQ_RULES, {"rules": RULES})
    _run(store, calendar, clock, notifier)
    before = store.get_request(req_id)
    writes = list(state.writes)
    rules = store.list_rules()
    _run(store, calendar, clock, notifier)
    after = store.get_request(req_id)
    assert before is not None and after is not None
    assert after.state == before.state == REQ_READY
    assert after.result == before.result == {"moves": 1}
    assert state.writes == writes
    assert store.list_rules() == rules == []
    assert _updates_are_none(state)


def test_rules_cancelled_is_not_applied(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path)
    _seed_copies(store, state)
    req_id = store.add_request(REQ_RULES, {"rules": RULES})
    _run(store, calendar, clock, notifier)
    ready = store.get_request(req_id)
    assert ready is not None
    store.update_request(dataclasses.replace(ready, state=REQ_CANCELLED))
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_CANCELLED
    assert req.result == {"moves": 1}
    assert [write["method"] for write in state.writes] == []
    assert store.list_rules() == []
    assert _updates_are_none(state)


def test_rules_confirmed_moves_future_copy_only(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path)
    _seed_copies(store, state)
    req_id = store.add_request(REQ_RULES, {"rules": RULES})
    pending = store.get_request(req_id)
    assert pending is not None
    store.update_request(dataclasses.replace(pending, state=REQ_CONFIRMED))
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    moves = [write for write in state.writes if write["method"] == "move"]
    future = store.get_link("main@x/fut")
    past = store.get_link("main@x/past")
    assert req is not None
    assert req.state == REQ_DONE
    assert req.result == {"moves": 1}
    assert len(moves) == 1
    assert moves[0]["calendarId"] == "work@x"
    assert moves[0]["destination"] == "home@x"
    assert moves[0]["sendUpdates"] == "none"
    assert future is not None and future.calendar_id == "home@x"
    assert past is not None and past.calendar_id == "work@x"
    assert store.list_rules() == PROPOSED
    assert _updates_are_none(state)


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


def test_archive_pending_writes_zip_and_does_not_delete(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path, ("main@x",))
    _seed_archive(state)
    req_id = store.add_request(REQ_ARCHIVE, {"cutoff": "2024-07-01", "destination": str(tmp_path)})
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_READY
    assert req.result["check"] == "ok"
    assert req.result["counts"] == {"main@x/2024": 27}
    assert (tmp_path / "cal-mirror-2024.zip").is_file()
    assert [write["method"] for write in state.writes] == []
    assert notifier.messages == []
    assert _updates_are_none(state)


def test_archive_confirmed_deletes_only_the_owned_event(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path, ("main@x",))
    _seed_archive(state)
    req_id = store.add_request(REQ_ARCHIVE, {"cutoff": "2024-07-01", "destination": str(tmp_path)})
    _run(store, calendar, clock, notifier)
    ready = store.get_request(req_id)
    assert ready is not None
    store.update_request(dataclasses.replace(ready, state=REQ_CONFIRMED))
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    deletes = [write for write in state.writes if write["method"] == "delete"]
    invited = calendar.get_event("main@x", "invited")
    series = calendar.get_event("main@x", "weekly")
    assert req is not None
    assert req.state == REQ_DONE
    ids = {write["eventId"] for write in deletes}
    assert req.result["deleted"] == 27
    assert len(deletes) == 27
    assert "owned" in ids and "weekly" not in ids and "invited" not in ids
    assert all(i == "owned" or i.startswith("weekly_") for i in ids)
    assert all(write["calendarId"] == "main@x" for write in deletes)
    assert all(write["sendUpdates"] == "none" for write in deletes)
    assert invited is not None and invited.status == "confirmed"
    assert series is not None and series.status == "confirmed"
    assert _updates_are_none(state)


def test_archive_confirmed_missing_zip_alerts_once(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path, ("main@x",))
    _seed_archive(state)
    req_id = store.add_request(REQ_ARCHIVE, {"cutoff": "2024-07-01", "destination": str(tmp_path)})
    _run(store, calendar, clock, notifier)
    ready = store.get_request(req_id)
    assert ready is not None
    stored = dict(ready.result)
    (tmp_path / "cal-mirror-2024.zip").unlink()
    store.update_request(dataclasses.replace(ready, state=REQ_CONFIRMED))
    _run(store, calendar, clock, notifier)
    failed = store.get_request(req_id)
    second = store.add_request(REQ_ARCHIVE, {"cutoff": "2024-07-01", "destination": str(tmp_path)})
    second_req = store.get_request(second)
    assert second_req is not None
    store.update_request(dataclasses.replace(second_req, state=REQ_CONFIRMED, result=stored))
    _run(store, calendar, clock, notifier)
    again = store.get_request(second)
    assert failed is not None and again is not None
    assert failed.state == REQ_FAILED
    assert again.state == REQ_FAILED
    assert [write["method"] for write in state.writes] == []
    assert [message for _title, message, _click in notifier.messages] == [
        "cal-mirror failure: archive"
    ]
    assert _updates_are_none(state)


def test_archive_confirmed_fails_when_events_change(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path, ("main@x",))
    _seed_archive(state)
    req_id = store.add_request(REQ_ARCHIVE, {"cutoff": "2024-07-01", "destination": str(tmp_path)})
    _run(store, calendar, clock, notifier)
    ready = store.get_request(req_id)
    assert ready is not None
    state.add_event(
        "main@x",
        _timed("extra", "2024-02-01T15:00:00Z", "2024-02-01T16:00:00Z", "Extra", "steve@x"),
    )
    store.update_request(dataclasses.replace(ready, state=REQ_CONFIRMED))
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_FAILED
    assert req.result == {
        "error": "the events to delete changed since the dry run; nothing was deleted"
    }
    assert [write["method"] for write in state.writes] == []
    assert _updates_are_none(state)


def test_archive_unknown_destination_fails(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path, ("main@x",))
    req_id = store.add_request(
        REQ_ARCHIVE, {"cutoff": "2024-07-01", "destination": str(tmp_path / "other")}
    )
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_FAILED
    assert req.result == {"error": "unknown archive destination"}
    assert [write["method"] for write in state.writes] == []
    assert _updates_are_none(state)


def test_rules_without_rules_param_fails(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path)
    req_id = store.add_request(REQ_RULES, {})
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_FAILED
    assert req.result == {"error": "'rules'"}
    assert store.list_rules() == []
    assert [write["method"] for write in state.writes] == []
    assert _updates_are_none(state)
