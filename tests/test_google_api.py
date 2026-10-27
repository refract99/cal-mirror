import json
from datetime import UTC, date, datetime

import httplib2
import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from cal_mirror.google_api import (
    SCOPES,
    GoogleCalendar,
    event_from_google,
    event_to_google,
    send_write,
)
from cal_mirror.model import (
    OP_CREATE,
    OP_DELETE,
    OP_MOVE,
    OP_UPDATE,
    Attachment,
    Attendee,
    CalendarInfo,
    Event,
    PlannedWrite,
)
from cal_mirror.ports import CalendarAuthError, CalendarError, SyncTokenExpired

SYNC_TOKEN = "fixture-sync-token-1"


def http_error(status, reason=None, uri="https://example.invalid/calendar/v3/events"):
    error = {"message": "failed"}
    if reason:
        error["errors"] = [{"reason": reason, "message": "failed"}]
    return HttpError(
        httplib2.Response({"status": str(status)}), json.dumps({"error": error}).encode(), uri=uri
    )


class Request:
    def __init__(self, outcome):
        self._outcome = outcome

    def execute(self):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class Resource:
    def __init__(self, service, name):
        self._service = service
        self._name = name

    def __getattr__(self, method):
        def call(**kwargs):
            self._service.calls.append((f"{self._name}.{method}", kwargs))
            return Request(self._service.queue.pop(0))

        return call


class StubService:
    def __init__(self, *outcomes):
        self.calls = []
        self.queue = list(outcomes)

    def events(self):
        return Resource(self, "events")

    def calendarList(self):
        return Resource(self, "calendarList")


def sample_event(**changes):
    fields = {
        "calendar_id": "c",
        "id": "src",
        "summary": "Standup",
        "description": "notes",
        "location": "Room 1",
        "start": datetime(2026, 11, 3, 13, 30, tzinfo=UTC),
        "end": datetime(2026, 11, 3, 14, 0, tzinfo=UTC),
        "recurrence": ("RRULE:FREQ=WEEKLY",),
        "attendees": (Attendee("a@example.com"), Attendee("b@example.com")),
        "attachments": (Attachment("https://example.com/f", title="F"),),
        "private": {"cal_mirror_source": "c/src"},
    }
    fields.update(changes)
    return Event(**fields)


def test_scopes():
    assert SCOPES == ["https://www.googleapis.com/auth/calendar"]


def test_timed_event_converted_to_utc():
    item = {
        "id": "e1",
        "start": {"dateTime": "2026-11-03T07:30:00-06:00"},
        "end": {"dateTime": "2026-11-03T08:00:00-06:00"},
    }
    event = event_from_google("c", item)
    assert event.start == datetime(2026, 11, 3, 13, 30, tzinfo=UTC)
    assert event.end == datetime(2026, 11, 3, 14, 0, tzinfo=UTC)
    assert event.all_day is False


def test_all_day_event():
    item = {"id": "e2", "start": {"date": "2026-11-03"}, "end": {"date": "2026-11-04"}}
    event = event_from_google("c", item)
    assert event.start == date(2026, 11, 3)
    assert event.all_day is True
    assert event_to_google(event)["start"] == {"date": "2026-11-03"}


def test_cancelled_event_without_times():
    event = event_from_google("c", {"id": "e3", "status": "cancelled"})
    assert event.status == "cancelled"
    assert event.start is None
    assert event.end is None
    assert event.transparency == "opaque"
    assert event.visibility == "default"


def test_conference_links_and_dial_in():
    item = {
        "id": "e4",
        "conferenceData": {
            "entryPoints": [
                {"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"},
                {
                    "entryPointType": "phone",
                    "uri": "tel:+1-555-0100",
                    "label": "+1 555-0100",
                    "pin": "123456",
                },
                {"entryPointType": "phone", "uri": "tel:+1-555-0199"},
            ]
        },
    }
    event = event_from_google("c", item)
    assert event.meeting_link == "https://meet.google.com/abc-defg-hij"
    assert event.dial_in == ("+1 555-0100 PIN 123456", "+1-555-0199")


def test_hangout_link_wins():
    item = {"id": "e5", "hangoutLink": "https://hangout.example/x"}
    assert event_from_google("c", item).meeting_link == "https://hangout.example/x"


