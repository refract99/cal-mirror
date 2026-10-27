import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from cal_mirror.model import (
    BLOCKED_TITLE,
    CHECK_HOURS,
    CHECK_NO_LINK,
    FAIL_API,
    FAIL_ARCHIVE,
    FAIL_DB,
    FAIL_TOKEN,
    KIND_BLOCKED,
    KIND_COPY,
    MARKER_KIND,
    MARKER_SOURCE,
    OP_CREATE,
    OP_DELETE,
    OP_MOVE,
    OP_UPDATE,
    REQ_ARCHIVE,
    REQ_CANCELLED,
    REQ_CONFIRMED,
    REQ_DONE,
    REQ_FAILED,
    REQ_PENDING,
    REQ_READY,
    REQ_RULES,
    RULE_GUEST_DOMAIN,
    RULE_ORGANIZER,
    RULE_TITLE_WORD,
    Attendee,
    Event,
    Settings,
    declined_by_self,
    is_busy,
    is_meeting,
    is_mirror,
    source_key,
)
from cal_mirror.ports import (
    CalendarAuthError,
    CalendarError,
    FixedClock,
    SyncTokenExpired,
)
from cal_mirror.web.deps import SESSION_COOKIE, current_user, get_store, session_hash, templates


def test_source_key() -> None:
    assert source_key(Event(calendar_id="sec@x", id="e1")) == "sec@x/e1"


def test_is_busy() -> None:
    assert is_busy(Event(calendar_id="c", id="e")) is True
    assert is_busy(Event(calendar_id="c", id="e", transparency="transparent")) is False
    assert is_busy(Event(calendar_id="c", id="e", status="cancelled")) is False


def test_declined_by_self() -> None:
    declined = Event(
        calendar_id="c",
        id="e",
        attendees=(Attendee(email="steve@x", response="declined", is_self=True),),
    )
    other = Event(
        calendar_id="c",
        id="e",
        attendees=(Attendee(email="a@other.com", response="declined"),),
    )
    accepted = Event(
        calendar_id="c",
        id="e",
        attendees=(Attendee(email="steve@x", response="accepted", is_self=True),),
    )
    assert declined_by_self(declined) is True
    assert declined_by_self(other) is False
    assert declined_by_self(accepted) is False
    assert declined_by_self(Event(calendar_id="c", id="e")) is False


def test_is_meeting() -> None:
    owner = "steve@x"
    guest = Attendee(email="a@other.com")
    timed = Event(calendar_id="c", id="e", attendees=(guest,))
    all_day = Event(calendar_id="c", id="e", all_day=True, attendees=(guest,))
    free = Event(calendar_id="c", id="e", transparency="transparent", attendees=(guest,))
    only_owner = Event(
        calendar_id="c",
        id="e",
        attendees=(Attendee(email="steve@x"), Attendee(email="other@x", is_self=True)),
    )
    assert is_meeting(timed, owner) is True
    assert is_meeting(all_day, owner) is False
    assert is_meeting(free, owner) is False
    assert is_meeting(only_owner, owner) is False


def test_is_mirror() -> None:
    marked = Event(calendar_id="c", id="e", private={MARKER_SOURCE: "c/e"})
    assert is_mirror(marked) is True
    assert is_mirror(Event(calendar_id="c", id="e")) is False


def test_hash_event_and_settings() -> None:
    left = Event(calendar_id="c", id="e", raw={"left": 1})
    right = Event(calendar_id="c", id="e", raw={"right": 2})
    assert left == right
    assert hash(left) == hash(right)
    assert hash(Settings()) == hash(Settings())


def test_current_user_missing_raises_signin() -> None:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    raised = False
    try:
        current_user(request)
    except HTTPException as exc:
        raised = True
        assert exc.status_code == 303
        assert exc.headers is not None
        assert exc.headers["Location"] == "/signin"
    assert raised is True


def test_session_hash() -> None:
    assert session_hash("abc") == hashlib.sha256(b"abc").hexdigest()


def test_fixed_clock_advance() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FixedClock(start)
    assert clock.now() == start
    clock.advance(60)
    assert clock.now() == start + timedelta(seconds=60)


def test_constant_values() -> None:
    assert MARKER_SOURCE == "cal_mirror_source"
    assert MARKER_KIND == "cal_mirror_kind"
    assert KIND_BLOCKED == "blocked"
    assert KIND_COPY == "copy"
    assert BLOCKED_TITLE == "BLOCKED"
    assert OP_CREATE == "create"
    assert OP_UPDATE == "update"
    assert OP_DELETE == "delete"
    assert OP_MOVE == "move"
    assert RULE_GUEST_DOMAIN == "guest_domain"
    assert RULE_ORGANIZER == "organizer"
    assert RULE_TITLE_WORD == "title_word"
    assert CHECK_NO_LINK == "no_call_link"
    assert CHECK_HOURS == "outside_hours"
    assert FAIL_TOKEN == "token"
    assert FAIL_API == "google_api"
    assert FAIL_DB == "database"
    assert FAIL_ARCHIVE == "archive"
    assert REQ_ARCHIVE == "archive"
    assert REQ_RULES == "rules"
    assert REQ_PENDING == "pending"
    assert REQ_READY == "ready"
    assert REQ_CONFIRMED == "confirmed"
    assert REQ_DONE == "done"
    assert REQ_FAILED == "failed"
    assert REQ_CANCELLED == "cancelled"
    assert SESSION_COOKIE == "cal_mirror_session"


def test_calendar_error_subclasses() -> None:
    assert issubclass(SyncTokenExpired, CalendarError)
    assert issubclass(CalendarAuthError, CalendarError)


def test_get_store_dependency() -> None:
    app = FastAPI()
    sentinel = object()
    app.state.store = sentinel

    @app.get("/store")
    def read_store(store: object = Depends(get_store)) -> dict[str, bool]:
        return {"same": store is sentinel}

    response = TestClient(app).get("/store")
    assert response.status_code == 200
    assert response.json() == {"same": True}


def test_base_template_contains_status() -> None:
    html = templates.get_template("base.html").render()
    assert "/status" in html
