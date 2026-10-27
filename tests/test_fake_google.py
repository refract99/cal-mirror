from googleapiclient.errors import HttpError

from cal_mirror.fake_google import FakeCalendar, FakeService


def _status(call) -> int | None:
    try:
        call()
    except HttpError as err:
        return err.resp.status
    return None


def _service(*calendar_ids: str) -> tuple[FakeCalendar, FakeService]:
    state = FakeCalendar()
    for calendar_id in calendar_ids:
        state.add_calendar(calendar_id)
    return state, FakeService(state)


def test_sync_sees_edit_then_empty():
    state, service = _service("a")
    created = state.add_event("a", {"summary": "x"})
    first = service.events().list(calendarId="a").execute()
    state.edit_event("a", "evt1", summary="y")
    second = service.events().list(calendarId="a", syncToken=first["nextSyncToken"]).execute()
    third = service.events().list(calendarId="a", syncToken=second["nextSyncToken"]).execute()
    assert (
        created["id"] == "evt1"
        and len(first["items"]) == 1
        and "nextSyncToken" in first
        and [item["summary"] for item in second["items"]] == ["y"]
        and third["items"] == []
    )


def test_cancel_is_in_sync_and_hidden_from_full_list():
    state, service = _service("a")
    state.add_event("a", {"summary": "x"})
    token = service.events().list(calendarId="a").execute()["nextSyncToken"]
    state.cancel_event("a", "evt1")
    changed = service.events().list(calendarId="a", syncToken=token, showDeleted=False).execute()
    full = service.events().list(calendarId="a", showDeleted=False).execute()
    assert changed["items"][0]["status"] == "cancelled" and full["items"] == []


def test_unknown_foreign_and_expired_sync_tokens_are_410():
    state, service = _service("a", "b")
    own = service.events().list(calendarId="a").execute()["nextSyncToken"]
    foreign = service.events().list(calendarId="b").execute()["nextSyncToken"]
    bogus = _status(lambda: service.events().list(calendarId="a", syncToken="bogus").execute())
    other = _status(lambda: service.events().list(calendarId="a", syncToken=foreign).execute())
    state.expire_sync_tokens()
    expired = _status(lambda: service.events().list(calendarId="a", syncToken=own).execute())
    assert bogus == 410 and other == 410 and expired == 410


def test_pages_of_two_then_sync_token():
    _state, service = _service("a")
    for index in range(5):
        _state.add_event("a", {"summary": str(index)})
    page1 = service.events().list(calendarId="a", maxResults=2).execute()
    page2 = (
        service.events()
        .list(calendarId="a", maxResults=2, pageToken=page1["nextPageToken"])
        .execute()
    )
    page3 = (
        service.events()
        .list(calendarId="a", maxResults=2, pageToken=page2["nextPageToken"])
        .execute()
    )
    assert (
        [len(page1["items"]), len(page2["items"]), len(page3["items"])] == [2, 2, 1]
        and set(page1) == {"items", "nextPageToken"}
        and page1["nextPageToken"] == "page-2"
        and set(page2) == {"items", "nextPageToken"}
        and page2["nextPageToken"] == "page-4"
        and set(page3) == {"items", "nextSyncToken"}
        and [item["summary"] for item in page1["items"]] == ["0", "1"]
        and [item["summary"] for item in page3["items"]] == ["4"]
    )


def test_patch_unknown_instance_creates_exception():
    state, service = _service("a")
    state.add_event(
        "a",
        {
            "summary": "series",
            "recurrence": ["RRULE:FREQ=DAILY"],
            "start": {"dateTime": "2026-10-27T15:00:00Z"},
            "end": {"dateTime": "2026-10-27T16:00:00Z"},
        },
    )
    item = (
        service.events()
        .patch(
            calendarId="a",
            eventId="evt1_20261027T150000Z",
            body={"summary": "moved"},
            sendUpdates="none",
        )
        .execute()
    )
    assert (
        item["recurringEventId"] == "evt1"
        and item["originalStartTime"] == {"dateTime": "2026-10-27T15:00:00Z"}
        and item["summary"] == "moved"
        and "recurrence" not in item
        and state.events["a"]["evt1_20261027T150000Z"]["summary"] == "moved"
    )


def test_get_missing_is_404_and_move_to_unknown_records_nothing():
    state, service = _service("a")
    state.add_event("a", {"summary": "x"})
    missing = _status(lambda: service.events().get(calendarId="a", eventId="nope").execute())
    moved = _status(
        lambda: (
            service.events()
            .move(calendarId="a", eventId="evt1", destination="nope", sendUpdates="none")
            .execute()
        )
    )
    kept = state.events["a"]["evt1"]["status"] == "confirmed"
    assert missing == 404 and moved == 404 and state.writes == [] and kept


