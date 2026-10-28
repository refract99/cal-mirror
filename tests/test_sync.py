import json
from dataclasses import replace
from datetime import UTC, datetime, time
from urllib.parse import quote

from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar
from cal_mirror.model import (
    KIND_BLOCKED,
    KIND_COPY,
    MARKER_KIND,
    RULE_GUEST_DOMAIN,
    Rule,
    Settings,
)
from cal_mirror.ports import CalendarAuthError, CalendarError, FixedClock, NotifyError
from cal_mirror.store_memory import MemoryStore
from cal_mirror.sync import RunResult, run_once

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
START = "2026-11-03T15:00:00Z"
END = "2026-11-03T16:00:00Z"
LATER = "2026-11-03T17:00:00Z"


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


class ListingFailure:
    """Delegates to GoogleCalendar, except list_changes raises a fixed error."""

    def __init__(self, inner: GoogleCalendar, error: CalendarError) -> None:
        self._inner = inner
        self._error = error

    def list_calendars(self):
        return self._inner.list_calendars()

    def list_changes(self, calendar_id, sync_token, time_min=None):
        raise self._error

    def list_events(self, calendar_id, time_min=None, time_max=None):
        return self._inner.list_events(calendar_id, time_min, time_max)

    def get_event(self, calendar_id, event_id):
        return self._inner.get_event(calendar_id, event_id)

    def apply(self, write):
        return self._inner.apply(write)


class AuthListingFailure(ListingFailure):
    def __init__(self, inner: GoogleCalendar) -> None:
        super().__init__(inner, CalendarAuthError("rejected"))


class ApiListingFailure(ListingFailure):
    def __init__(self, inner: GoogleCalendar) -> None:
        super().__init__(inner, CalendarError("unavailable"))


def _world(*, first: bool = False, **kwargs):
    store = MemoryStore()
    state = FakeCalendar()
    state.add_calendar("main@x", primary=True)
    state.add_calendar("work@x")
    state.add_calendar("home@x")
    store.save_settings(_settings(**kwargs))
    if not first:
        store.set_meta("first_start_done", "1")
    clock = FixedClock(NOW)
    notifier = RecordingNotifier()
    calendar = GoogleCalendar(FakeService(state))
    return store, state, clock, notifier, calendar


def _timed(summary: str, start: str = START, end: str = END, **extra) -> dict:
    item = {"summary": summary, "start": {"dateTime": start}, "end": {"dateTime": end}}
    item.update(extra)
    return item


def _live(state: FakeCalendar, calendar_id: str, kind: str) -> list[dict]:
    found = []
    for item in state.events[calendar_id].values():
        private = (item.get("extendedProperties") or {}).get("private") or {}
        if private.get(MARKER_KIND) == kind and item.get("status") != "cancelled":
            found.append(item)
    return found


def _assert_safe(
    state: FakeCalendar, sent: list[tuple[str, str, str]], titles: tuple[str, ...]
) -> None:
    for entry in state.writes:
        assert entry["sendUpdates"] == "none"
        body = entry.get("body")
        if isinstance(body, dict):
            assert not body.get("attendees")
    for title, message, _click in sent:
        for secret in titles:
            assert secret not in title
            assert secret not in message


def test_blocked_create_edit_cancel():
    store, state, clock, note, calendar = _world()
    src = state.add_event("work@x", _timed("ZebraBusyTitle"))
    assert run_once(store, calendar, note, clock) == RunResult(writes=1, notices=0)
    links = store.list_links()
    assert len(links) == 1
    assert links[0].kind == KIND_BLOCKED
    assert links[0].calendar_id == "main@x"
    assert links[0].source == f"work@x/{src['id']}"
    assert links[0].start == datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
    mirror = state.events["main@x"][links[0].event_id]
    assert mirror["summary"] == "BLOCKED"
    assert mirror["visibility"] == "private"
    assert mirror["start"]["dateTime"] == START
    assert mirror["end"]["dateTime"] == END
    assert store.get_status().counts["blocked"] == 1
    assert store.get_status().counts["copies"] == 0
    assert store.get_status().last_good_sync == NOW
    state.edit_event("work@x", src["id"], end={"dateTime": LATER})
    assert run_once(store, calendar, note, clock) == RunResult(writes=1, notices=0)
    assert state.writes[-1]["method"] == "patch"
    assert state.writes[-1]["eventId"] == links[0].event_id
    assert state.writes[-1]["body"]["end"]["dateTime"] == LATER
    assert store.list_links()[0].event_id == links[0].event_id
    state.cancel_event("work@x", src["id"])
    assert run_once(store, calendar, note, clock) == RunResult(writes=1, notices=0)
    assert state.writes[-1]["method"] == "delete"
    assert state.writes[-1]["eventId"] == links[0].event_id
    assert store.list_links() == []
    assert state.events["main@x"][links[0].event_id]["status"] == "cancelled"
    _assert_safe(state, note.sent, ("ZebraBusyTitle",))


