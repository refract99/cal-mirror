from datetime import UTC, date, datetime

from cal_mirror.model import (
    KIND_BLOCKED,
    MARKER_KIND,
    MARKER_SOURCE,
    OP_CREATE,
    OP_DELETE,
    OP_UPDATE,
    Attendee,
    Event,
    Link,
    Settings,
)
from cal_mirror.plan_blocked import blocked_body, plan_blocked

S = Settings(main_calendar="main@x", secondary_calendars=("sec@x",))
T1 = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 3, 16, 0, tzinfo=UTC)
L = Link(source="sec@x/e1", kind=KIND_BLOCKED, calendar_id="main@x", event_id="m1")
LINKS = {"sec@x/e1": L}


def ev(**kw):
    base = {
        "calendar_id": "sec@x",
        "id": "e1",
        "summary": "Dentist",
        "description": "d",
        "location": "here",
        "start": T1,
        "end": T2,
    }
    base.update(kw)
    return Event(**base)


def test_create():
    writes = plan_blocked([ev()], {}, S)
    assert len(writes) == 1
    w = writes[0]
    assert w.op == OP_CREATE
    assert w.calendar_id == "main@x"
    assert w.source == "sec@x/e1"
    assert w.event_id == ""
    assert w.reason == "new"
    b = w.body
    assert b.summary == "BLOCKED"
    assert b.visibility == "private"
    assert b.transparency == "opaque"
    assert b.start == T1
    assert b.end == T2
    assert b.description == ""
    assert b.location == ""
    assert b.attendees == ()


def test_edit_is_update_and_repeatable():
    first = plan_blocked([ev(summary="Dentist 2")], LINKS, S)
    second = plan_blocked([ev(summary="Dentist 2")], LINKS, S)
    assert len(first) == 1
    assert first[0].op == OP_UPDATE
    assert first[0].event_id == "m1"
    assert first[0].reason == "changed"
    assert first == second


def test_cancel():
    cancelled = ev(status="cancelled")
    writes = plan_blocked([cancelled], LINKS, S)
    assert len(writes) == 1
    assert writes[0].op == OP_DELETE
    assert writes[0].event_id == "m1"
    assert writes[0].reason == "cancelled"
    assert writes[0].body is None
    assert plan_blocked([cancelled], {}, S) == []


def test_busy_to_free_and_free_to_busy():
    writes = plan_blocked([ev(transparency="transparent")], LINKS, S)
    assert [(w.op, w.event_id, w.reason) for w in writes] == [(OP_DELETE, "m1", "free")]
    assert plan_blocked([ev(transparency="transparent")], {}, S) == []
    assert [w.op for w in plan_blocked([ev()], {}, S)] == [OP_CREATE]


def test_declined():
    me = (Attendee(email="steve@x", response="declined", is_self=True),)
    writes = plan_blocked([ev(attendees=me)], LINKS, S)
    assert [(w.op, w.event_id, w.reason) for w in writes] == [(OP_DELETE, "m1", "declined")]
    assert plan_blocked([ev(attendees=me)], {}, S) == []


def test_unselected():
    other = ev(calendar_id="other@x")
    assert plan_blocked([other], {}, S) == []
    assert plan_blocked([other], {"other@x/e1": L}, S) == []
    assert plan_blocked([ev(calendar_id="main@x")], {}, S) == []
    assert plan_blocked([ev()], {}, Settings(secondary_calendars=("sec@x",))) == []


def test_mirror_input():
    mirror = ev(private={MARKER_SOURCE: "main@x/z"})
    assert plan_blocked([mirror], {}, S) == []
    assert plan_blocked([mirror], LINKS, S) == []


def test_all_day_and_recurrence():
    event = ev(
        start=date(2026, 11, 3),
        end=date(2026, 11, 4),
        all_day=True,
        recurrence=("RRULE:FREQ=WEEKLY",),
    )
    body = plan_blocked([event], {}, S)[0].body
    assert body.start == date(2026, 11, 3)
    assert body.end == date(2026, 11, 4)
    assert body.all_day is True
    assert body.recurrence == ("RRULE:FREQ=WEEKLY",)


def test_exception():
    series = {
        "sec@x/s1": Link(source="sec@x/s1", kind=KIND_BLOCKED, calendar_id="main@x", event_id="ms")
    }
    inst = ev(id="s1_20261103T150000Z", recurring_event_id="s1")
    w = plan_blocked([inst], series, S)[0]
    assert w.op == OP_UPDATE
    assert w.event_id == "ms_20261103T150000Z"
    assert w.source == "sec@x/s1_20261103T150000Z"
    assert w.reason == "instance changed"
    gone = ev(id="s1_20261103T150000Z", recurring_event_id="s1", status="cancelled")
    d = plan_blocked([gone], series, S)[0]
    assert d.op == OP_DELETE
    assert d.event_id == "ms_20261103T150000Z"
    assert d.reason == "cancelled"
    assert plan_blocked([gone], {}, S) == []
    assert plan_blocked([inst], {}, S)[0].op == OP_CREATE


def test_exception_own_link_first():
    key = "sec@x/s1_x"
    links = {
        key: Link(source=key, kind=KIND_BLOCKED, calendar_id="main@x", event_id="own"),
        "sec@x/s1": Link(source="sec@x/s1", kind=KIND_BLOCKED, calendar_id="main@x", event_id="ms"),
    }
    w = plan_blocked([ev(id="s1_x", recurring_event_id="s1")], links, S)[0]
    assert (w.op, w.event_id, w.reason) == (OP_UPDATE, "own", "changed")


def test_order_and_repeats():
    a = ev(id="a")
    b = ev(id="b")
    a2 = ev(id="a", summary="later")
    writes = plan_blocked([a, b, a2], {}, S)
    assert [w.source for w in writes] == ["sec@x/b", "sec@x/a"]


def test_blocked_body_fields():
    event = ev(
        attendees=(Attendee(email="g@x"),),
        meeting_link="http://m",
        dial_in=("1",),
        organizer_email="o@x",
        creator_email="c@x",
        conference={"a": 1},
        raw={"a": 1},
        status="tentative",
        recurring_event_id="r",
    )
    b = blocked_body(event, "main@x")
    assert b.calendar_id == "main@x"
    assert b.id == ""
    assert b.summary == "BLOCKED"
    assert b.description == ""
    assert b.location == ""
    assert b.start == T1
    assert b.end == T2
    assert b.all_day is False
    assert b.recurrence == ()
    assert b.transparency == "opaque"
    assert b.visibility == "private"
    assert b.attendees == ()
    assert b.attachments == ()
    assert b.meeting_link == ""
    assert b.dial_in == ()
    assert b.organizer_email == ""
    assert b.creator_email == ""
    assert not b.conference
    assert not b.raw
    assert b.status == "confirmed"
    assert b.recurring_event_id == ""
    assert b.private == {MARKER_SOURCE: "sec@x/e1", MARKER_KIND: KIND_BLOCKED}


def test_no_new_blocked_event_for_a_past_event():
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    past = ev(start=now - timedelta(days=2), end=now - timedelta(days=2) + timedelta(hours=1))
    future = ev(id="e2", start=now + timedelta(days=1), end=now + timedelta(days=1, hours=1))
    writes = plan_blocked([past, future], {}, S, now)
    from cal_mirror.model import source_key

    assert [w.source for w in writes] == [source_key(future)]
