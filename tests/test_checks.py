from dataclasses import replace
from datetime import UTC, date, datetime, time
from typing import Any

from cal_mirror.checks import (
    has_call_link,
    meeting_failures,
    notice_text,
    plan_notices,
    summary_text,
)
from cal_mirror.model import (
    CHECK_HOURS,
    CHECK_NO_LINK,
    Attendee,
    Event,
    Notice,
    Settings,
    source_key,
)

S = Settings(
    main_calendar="main@x",
    owner_email="steve@x",
    allowed_hours={1: (time(8), time(17))},
)
G = (Attendee(email="a@other.com"),)
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
FP = "2026-11-03T13:30:00+00:00|2026-11-03T14:00:00+00:00"
MOVED_FP = "2026-11-03T13:00:00+00:00|2026-11-03T13:30:00+00:00"


def M(start: datetime | date, end: datetime | date | None, **kw: Any) -> Event:
    fields: dict[str, Any] = {
        "calendar_id": "main@x",
        "id": "m1",
        "start": start,
        "end": end,
        "attendees": G,
        "meeting_link": "https://meet.google.com/abc-defg-hij",
    }
    fields.update(kw)
    return Event(**fields)


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def _bare(**kw: Any) -> Event:
    return Event(calendar_id="main@x", id="m1", meeting_link="", **kw)


def test_has_call_link_zoom_location() -> None:
    event = _bare(location="https://us02web.zoom.us/j/123")
    assert has_call_link(event) is True


def test_has_call_link_teams_live_description() -> None:
    event = _bare(description='<a href="https://teams.live.com/meet/9">join</a>')
    assert has_call_link(event) is True


def test_has_call_link_bare_meet_host() -> None:
    event = _bare(location="meet.google.com/abc-defg-hij")
    assert has_call_link(event) is True


def test_has_call_link_video_conference() -> None:
    event = _bare(conference={"entryPoints": [{"entryPointType": "video", "uri": "https://x"}]})
    assert has_call_link(event) is True


def test_has_call_link_phone_conference_only() -> None:
    event = _bare(conference={"entryPoints": [{"entryPointType": "phone", "uri": "tel:+1"}]})
    assert has_call_link(event) is False


def test_has_call_link_zoom_lookalike_host() -> None:
    event = _bare(location="zoom.us.example.com")
    assert has_call_link(event) is False


def test_has_call_link_notzoom() -> None:
    event = _bare(location="notzoom.us")
    assert has_call_link(event) is False


def test_has_call_link_room_only() -> None:
    event = _bare(location="Room 4")
    assert has_call_link(event) is False


def test_hours_before_open_fails() -> None:
    event = M(_utc(2026, 11, 3, 13, 30), _utc(2026, 11, 3, 14, 0))
    assert meeting_failures(event, S) == (CHECK_HOURS,)


def test_hours_during_dst_passes() -> None:
    event = M(_utc(2026, 10, 27, 13, 30), _utc(2026, 10, 27, 14, 0))
    assert meeting_failures(event, S) == ()


def test_hours_inclusive_bounds_pass() -> None:
    event = M(_utc(2026, 11, 3, 14, 0), _utc(2026, 11, 3, 23, 0))
    assert meeting_failures(event, S) == ()


def test_hours_end_one_minute_late_fails() -> None:
    event = M(_utc(2026, 11, 3, 14, 0), _utc(2026, 11, 3, 23, 1))
    assert meeting_failures(event, S) == (CHECK_HOURS,)


def test_hours_missing_weekday_fails() -> None:
    event = M(_utc(2026, 11, 7, 15, 0), _utc(2026, 11, 7, 16, 0))
    assert meeting_failures(event, S) == (CHECK_HOURS,)


def test_hours_cross_midnight_fails() -> None:
    settings = replace(S, allowed_hours={1: (time(0), time(23, 59))})
    event = M(_utc(2026, 11, 4, 4, 0), _utc(2026, 11, 4, 6, 30))
    assert meeting_failures(event, settings) == (CHECK_HOURS,)