def test_seeding_records_nothing_and_writes_record_kwargs():
    state, service = _service("a", "b")
    state.add_event(
        "a",
        {
            "summary": "series",
            "recurrence": ["RRULE:FREQ=DAILY"],
            "start": {"date": "2026-10-27"},
            "end": {"date": "2026-10-28"},
        },
    )
    state.edit_event("a", "evt1", summary="edited")
    state.add_exception("a", "evt1", "2026-10-27", summary="one")
    state.cancel_event("a", "evt1_20261027")
    service.events().insert(calendarId="a", body={"summary": "fresh"}, sendUpdates="none").execute()
    service.events().patch(
        calendarId="a",
        eventId="evt2",
        body={"summary": "patched"},
        sendUpdates="all",
    ).execute()
    service.events().delete(calendarId="a", eventId="evt2", sendUpdates="none").execute()
    service.events().move(
        calendarId="a", eventId="evt1", destination="b", sendUpdates=None
    ).execute()
    methods = [entry["method"] for entry in state.writes]
    assert (
        methods == ["insert", "patch", "delete", "move"]
        and state.writes[0]["sendUpdates"] == "none"
        and state.writes[0]["conferenceDataVersion"] == 1
        and state.writes[0]["supportsAttachments"] is True
        and state.writes[0]["calendarId"] == "a"
        and "attendees" not in state.writes[0]["body"]
        and state.writes[1]["eventId"] == "evt2"
        and state.writes[1]["body"] == {"summary": "patched"}
        and state.writes[2]
        == {
            "method": "delete",
            "calendarId": "a",
            "eventId": "evt2",
            "sendUpdates": "none",
        }
        and state.writes[3]["destination"] == "b"
        and state.writes[3]["sendUpdates"] is None
    )


def test_returned_items_are_deep_copies():
    state = FakeCalendar()
    seeded = state.add_calendar("a", summary="Cal")
    seeded["summary"] = "mutated"
    raw = {"summary": "x", "start": {"dateTime": "2026-10-27T10:00:00Z"}}
    returned = state.add_event("a", raw)
    raw["summary"] = "input"
    raw["start"]["dateTime"] = "input"
    returned["summary"] = "returned"
    service = FakeService(state)
    listed = service.events().list(calendarId="a").execute()
    listed["items"][0]["summary"] = "listed"
    assert (
        state.calendars["a"]["summary"] == "Cal"
        and state.events["a"]["evt1"]["summary"] == "x"
        and state.events["a"]["evt1"]["start"]["dateTime"] == "2026-10-27T10:00:00Z"
    )


def test_time_window_uses_start_for_timed_and_all_day():
    state, service = _service("a")
    state.add_event(
        "a",
        {
            "summary": "timed-in",
            "start": {"dateTime": "2026-10-27T10:00:00Z"},
            "end": {"dateTime": "2026-10-27T11:00:00Z"},
        },
    )
    state.add_event(
        "a",
        {
            "summary": "timed-at-max",
            "start": {"dateTime": "2026-10-27T12:00:00Z"},
            "end": {"dateTime": "2026-10-27T13:00:00Z"},
        },
    )
    state.add_event(
        "a",
        {
            "summary": "all-day-in",
            "start": {"date": "2026-10-27"},
            "end": {"date": "2026-10-28"},
        },
    )
    state.add_event(
        "a",
        {
            "summary": "all-day-later",
            "start": {"date": "2026-10-28"},
            "end": {"date": "2026-10-29"},
        },
    )
    found = (
        service.events()
        .list(
            calendarId="a",
            timeMin="2026-10-27T09:00:00Z",
            timeMax="2026-10-27T12:00:00Z",
            showDeleted=False,
        )
        .execute()
    )
    assert [item["summary"] for item in found["items"]] == ["timed-in", "all-day-in"]


def test_move_is_on_both_sync_feeds():
    state, service = _service("a", "b")
    state.add_event("a", {"summary": "x", "status": "confirmed"})
    token_a = service.events().list(calendarId="a").execute()["nextSyncToken"]
    token_b = service.events().list(calendarId="b").execute()["nextSyncToken"]
    moved = (
        service.events()
        .move(calendarId="a", eventId="evt1", destination="b", sendUpdates="none")
        .execute()
    )
    from_a = service.events().list(calendarId="a", syncToken=token_a).execute()
    from_b = service.events().list(calendarId="b", syncToken=token_b).execute()
    assert (
        moved["id"] == "evt1"
        and moved["status"] == "confirmed"
        and from_a["items"][0]["status"] == "cancelled"
        and from_a["items"][0]["summary"] == "x"
        and from_b["items"][0]["status"] == "confirmed"
        and from_b["items"][0]["id"] == "evt1"
    )


