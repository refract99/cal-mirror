from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar
from cal_mirror.model import REQ_ARCHIVE, REQ_READY, Request, Settings
from cal_mirror.ports import FixedClock
from cal_mirror.requests_runner import process_requests
from cal_mirror.store_memory import MemoryStore
from cal_mirror.web.deps import current_user
from cal_mirror.web.page_archive import router

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
CUTOFF = "2024-07-01"


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send(self, title: str, message: str, click: str = "") -> None:
        self.messages.append((title, message, click))


class StubStore:
    def __init__(self, requests):
        self.requests = list(requests)
        self.settings = Settings(archive_destinations=("/mnt/usb",))

    def get_settings(self):
        return self.settings

    def get_request(self, request_id):
        for req in self.requests:
            if req.id == request_id:
                return req
        return None

    def list_requests(self, state=None):
        return [req for req in self.requests if state is None or req.state == state]


def _world(tmp_path):
    store = MemoryStore()
    store.save_settings(
        Settings(
            main_calendar="main@x",
            owner_email="steve@x",
            archive_destinations=(str(tmp_path),),
        )
    )
    state = FakeCalendar()
    state.add_calendar("main@x")
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


def _ready(tmp_path, item: dict):
    store, state, calendar, clock, notifier = _world(tmp_path)
    state.add_event("main@x", item)
    req_id = store.add_request(REQ_ARCHIVE, {"cutoff": CUTOFF, "destination": str(tmp_path)})
    process_requests(store, calendar, clock, notifier)
    req = store.get_request(req_id)
    assert req is not None
    assert req.state == REQ_READY
    return req


def _page(result):
    store = StubStore(
        [
            Request(
                id=1,
                kind=REQ_ARCHIVE,
                state=REQ_READY,
                params={"cutoff": "2026-01-01", "destination": "/mnt/usb"},
                result=result,
                created=NOW,
            )
        ]
    )
    app = FastAPI()
    app.state.store = store
    app.state.clock = FixedClock(NOW)
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: "steve"
    return TestClient(app).get("/archive/1").text


def test_ready_reports_one_unbounded_series(tmp_path):
    req = _ready(
        tmp_path,
        _timed(
            "s1",
            "1990-01-01T15:00:00Z",
            "1990-01-01T16:00:00Z",
            recurrence=["RRULE:FREQ=DAILY;COUNT=5001"],
        ),
    )
    assert req.result["kept_unbounded"] == 1
    assert req.result["deletes"] == []


def test_ready_reports_zero_for_one_owned_single(tmp_path):
    req = _ready(
        tmp_path,
        _timed("owned", "2024-03-01T15:00:00Z", "2024-03-01T16:00:00Z"),
    )
    assert req.result["kept_unbounded"] == 0


def test_ready_page_shows_kept_line():
    text = _page({"check": "ok", "kept_unbounded": 2})
    assert "Kept, too large to check: 2 recurring series." in text


def test_ready_page_omits_kept_line_for_zero():
    text = _page({"check": "ok", "kept_unbounded": 0})
    assert "kept-unbounded" not in text


def test_ready_page_omits_kept_line_when_missing():
    text = _page({"check": "ok"})
    assert "kept-unbounded" not in text


def test_ready_page_omits_kept_line_for_bool():
    text = _page({"check": "ok", "kept_unbounded": True})
    assert "kept-unbounded" not in text
