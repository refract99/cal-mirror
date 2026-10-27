from datetime import UTC, date, datetime, timedelta

from cal_mirror.cleanup import CleanupPlan, plan_cleanup, series_ends_before, verify_archive
from cal_mirror.model import KIND_BLOCKED, MARKER_SOURCE, OP_DELETE, Event, Link

CUTOFF = date(2024, 7, 1)
OWNER = "steve@x.com"


def at(day: int, hour: int = 0, minute: int = 0, month: int = 6) -> datetime:
    return datetime(2024, month, day, hour, minute, tzinfo=UTC)


def ev(id: str = "e1", calendar_id: str = "main", **kwargs) -> Event:
    kwargs.setdefault("organizer_email", OWNER)
    return Event(calendar_id=calendar_id, id=id, **kwargs)


def timed(start: datetime, end: datetime, **kwargs) -> Event:
    return ev(start=start, end=end, **kwargs)


def plan(*events: Event, links=None, owner: str = OWNER) -> CleanupPlan:
    grouped: dict[str, list[Event]] = {}
    for event in events:
        grouped.setdefault(event.calendar_id, []).append(event)
    return plan_cleanup(grouped, CUTOFF, owner, links or {})


def pairs(result: CleanupPlan) -> list[tuple[str, str]]:
    return [(w.calendar_id, w.event_id) for w in result.deletes]


def series(recurrence: tuple[str, ...], start=None, end=None, **kwargs) -> Event:
    start = start or at(1, 15, month=1)
    end = end or start + timedelta(hours=1)
    return ev("s1", start=start, end=end, recurrence=recurrence, **kwargs)


def test_timed_before_cutoff_deleted():
    assert pairs(plan(timed(at(30, 22), at(30, 23)))) == [("main", "e1")]


def test_timed_crossing_cutoff_kept():
    assert pairs(plan(timed(at(30, 23, 30), at(1, 0, 30, month=7)))) == []


def test_timed_ending_exactly_at_cutoff_deleted():
    assert pairs(plan(timed(at(30, 23), at(1, 0, month=7)))) == [("main", "e1")]


def test_all_day_before_cutoff_deleted():
    event = ev(start=date(2024, 6, 30), end=date(2024, 7, 1), all_day=True)
    assert pairs(plan(event)) == [("main", "e1")]


def test_all_day_on_cutoff_kept():
    event = ev(start=date(2024, 7, 1), end=date(2024, 7, 2), all_day=True)
    assert pairs(plan(event)) == []


def test_owner_match_ignores_case():
    assert pairs(plan(timed(at(1), at(2), organizer_email="Steve@X.com"))) == [("main", "e1")]


def test_invited_event_kept():
    event = timed(at(1), at(2), organizer_email="boss@corp.com", creator_email="boss@corp.com")
    result = plan(event)
    assert pairs(result) == [] and result.sources == frozenset()


def test_group_organizer_with_owner_creator_deleted():
    event = timed(
        at(1), at(2), organizer_email="sec@group.calendar.google.com", creator_email="steve@x.com"
    )
    assert pairs(plan(event)) == [("main", "e1")]


def test_mirror_event_kept():
    event = timed(at(1), at(2), creator_email=OWNER, private={MARKER_SOURCE: "main/e9"})
    assert pairs(plan(event)) == []


def test_cancelled_event_kept():
    assert pairs(plan(timed(at(1), at(2), status="cancelled"))) == []


def test_empty_owner_gives_empty_plan():
    result = plan(timed(at(1), at(2), organizer_email=""), owner="")
    assert result.deletes == () and result.counts == {} and result.sources == frozenset()


def test_series_with_until_deleted():
    master = series(("RRULE:FREQ=WEEKLY;UNTIL=20240601T150000Z",))
    assert series_ends_before(master, CUTOFF) and pairs(plan(master)) == [("main", "s1")]


def test_endless_series_kept():
    master = series(("RRULE:FREQ=WEEKLY",))
    assert not series_ends_before(master, CUTOFF) and pairs(plan(master)) == []


def test_count_series_running_past_cutoff_kept():
    master = series(("RRULE:FREQ=DAILY;COUNT=3",), start=at(29, 15))
    assert not series_ends_before(master, CUTOFF)


def test_exdate_removes_last_occurrence():
    master = series(("RRULE:FREQ=DAILY;COUNT=3", "EXDATE:20240701T150000Z"), start=at(29, 15))
    assert series_ends_before(master, CUTOFF)


def test_series_last_occurrence_runs_past_cutoff():
    master = series(("RRULE:FREQ=DAILY;COUNT=2",), start=at(29, 23), end=at(30, 1))
    assert not series_ends_before(master, CUTOFF)


