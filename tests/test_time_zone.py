from datetime import UTC, date, datetime

import pytest
from googleapiclient.errors import HttpError

from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar, event_from_google, event_to_google
from cal_mirror.model import OP_CREATE, Event, PlannedWrite
from cal_mirror.plan_blocked import blocked_body
from cal_mirror.plan_copy import copy_body

T1 = datetime(2026, 11, 3, 8, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)

_BERLIN_ITEM = {
    "id": "src",
    "start": {"dateTime": "2026-11-03T09:00:00+01:00", "timeZone": "Europe/Berlin"},
    "recurrence": ["RRULE:FREQ=WEEKLY"],
}


def _berlin_source() -> Event:
    return event_from_google("sec@x", _BERLIN_ITEM)


def _event(**changes) -> Event:
    fields = {"calendar_id": "c", "id": "e", "start": T1, "end": T2}
    fields.update(changes)
    return Event(**fields)


def test_read_berlin_time_zone():
    assert _berlin_source().time_zone == "Europe/Berlin"


def test_read_missing_time_zone_is_empty():
    event = event_from_google(
        "c",
        {"id": "e", "start": {"dateTime": "2026-11-03T09:00:00+01:00"}},
    )
    assert event.time_zone == ""


def test_write_recurring_with_zone():
    body = event_to_google(_event(recurrence=("RRULE:FREQ=WEEKLY",), time_zone="Europe/Berlin"))
    assert body["start"] == {"dateTime": "2026-11-03T08:00:00Z", "timeZone": "Europe/Berlin"}
    assert body["end"] == {"dateTime": "2026-11-03T09:00:00Z", "timeZone": "Europe/Berlin"}


def test_write_recurring_without_zone_uses_utc():
    body = event_to_google(_event(recurrence=("RRULE:FREQ=WEEKLY",), time_zone=""))
    assert body["start"] == {"dateTime": "2026-11-03T08:00:00Z", "timeZone": "UTC"}
    assert body["end"] == {"dateTime": "2026-11-03T09:00:00Z", "timeZone": "UTC"}


def test_write_single_without_zone_omits_time_zone():
    body = event_to_google(_event(time_zone=""))
    assert body["start"] == {"dateTime": "2026-11-03T08:00:00Z"}
    assert body["end"] == {"dateTime": "2026-11-03T09:00:00Z"}


def test_write_single_with_zone():
    body = event_to_google(_event(time_zone="America/Chicago"))
    assert body["start"]["timeZone"] == "America/Chicago"
    assert body["end"]["timeZone"] == "America/Chicago"


def test_write_all_day_recurring_omits_time_zone():
    event = Event(
        calendar_id="c",
        id="e",
        start=date(2026, 11, 3),
        end=date(2026, 11, 4),
        all_day=True,
        recurrence=("RRULE:FREQ=WEEKLY",),
        time_zone="Europe/Berlin",
    )
    body = event_to_google(event)
    assert body["start"] == {"date": "2026-11-03"}
    assert "timeZone" not in body["start"]
    assert "timeZone" not in body["end"]


def test_blocked_body_copies_time_zone():
    assert blocked_body(_berlin_source(), "main@x").time_zone == "Europe/Berlin"


def test_copy_body_copies_time_zone():
    assert copy_body(_berlin_source(), "home@x").time_zone == "Europe/Berlin"


def test_fake_insert_recurring_timed_without_zone_is_400():
    state = FakeCalendar()
    state.add_calendar("cal")
    service = FakeService(state)
    before = dict(state.events["cal"])
    body = {
        "recurrence": ["RRULE:FREQ=DAILY"],
        "start": {"dateTime": "2026-10-27T15:00:00Z"},
        "end": {"dateTime": "2026-10-27T16:00:00Z"},
    }
    with pytest.raises(HttpError) as err:
        service.events().insert(calendarId="cal", body=body, sendUpdates="none").execute()
    assert int(err.value.resp.status) == 400
    assert state.events["cal"] == before


def test_fake_insert_recurring_timed_with_utc_succeeds():
    state = FakeCalendar()
    state.add_calendar("cal")
    service = FakeService(state)
    body = {
        "recurrence": ["RRULE:FREQ=DAILY"],
        "start": {"dateTime": "2026-10-27T15:00:00Z", "timeZone": "UTC"},
        "end": {"dateTime": "2026-10-27T16:00:00Z", "timeZone": "UTC"},
    }
    item = service.events().insert(calendarId="cal", body=body, sendUpdates="none").execute()
    assert item["start"]["timeZone"] == "UTC"
    assert state.events["cal"][item["id"]]["start"]["timeZone"] == "UTC"


def test_fake_insert_single_timed_without_zone_succeeds():
    state = FakeCalendar()
    state.add_calendar("cal")
    service = FakeService(state)
    body = {
        "start": {"dateTime": "2026-10-27T15:00:00Z"},
        "end": {"dateTime": "2026-10-27T16:00:00Z"},
    }
    item = service.events().insert(calendarId="cal", body=body, sendUpdates="none").execute()
    assert item["id"] in state.events["cal"]


def test_fake_insert_recurring_all_day_succeeds():
    state = FakeCalendar()
    state.add_calendar("cal")
    service = FakeService(state)
    body = {
        "recurrence": ["RRULE:FREQ=DAILY"],
        "start": {"date": "2026-11-03"},
        "end": {"date": "2026-11-04"},
    }
    item = service.events().insert(calendarId="cal", body=body, sendUpdates="none").execute()
    assert item["id"] in state.events["cal"]
    assert "timeZone" not in item["start"]


def test_fake_patch_summary_of_seeded_recurring_succeeds():
    state = FakeCalendar()
    state.add_calendar("cal")
    state.add_event(
        "cal",
        {
            "id": "e",
            "summary": "Series",
            "recurrence": ["RRULE:FREQ=WEEKLY"],
            "start": {"dateTime": "2026-10-27T15:00:00Z"},
            "end": {"dateTime": "2026-10-27T16:00:00Z"},
        },
    )
    service = FakeService(state)
    item = (
        service.events()
        .patch(calendarId="cal", eventId="e", body={"summary": "Later"}, sendUpdates="none")
        .execute()
    )
    assert item["summary"] == "Later"
    assert state.events["cal"]["e"]["recurrence"] == ["RRULE:FREQ=WEEKLY"]


def test_blocked_apply_stores_source_zone():
    state = FakeCalendar()
    state.add_calendar("main@x")
    body = blocked_body(_berlin_source(), "main@x")
    GoogleCalendar(FakeService(state)).apply(
        PlannedWrite(op=OP_CREATE, calendar_id="main@x", body=body)
    )
    stored = next(iter(state.events["main@x"].values()))
    assert stored["start"]["timeZone"] == "Europe/Berlin"
