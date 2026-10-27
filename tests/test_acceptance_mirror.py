"""Acceptance criteria 1-5: mirror through run_once and the fake Calendar API."""

from datetime import UTC, datetime, time

from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar
from cal_mirror.model import MARKER_SOURCE, RULE_GUEST_DOMAIN, Rule, Settings
from cal_mirror.ports import FixedClock
from cal_mirror.store_memory import MemoryStore
from cal_mirror.sync import run_once

_AT = "2026-11-03T15:00:00Z"
_END = "2026-11-03T16:00:00Z"
_LATER = "2026-11-03T18:00:00Z"
_MEET = "https://meet.google.com/abc-defg-hij"
_SELF = {"email": "steve@x", "self": True, "responseStatus": "accepted"}


class Note:
    """Records ntfy sends. Criteria 1-5 do not read `sent`."""

    sent: list[tuple[str, str, str]]

    def __init__(self) -> None:
        self.sent = []

    def send(self, title: str, message: str, click: str = "") -> None:
        self.sent += [(title, message, click)]


def _rig():
    clock = FixedClock(datetime(2026, 10, 24, 12, 0, tzinfo=UTC))
    state = FakeCalendar()
    for cid in ("main@x", "work@x", "home@x", "other@x"):
        state.add_calendar(cid, access_role="owner")
    cal = GoogleCalendar(FakeService(state))
    store = MemoryStore()
    hours = {day: (time(8), time(17)) for day in range(5)}
    saved = Settings(
        owner_email="steve@x",
        web_url="https://cal.example",
        main_calendar="main@x",
        default_calendar="home@x",
        secondary_calendars=("work@x", "home@x"),
        allowed_hours=hours,
    )
    store.save_settings(saved)
    return clock, state, cal, store, Note()


def _span(summary: str, **extra) -> dict:
    fields = {"summary": summary, "start": {"dateTime": _AT}, "end": {"dateTime": _END}}
    fields.update(extra)
    return fields


def _live(state: FakeCalendar, calendar_id: str) -> list[dict]:
    return [
        item for item in state.events[calendar_id].values() if item.get("status") != "cancelled"
    ]


def _private(item: dict) -> dict:
    return (item.get("extendedProperties") or {}).get("private") or {}


def _mirrors(state: FakeCalendar, source: str) -> list[dict]:
    found = []
    for bucket in state.events.values():
        for item in bucket.values():
            if item.get("status") != "cancelled" and _private(item).get(MARKER_SOURCE) == source:
                found.append(item)
    return found


def _all_mirrors(state: FakeCalendar) -> list[dict]:
    found = []
    for bucket in state.events.values():
        for item in bucket.values():
            if item.get("status") != "cancelled" and MARKER_SOURCE in _private(item):
                found.append(item)
    return found


def _sync(clock, store, cal, note, *, follow: bool) -> None:
    if follow:
        clock.advance(300)
    run_once(store, cal, note, clock)


def test_criterion_1_blocked_on_main():
    clock, state, cal, store, note = _rig()
    src = state.add_event("work@x", _span("Desk time"))
    _sync(clock, store, cal, note, follow=False)
    mirrors = _live(state, "main@x")
    assert len(mirrors) == 1
    blocked = mirrors[0]
    assert blocked["summary"] == "BLOCKED"
    assert blocked["visibility"] == "private"
    assert blocked["start"] == src["start"]
    assert blocked["end"] == src["end"]
    assert not blocked.get("description")
    assert not blocked.get("location")
    assert not blocked.get("attendees")
    blocked_id = blocked["id"]
    state.edit_event("work@x", src["id"], end={"dateTime": _LATER})
    _sync(clock, store, cal, note, follow=True)
    updated = state.events["main@x"][blocked_id]
    assert updated["id"] == blocked_id
    assert updated["end"]["dateTime"] == _LATER
    state.cancel_event("work@x", src["id"])
    _sync(clock, store, cal, note, follow=True)
    assert _live(state, "main@x") == []