def test_no_link_in_hours() -> None:
    event = M(_utc(2026, 11, 3, 14, 0), _utc(2026, 11, 3, 15, 0), meeting_link="")
    assert meeting_failures(event, S) == (CHECK_NO_LINK,)


def test_no_link_and_outside_hours() -> None:
    event = M(_utc(2026, 11, 3, 13, 30), _utc(2026, 11, 3, 14, 0), meeting_link="")
    assert meeting_failures(event, S) == (CHECK_NO_LINK, CHECK_HOURS)


def test_check_no_link_off_drops_that_check() -> None:
    event = M(_utc(2026, 11, 3, 13, 30), _utc(2026, 11, 3, 14, 0), meeting_link="")
    settings = replace(S, check_no_link=False)
    assert meeting_failures(event, settings) == (CHECK_HOURS,)


def test_check_hours_off_drops_that_check() -> None:
    event = M(_utc(2026, 11, 3, 13, 30), _utc(2026, 11, 3, 14, 0), meeting_link="")
    settings = replace(S, check_hours=False)
    assert meeting_failures(event, settings) == (CHECK_NO_LINK,)


def test_all_day_is_not_a_meeting() -> None:
    event = M(date(2026, 11, 3), None, all_day=True, meeting_link="")
    assert meeting_failures(event, S) == ()


def test_only_owner_attendee_is_not_a_meeting() -> None:
    event = M(
        _utc(2026, 11, 3, 13, 30),
        _utc(2026, 11, 3, 14, 0),
        meeting_link="",
        attendees=(Attendee(email="steve@x"),),
    )
    assert meeting_failures(event, S) == ()


def test_only_self_attendee_is_not_a_meeting() -> None:
    event = M(
        _utc(2026, 11, 3, 13, 30),
        _utc(2026, 11, 3, 14, 0),
        meeting_link="",
        attendees=(Attendee(email="a@other.com", is_self=True),),
    )
    assert meeting_failures(event, S) == ()


def test_transparent_is_not_a_meeting() -> None:
    event = M(
        _utc(2026, 11, 3, 13, 30),
        _utc(2026, 11, 3, 14, 0),
        meeting_link="",
        transparency="transparent",
    )
    assert meeting_failures(event, S) == ()


def _failing() -> Event:
    return M(_utc(2026, 11, 3, 13, 30), _utc(2026, 11, 3, 14, 0), meeting_link="")


def test_plan_empty_state_notices_both_checks() -> None:
    event = _failing()
    notices, state = plan_notices([event], {}, S, NOW)
    assert notices == [
        Notice(source_key(event), CHECK_NO_LINK, event.start),
        Notice(source_key(event), CHECK_HOURS, event.start),
    ]
    assert state == {"main@x/m1": {CHECK_NO_LINK: FP, CHECK_HOURS: FP}}


def test_plan_same_fingerprint_sends_nothing() -> None:
    event = _failing()
    prior = {"main@x/m1": {CHECK_NO_LINK: FP, CHECK_HOURS: FP}}
    notices, state = plan_notices([event], prior, S, NOW)
    assert notices == []
    assert state == prior


def test_plan_moved_meeting_notices_again() -> None:
    event = M(_utc(2026, 11, 3, 13, 0), _utc(2026, 11, 3, 13, 30), meeting_link="")
    prior = {"main@x/m1": {CHECK_NO_LINK: FP, CHECK_HOURS: FP}}
    notices, state = plan_notices([event], prior, S, NOW)
    assert notices == [
        Notice(source_key(event), CHECK_NO_LINK, event.start),
        Notice(source_key(event), CHECK_HOURS, event.start),
    ]
    assert state == {"main@x/m1": {CHECK_NO_LINK: MOVED_FP, CHECK_HOURS: MOVED_FP}}


