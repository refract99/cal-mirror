import copy

from cal_mirror.fake_google import FakeCalendar
from cal_mirror.google_api import GoogleCalendar
from cal_mirror.model import KIND_BLOCKED, KIND_COPY, MARKER_KIND, MARKER_SOURCE, Rule
from cal_mirror.sync import RunResult, mirror_event_id, run_once
from tests.test_sync_fixes import FirstLinkFails, _live, _timed, _world

START = "2026-11-03T15:00:00Z"
END = "2026-11-03T16:00:00Z"


class _CountGets:
    """Counts get_event calls and delegates everything else."""

    def __init__(self, inner: GoogleCalendar) -> None:
        self._inner = inner
        self.gets = 0

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def get_event(self, calendar_id: str, event_id: str):
        self.gets += 1
        return self._inner.get_event(calendar_id, event_id)


def _no_mail(state: FakeCalendar) -> None:
    for entry in state.writes:
        assert entry["sendUpdates"] == "none"
        body = entry.get("body")
        if body is not None:
            assert body["attendees"] == []


def _seed(
    state: FakeCalendar, calendar_id: str, event_id: str, source: str, *, cancelled: bool
) -> dict:
    item = {
        "id": event_id,
        "summary": "Seed",
        "status": "cancelled" if cancelled else "confirmed",
        "start": {"dateTime": START},
        "end": {"dateTime": END},
        "extendedProperties": {"private": {MARKER_SOURCE: source, MARKER_KIND: KIND_COPY}},
    }
    return state.add_event(calendar_id, item)


def test_reschedule_after_lost_link_keeps_one_blocked() -> None:
    store, state, clock, note, calendar = _world(store=FirstLinkFails())
    src = state.add_event("work@x", _timed("Busy"))
    run_once(store, calendar, note, clock)
    state.edit_event(
        "work@x",
        src["id"],
        start={"dateTime": "2026-11-04T15:00:00Z"},
        end={"dateTime": "2026-11-04T16:00:00Z"},
    )
    second = run_once(store, calendar, note, clock)
    assert second.failure == ""
    live = _live(state, "main@x", KIND_BLOCKED)
    assert len(live) == 1
    mid = mirror_event_id(f"work@x/{src['id']}", KIND_BLOCKED)
    assert live[0]["id"] == mid
    assert live[0]["start"]["dateTime"] == "2026-11-04T15:00:00Z"
    assert [entry["method"] for entry in state.writes] == ["insert", "patch"]
    links = store.list_links()
    assert len(links) == 1
    assert links[0].event_id == mid
    _no_mail(state)


def test_lost_copy_is_moved_then_created() -> None:
    store, state, clock, note, calendar = _world(store=FirstLinkFails())
    src = state.add_event(
        "main@x",
        _timed(
            "Sync",
            attendees=[{"email": "ann@acme.com"}],
            hangoutLink="https://meet.google.com/abc",
        ),
    )
    run_once(store, calendar, note, clock)
    store.save_rules([Rule(id=1, kind="title_word", value="Acme", calendar_id="work@x")])
    state.edit_event("main@x", src["id"], summary="Acme Sync")
    second = run_once(store, calendar, note, clock)
    assert second == RunResult(writes=2)
    assert [(entry["method"], entry["calendarId"]) for entry in state.writes] == [
        ("insert", "home@x"),
        ("move", "home@x"),
        ("patch", "work@x"),
    ]
    assert state.writes[1]["destination"] == "work@x"
    assert _live(state, "home@x", KIND_COPY) == []
    live = _live(state, "work@x", KIND_COPY)
    assert len(live) == 1
    assert live[0]["id"] == mirror_event_id(f"main@x/{src['id']}", KIND_COPY)
    assert live[0]["summary"] == "Acme Sync"
    link = store.get_link(f"main@x/{src['id']}")
    assert link is not None
    assert link.calendar_id == "work@x"
    assert link.rule_id == 1
    _no_mail(state)


def test_other_source_and_cancelled_copy_are_left() -> None:
    store, state, clock, note, calendar = _world()
    state.add_calendar("other@x")
    src = state.add_event(
        "main@x",
        _timed(
            "Sync",
            attendees=[{"email": "ann@acme.com"}],
            hangoutLink="https://meet.google.com/abc",
        ),
    )
    mid = mirror_event_id(f"main@x/{src['id']}", KIND_COPY)
    _seed(state, "work@x", mid, "work@x/other", cancelled=False)
    _seed(state, "other@x", mid, f"main@x/{src['id']}", cancelled=True)
    work_before = copy.deepcopy(state.events["work@x"][mid])
    other_before = copy.deepcopy(state.events["other@x"][mid])
    run_once(store, calendar, note, clock)
    assert [(entry["method"], entry["calendarId"]) for entry in state.writes] == [
        ("insert", "home@x")
    ]
    assert state.events["work@x"][mid] == work_before
    assert state.events["other@x"][mid] == other_before
    _no_mail(state)


def test_blocked_create_does_not_get_event() -> None:
    store, state, clock, note, inner = _world()
    state.add_event("work@x", _timed("Busy"))
    calendar = _CountGets(inner)
    result = run_once(store, calendar, note, clock)
    assert result.writes == 1
    assert calendar.gets == 0
    assert [entry["method"] for entry in state.writes] == ["insert"]
    _no_mail(state)


def test_series_exception_uses_source_kind_id() -> None:
    store, state, clock, note, calendar = _world()
    series = state.add_event("work@x", _timed("Series", recurrence=["RRULE:FREQ=WEEKLY"]))
    state.add_exception("work@x", series["id"], "2026-11-10T15:00:00Z", summary="Moved")
    assert run_once(store, calendar, note, clock).failure == ""
    patches = [entry for entry in state.writes if entry["method"] == "patch"]
    assert len(patches) == 1
    expected = mirror_event_id(f"work@x/{series['id']}", KIND_BLOCKED) + "_20261110T150000Z"
    assert patches[0]["eventId"] == expected
    _no_mail(state)