def test_copy_follows_guest_domain_rule():
    store, state, clock, note, calendar = _world()
    store.save_rules([Rule(id=7, kind=RULE_GUEST_DOMAIN, value="acme.com", calendar_id="work@x")])
    src = state.add_event(
        "main@x",
        _timed(
            "AcmeReviewTitle",
            location="Room 4",
            description="Agenda",
            hangoutLink="https://meet.google.com/abc-defg-hij",
            organizer={"email": "boss@other.com"},
            attendees=[{"email": "ann@acme.com"}, {"email": "steve@x", "self": True}],
        ),
    )
    assert run_once(store, calendar, note, clock) == RunResult(writes=1, notices=0)
    link = store.get_link(f"main@x/{src['id']}")
    assert link is not None
    assert link.kind == KIND_COPY
    assert link.calendar_id == "work@x"
    assert link.rule_id == 7
    copied = state.events["work@x"][link.event_id]
    assert copied["summary"] == "AcmeReviewTitle"
    assert copied["location"] == "Room 4"
    assert copied["description"].startswith("Meeting link: ")
    assert "https://meet.google.com/abc-defg-hij" in copied["description"]
    assert store.list_placements() == []
    state.edit_event("main@x", src["id"], location="Room 9")
    assert run_once(store, calendar, note, clock) == RunResult(writes=1, notices=0)
    assert state.writes[-1]["method"] == "patch"
    assert state.writes[-1]["eventId"] == link.event_id
    assert state.events["work@x"][link.event_id]["location"] == "Room 9"
    assert store.list_links()[0].event_id == link.event_id
    state.cancel_event("main@x", src["id"])
    assert run_once(store, calendar, note, clock) == RunResult(writes=1, notices=0)
    assert state.writes[-1]["method"] == "delete"
    assert state.writes[-1]["eventId"] == link.event_id
    assert store.list_links() == []
    _assert_safe(state, note.sent, ("AcmeReviewTitle",))


def test_copy_default_records_one_placement():
    store, state, clock, note, calendar = _world()
    src = state.add_event(
        "main@x",
        _timed(
            "DefaultPlaceTitle",
            location="Kitchen",
            hangoutLink="https://meet.google.com/abc-defg-hij",
            organizer={"email": "boss@other.com"},
            attendees=[{"email": "ann@acme.com"}],
        ),
    )
    assert run_once(store, calendar, note, clock) == RunResult(writes=1, notices=0)
    link = store.get_link(f"main@x/{src['id']}")
    assert link is not None
    assert link.calendar_id == "home@x"
    assert link.kind == KIND_COPY
    assert link.rule_id is None
    copied = state.events["home@x"][link.event_id]
    assert copied["summary"] == "DefaultPlaceTitle"
    assert copied["description"].startswith("Meeting link: ")
    placements = store.list_placements()
    assert len(placements) == 1
    assert placements[0].source == f"main@x/{src['id']}"
    assert placements[0].calendar_id == "home@x"
    assert placements[0].title == "DefaultPlaceTitle"
    assert placements[0].organizer_email == "boss@other.com"
    assert placements[0].guest_domains == ("acme.com",)
    assert placements[0].at == NOW
    state.edit_event("main@x", src["id"], location="Den")
    run_once(store, calendar, note, clock)
    assert len(store.list_placements()) == 1
    state.cancel_event("main@x", src["id"])
    run_once(store, calendar, note, clock)
    assert store.list_links() == []
    assert len(store.list_placements()) == 1
    _assert_safe(state, note.sent, ("DefaultPlaceTitle",))