def all_day_series(rule: str) -> Event:
    return series((rule,), start=date(2024, 1, 1), end=date(2024, 1, 2), all_day=True)


def test_all_day_series_until_before_cutoff():
    assert series_ends_before(all_day_series("RRULE:FREQ=WEEKLY;UNTIL=20240624"), CUTOFF)


def test_all_day_series_until_on_cutoff_kept():
    assert not series_ends_before(all_day_series("RRULE:FREQ=WEEKLY;UNTIL=20240701"), CUTOFF)


def test_all_day_series_mismatched_until_kept():
    assert not series_ends_before(
        all_day_series("RRULE:FREQ=WEEKLY;UNTIL=20240601T000000Z"), CUTOFF
    )


def exception(master_id: str = "s1") -> Event:
    return ev(
        f"{master_id}_20240108T150000Z",
        start=at(8, 15, month=1),
        end=at(8, 16, month=1),
        recurring_event_id=master_id,
    )


def test_exception_of_deleted_series_not_separate_delete():
    master = series(("RRULE:FREQ=WEEKLY;UNTIL=20240601T150000Z",))
    assert pairs(plan(master, exception())) == [("main", "s1")]


def test_exception_of_kept_series_kept():
    master = series(("RRULE:FREQ=WEEKLY",))
    assert pairs(plan(master, exception())) == []


def test_mirror_delete_follows_source():
    source = ev("e1", "sec", start=at(1), end=at(2))
    link = Link(source="sec/e1", kind=KIND_BLOCKED, calendar_id="main", event_id="m1")
    result = plan(source, links={"sec/e1": link})
    assert (
        pairs(result) == [("sec", "e1"), ("main", "m1")]
        and result.counts == {"main/2024": 1, "sec/2024": 1}
        and result.sources == frozenset({"sec/e1"})
        and all(w.op == OP_DELETE for w in result.deletes)
        and [w.reason for w in result.deletes] == ["cleanup", "cleanup mirror"]
    )


def test_exception_link_makes_no_delete():
    master = series(("RRULE:FREQ=WEEKLY;UNTIL=20240601T150000Z",))
    link = Link(source="main/s1_x", kind=KIND_BLOCKED, calendar_id="main", event_id="m2")
    assert pairs(plan(master, exception(), links={"main/s1_20240108T150000Z": link})) == [
        ("main", "s1")
    ]


def test_verify_archive():
    result = plan(ev("e1", "sec", start=at(1), end=at(2)), ev("e2", "sec", start=at(1), end=at(2)))
    assert (
        verify_archive(result, {"sec/e1", "sec/e2", "extra"})
        and not verify_archive(result, {"sec/e1"})
        and verify_archive(plan(), set())
    )


def test_counts_across_calendars_and_years():
    events = (
        ev("a1", "a", start=datetime(2023, 5, 1, tzinfo=UTC), end=datetime(2023, 5, 2, tzinfo=UTC)),
        ev("a2", "a", start=at(1), end=at(2)),
        ev("a3", "a", start=at(3), end=at(4)),
        ev("b1", "b", start=at(1), end=at(2)),
    )
    result = plan(*events)
    assert result.counts == {"a/2023": 1, "a/2024": 2, "b/2024": 1} and sum(
        result.counts.values()
    ) == len(result.deletes)


def test_sources_hold_no_mirror_key():
    link = Link(source="main/e1", kind=KIND_BLOCKED, calendar_id="sec", event_id="m1")
    result = plan(ev(start=at(1), end=at(2)), links={"main/e1": link})
    assert result.sources == frozenset({"main/e1"}) and len(result.deletes) == 2


def test_delete_order_by_source_key_mirror_after_source():
    links = {
        "b/x": Link(source="b/x", kind=KIND_BLOCKED, calendar_id="main", event_id="m2"),
        "a/y": Link(source="a/y", kind=KIND_BLOCKED, calendar_id="main", event_id="m1"),
    }
    result = plan(
        ev("x", "b", start=at(1), end=at(2)), ev("y", "a", start=at(1), end=at(2)), links=links
    )
    assert pairs(result) == [("a", "y"), ("main", "m1"), ("b", "x"), ("main", "m2")]


def test_same_input_gives_equal_plans():
    events = (ev("x", "b", start=at(1), end=at(2)), ev("y", "a", start=at(1), end=at(2)))
    assert plan(*events) == plan(*events)


def test_kept_events_never_listed():
    invited = timed(at(1), at(2), id="inv", organizer_email="boss@corp.com")
    running = series(("RRULE:FREQ=WEEKLY",))
    result = plan(invited, running)
    assert result.deletes == () and result.sources == frozenset() and result.counts == {}