def test_event_fields_mapped():
    item = {
        "id": "e6",
        "summary": "S",
        "recurrence": ["RRULE:FREQ=DAILY"],
        "recurringEventId": "series",
        "organizer": {"email": "o@example.com"},
        "creator": {"email": "c@example.com"},
        "updated": "2026-10-01T10:00:00.000Z",
        "attendees": [{"email": "a@example.com", "self": True}],
        "attachments": [{"fileUrl": "u", "title": "t", "mimeType": "m", "fileId": "i"}],
        "extendedProperties": {"private": {"k": "v"}},
    }
    event = event_from_google("c", item)
    assert event.recurrence == ("RRULE:FREQ=DAILY",)
    assert event.recurring_event_id == "series"
    assert event.organizer_email == "o@example.com"
    assert event.creator_email == "c@example.com"
    assert event.updated == datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    assert event.attendees == (Attendee("a@example.com", "needsAction", True, False),)
    assert event.attachments == (Attachment("u", "t", "m", "i"),)
    assert event.private == {"k": "v"}
    assert event.raw == item


def test_body_strips_guests_and_keeps_keys():
    body = event_to_google(sample_event())
    assert body["attendees"] == []
    assert set(body) == {
        "summary",
        "description",
        "location",
        "start",
        "end",
        "recurrence",
        "transparency",
        "visibility",
        "attachments",
        "extendedProperties",
        "attendees",
    }
    assert body["start"] == {"dateTime": "2026-11-03T13:30:00Z", "timeZone": "UTC"}
    assert body["attachments"] == [{"fileUrl": "https://example.com/f", "title": "F"}]


def test_body_drops_create_request():
    conference = {"createRequest": {"requestId": "r"}, "conferenceId": "c"}
    event = sample_event(conference=conference)
    assert event_to_google(event)["conferenceData"] == {"conferenceId": "c"}
    assert event.conference == {"createRequest": {"requestId": "r"}, "conferenceId": "c"}


def test_round_trip():
    event = sample_event()
    back = event_from_google("c", event_to_google(event))
    assert back.summary == event.summary
    assert back.start == event.start
    assert back.end == event.end
    assert back.recurrence == event.recurrence
    assert back.attachments == event.attachments
    assert back.private == event.private


def test_send_write_create():
    service = StubService({"id": "new"})
    body = sample_event()
    write = PlannedWrite(OP_CREATE, "dest", body=body)
    assert send_write(service, write) == "new"
    assert service.calls == [
        (
            "events.insert",
            {
                "calendarId": "dest",
                "body": event_to_google(body),
                "sendUpdates": "none",
                "conferenceDataVersion": 1,
                "supportsAttachments": True,
            },
        )
    ]


def test_send_write_update():
    service = StubService({"id": "ev"})
    body = sample_event()
    write = PlannedWrite(OP_UPDATE, "dest", event_id="ev", body=body)
    assert send_write(service, write) == "ev"
    assert service.calls == [
        (
            "events.patch",
            {
                "calendarId": "dest",
                "eventId": "ev",
                "body": {**event_to_google(body), "conferenceData": None},
                "sendUpdates": "none",
                "conferenceDataVersion": 1,
                "supportsAttachments": True,
            },
        )
    ]


def test_send_write_delete():
    service = StubService({})
    assert send_write(service, PlannedWrite(OP_DELETE, "dest", event_id="ev")) == "ev"
    assert service.calls == [
        ("events.delete", {"calendarId": "dest", "eventId": "ev", "sendUpdates": "none"})
    ]


def test_send_write_move():
    service = StubService({"id": "moved"})
    write = PlannedWrite(OP_MOVE, "dest", event_id="ev", destination="other")
    assert send_write(service, write) == "moved"
    assert service.calls == [
        (
            "events.move",
            {
                "calendarId": "dest",
                "eventId": "ev",
                "destination": "other",
                "sendUpdates": "none",
            },
        )
    ]


def test_send_write_value_errors():
    service = StubService()
    for write in (
        PlannedWrite(OP_CREATE, "dest"),
        PlannedWrite(OP_UPDATE, "dest", event_id="ev"),
        PlannedWrite("explode", "dest"),
    ):
        with pytest.raises(ValueError):
            send_write(service, write)
    assert service.calls == []


def test_list_calendars_pages():
    service = StubService(
        {
            "items": [{"id": "a", "summary": "A", "accessRole": "owner", "primary": True}],
            "nextPageToken": "p2",
        },
        {"items": [{"id": "b"}]},
    )
    found = GoogleCalendar(service).list_calendars()
    assert found == [CalendarInfo("a", "A", "owner", True), CalendarInfo("b")]
    assert service.calls == [
        ("calendarList.list", {"pageToken": None}),
        ("calendarList.list", {"pageToken": "p2"}),
    ]


