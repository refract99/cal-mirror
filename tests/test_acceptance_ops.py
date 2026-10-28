import zipfile
from datetime import UTC, datetime, time, timedelta

from fastapi.testclient import TestClient

from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar
from cal_mirror.model import (
    REQ_CANCELLED,
    REQ_DONE,
    REQ_FAILED,
    REQ_READY,
    REQ_RULES,
    RULE_TITLE_WORD,
    Rule,
    Settings,
)
from cal_mirror.ports import CalendarAuthError, CalendarError, FixedClock
from cal_mirror.requests_runner import process_requests
from cal_mirror.store_memory import MemoryStore
from cal_mirror.sync import run_once
from cal_mirror.web.app import create_app
from cal_mirror.web.deps import SESSION_COOKIE, session_hash

TOKEN = "fixture-session-1"
NO_LINK = "Tue 2026-11-03 7:30 AM CST meeting: no call link"
OUTSIDE = "Tue 2026-11-03 7:30 AM CST meeting: outside hours"
FIRST_START = "cal-mirror first start: no future meetings fail checks"
GUEST = [
    {"email": "steve@x", "self": True, "responseStatus": "accepted"},
    {"email": "ann@acme.com", "responseStatus": "needsAction"},
]


class Note:
    sent: list[tuple[str, str, str]]

    def __init__(self) -> None:
        self.sent = []

    def send(self, title: str, message: str, click: str = "") -> None:
        self.sent.append((title, message, click))


class ListingGate:
    """Calendar port that raises from list_changes while fail is set."""

    def __init__(self, cal: GoogleCalendar) -> None:
        self._cal = cal
        self.fail: CalendarError | None = None

    def list_changes(self, calendar_id: str, sync_token: str | None, time_min=None):
        if self.fail is not None:
            raise self.fail
        return self._cal.list_changes(calendar_id, sync_token, time_min)

    def __getattr__(self, name: str):
        return getattr(self._cal, name)


def _texts(note: Note) -> list[str]:
    return [message for _title, message, _click in note.sent]


def _assert_send_updates(state: FakeCalendar) -> None:
    for entry in state.writes:
        assert entry["sendUpdates"] == "none"


def _zip_names(path) -> set[str]:
    with zipfile.ZipFile(path) as archive:
        return set(archive.namelist())


def _world(tmp_path=None):
    clock = FixedClock(datetime(2026, 10, 24, 12, 0, tzinfo=UTC))
    state = FakeCalendar()
    for calendar_id in ("main@x", "work@x", "home@x", "other@x"):
        state.add_calendar(calendar_id)
    cal = GoogleCalendar(FakeService(state))
    store = MemoryStore()
    hours = {day: (time(8), time(17)) for day in range(5)}
    destinations = () if tmp_path is None else (str(tmp_path),)
    store.save_settings(
        Settings(
            owner_email="steve@x",
            main_calendar="main@x",
            secondary_calendars=("work@x", "home@x"),
            default_calendar="home@x",
            web_url="https://cal.example",
            allowed_hours=hours,
            archive_destinations=destinations,
        )
    )
    return store, state, cal, clock, Note()


def _client(store, clock) -> TestClient:
    app = create_app(store, clock)
    client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    store.create_session(session_hash(TOKEN), clock.now() + timedelta(days=30))
    client.cookies.set(SESSION_COOKIE, TOKEN)
    return client


def _timed(event_id: str, day: str, summary: str, **extra) -> dict:
    item = {
        "id": event_id,
        "summary": summary,
        "status": "confirmed",
        "start": {"dateTime": f"{day}T15:00:00Z"},
        "end": {"dateTime": f"{day}T16:00:00Z"},
        "organizer": {"email": "steve@x"},
    }
    item.update(extra)
    return item


def _deletes(state: FakeCalendar) -> list[str]:
    return [entry["eventId"] for entry in state.writes if entry["method"] == "delete"]


def _seed_years(state: FakeCalendar) -> None:
    state.add_event("main@x", _timed("m24", "2024-03-01", "Old main"))
    state.add_event("main@x", _timed("m25", "2025-03-01", "Newer main"))
    state.add_event("work@x", _timed("w24", "2024-05-01", "Old work"))


def _dry_archive(client, store, cal, clock, note, tmp_path) -> int:
    response = client.post(
        "/archive",
        data={"cutoff": "2026-01-01", "destination": str(tmp_path)},
    )
    assert response.status_code == 303
    req_id = int(response.headers["location"].rsplit("/", 1)[-1])
    process_requests(store, cal, clock, note)
    return req_id


