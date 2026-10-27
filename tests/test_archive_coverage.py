import dataclasses
from datetime import UTC, date, datetime

from cal_mirror.cleanup import plan_cleanup, verify_archive
from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar
from cal_mirror.model import (
    KIND_BLOCKED,
    KIND_COPY,
    MARKER_KIND,
    MARKER_SOURCE,
    REQ_ARCHIVE,
    REQ_CONFIRMED,
    REQ_DONE,
    REQ_FAILED,
    Event,
    Link,
    Settings,
)
from cal_mirror.ports import FixedClock
from cal_mirror.requests_runner import process_requests
from cal_mirror.store_memory import MemoryStore

CUTOFF = date(2024, 7, 1)
OWNER = "steve@x"
START = datetime(2024, 6, 1, 0, 0, tzinfo=UTC)
END = datetime(2024, 6, 1, 1, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
RUNNER_CUTOFF = "2024-07-01"


def _event(event_id: str, calendar_id: str) -> Event:
    return Event(
        calendar_id=calendar_id,
        id=event_id,
        organizer_email=OWNER,
        start=START,
        end=END,
    )


def _linked(source: str = "sec/e1") -> tuple:
    event = _event("e1", "sec")
    link = Link(source=source, kind=KIND_BLOCKED, calendar_id="main", event_id="m1")
    plan = plan_cleanup({"sec": [event]}, CUTOFF, OWNER, {"sec/e1": link})
    return plan


def test_source_alone_is_not_enough():
    assert not verify_archive(_linked(), {"sec/e1"})


def test_mirror_alone_is_not_enough():
    assert not verify_archive(_linked(), {"main/m1"})


def test_source_and_mirror_target_pass():
    assert verify_archive(_linked(), {"sec/e1", "main/m1"})


def test_extra_archived_keys_pass():
    assert verify_archive(_linked(), {"sec/e1", "main/m1", "x/y"})


def test_mirror_link_source_is_not_the_target():
    assert not verify_archive(_linked("main/other"), {"sec/e1", "main/other"})


def test_mirror_target_passes_when_link_source_differs():
    assert verify_archive(_linked("main/other"), {"sec/e1", "main/m1"})


def _same_id_plan():
    event = _event("x", "main")
    link = Link(source="main/x", kind=KIND_BLOCKED, calendar_id="sec", event_id="x")
    return plan_cleanup({"main": [event]}, CUTOFF, OWNER, {"main/x": link})


def test_same_event_id_on_another_calendar_needs_both():
    assert not verify_archive(_same_id_plan(), {"main/x"})


def test_same_event_id_on_another_calendar_passes():
    assert verify_archive(_same_id_plan(), {"main/x", "sec/x"})


def _one_source():
    return plan_cleanup({"sec": [_event("e1", "sec")]}, CUTOFF, OWNER, {})


def test_repeated_delete_pair_fails():
    plan = dataclasses.replace(_one_source(), deletes=_one_source().deletes * 2)
    assert not verify_archive(plan, {"sec/e1"})


def test_inflated_counts_fail():
    plan = dataclasses.replace(_one_source(), counts={"sec/2024": 2})
    assert not verify_archive(plan, {"sec/e1"})


def test_empty_counts_fail():
    plan = dataclasses.replace(_one_source(), counts={})
    assert not verify_archive(plan, {"sec/e1"})


def _two_sources():
    return plan_cleanup(
        {"sec": [_event("e1", "sec"), _event("e2", "sec")]},
        CUTOFF,
        OWNER,
        {},
    )


def test_two_singles_missing_one_fails():
    assert not verify_archive(_two_sources(), {"sec/e1"})


def test_two_singles_with_extra_pass():
    assert verify_archive(_two_sources(), {"sec/e1", "sec/e2", "extra"})


def test_empty_plan_verifies():
    plan = plan_cleanup({}, CUTOFF, OWNER, {})
    assert verify_archive(plan, set())
    assert verify_archive(plan, {"a/b"})


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send(self, title: str, message: str, click: str = "") -> None:
        self.messages.append((title, message, click))


def _world(tmp_path):
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
    state.add_calendar("main@x")
    state.add_calendar("work@x")
    clock = FixedClock(NOW)
    notifier = RecordingNotifier()
    return store, state, GoogleCalendar(FakeService(state)), clock, notifier


def _timed(event_id: str, start: str, end: str, **extra) -> dict:
    item = {
        "id": event_id,
        "summary": "Mine",
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "organizer": {"email": "steve@x"},
        "status": "confirmed",
    }
    item.update(extra)
    return item


def _seed(state: FakeCalendar, copy_start: str) -> None:
    state.add_event(
        "main@x",
        _timed("owned", "2024-03-01T15:00:00Z", "2024-03-01T16:00:00Z"),
    )
    state.add_event(
        "work@x",
        _timed(
            "copy-owned",
            copy_start,
            copy_start.replace("T15:", "T16:"),
            extendedProperties={"private": {MARKER_SOURCE: "main@x/owned", MARKER_KIND: KIND_COPY}},
        ),
    )


def _archive(store: MemoryStore, tmp_path) -> int:
    return store.add_request(REQ_ARCHIVE, {"cutoff": RUNNER_CUTOFF, "destination": str(tmp_path)})


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


def test_mirror_after_cutoff_fails_the_archive_check(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path)
    _seed(state, "2024-08-01T15:00:00Z")
    store.put_link(
        Link(source="main@x/owned", kind=KIND_COPY, calendar_id="work@x", event_id="copy-owned")
    )
    req_id = _archive(store, tmp_path)
    _run(store, calendar, clock, notifier)
    ready = store.get_request(req_id)
    assert ready is not None
    assert ready.result["check"] == "failed"
    assert ready.result["deletes"] == [["main@x", "owned"], ["work@x", "copy-owned"]]
    _confirm(store, req_id)
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_FAILED
    assert req.result == {"error": "archive check failed"}
    assert _delete_writes(state) == []


def test_mirror_before_cutoff_passes_and_both_delete(tmp_path):
    store, state, calendar, clock, notifier = _world(tmp_path)
    _seed(state, "2024-03-01T15:00:00Z")
    store.put_link(
        Link(source="main@x/owned", kind=KIND_COPY, calendar_id="work@x", event_id="copy-owned")
    )
    req_id = _archive(store, tmp_path)
    _run(store, calendar, clock, notifier)
    ready = store.get_request(req_id)
    assert ready is not None
    assert ready.result["check"] == "ok"
    _confirm(store, req_id)
    _run(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_DONE
    assert _delete_writes(state) == [("main@x", "owned"), ("work@x", "copy-owned")]