def test_list_changes_with_sync_token_pages():
    service = StubService(
        {"items": [{"id": "1"}], "nextPageToken": "p2"},
        {"items": [{"id": "2"}], "nextSyncToken": "next-1"},
    )
    changes, token = GoogleCalendar(service).list_changes(
        "c", SYNC_TOKEN, datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert [e.id for e in changes] == ["1", "2"]
    assert token == "next-1"
    base = {"calendarId": "c", "showDeleted": True, "singleEvents": False, "maxResults": 2500}
    assert service.calls == [
        ("events.list", {**base, "pageToken": None, "syncToken": SYNC_TOKEN}),
        ("events.list", {**base, "pageToken": "p2", "syncToken": SYNC_TOKEN}),
    ]


def test_list_changes_with_time_min():
    service = StubService({"items": []})
    changes, token = GoogleCalendar(service).list_changes(
        "c", None, datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    )
    assert (changes, token) == ([], "")
    kwargs = service.calls[0][1]
    assert kwargs["timeMin"] == "2026-01-02T03:04:05Z"
    assert "syncToken" not in kwargs


def test_list_events_pages_and_window():
    service = StubService(
        {"items": [{"id": "1"}], "nextPageToken": "p2"},
        {"items": [{"id": "2"}], "nextSyncToken": "ignored"},
    )
    found = GoogleCalendar(service).list_events(
        "c", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)
    )
    assert [e.id for e in found] == ["1", "2"]
    for _, kwargs in service.calls:
        assert kwargs["timeMin"] == "2026-01-01T00:00:00Z"
        assert kwargs["timeMax"] == "2026-02-01T00:00:00Z"
        assert "syncToken" not in kwargs
        assert kwargs["showDeleted"] is True
        assert kwargs["singleEvents"] is False


def test_get_event():
    service = StubService({"id": "ev", "summary": "S"})
    event = GoogleCalendar(service).get_event("c", "ev")
    assert event is not None
    assert event.summary == "S"
    assert service.calls == [("events.get", {"calendarId": "c", "eventId": "ev"})]


def test_get_event_missing_returns_none():
    assert GoogleCalendar(StubService(http_error(404))).get_event("c", "ev") is None


def test_sync_token_expired_hides_token():
    uri = f"https://example.invalid/calendar/v3/events?syncToken={SYNC_TOKEN}"
    service = StubService(http_error(410, uri=uri))
    with pytest.raises(SyncTokenExpired) as caught:
        GoogleCalendar(service).list_changes("c", SYNC_TOKEN)
    assert SYNC_TOKEN not in str(caught.value)


def test_gone_outside_list_changes_is_plain_error():
    with pytest.raises(CalendarError) as caught:
        GoogleCalendar(StubService(http_error(410))).list_events("c")
    assert not isinstance(caught.value, SyncTokenExpired)


def test_unauthorized_is_auth_error():
    with pytest.raises(CalendarAuthError):
        GoogleCalendar(StubService(http_error(401))).list_calendars()


def test_forbidden_is_auth_error():
    with pytest.raises(CalendarAuthError):
        GoogleCalendar(StubService(http_error(403, "insufficientPermissions"))).list_calendars()


def test_rate_limit_is_plain_error():
    for reason in ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"):
        with pytest.raises(CalendarError) as caught:
            GoogleCalendar(StubService(http_error(403, reason))).list_calendars()
        assert not isinstance(caught.value, CalendarAuthError)


def test_server_error_message():
    with pytest.raises(CalendarError) as caught:
        GoogleCalendar(StubService(http_error(500))).list_events("c")
    assert "500" in str(caught.value)
    assert str(caught.value) == "Google API events.list failed: HTTP 500"
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def test_refresh_error_is_auth_error():
    with pytest.raises(CalendarAuthError, match="Google token rejected"):
        GoogleCalendar(StubService(RefreshError("bad"))).list_calendars()


def test_write_errors_translated():
    write = PlannedWrite(OP_DELETE, "dest", event_id="ev")
    with pytest.raises(CalendarError, match="events.delete failed: HTTP 500"):
        GoogleCalendar(StubService(http_error(500))).apply(write)
    with pytest.raises(CalendarAuthError):
        GoogleCalendar(StubService(RefreshError("bad"))).apply(write)


def test_every_write_is_silent_and_guest_free():
    body = sample_event(conference={"createRequest": {"requestId": "r"}, "conferenceId": "c"})
    service = StubService({"id": "1"}, {"id": "2"}, {}, {"id": "4"})
    calendar = GoogleCalendar(service)
    calendar.apply(PlannedWrite(OP_CREATE, "dest", body=body))
    calendar.apply(PlannedWrite(OP_UPDATE, "dest", event_id="ev", body=body))
    calendar.apply(PlannedWrite(OP_DELETE, "dest", event_id="ev"))
    calendar.apply(PlannedWrite(OP_MOVE, "dest", event_id="ev", destination="other"))
    assert len(service.calls) == 4
    for name, kwargs in service.calls:
        assert name.startswith("events.")
        assert kwargs["sendUpdates"] == "none"
        assert not kwargs.get("body", {}).get("attendees")
        assert "createRequest" not in kwargs.get("body", {}).get("conferenceData", {})
