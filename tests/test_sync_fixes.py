from dataclasses import replace
from datetime import UTC, datetime, time

from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar
from cal_mirror.model import KIND_BLOCKED, KIND_COPY, MARKER_KIND, Settings
from cal_mirror.ports import CalendarError, FixedClock, StoreError
from cal_mirror.store_memory import MemoryStore
from cal_mirror.sync import RunResult, mirror_event_id, run_once

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
START = "2026-11-03T15:00:00Z"
END = "2026-11-03T16:00:00Z"
CLICK = "https://cal.example/status"
NO_LINK = "Tue 2026-11-03 9:00 AM CST meeting: no call link"


def _hours() -> dict[int, tuple[time, time]]:
    return {day: (time(8, 0), time(17, 0)) for day in range(5)}


def _settings(**kwargs) -> Settings:
    base = Settings(
        main_calendar="main@x",
        secondary_calendars=("work@x", "home@x"),
        default_calendar="home@x",
        owner_email="steve@x",
        time_zone="America/Chicago",
        allowed_hours=_hours(),
        web_url="https://cal.example",
    )
    return replace(base, **kwargs)


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, title: str, message: str, click: str = "") -> None:
        self.sent.append((title, message, click))


class FirstLinkFails(MemoryStore):
    """First put_link raises; later calls use MemoryStore."""

    def __init__(self) -> None:
        super().__init__()
        self._broken = True

    def put_link(self, link) -> None:
        if self._broken:
            self._broken = False
            raise StoreError("link")
        super().put_link(link)


class RecordingCalendar:
    """Delegates to GoogleCalendar and records list_changes calendar ids."""

    def __init__(self, inner: GoogleCalendar) -> None:
        self._inner = inner
        self.changes: list[str] = []

    def list_calendars(self):
        return self._inner.list_calendars()

    def list_changes(self, calendar_id, sync_token, time_min=None):
        self.changes.append(calendar_id)
        return self._inner.list_changes(calendar_id, sync_token, time_min=time_min)

    def list_events(self, calendar_id, time_min=None, time_max=None):
        return self._inner.list_events(calendar_id, time_min, time_max)

    def get_event(self, calendar_id, event_id):
        return self._inner.get_event(calendar_id, event_id)

    def apply(self, write):
        return self._inner.apply(write)


class WorkListFailsOnce:
    """list_changes for work@x raises CalendarError on the first call only."""

    def __init__(self, inner: GoogleCalendar) -> None:
        self._inner = inner
        self._used = False

    def list_calendars(self):
        return self._inner.list_calendars()

    def list_changes(self, calendar_id, sync_token, time_min=None):
        if calendar_id == "work@x" and not self._used:
            self._used = True
            raise CalendarError("unavailable")
        return self._inner.list_changes(calendar_id, sync_token, time_min=time_min)

    def list_events(self, calendar_id, time_min=None, time_max=None):
        return self._inner.list_events(calendar_id, time_min, time_max)

    def get_event(self, calendar_id, event_id):
        return self._inner.get_event(calendar_id, event_id)

    def apply(self, write):
        return self._inner.apply(write)


def _world(*, store: MemoryStore | None = None, **kwargs):
    store = MemoryStore() if store is None else store
    state = FakeCalendar()
    state.add_calendar("main@x", primary=True)
    state.add_calendar("work@x")
    state.add_calendar("home@x")
    store.save_settings(_settings(**kwargs))
    store.set_meta("first_start_done", "1")
    clock = FixedClock(NOW)
    notifier = RecordingNotifier()
    calendar = GoogleCalendar(FakeService(state))
    return store, state, clock, notifier, calendar


def _timed(summary: str, **extra) -> dict:
    item = {"summary": summary, "start": {"dateTime": START}, "end": {"dateTime": END}}
    item.update(extra)
    return item


def _live(state: FakeCalendar, calendar_id: str, kind: str) -> list[dict]:
    found = []
    for item in state.events[calendar_id].values():
        private = (item.get("extendedProperties") or {}).get("private") or {}
        if private.get(MARKER_KIND) == kind and item.get("status") != "cancelled":
            found.append(item)
    return found


def _criterion_5(state: FakeCalendar) -> None:
    for entry in state.writes:
        assert entry["sendUpdates"] == "none"
        assert entry["body"]["attendees"] == []


def test_mirror_event_id_blocked() -> None:
    found = mirror_event_id("work@x/evt1", KIND_BLOCKED)
    assert found == "m76pb0an48sj5djeohbdo758qr4ul81ub1vdsdvcph6qg5g3qj0g"


def test_mirror_event_id_copy() -> None:
    found = mirror_event_id("main@x/evt1", KIND_COPY)
    assert found == "ha3duggsrm015i21569d51he07o67hcn2s39jt8fpsgo0c2mhue0"


def test_mirror_event_id_kind_matters() -> None:
    found = mirror_event_id("work@x/evt1", KIND_COPY)
    assert found == "qmi4iev7of8f5eqmqttvuorn1ja6jlndn5vhut0suc3n4vo05qh0"