def test_delete_twice_is_410():
    state, service = _service("a")
    state.add_event("a", {"summary": "x"})
    first = service.events().delete(calendarId="a", eventId="evt1", sendUpdates="none").execute()
    again = service.events().delete(calendarId="a", eventId="evt1", sendUpdates="none").execute
    second = _status(again)
    assert first == "" and second == 410 and len(state.writes) == 1


def test_insert_duplicate_id_is_409():
    state, service = _service("a")
    state.add_event("a", {"id": "custom", "summary": "x"})
    status = _status(
        lambda: (
            service.events()
            .insert(calendarId="a", body={"id": "custom", "summary": "y"}, sendUpdates="none")
            .execute()
        )
    )
    assert status == 409 and state.writes == [] and state.events["a"]["custom"]["summary"] == "x"


def test_calendar_list_items():
    state = FakeCalendar()
    service = FakeService(state)
    state.add_calendar("a", summary="Main", access_role="owner", primary=True)
    state.add_calendar("b", summary="Other", access_role="writer", primary=False)
    listed = service.calendarList().list().execute()
    assert listed == {
        "items": [
            {"id": "a", "summary": "Main", "accessRole": "owner", "primary": True},
            {"id": "b", "summary": "Other", "accessRole": "writer", "primary": False},
        ]
    }


def test_nothing_happens_until_execute_and_ids_are_shared():
    state, service = _service("a")
    pending = service.events().insert(calendarId="a", body={"summary": "later"}, sendUpdates="none")
    idle = state.events["a"] == {} and state.writes == []
    created = pending.execute()
    seeded = state.add_event("a", {"summary": "next"})
    assert (
        idle
        and created["id"] == "evt1"
        and seeded["id"] == "evt2"
        and created["status"] == "confirmed"
    )


def test_bad_page_and_window_with_sync_are_400():
    _state, service = _service("a")
    token = service.events().list(calendarId="a").execute()["nextSyncToken"]
    bad_page = _status(lambda: service.events().list(calendarId="a", pageToken="nope").execute())
    with_window = _status(
        lambda: (
            service.events()
            .list(calendarId="a", syncToken=token, timeMin="2026-10-27T00:00:00Z")
            .execute()
        )
    )
    assert bad_page == 400 and with_window == 400


def test_all_day_exception_and_series_window():
    state, service = _service("a")
    state.add_event(
        "a",
        {
            "summary": "series",
            "recurrence": ["RRULE:FREQ=DAILY"],
            "start": {"dateTime": "2026-10-01T10:00:00Z"},
            "end": {"dateTime": "2026-10-01T11:00:00Z"},
        },
    )
    made = state.add_exception("a", "evt1", "2026-10-27", summary="day")
    window = (
        service.events()
        .list(
            calendarId="a",
            timeMin="2026-10-27T00:00:00Z",
            timeMax="2026-10-28T00:00:00Z",
            singleEvents=True,
        )
        .execute()
    )
    summaries = [item["summary"] for item in window["items"]]
    patched = (
        service.events()
        .patch(
            calendarId="a",
            eventId="evt1_20261028",
            body={"summary": "other"},
            sendUpdates="none",
        )
        .execute()
    )
    assert (
        made["id"] == "evt1_20261027"
        and made["originalStartTime"] == {"date": "2026-10-27"}
        and "recurrence" not in made
        and summaries == ["series"]
        and patched["originalStartTime"] == {"date": "2026-10-28"}
        and patched["recurringEventId"] == "evt1"
        and "recurrence" not in patched
    )


def test_item_without_start_passes_time_window():
    state, service = _service("a")
    state.add_event("a", {"summary": "loose"})
    found = (
        service.events()
        .list(
            calendarId="a",
            timeMin="2026-10-27T00:00:00Z",
            timeMax="2026-10-28T00:00:00Z",
        )
        .execute()
    )
    assert [item["summary"] for item in found["items"]] == ["loose"]


def test_unknown_helper_target_raises_key_error():
    state = FakeCalendar()
    missing_calendar = False
    missing_event = False
    try:
        state.add_event("a", {"summary": "x"})
    except KeyError:
        missing_calendar = True
    state.add_calendar("a")
    try:
        state.edit_event("a", "evt1", summary="y")
    except KeyError:
        missing_event = True
    assert missing_calendar and missing_event


def test_updated_stamp_uses_change_number():
    state = FakeCalendar()
    state.add_calendar("a")
    created = state.add_event("a", {"summary": "x"})
    edited = state.edit_event("a", "evt1", summary="y")
    assert (
        created["updated"] == "2026-01-01T00:00:02.000Z"
        and edited["updated"] == "2026-01-01T00:00:03.000Z"
        and edited["summary"] == "y"
    )


def test_default_service_has_its_own_state():
    service = FakeService()
    service.state.add_calendar("a", summary="A")
    listed = service.calendarList().list(pageToken=None).execute()
    assert listed["items"][0]["id"] == "a" and "nextPageToken" not in listed