def test_criterion_6_archive_one_zip_per_year(tmp_path):
    store, state, cal, clock, note = _world(tmp_path)
    client = _client(store, clock)
    _seed_years(state)
    req_id = _dry_archive(client, store, cal, clock, note, tmp_path)
    ready = store.get_request(req_id)
    assert ready is not None
    assert ready.state == REQ_READY
    assert ready.result["check"] == "ok"
    assert _zip_names(tmp_path / "cal-mirror-2024.zip") == {
        "main@x.ics",
        "main@x.json",
        "work@x.ics",
        "work@x.json",
    }
    assert _zip_names(tmp_path / "cal-mirror-2025.zip") == {"main@x.ics", "main@x.json"}
    assert _deletes(state) == []
    process_requests(store, cal, clock, note)
    assert _deletes(state) == []
    cancelled = client.post(f"/archive/{req_id}/cancel")
    assert cancelled.status_code == 303
    process_requests(store, cal, clock, note)
    finished = store.get_request(req_id)
    assert finished is not None
    assert finished.state == REQ_CANCELLED
    assert _deletes(state) == []
    _assert_send_updates(state)


def test_criterion_6_failed_check_deletes_nothing(tmp_path):
    store, state, cal, clock, note = _world(tmp_path)
    client = _client(store, clock)
    _seed_years(state)
    req_id = _dry_archive(client, store, cal, clock, note, tmp_path)
    (tmp_path / "cal-mirror-2024.zip").write_bytes(b"not a zip")
    confirmed = client.post(f"/archive/{req_id}/confirm")
    assert confirmed.status_code == 303
    process_requests(store, cal, clock, note)
    failed = store.get_request(req_id)
    assert failed is not None
    assert failed.state == REQ_FAILED
    assert _deletes(state) == []
    assert _texts(note) == ["cal-mirror failure: archive"]
    _assert_send_updates(state)


def test_criterion_7_cleanup_sweeps_old_events_and_keeps_running_series(tmp_path):
    store, state, cal, clock, note = _world(tmp_path)
    client = _client(store, clock)
    state.add_event("main@x", _timed("single", "2024-03-01", "Single"))
    state.add_event(
        "main@x",
        _timed(
            "invited",
            "2024-03-02",
            "Invited",
            organizer={"email": "boss@corp.com"},
            attendees=[{"email": "steve@x", "self": True, "responseStatus": "accepted"}],
        ),
    )
    state.add_event(
        "main@x",
        _timed("running", "2024-01-01", "Running", recurrence=["RRULE:FREQ=WEEKLY"]),
    )
    state.add_event(
        "main@x",
        _timed(
            "ended",
            "2024-01-01",
            "Ended",
            recurrence=["RRULE:FREQ=WEEKLY;UNTIL=20240301T000000Z"],
        ),
    )
    response = client.post(
        "/archive",
        data={"cutoff": "2024-07-01", "destination": str(tmp_path)},
    )
    assert response.status_code == 303
    req_id = int(response.headers["location"].rsplit("/", 1)[-1])
    process_requests(store, cal, clock, note)
    ready = store.get_request(req_id)
    assert ready is not None and ready.state == REQ_READY
    counts = ready.result["counts"]
    confirmed = client.post(f"/archive/{req_id}/confirm")
    assert confirmed.status_code == 303
    process_requests(store, cal, clock, note)
    done = store.get_request(req_id)
    invited = cal.get_event("main@x", "invited")
    running = cal.get_event("main@x", "running")
    assert done is not None
    assert done.state == REQ_DONE
    deleted = sorted(_deletes(state))
    assert [d for d in deleted if "_" not in d] == ["ended", "invited", "single"]
    assert all(d.startswith("running_") for d in deleted if "_" in d)
    assert len(_deletes(state)) == sum(counts.values())
    assert invited is not None and invited.status == "cancelled"
    assert running is not None and running.status != "cancelled"
    _assert_send_updates(state)


def test_criterion_10_one_alert_each_way():
    store, state, cal, clock, note = _world()
    store.set_meta("first_start_done", "1")
    gate = ListingGate(cal)
    gate.fail = CalendarAuthError("rejected")
    run_once(store, gate, note, clock)
    clock.advance(300)
    run_once(store, gate, note, clock)
    clock.advance(300)
    run_once(store, gate, note, clock)
    assert _texts(note) == ["cal-mirror failure: token"]
    store.set_secret("google_token", "fixture-token-1")
    page = _client(store, clock).get("/google")
    assert page.status_code == 200
    assert 'id="google-state">Reconnect<' in page.text
    gate.fail = None
    clock.advance(300)
    run_once(store, gate, note, clock)
    assert _texts(note) == ["cal-mirror failure: token", "cal-mirror recovered: token"]
    clock.advance(300)
    run_once(store, gate, note, clock)
    assert _texts(note) == ["cal-mirror failure: token", "cal-mirror recovered: token"]
    gate.fail = CalendarError("unavailable")
    clock.advance(300)
    run_once(store, gate, note, clock)
    clock.advance(300)
    run_once(store, gate, note, clock)
    clock.advance(300)
    run_once(store, gate, note, clock)
    assert _texts(note) == [
        "cal-mirror failure: token",
        "cal-mirror recovered: token",
        "cal-mirror failure: google_api",
    ]
    gate.fail = None
    clock.advance(300)
    run_once(store, gate, note, clock)
    assert _texts(note)[-1] == "cal-mirror recovered: google_api"
    assert _texts(note).count("cal-mirror failure: google_api") == 1
    assert _texts(note).count("cal-mirror recovered: google_api") == 1
    _assert_send_updates(state)