def test_criterion_2_copy_by_rule_and_default():
    clock, state, cal, store, note = _rig()
    store.save_rules([Rule(1, RULE_GUEST_DOMAIN, "acme.com", "work@x")])
    acme = state.add_event(
        "main@x",
        _span(
            "Acme review",
            location="Lab",
            description="Agenda",
            hangoutLink=_MEET,
            conferenceData={"entryPoints": [{"entryPointType": "video", "uri": _MEET}]},
            attachments=[{"fileUrl": "https://drive.example/a1", "title": "agenda"}],
            attendees=[{"email": "ann@acme.com"}, _SELF],
        ),
    )
    bob = state.add_event(
        "main@x",
        _span(
            "Other review",
            attendees=[{"email": "bob@other.org"}, _SELF],
        ),
    )
    _sync(clock, store, cal, note, follow=False)
    assert len(_live(state, "work@x")) == 1
    copy_id = _live(state, "work@x")[0]["id"]
    copy = cal.get_event("work@x", copy_id)
    source = cal.get_event("main@x", acme["id"])
    assert copy is not None and source is not None
    assert copy.summary == source.summary
    assert copy.location == source.location
    assert copy.attachments[0].file_url == source.attachments[0].file_url
    assert copy.meeting_link == source.meeting_link
    assert copy.attendees == ()
    assert copy.description.startswith(f"Meeting link: {_MEET}")
    guest_line = "- ann@acme.com (needsAction)"
    assert copy.description.index(guest_line) < copy.description.index("Agenda")
    assert len(_live(state, "home@x")) == 1
    placed = {row.source for row in store.list_placements()}
    assert f"main@x/{bob['id']}" in placed
    state.edit_event("main@x", acme["id"], summary="Acme review moved")
    _sync(clock, store, cal, note, follow=True)
    edited = cal.get_event("work@x", copy.id)
    assert edited is not None
    assert edited.id == copy.id
    assert edited.summary == "Acme review moved"
    state.cancel_event("main@x", acme["id"])
    _sync(clock, store, cal, note, follow=True)
    gone = cal.get_event("work@x", copy.id)
    assert gone is None or gone.status == "cancelled"
    assert _mirrors(state, f"main@x/{acme['id']}") == []


def test_criterion_3_no_loop():
    clock, state, cal, store, note = _rig()
    busy = state.add_event("work@x", _span("Focus block"))
    meeting = state.add_event(
        "main@x",
        _span("Standup", attendees=[{"email": "ann@acme.com"}, _SELF]),
    )
    sources = (f"work@x/{busy['id']}", f"main@x/{meeting['id']}")
    after_two = 0
    for turn in range(10):
        _sync(clock, store, cal, note, follow=turn > 0)
        if turn == 1:
            after_two = len(state.writes)
    assert len(state.writes) == after_two
    for source in sources:
        assert len(_mirrors(state, source)) == 1
    assert len(_all_mirrors(state)) == 2


def test_criterion_4_skipped_events():
    clock, state, cal, store, note = _rig()
    state.add_event("work@x", _span("Out of office", transparency="transparent"))
    state.add_event(
        "work@x",
        _span(
            "Skip me",
            attendees=[
                {"email": "steve@x", "self": True, "responseStatus": "declined"},
                {"email": "pat@guest.example"},
            ],
        ),
    )
    state.add_event("other@x", _span("Elsewhere"))
    state.add_event("main@x", _span("Free note", transparency="transparent"))
    _sync(clock, store, cal, note, follow=False)
    assert not any(entry["method"] == "insert" for entry in state.writes)
    busy = state.add_event("work@x", _span("Becomes free"))
    _sync(clock, store, cal, note, follow=True)
    assert len(_mirrors(state, f"work@x/{busy['id']}")) == 1
    state.edit_event("work@x", busy["id"], transparency="transparent")
    _sync(clock, store, cal, note, follow=True)
    assert _mirrors(state, f"work@x/{busy['id']}") == []
    assert _all_mirrors(state) == []


def test_criterion_5_no_updates_no_guests():
    clock, state, cal, store, note = _rig()
    work = state.add_event("work@x", _span("Secondary busy"))
    main = state.add_event(
        "main@x",
        _span("Main meeting", attendees=[{"email": "ann@acme.com"}, _SELF]),
    )
    _sync(clock, store, cal, note, follow=False)
    state.edit_event("work@x", work["id"], end={"dateTime": _LATER})
    state.edit_event("main@x", main["id"], summary="Main meeting edited")
    _sync(clock, store, cal, note, follow=True)
    state.cancel_event("work@x", work["id"])
    state.cancel_event("main@x", main["id"])
    _sync(clock, store, cal, note, follow=True)
    methods = {entry["method"] for entry in state.writes}
    assert "insert" in methods
    assert "patch" in methods
    assert "delete" in methods
    for entry in state.writes:
        assert entry["sendUpdates"] == "none"
        body = entry.get("body") or {}
        assert not body.get("attendees")