def test_ten_runs_do_not_loop():
    store, state, clock, note, calendar = _world()
    state.add_event("work@x", _timed("LoopBusyTitle"))
    state.add_event(
        "main@x",
        _timed(
            "LoopCopyTitle",
            hangoutLink="https://meet.google.com/abc-defg-hij",
            attendees=[{"email": "ann@acme.com"}],
        ),
    )
    assert run_once(store, calendar, note, clock) == RunResult(writes=2, notices=0)
    assert len(_live(state, "main@x", KIND_BLOCKED)) == 1
    assert len(_live(state, "home@x", KIND_COPY)) == 1
    base = len(state.writes)
    for index in range(10):
        assert run_once(store, calendar, note, clock) == RunResult()
        if index >= 2:
            assert len(state.writes) == base
    assert len(state.writes) == base
    assert len(_live(state, "main@x", KIND_BLOCKED)) == 1
    assert len(_live(state, "home@x", KIND_COPY)) == 1
    assert len(store.list_links()) == 2
    _assert_safe(state, note.sent, ("LoopBusyTitle", "LoopCopyTitle"))


def test_free_declined_unselected_and_busy_to_free():
    store, state, clock, note, calendar = _world()
    state.add_calendar("other@x")
    state.add_event("work@x", _timed("FreeTitle", transparency="transparent"))
    state.add_event(
        "work@x",
        _timed(
            "DeclinedTitle",
            attendees=[{"email": "steve@x", "self": True, "responseStatus": "declined"}],
        ),
    )
    other = state.add_event("other@x", _timed("OtherTitle"))
    assert run_once(store, calendar, note, clock) == RunResult()
    assert store.list_links() == []
    assert store.get_sync_token("other@x") is None
    assert store.get_link(f"other@x/{other['id']}") is None
    busy = state.add_event("work@x", _timed("BusyThenFreeTitle"))
    assert run_once(store, calendar, note, clock) == RunResult(writes=1, notices=0)
    assert len(store.list_links()) == 1
    assert len(_live(state, "main@x", KIND_BLOCKED)) == 1
    state.edit_event("work@x", busy["id"], transparency="transparent")
    assert run_once(store, calendar, note, clock) == RunResult(writes=1, notices=0)
    assert store.list_links() == []
    assert _live(state, "main@x", KIND_BLOCKED) == []
    assert state.writes[-1]["method"] == "delete"
    _assert_safe(
        state,
        note.sent,
        ("FreeTitle", "DeclinedTitle", "OtherTitle", "BusyThenFreeTitle"),
    )


def test_series_and_exception_one_insert():
    store, state, clock, note, calendar = _world()
    series = state.add_event(
        "work@x",
        _timed("SeriesTitle", recurrence=["RRULE:FREQ=WEEKLY"]),
    )
    state.add_exception("work@x", series["id"], "2026-11-10T15:00:00Z", summary="MovedTitle")
    assert run_once(store, calendar, note, clock) == RunResult(writes=2, notices=0)
    inserts = [entry for entry in state.writes if entry["method"] == "insert"]
    patches = [entry for entry in state.writes if entry["method"] == "patch"]
    assert len(inserts) == 1
    assert len(patches) == 1
    assert inserts[0]["calendarId"] == "main@x"
    series_link = store.get_link(f"work@x/{series['id']}")
    assert series_link is not None
    assert patches[0]["eventId"] == f"{series_link.event_id}_20261110T150000Z"
    assert patches[0]["calendarId"] == "main@x"
    assert len(_live(state, "main@x", KIND_BLOCKED)) == 2
    _assert_safe(state, note.sent, ("SeriesTitle", "MovedTitle"))