def test_lost_link_retries_the_same_mirror() -> None:
    store, state, clock, note, calendar = _world(store=FirstLinkFails())
    src = state.add_event("work@x", _timed("RetryBusyTitle"))
    first = run_once(store, calendar, note, clock)
    assert first == RunResult(writes=1, failure="database")
    second = run_once(store, calendar, note, clock)
    assert second.failure == ""
    live = _live(state, "main@x", KIND_BLOCKED)
    assert len(live) == 1
    source = f"work@x/{src['id']}"
    assert live[0]["id"] == mirror_event_id(source, KIND_BLOCKED)
    assert [entry["method"] for entry in state.writes] == ["insert", "patch"]
    assert len(store.list_links()) == 1
    _criterion_5(state)


def test_reader_calendar_is_unread_until_owned() -> None:
    store, state, clock, note, inner = _world()
    state.calendars["work@x"]["accessRole"] = "reader"
    state.add_event("work@x", _timed("ReaderBusyTitle"))
    calendar = RecordingCalendar(inner)
    first = run_once(store, calendar, note, clock)
    assert state.writes == []
    assert "work@x" not in calendar.changes
    assert first == RunResult(failure="google_api")
    assert store.get_status().failure == "calendar not owned: work@x"
    second = run_once(store, calendar, note, clock)
    assert second.failure == "google_api"
    assert note.sent == [("cal-mirror", "cal-mirror failure: google_api", CLICK)]
    state.calendars["work@x"]["accessRole"] = "owner"
    third = run_once(store, calendar, note, clock)
    assert third.failure == ""
    assert len(_live(state, "main@x", KIND_BLOCKED)) == 1
    assert note.sent[-1] == ("cal-mirror", "cal-mirror recovered: google_api", CLICK)
    assert store.get_status().failure == ""
    _criterion_5(state)


def test_unowned_main_skips_the_mirror_and_source_token() -> None:
    store, state, clock, note, inner = _world()
    state.calendars["main@x"]["accessRole"] = "writer"
    state.add_event("work@x", _timed("MainWriterBusyTitle"))
    calendar = RecordingCalendar(inner)
    run_once(store, calendar, note, clock)
    assert state.writes == []
    assert "main@x" not in calendar.changes
    assert store.get_status().failure == "calendar not owned: main@x"
    assert store.get_sync_token("work@x") is None
    _criterion_5(state)


def test_unowned_default_skips_the_copy_until_owned() -> None:
    store, state, clock, note, calendar = _world()
    state.calendars["home@x"]["accessRole"] = "reader"
    state.add_event(
        "main@x",
        _timed(
            "DefaultHoldTitle",
            hangoutLink="https://meet.google.com/abc-defg-hij",
            attendees=[{"email": "ann@acme.com"}],
        ),
    )
    run_once(store, calendar, note, clock)
    assert state.writes == []
    assert store.list_links() == []
    assert store.list_placements() == []
    assert store.get_sync_token("main@x") is None
    state.calendars["home@x"]["accessRole"] = "owner"
    run_once(store, calendar, note, clock)
    links = store.list_links()
    assert len(links) == 1
    assert links[0].kind == KIND_COPY
    assert links[0].calendar_id == "home@x"
    assert len(store.list_placements()) == 1
    _criterion_5(state)


def test_main_notice_is_sent_when_another_calendar_fails() -> None:
    store, state, clock, note, inner = _world()
    assert run_once(store, inner, note, clock) == RunResult()
    state.add_event("main@x", _timed("NoLinkTitle", attendees=[{"email": "ann@acme.com"}]))
    calendar = WorkListFailsOnce(inner)
    result = run_once(store, calendar, note, clock)
    assert result == RunResult(writes=1, notices=1, failure="google_api")
    assert note.sent[-1][1] == NO_LINK
    run_once(store, calendar, note, clock)
    run_once(store, calendar, note, clock)
    texts = [message for _title, message, _click in note.sent]
    assert texts.count(NO_LINK) == 1
    _criterion_5(state)


def test_cleared_conference_is_removed_from_the_copy() -> None:
    store, state, clock, note, calendar = _world()
    src = state.add_event(
        "main@x",
        _timed(
            "ConfTitle",
            attendees=[{"email": "ann@acme.com"}],
            conferenceData={
                "entryPoints": [
                    {
                        "entryPointType": "video",
                        "uri": "https://meet.google.com/abc-defg-hij",
                    }
                ]
            },
        ),
    )
    assert run_once(store, calendar, note, clock).failure == ""
    link = store.get_link(f"main@x/{src['id']}")
    assert link is not None
    state.edit_event("main@x", src["id"], conferenceData={})
    assert run_once(store, calendar, note, clock).failure == ""
    copied = state.events[link.calendar_id][link.event_id]
    assert not copied.get("conferenceData")
    assert "Meeting link:" not in copied.get("description", "")
    _criterion_5(state)
