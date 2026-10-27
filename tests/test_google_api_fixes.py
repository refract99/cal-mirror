from datetime import UTC, datetime

import pytest

from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar, event_to_google
from cal_mirror.model import OP_CREATE, OP_UPDATE, Attendee, Event, PlannedWrite
from cal_mirror.ports import CalendarError


def sample() -> Event:
    return Event(
        calendar_id="src@x",
        id="src1",
        summary="Standup",
        start=datetime(2026, 11, 3, 15, 0, tzinfo=UTC),
        end=datetime(2026, 11, 3, 16, 0, tzinfo=UTC),
        attendees=(Attendee("a@example.com"), Attendee("b@example.com")),
    )


def world() -> tuple[FakeCalendar, GoogleCalendar]:
    state = FakeCalendar()
    state.add_calendar("main@x")
    return state, GoogleCalendar(FakeService(state))


def _create(event_id: str = "mirrorid0001", calendar_id: str = "main@x") -> PlannedWrite:
    return PlannedWrite(OP_CREATE, calendar_id, event_id=event_id, body=sample())


def test_create_with_id_inserts_that_id():
    state, cal = world()
    body = sample()
    assert cal.apply(PlannedWrite(OP_CREATE, "main@x", event_id="mirrorid0001", body=body)) == (
        "mirrorid0001"
    )
    assert state.writes == [
        {
            "method": "insert",
            "calendarId": "main@x",
            "body": {**event_to_google(body), "id": "mirrorid0001"},
            "sendUpdates": "none",
            "conferenceDataVersion": 1,
            "supportsAttachments": True,
        }
    ]


def test_create_again_patches_the_same_event():
    state, cal = world()
    body = sample()
    write = PlannedWrite(OP_CREATE, "main@x", event_id="mirrorid0001", body=body)
    cal.apply(write)
    assert cal.apply(write) == "mirrorid0001"
    recorded = state.writes[-1]
    assert recorded["method"] == "patch"
    assert recorded["eventId"] == "mirrorid0001"
    assert recorded["body"] == {
        **event_to_google(body),
        "conferenceData": None,
        "status": "confirmed",
    }
    assert list(state.events["main@x"]) == ["mirrorid0001"]


def test_create_after_cancel_confirms_the_same_event():
    state, cal = world()
    write = _create()
    cal.apply(write)
    state.cancel_event("main@x", "mirrorid0001")
    assert cal.apply(write) == "mirrorid0001"
    assert state.events["main@x"]["mirrorid0001"]["status"] == "confirmed"


def test_create_without_id_omits_id():
    state, cal = world()
    body = sample()
    assert cal.apply(PlannedWrite(OP_CREATE, "main@x", body=body)) == "evt1"
    assert "id" not in state.writes[0]["body"]
    assert state.writes[0]["body"] == event_to_google(body)
    assert state.writes[0]["method"] == "insert"


def test_create_on_unknown_calendar_is_insert_404():
    state, cal = world()
    with pytest.raises(CalendarError, match="events.insert failed: HTTP 404"):
        cal.apply(_create(calendar_id="nope@x"))
    assert state.writes == []


def test_update_empty_conference_clears_conference_data():
    state, cal = world()
    state.add_event(
        "main@x",
        {
            "id": "mirrorid0001",
            "summary": "Standup",
            "conferenceData": {
                "conferenceId": "old",
                "entryPoints": [{"entryPointType": "video", "uri": "https://meet.example/x"}],
            },
        },
    )
    body = sample()
    cal.apply(PlannedWrite(OP_UPDATE, "main@x", event_id="mirrorid0001", body=body))
    assert state.writes[-1]["body"]["conferenceData"] is None
    got = cal.get_event("main@x", "mirrorid0001")
    assert got is not None
    assert got.meeting_link == ""
    assert got.conference == {}


def test_update_sends_conference_when_present():
    state, cal = world()
    state.add_event("main@x", {"id": "mirrorid0001", "summary": "Standup"})
    body = sample()
    body = Event(
        calendar_id=body.calendar_id,
        id=body.id,
        summary=body.summary,
        start=body.start,
        end=body.end,
        attendees=body.attendees,
        conference={"conferenceId": "c"},
    )
    cal.apply(PlannedWrite(OP_UPDATE, "main@x", event_id="mirrorid0001", body=body))
    assert state.writes[-1]["body"]["conferenceData"] == {"conferenceId": "c"}


def test_every_recorded_write_is_silent_and_guest_free():
    state, cal = world()
    body = sample()
    cal.apply(PlannedWrite(OP_CREATE, "main@x", event_id="mirrorid0001", body=body))
    cal.apply(PlannedWrite(OP_CREATE, "main@x", event_id="mirrorid0001", body=body))
    cal.apply(PlannedWrite(OP_UPDATE, "main@x", event_id="mirrorid0001", body=body))
    cal.apply(PlannedWrite(OP_CREATE, "main@x", body=body))
    assert state.writes
    for entry in state.writes:
        assert entry["sendUpdates"] == "none"
        assert entry["body"]["attendees"] == []