def test_first_start_summary_then_later_notices():
    store, state, clock, note, calendar = _world(first=True)
    past = state.add_event(
        "work@x",
        _timed("PastTitle", "2026-10-01T15:00:00Z", "2026-10-01T16:00:00Z"),
    )
    state.add_event(
        "main@x",
        _timed("FutureQuietTitle", attendees=[{"email": "pat@other.com"}]),
    )
    result = run_once(store, calendar, note, clock)
    assert result.failure == ""
    assert result.notices == 1
    assert store.get_link(f"work@x/{past['id']}") is None
    assert _live(state, "main@x", KIND_BLOCKED) == []
    assert store.get_meta("first_start_done") == "1"
    summary = "cal-mirror first start: 1 future meeting fails checks (1 no call link)"
    click = "https://cal.example/status"
    assert note.sent == [("cal-mirror", summary, click)]
    assert "FutureQuietTitle" not in note.sent[0][1]
    run_once(store, calendar, note, clock)
    assert note.sent == [("cal-mirror", summary, click)]
    added = state.add_event(
        "main@x",
        _timed(
            "SevenThirtyTitle",
            "2026-11-03T13:30:00Z",
            "2026-11-03T14:00:00Z",
            attendees=[{"email": "pat@other.com"}],
        ),
    )
    run_once(store, calendar, note, clock)
    source = f"main@x/{added['id']}"
    notice_click = f"https://cal.example/status?notice={quote(source, safe='')}"
    no_link = "Tue 2026-11-03 7:30 AM CST meeting: no call link"
    outside = "Tue 2026-11-03 7:30 AM CST meeting: outside hours"
    assert ("cal-mirror", no_link, notice_click) in note.sent
    assert ("cal-mirror", outside, notice_click) in note.sent
    assert note.sent[-2][1] == no_link
    assert note.sent[-1][1] == outside
    recent = json.loads(store.get_meta("recent_notices"))
    assert recent[0]["title"] == "SevenThirtyTitle"
    assert recent[0]["source"] == source
    assert "SevenThirtyTitle" not in no_link
    held = len(note.sent)
    run_once(store, calendar, note, clock)
    assert len(note.sent) == held
    state.edit_event("main@x", added["id"], description="Only the notes changed")
    run_once(store, calendar, note, clock)
    assert len(note.sent) == held
    store.save_settings(replace(store.get_settings(), check_no_link=False))
    state.add_event(
        "main@x",
        _timed("InHoursNoLinkTitle", attendees=[{"email": "pat@other.com"}]),
    )
    run_once(store, calendar, note, clock)
    assert len(note.sent) == held
    _assert_safe(
        state,
        note.sent,
        ("PastTitle", "FutureQuietTitle", "SevenThirtyTitle", "InHoursNoLinkTitle"),
    )


def test_expired_sync_token_does_not_duplicate():
    store, state, clock, note, calendar = _world()
    src = state.add_event("work@x", _timed("ExpireTitle"))
    assert run_once(store, calendar, note, clock) == RunResult(writes=1, notices=0)
    link = store.get_link(f"work@x/{src['id']}")
    assert link is not None
    state.expire_sync_tokens()
    assert run_once(store, calendar, note, clock).failure == ""
    assert store.get_link(f"work@x/{src['id']}").event_id == link.event_id
    inserts = [entry for entry in state.writes if entry["method"] == "insert"]
    assert len(inserts) == 1
    assert len(_live(state, "main@x", KIND_BLOCKED)) == 1
    _assert_safe(state, note.sent, ("ExpireTitle",))


def test_auth_failure_alerts_once_then_recovers():
    store, state, clock, note, _calendar = _world()
    failing = AuthListingFailure(GoogleCalendar(FakeService(state)))
    assert run_once(store, failing, note, clock) == RunResult(failure="token")
    assert run_once(store, failing, note, clock) == RunResult(failure="token")
    click = "https://cal.example/status"
    assert note.sent == [("cal-mirror", "cal-mirror failure: token", click)]
    status = store.get_status()
    assert status.token_ok is False
    assert status.failure == "token"
    assert status.last_good_sync is None
    assert store.get_sync_token("main@x") is None
    assert store.get_meta("first_start_done") == "1"
    good = GoogleCalendar(FakeService(state))
    assert run_once(store, good, note, clock) == RunResult()
    assert note.sent[-1] == ("cal-mirror", "cal-mirror recovered: token", click)
    assert len(note.sent) == 2
    assert store.get_status().token_ok is True
    assert store.get_status().failure == ""
    assert store.get_status().last_good_sync == NOW
    _assert_safe(state, note.sent, ())


def test_api_failure_alerts_on_the_second_run():
    store, state, clock, note, _calendar = _world()
    failing = ApiListingFailure(GoogleCalendar(FakeService(state)))
    assert run_once(store, failing, note, clock) == RunResult(failure="google_api")
    assert note.sent == []
    status = store.get_status()
    assert status.failure == "google_api"
    assert status.token_ok is True
    assert run_once(store, failing, note, clock) == RunResult(failure="google_api")
    click = "https://cal.example/status"
    assert note.sent == [("cal-mirror", "cal-mirror failure: google_api", click)]
    assert store.get_status().failure == "google_api"
    _assert_safe(state, note.sent, ())