def test_plan_link_and_hours_fixed_clears_state() -> None:
    event = M(_utc(2026, 11, 3, 14, 0), _utc(2026, 11, 3, 15, 0))
    prior = {"main@x/m1": {CHECK_NO_LINK: FP, CHECK_HOURS: FP}}
    notices, state = plan_notices([event], prior, S, NOW)
    assert notices == []
    assert state == {"main@x/m1": {}}


def test_plan_cancelled_clears_state() -> None:
    event = M(
        _utc(2026, 11, 3, 13, 30),
        _utc(2026, 11, 3, 14, 0),
        meeting_link="",
        status="cancelled",
    )
    prior = {"main@x/m1": {CHECK_NO_LINK: FP, CHECK_HOURS: FP}}
    notices, state = plan_notices([event], prior, S, NOW)
    assert notices == []
    assert state == {"main@x/m1": {}}


def test_plan_past_meeting_sends_nothing() -> None:
    event = M(_utc(2026, 10, 23, 15, 0), _utc(2026, 10, 23, 16, 0), meeting_link="")
    notices, state = plan_notices([event], {}, S, NOW)
    assert notices == []
    assert state == {"main@x/m1": {}}


def test_plan_other_calendar_is_absent() -> None:
    event = M(
        _utc(2026, 11, 3, 13, 30),
        _utc(2026, 11, 3, 14, 0),
        meeting_link="",
        calendar_id="sec@x",
    )
    notices, state = plan_notices([event], {}, S, NOW)
    assert notices == []
    assert state == {}
    assert "sec@x/m1" not in state


def test_notice_text_no_call_link() -> None:
    notice = Notice("main@x/m1", CHECK_NO_LINK, _utc(2026, 11, 3, 13, 30))
    assert notice_text(notice, S) == "Tue 2026-11-03 7:30 AM CST meeting: no call link"


def test_notice_text_outside_hours_noon() -> None:
    notice = Notice("main@x/m1", CHECK_HOURS, _utc(2026, 11, 3, 18, 0))
    assert notice_text(notice, S) == "Tue 2026-11-03 12:00 PM CST meeting: outside hours"


def test_notice_text_dst_abbreviation() -> None:
    notice = Notice("main@x/m1", CHECK_NO_LINK, _utc(2026, 10, 27, 14, 0))
    assert notice_text(notice, S) == "Tue 2026-10-27 9:00 AM CDT meeting: no call link"


def test_notice_text_midnight() -> None:
    notice = Notice("main@x/m1", CHECK_HOURS, _utc(2026, 11, 4, 6, 5))
    assert notice_text(notice, S) == "Wed 2026-11-04 12:05 AM CST meeting: outside hours"


def test_notice_text_never_includes_event_title() -> None:
    notice = Notice("main@x/m1", CHECK_NO_LINK, _utc(2026, 11, 3, 13, 30))
    text = notice_text(notice, S)
    assert text == "Tue 2026-11-03 7:30 AM CST meeting: no call link"
    assert "Weekly sync" not in text


def test_summary_three_meetings() -> None:
    start = _utc(2026, 11, 3, 13, 30)
    notices = [
        Notice("a", CHECK_NO_LINK, start),
        Notice("a", CHECK_HOURS, start),
        Notice("b", CHECK_NO_LINK, start),
        Notice("c", CHECK_HOURS, start),
    ]
    assert summary_text(notices, S) == (
        "cal-mirror first start: 3 future meetings fail checks (2 no call link, 2 outside hours)"
    )


def test_summary_one_meeting() -> None:
    notices = [Notice("a", CHECK_NO_LINK, _utc(2026, 11, 3, 13, 30))]
    assert summary_text(notices, S) == (
        "cal-mirror first start: 1 future meeting fails checks (1 no call link)"
    )


def test_summary_none() -> None:
    assert summary_text([], S) == "cal-mirror first start: no future meetings fail checks"
