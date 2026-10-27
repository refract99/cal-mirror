from datetime import UTC, date, datetime

from cal_mirror.model import (
    KIND_BLOCKED,
    KIND_COPY,
    MARKER_KIND,
    MARKER_SOURCE,
    OP_CREATE,
    OP_DELETE,
    OP_UPDATE,
    Attachment,
    Attendee,
    Event,
    Link,
    Settings,
)
from cal_mirror.plan_copy import copy_body, details_block, plan_copies

S = Settings(
    main_calendar="main@x", secondary_calendars=("work@x", "home@x"), default_calendar="home@x"
)
T1 = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
ATTACH = (Attachment(file_url="https://drive.google.com/f1", title="Deck"),)
ATTENDEES = (
    Attendee(email="ann@acme.com", response="accepted", organizer=True),
    Attendee(email="steve@x", response="needsAction", is_self=True),
)
E = Event(
    calendar_id="main@x",
    id="e1",
    summary="Review",
    description="Agenda",
    location="Room 4",
    start=T1,
    end=T2,
    organizer_email="ann@acme.com",
    attendees=ATTENDEES,
    attachments=ATTACH,
    meeting_link="https://meet.google.com/abc-defg-hij",
    dial_in=("+1 555-0100 PIN 1234",),
    conference={"conferenceId": "abc-defg-hij", "createRequest": {"requestId": "r1"}},
)
L = Link(source="main@x/e1", kind=KIND_COPY, calendar_id="work@x", event_id="c1")
BLOCK = (
    "Meeting link: https://meet.google.com/abc-defg-hij\n"
    "Dial-in: +1 555-0100 PIN 1234\n"
    "Organizer: ann@acme.com\n"
    "Participants:\n"
    "- ann@acme.com (accepted)\n"
    "- steve@x (needsAction)"
)


def changed(event, **fields):
    values = {**event.__dict__, **fields}
    return Event(**values)


class Place:
    def __init__(self, calendar_id):
        self.calendar_id = calendar_id
        self.calls = []

    def __call__(self, event):
        self.calls.append(event)
        return self.calendar_id


def test_details_block_exact():
    assert details_block(E) == BLOCK


def test_details_block_empty():
    assert details_block(Event(calendar_id="main@x", id="e2")) == ""


def test_copy_body_description():
    assert copy_body(E, "work@x").description == BLOCK + "\n\nAgenda"


def test_create_rule_chosen_calendar():
    writes = plan_copies([E], {}, S, Place("work@x"))
    assert len(writes) == 1
    w = writes[0]
    assert w.op == OP_CREATE
    assert w.calendar_id == "work@x"
    assert w.event_id == ""
    assert w.source == "main@x/e1"
    assert w.reason == "new"
    assert w.body.calendar_id == "work@x"


def test_create_default_calendar():
    writes = plan_copies([E], {}, S, Place(S.default_calendar))
    assert writes[0].op == OP_CREATE
    assert writes[0].calendar_id == "home@x"


def test_place_called_once_per_create():
    place = Place("work@x")
    other = changed(E, id="e3")
    plan_copies([E, other], {}, S, place)
    assert len(place.calls) == 2


def test_copy_body_fields():
    body = copy_body(E, "work@x")
    assert body.calendar_id == "work@x"
    assert body.id == ""
    assert body.attendees == ()
    assert body.summary == "Review"
    assert body.location == "Room 4"
    assert body.start == T1
    assert body.end == T2
    assert body.attachments == ATTACH
    assert body.meeting_link == "https://meet.google.com/abc-defg-hij"
    assert body.dial_in == ("+1 555-0100 PIN 1234",)
    assert body.transparency == "opaque"
    assert body.status == "confirmed"
    assert body.visibility == "default"
    assert body.organizer_email == ""
    assert body.creator_email == ""
    assert body.recurring_event_id == ""
    assert dict(body.raw) == {}
    assert dict(body.conference) == {"conferenceId": "abc-defg-hij"}
    assert dict(body.private) == {MARKER_SOURCE: "main@x/e1", MARKER_KIND: KIND_COPY}
    assert "createRequest" in E.conference


def test_copy_body_all_day_and_recurrence():
    ev = changed(
        E,
        all_day=True,
        start=date(2026, 11, 3),
        end=date(2026, 11, 4),
        recurrence=("RRULE:FREQ=WEEKLY",),
    )
    body = copy_body(ev, "work@x")
    assert body.all_day is True
    assert body.start == date(2026, 11, 3)
    assert body.end == date(2026, 11, 4)
    assert body.recurrence == ("RRULE:FREQ=WEEKLY",)


def test_edit_updates_linked_copy():
    place = Place("home@x")
    links = {"main@x/e1": L}
    writes = plan_copies([E], links, S, place)
    assert len(writes) == 1
    w = writes[0]
    assert (w.op, w.calendar_id, w.event_id, w.reason) == (OP_UPDATE, "work@x", "c1", "changed")
    assert w.source == "main@x/e1"
    assert w.body.calendar_id == "work@x"
    assert place.calls == []
    again = plan_copies([E], links, S, place)
    assert [x.op for x in again] == [OP_UPDATE]


