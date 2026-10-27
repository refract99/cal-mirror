from datetime import UTC, date, datetime, timedelta

from cal_mirror.cleanup import (
    MAX_OCCURRENCES,
    CleanupPlan,
    plan_cleanup,
    series_ends_before,
    verify_archive,
)
from cal_mirror.model import KIND_BLOCKED, Event, Link

CUTOFF = date(2024, 7, 1)
OWNER = "steve@x.com"


def at(day: int, hour: int = 0, minute: int = 0, month: int = 6) -> datetime:
    return datetime(2024, month, day, hour, minute, tzinfo=UTC)


def ev(id: str = "e1", calendar_id: str = "main", **kwargs) -> Event:
    kwargs.setdefault("organizer_email", OWNER)
    return Event(calendar_id=calendar_id, id=id, **kwargs)


def plan(*events: Event, links=None, owner: str = OWNER) -> CleanupPlan:
    grouped: dict[str, list[Event]] = {}
    for event in events:
        grouped.setdefault(event.calendar_id, []).append(event)
    return plan_cleanup(grouped, CUTOFF, owner, links or {})


def pairs(result: CleanupPlan) -> list[tuple[str, str]]:
    return [(w.calendar_id, w.event_id) for w in result.deletes]


def master() -> Event:
    start = datetime(2024, 1, 1, 15, tzinfo=UTC)
    return ev(
        "s1",
        start=start,
        end=start + timedelta(hours=1),
        recurrence=("RRULE:FREQ=WEEKLY;UNTIL=20240601T150000Z",),
    )


def exception_at(
    start: datetime | None,
    end: datetime | None,
    calendar_id: str = "main",
    **kwargs,
) -> Event:
    return ev(
        "s1_20240527T150000Z",
        calendar_id=calendar_id,
        start=start,
        end=end,
        recurring_event_id="s1",
        **kwargs,
    )


def daily_from_1990(count: int, **kwargs) -> Event:
    start = datetime(1990, 1, 1, 15, tzinfo=UTC)
    return ev(
        "s1",
        start=start,
        end=start + timedelta(hours=1),
        recurrence=(f"RRULE:FREQ=DAILY;COUNT={count}",),
        **kwargs,
    )


def test_exception_moved_past_cutoff_keeps_series():
    later = exception_at(datetime(2024, 8, 5, 15, tzinfo=UTC), datetime(2024, 8, 5, 16, tzinfo=UTC))
    result = plan(master(), later)
    assert result.deletes == () and result.kept_unbounded == 0


def test_exception_before_cutoff_series_deleted():
    earlier = exception_at(
        datetime(2024, 6, 30, 15, tzinfo=UTC), datetime(2024, 6, 30, 16, tzinfo=UTC)
    )
    assert pairs(plan(master(), earlier)) == [("main", "s1")]


def test_exception_ending_past_cutoff_keeps_series():
    crossing = exception_at(
        datetime(2024, 6, 30, 23, 30, tzinfo=UTC), datetime(2024, 7, 1, 0, 30, tzinfo=UTC)
    )
    assert plan(master(), crossing).deletes == ()


def test_cancelled_exception_past_cutoff_series_deleted():
    cancelled = exception_at(
        datetime(2024, 8, 5, 15, tzinfo=UTC),
        datetime(2024, 8, 5, 16, tzinfo=UTC),
        status="cancelled",
    )
    assert pairs(plan(master(), cancelled)) == [("main", "s1")]


def test_exception_without_start_keeps_series():
    assert plan(master(), exception_at(None, None)).deletes == ()


def test_exception_on_another_calendar_does_not_keep_series():
    other = exception_at(
        datetime(2024, 8, 5, 15, tzinfo=UTC),
        datetime(2024, 8, 5, 16, tzinfo=UTC),
        calendar_id="other",
    )
    assert pairs(plan(master(), other)) == [("main", "s1")]


def test_daily_count_at_bound_deleted():
    event = daily_from_1990(5000)
    result = plan(event)
    assert series_ends_before(event, CUTOFF) and pairs(result) == [("main", "s1")]


def test_daily_count_past_bound_kept_unbounded():
    event = daily_from_1990(5001)
    result = plan(event)
    assert (
        not series_ends_before(event, CUTOFF)
        and result.deletes == ()
        and result.kept_unbounded == 1
    )


def test_minutely_huge_count_kept_unbounded():
    start = datetime(1900, 1, 1, 15, tzinfo=UTC)
    event = ev(
        "s1",
        start=start,
        end=start + timedelta(hours=1),
        recurrence=("RRULE:FREQ=MINUTELY;COUNT=2000000",),
    )
    result = plan(event)
    assert (
        not series_ends_before(event, CUTOFF)
        and result.deletes == ()
        and result.kept_unbounded == 1
    )


def test_secondly_until_past_bound_kept_unbounded():
    start = datetime(1990, 1, 1, tzinfo=UTC)
    event = ev(
        "s1",
        start=start,
        end=start + timedelta(hours=1),
        recurrence=("RRULE:FREQ=SECONDLY;UNTIL=20240101T000000Z",),
    )
    result = plan(event)
    assert (
        not series_ends_before(event, CUTOFF)
        and result.deletes == ()
        and result.kept_unbounded == 1
    )


def test_open_weekly_series_not_counted_unbounded():
    start = datetime(2024, 1, 1, 15, tzinfo=UTC)
    event = ev(
        "s1",
        start=start,
        end=start + timedelta(hours=1),
        recurrence=("RRULE:FREQ=WEEKLY",),
    )
    result = plan(event)
    assert result.deletes == () and result.kept_unbounded == 0


def test_invited_unbounded_master_not_counted():
    event = daily_from_1990(5001, organizer_email="boss@corp.com", creator_email="boss@corp.com")
    assert plan(event).kept_unbounded == 0


def test_verify_archive_rejects_missing_mirror_source():
    source = ev("e1", "sec", start=at(1), end=at(2))
    link = Link(source="main/other", kind=KIND_BLOCKED, calendar_id="main", event_id="m1")
    result = plan(source, links={"sec/e1": link})
    assert not verify_archive(result, {"sec/e1"})


def test_verify_archive_accepts_source_and_mirror_target():
    source = ev("e1", "sec", start=at(1), end=at(2))
    link = Link(source="main/other", kind=KIND_BLOCKED, calendar_id="main", event_id="m1")
    result = plan(source, links={"sec/e1": link})
    assert verify_archive(result, {"sec/e1", "main/m1"})


def test_verify_archive_two_sources_missing_one():
    result = plan(ev("e1", "sec", start=at(1), end=at(2)), ev("e2", "sec", start=at(1), end=at(2)))
    assert not verify_archive(result, {"sec/e1"})


def test_verify_archive_two_sources_with_extra():
    result = plan(ev("e1", "sec", start=at(1), end=at(2)), ev("e2", "sec", start=at(1), end=at(2)))
    assert verify_archive(result, {"sec/e1", "sec/e2", "extra"})


def test_verify_archive_empty_plan():
    assert verify_archive(plan(), set())


def test_max_occurrences_is_5000():
    assert MAX_OCCURRENCES == 5000


def test_cleanup_plan_kept_unbounded_defaults_to_zero():
    assert CleanupPlan(deletes=(), counts={}, sources=frozenset()).kept_unbounded == 0


def test_counts_sum_ignores_unbounded_kept_series():
    single = ev(start=at(1), end=at(2))
    result = plan(single, daily_from_1990(5001))
    assert sum(result.counts.values()) == len(result.deletes) and result.kept_unbounded == 1