def test_empty_main_changes_nothing():
    store, state, clock, note, calendar = _world(main_calendar="")
    state.add_event("work@x", _timed("IgnoredTitle"))
    before = store.get_status()
    assert run_once(store, calendar, note, clock) == RunResult()
    assert store.get_status() == before
    assert store.get_alert_state().active == ""
    assert note.sent == []
    assert store.get_sync_token("work@x") is None
    assert store.get_meta("first_start_done") == "1"


def test_notify_error_is_logged_and_the_run_continues():
    store, state, clock, _note, calendar = _world(first=True)

    class Boom:
        def send(self, title: str, message: str, click: str = "") -> None:
            raise NotifyError("down")

    assert run_once(store, calendar, Boom(), clock) == RunResult()
    assert store.get_meta("first_start_done") == "1"
    assert store.get_status().failure == ""
    assert store.get_status().last_good_sync == NOW
    _assert_safe(state, [], ())


def test_auth_error_names_the_reason() -> None:
    import json

    import httplib2
    from googleapiclient.errors import HttpError

    from cal_mirror.google_api import _translate

    body = {"error": {"message": "x", "errors": [{"reason": "accessNotConfigured"}]}}
    err = HttpError(httplib2.Response({"status": "403"}), json.dumps(body).encode())
    assert str(_translate(err, "calendarList.list")) == (
        "Google API calendarList.list failed: HTTP 403 (accessNotConfigured): x"
    )


def test_no_default_copies_only_events_a_rule_matches() -> None:
    from cal_mirror.model import RULE_ORGANIZER, Event, Rule
    from cal_mirror.rules import choose_calendar

    rules = [Rule(1, RULE_ORGANIZER, "boardy@boardy.ai", "blocksentience")]
    boardy = Event(calendar_id="main", id="e1", summary="x", organizer_email="boardy@boardy.ai")
    other = Event(calendar_id="main", id="e2", summary="y", organizer_email="a@b.c")
    assert choose_calendar(boardy, rules, "")[0] == "blocksentience"
    assert choose_calendar(other, rules, "")[0] == ""


def test_boardy_only_setup_copies_only_boardy_events_even_when_rule_comes_later():
    from cal_mirror.model import RULE_ORGANIZER

    store, state, clock, note, calendar = _world(default_calendar="")
    boardy = state.add_event(
        "main@x", _timed("Boardy intro", organizer={"email": "boardy@boardy.ai"})
    )
    other = state.add_event("main@x", _timed("Other", organizer={"email": "a@b.c"}))
    run_once(store, calendar, note, clock)
    assert store.list_links() == []
    store.save_rules(
        [Rule(id=1, kind=RULE_ORGANIZER, value="boardy@boardy.ai", calendar_id="home@x")]
    )
    store.set_sync_token("main@x", None)
    store.set_sync_token("work@x", None)
    store.set_sync_token("home@x", None)
    run_once(store, calendar, note, clock)
    assert store.get_link(f"main@x/{boardy['id']}").calendar_id == "home@x"
    assert store.get_link(f"main@x/{other['id']}") is None


def test_status_records_why_each_main_event_was_or_was_not_copied():
    import json

    from cal_mirror.model import RULE_ORGANIZER

    store, state, clock, note, calendar = _world(default_calendar="")
    store.save_rules([Rule(1, RULE_ORGANIZER, "boardy@boardy.ai", "home@x")])
    state.add_event("main@x", _timed("Boardy intro", organizer={"email": "boardy@boardy.ai"}))
    state.add_event("main@x", _timed("Other", organizer={"email": "a@b.c"}))
    state.add_event("main@x", _timed("Free one", transparency="transparent"))
    run_once(store, calendar, note, clock)
    verdicts = {r["title"]: r["verdict"] for r in json.loads(store.get_meta("event_decisions"))}
    assert verdicts["Boardy intro"].startswith("copied to ")
    assert verdicts["Other"] == "not copied: no rule matched (organizer a@b.c)"
    assert verdicts["Free one"] == "not copied: shown as Free, not Busy"