def test_participants_change_rewrites_block():
    ev = changed(E, attendees=(*ATTENDEES, Attendee(email="bob@acme.com", response="declined")))
    writes = plan_copies([ev], {"main@x/e1": L}, S, Place("work@x"))
    desc = writes[0].body.description
    assert writes[0].op == OP_UPDATE
    assert "- bob@acme.com (declined)" in desc
    assert desc.startswith("Meeting link: ")


def test_cancel_deletes_copy():
    writes = plan_copies([changed(E, status="cancelled")], {"main@x/e1": L}, S, Place("work@x"))
    w = writes[0]
    assert (w.op, w.calendar_id, w.event_id, w.reason) == (OP_DELETE, "work@x", "c1", "cancelled")
    assert w.body is None
    assert plan_copies([changed(E, status="cancelled")], {}, S, Place("work@x")) == []


def test_free_deletes_copy():
    ev = changed(E, transparency="transparent")
    w = plan_copies([ev], {"main@x/e1": L}, S, Place("work@x"))[0]
    assert (w.op, w.event_id, w.reason) == (OP_DELETE, "c1", "free")
    assert plan_copies([ev], {}, S, Place("work@x")) == []


def test_declined_deletes_copy():
    mine = Attendee(email="steve@x", response="declined", is_self=True)
    ev = changed(E, attendees=(ATTENDEES[0], mine))
    w = plan_copies([ev], {"main@x/e1": L}, S, Place("work@x"))[0]
    assert (w.op, w.calendar_id, w.event_id, w.reason) == (OP_DELETE, "work@x", "c1", "declined")
    assert plan_copies([ev], {}, S, Place("work@x")) == []


def test_blocked_on_main_ignored():
    blocked = Event(
        calendar_id="main@x",
        id="b1",
        summary="BLOCKED",
        private={MARKER_SOURCE: "work@x/z", MARKER_KIND: KIND_BLOCKED},
    )
    link = Link(source="main@x/b1", kind=KIND_COPY, calendar_id="work@x", event_id="x")
    assert plan_copies([blocked], {}, S, Place("work@x")) == []
    assert plan_copies([blocked], {"main@x/b1": link}, S, Place("work@x")) == []


def test_other_calendar_and_no_main_ignored():
    other = changed(E, calendar_id="work@x")
    assert plan_copies([other], {}, S, Place("work@x")) == []
    assert plan_copies([E], {}, Settings(), Place("work@x")) == []


def test_no_details():
    ev = Event(calendar_id="main@x", id="e2", description="d")
    assert copy_body(ev, "work@x").description == "d"
    assert copy_body(changed(ev, description=""), "work@x").description == ""


def test_exception_updates_instance():
    ev = Event(
        calendar_id="main@x",
        id="s1_20261103T150000Z",
        recurring_event_id="s1",
        start=T1,
        end=T2,
    )
    series = Link(source="main@x/s1", kind=KIND_COPY, calendar_id="work@x", event_id="cs")
    place = Place("home@x")
    w = plan_copies([ev], {"main@x/s1": series}, S, place)[0]
    assert (w.op, w.calendar_id, w.event_id) == (OP_UPDATE, "work@x", "cs_20261103T150000Z")
    assert w.source == "main@x/s1_20261103T150000Z"
    assert w.reason == "instance changed"
    assert w.body.calendar_id == "work@x"
    assert place.calls == []


def test_exception_cancelled_deletes_instance():
    ev = Event(
        calendar_id="main@x",
        id="s1_20261103T150000Z",
        recurring_event_id="s1",
        status="cancelled",
    )
    series = Link(source="main@x/s1", kind=KIND_COPY, calendar_id="work@x", event_id="cs")
    w = plan_copies([ev], {"main@x/s1": series}, S, Place("home@x"))[0]
    assert (w.op, w.calendar_id, w.event_id) == (OP_DELETE, "work@x", "cs_20261103T150000Z")
    assert w.reason == "cancelled"
    assert plan_copies([ev], {}, S, Place("home@x")) == []


def test_exception_own_link_first_and_create_without_links():
    ev = Event(calendar_id="main@x", id="s1_20261103T150000Z", recurring_event_id="s1")
    own = Link(
        source="main@x/s1_20261103T150000Z", kind=KIND_COPY, calendar_id="home@x", event_id="own"
    )
    series = Link(source="main@x/s1", kind=KIND_COPY, calendar_id="work@x", event_id="cs")
    links = {own.source: own, series.source: series}
    w = plan_copies([ev], links, S, Place("work@x"))[0]
    assert (w.calendar_id, w.event_id, w.reason) == ("home@x", "own", "changed")
    created = plan_copies([ev], {}, S, Place("work@x"))[0]
    assert created.op == OP_CREATE


def test_duplicate_source_last_wins_and_order_kept():
    first = changed(E, summary="old")
    second = changed(E, id="e9", summary="mid")
    last = changed(E, summary="new")
    writes = plan_copies([first, second, last], {}, S, Place("work@x"))
    assert [w.source for w in writes] == ["main@x/e9", "main@x/e1"]
    assert writes[1].body.summary == "new"