def _rules_post(client) -> None:
    response = client.post(
        "/rules",
        data={
            "count": "1",
            "kind_0": "title_word",
            "value_0": "Review",
            "calendar_0": "work@x",
            "position_0": "1",
        },
    )
    assert response.status_code == 303


def _latest_rules(store) -> int:
    return max(req.id for req in store.list_requests() if req.kind == REQ_RULES)


def test_criterion_11_rule_change_moves_future_copies():
    store, state, cal, clock, note = _world()
    client = _client(store, clock)
    review_a = state.add_event("main@x", _timed("a", "2026-10-26", "Review A", attendees=GUEST))
    review_b = state.add_event("main@x", _timed("b", "2026-11-03", "Review B", attendees=GUEST))
    run_once(store, cal, note, clock)
    link_a = store.get_link(f"main@x/{review_a['id']}")
    link_b = store.get_link(f"main@x/{review_b['id']}")
    assert link_a is not None and link_a.calendar_id == "home@x"
    assert link_b is not None and link_b.calendar_id == "home@x"
    copy_b = link_b.event_id
    clock.advance(4 * 86400)
    _rules_post(client)
    process_requests(store, cal, clock, note)
    shown = client.get("/rules")
    assert shown.status_code == 200
    assert "1 future copy will move" in shown.text
    assert [entry["method"] for entry in state.writes if entry["method"] == "move"] == []
    assert store.list_rules() == []
    req_id = _latest_rules(store)
    cancelled = client.post(f"/rules/requests/{req_id}/cancel")
    assert cancelled.status_code == 303
    process_requests(store, cal, clock, note)
    assert [entry["method"] for entry in state.writes if entry["method"] == "move"] == []
    assert store.list_rules() == []
    _rules_post(client)
    process_requests(store, cal, clock, note)
    req_id = _latest_rules(store)
    confirmed = client.post(f"/rules/requests/{req_id}/confirm")
    assert confirmed.status_code == 303
    process_requests(store, cal, clock, note)
    moves = [entry for entry in state.writes if entry["method"] == "move"]
    stayed = store.get_link(f"main@x/{review_a['id']}")
    moved = store.get_link(f"main@x/{review_b['id']}")
    assert len(moves) == 1
    assert moves[0]["calendarId"] == "home@x"
    assert moves[0]["destination"] == "work@x"
    assert moves[0]["eventId"] == copy_b
    assert stayed is not None and stayed.calendar_id == "home@x"
    assert state.events["home@x"][stayed.event_id]["status"] != "cancelled"
    assert moved is not None and moved.calendar_id == "work@x"
    assert store.list_rules() == [Rule(1, RULE_TITLE_WORD, "Review", "work@x")]
    inserts = [entry for entry in state.writes if entry["method"] == "insert"]
    clock.advance(300)
    run_once(store, cal, note, clock)
    assert [entry for entry in state.writes if entry["method"] == "insert"] == inserts
    assert [entry for entry in state.writes if entry["method"] == "move"] == moves
    _assert_send_updates(state)


def test_criterion_12_meeting_checks():
    store, state, cal, clock, note = _world()
    run_once(store, cal, note, clock)
    assert _texts(note) == [FIRST_START]
    state.add_event(
        "main@x",
        {
            "summary": "Secret plan",
            "start": {"dateTime": "2026-11-03T13:30:00Z"},
            "end": {"dateTime": "2026-11-03T14:00:00Z"},
            "attendees": GUEST,
        },
    )
    clock.advance(300)
    run_once(store, cal, note, clock)
    assert _texts(note) == [FIRST_START, NO_LINK, OUTSIDE]
    secret = next(
        item for item in state.events["main@x"].values() if item["summary"] == "Secret plan"
    )
    state.edit_event("main@x", secret["id"], description="Only the notes")
    clock.advance(300)
    run_once(store, cal, note, clock)
    assert _texts(note) == [FIRST_START, NO_LINK, OUTSIDE]
    settings = store.get_settings()
    store.save_settings(
        Settings(
            main_calendar=settings.main_calendar,
            secondary_calendars=settings.secondary_calendars,
            default_calendar=settings.default_calendar,
            owner_email=settings.owner_email,
            allowed_hours=settings.allowed_hours,
            web_url=settings.web_url,
            check_no_link=False,
            check_hours=False,
        )
    )
    state.add_event(
        "main@x",
        {
            "summary": "Secret plan",
            "start": {"dateTime": "2026-11-09T13:30:00Z"},
            "end": {"dateTime": "2026-11-09T14:00:00Z"},
            "attendees": GUEST,
        },
    )
    clock.advance(300)
    run_once(store, cal, note, clock)
    assert _texts(note) == [FIRST_START, NO_LINK, OUTSIDE]
    assert all("Secret plan" not in message for message in _texts(note))
    _assert_send_updates(state)
