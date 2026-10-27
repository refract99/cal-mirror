from datetime import UTC, date, datetime

from cal_mirror.model import KIND_BLOCKED, KIND_COPY, Event, Link, PlannedWrite
from cal_mirror.plan_move import count_moves, is_future, plan_moves

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
LINK = Link(source="main@x/e1", kind=KIND_COPY, calendar_id="old@x", event_id="c1")
MOVE = PlannedWrite(
    op="move", calendar_id="old@x", event_id="c1", destination="new@x", source="main@x/e1"
)


def place(event: Event) -> str:
    return "new@x"


def timed(start: datetime, end: datetime, **kwargs) -> Event:
    return Event(calendar_id="main@x", id="e1", start=start, end=end, **kwargs)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def future_timed() -> Event:
    return timed(at(25, 9), at(25, 10))


def weekly(*rules: str) -> Event:
    return timed(
        datetime(2026, 8, 3, 9, tzinfo=UTC), datetime(2026, 8, 3, 10, tzinfo=UTC), recurrence=rules
    )


def weekly_all_day(*rules: str) -> Event:
    return Event(
        calendar_id="main@x",
        id="e1",
        start=date(2026, 8, 3),
        end=date(2026, 8, 4),
        all_day=True,
        recurrence=rules,
    )


def test_future_timed_moves():
    assert plan_moves([(LINK, future_timed())], NOW, place) == [MOVE]


def test_past_timed_stays():
    assert plan_moves([(LINK, timed(at(20, 9), at(20, 10)))], NOW, place) == []


def test_in_progress_timed_moves():
    assert plan_moves([(LINK, timed(at(24, 11, 30), at(24, 12, 30)))], NOW, place) == [MOVE]


def test_all_day_covering_today_moves():
    event = Event(
        calendar_id="main@x",
        id="e1",
        start=date(2026, 10, 24),
        end=date(2026, 10, 25),
        all_day=True,
    )
    assert plan_moves([(LINK, event)], NOW, place) == [MOVE]


def test_all_day_yesterday_stays():
    event = Event(
        calendar_id="main@x",
        id="e1",
        start=date(2026, 10, 23),
        end=date(2026, 10, 24),
        all_day=True,
    )
    assert plan_moves([(LINK, event)], NOW, place) == []


def test_same_calendar_stays():
    def same(event: Event) -> str:
        return "old@x"

    assert plan_moves([(LINK, future_timed())], NOW, same) == []


def test_empty_destination_stays():
    def nowhere(event: Event) -> str:
        return ""

    assert plan_moves([(LINK, future_timed())], NOW, nowhere) == []


def test_blocked_link_skipped_without_calling_place():
    calls = []

    def spy(event: Event) -> str:
        calls.append(event)
        return "new@x"

    blocked = Link(source="main@x/e1", kind=KIND_BLOCKED, calendar_id="old@x", event_id="c1")
    assert plan_moves([(blocked, future_timed())], NOW, spy) == []
    assert calls == []


def test_series_ended_by_until_stays():
    event = weekly("RRULE:FREQ=WEEKLY;UNTIL=20260921T235959Z")
    assert plan_moves([(LINK, event)], NOW, place) == []


def test_endless_series_moves():
    assert plan_moves([(LINK, weekly("RRULE:FREQ=WEEKLY"))], NOW, place) == [MOVE]


def test_series_ended_by_count_stays():
    assert plan_moves([(LINK, weekly("RRULE:FREQ=WEEKLY;COUNT=3"))], NOW, place) == []


def test_all_day_series_with_date_until_stays():
    event = weekly_all_day("RRULE:FREQ=WEEKLY;UNTIL=20260921")
    assert plan_moves([(LINK, event)], NOW, place) == []


def test_all_day_endless_series_moves():
    assert plan_moves([(LINK, weekly_all_day("RRULE:FREQ=WEEKLY"))], NOW, place) == [MOVE]


def test_unreadable_recurrence_stays():
    assert plan_moves([(LINK, weekly("RRULE:FREQ=NONSENSE"))], NOW, place) == []


def test_exception_instance_skipped():
    event = timed(at(25, 9), at(25, 10), recurring_event_id="e0")
    assert plan_moves([(LINK, event)], NOW, place) == []


def test_cancelled_source_skipped():
    event = timed(at(25, 9), at(25, 10), status="cancelled")
    assert plan_moves([(LINK, event)], NOW, place) == []


def mixed() -> list[tuple[Link, Event]]:
    def pair(n: int, event: Event) -> tuple[Link, Event]:
        link = Link(source=f"main@x/e{n}", kind=KIND_COPY, calendar_id="old@x", event_id=f"c{n}")
        return link, event

    return [
        pair(1, future_timed()),
        pair(2, timed(at(20, 9), at(20, 10))),
        pair(3, timed(at(26, 9), at(26, 10))),
    ]


def test_two_future_one_past_in_input_order():
    writes = plan_moves(mixed(), NOW, place)
    assert [w.event_id for w in writes] == ["c1", "c3"]


def test_count_moves_matches_plan_moves():
    assert count_moves(mixed(), NOW, place) == 2
    assert count_moves(mixed(), NOW, place) == len(plan_moves(mixed(), NOW, place))


def test_is_future_timed():
    assert is_future(future_timed(), NOW)
    assert not is_future(timed(at(20, 9), at(20, 10)), NOW)


def test_is_future_all_day():
    today = Event(calendar_id="main@x", id="e1", start=date(2026, 10, 24), end=date(2026, 10, 25))
    past = Event(calendar_id="main@x", id="e1", start=date(2026, 10, 23), end=date(2026, 10, 24))
    assert is_future(today, NOW)
    assert not is_future(past, NOW)


def test_is_future_without_start():
    assert not is_future(Event(calendar_id="main@x", id="e1"), NOW)


def test_is_future_endless_series():
    assert is_future(weekly("RRULE:FREQ=WEEKLY"), NOW)
