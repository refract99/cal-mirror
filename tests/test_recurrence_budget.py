import json
import signal
import time
import zipfile
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from cal_mirror.archive import write_archive
from cal_mirror.cleanup import bounded_occurrences, plan_cleanup, series_ends_before
from cal_mirror.model import Event

CUTOFF = date(2024, 7, 1)
OWNER = "steve@x.com"
D = datetime(2024, 1, 1, 9, 0, tzinfo=UTC)
HOSTILE = (
    "RRULE:FREQ=SECONDLY;COUNT=2000000000",
    "EXRULE:FREQ=SECONDLY;COUNT=2000000000",
)
START = datetime(2020, 1, 1, 15, 0, tzinfo=UTC)


def _master(
    recurrence: tuple[str, ...],
    start: datetime | date = START,
    end: datetime | date | None = None,
) -> Event:
    if end is None and isinstance(start, datetime):
        end = start + timedelta(hours=1)
    return Event(
        calendar_id="main",
        id="s1",
        summary="s1",
        start=start,
        end=end,
        all_day=not isinstance(start, datetime),
        recurrence=recurrence,
        organizer_email=OWNER,
        raw={"id": "s1"},
    )


def _plan(event: Event):
    return plan_cleanup({event.calendar_id: [event]}, CUTOFF, OWNER, {})


def _pairs(event: Event) -> list[tuple[str, str]]:
    return [(write.calendar_id, write.event_id) for write in _plan(event).deletes]


def _timed[T](fn: Callable[[], T]) -> tuple[T, float]:
    def _handler(signum: int, frame: object) -> None:
        raise TimeoutError

    signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, 10)
    started = time.monotonic()
    try:
        return fn(), time.monotonic() - started
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _names(paths: list[Path]) -> list[str]:
    return [path.name for path in paths]


def _holds_master(path: Path) -> bool:
    with zipfile.ZipFile(path) as archive:
        document = json.loads(archive.read("main.json"))
        ics = archive.read("main.ics").decode()
    events = document["events"]
    return len(events) == 1 and events[0]["id"] == "s1" and "main/s1" in ics


def test_hostile_secondly_exrule_kept_unbounded() -> None:
    event = _master(HOSTILE)
    (result, ends), elapsed = _timed(lambda: (_plan(event), series_ends_before(event, CUTOFF)))
    assert result.deletes == () and result.kept_unbounded == 1 and ends is False and elapsed < 2.0


def test_hostile_secondly_exrule_lowercase_kept_unbounded() -> None:
    event = _master(tuple(line.lower() for line in HOSTILE))
    (result, ends), elapsed = _timed(lambda: (_plan(event), series_ends_before(event, CUTOFF)))
    assert result.deletes == () and result.kept_unbounded == 1 and ends is False and elapsed < 2.0


def test_daily_with_exrule_kept_unbounded() -> None:
    event = _master(("RRULE:FREQ=DAILY;COUNT=10", "EXRULE:FREQ=DAILY;COUNT=10"))
    result = _plan(event)
    assert result.deletes == () and result.kept_unbounded == 1


def test_two_rrules_kept_unbounded() -> None:
    event = _master(("RRULE:FREQ=DAILY;COUNT=2", "RRULE:FREQ=WEEKLY;COUNT=2"))
    result = _plan(event)
    assert result.deletes == () and result.kept_unbounded == 1


def test_hourly_count_kept_unbounded() -> None:
    event = _master(("RRULE:FREQ=HOURLY;COUNT=3",))
    result = _plan(event)
    assert result.deletes == () and result.kept_unbounded == 1


def test_daily_byhour_kept_unbounded() -> None:
    event = _master(("RRULE:FREQ=DAILY;BYHOUR=9;COUNT=3",))
    result = _plan(event)
    assert result.deletes == () and result.kept_unbounded == 1


def test_weekly_ordinal_byday_kept_unbounded() -> None:
    event = _master(("RRULE:FREQ=WEEKLY;BYDAY=1MO;COUNT=3",))
    result = _plan(event)
    assert result.deletes == () and result.kept_unbounded == 1


def test_exdate_over_budget_kept_unbounded() -> None:
    stamps = ",".join(
        (START + timedelta(minutes=i)).strftime("%Y%m%dT%H%M%SZ") for i in range(5001)
    )
    event = _master(("RRULE:FREQ=DAILY;COUNT=3", f"EXDATE:{stamps}"))
    result = _plan(event)
    assert result.deletes == () and result.kept_unbounded == 1


def test_weekly_byday_count_deleted() -> None:
    event = _master(("RRULE:FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10",))
    assert _pairs(event) == [("main", "s1")]


def test_monthly_byday_until_deleted() -> None:
    event = _master(("RRULE:FREQ=MONTHLY;BYDAY=2TU;UNTIL=20231231T000000Z",))
    assert _pairs(event) == [("main", "s1")]


def test_yearly_bymonth_deleted() -> None:
    event = _master(("RRULE:FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=1;COUNT=3",))
    assert _pairs(event) == [("main", "s1")]


def test_daily_count_at_bound_from_1990_deleted() -> None:
    start = datetime(1990, 1, 1, 15, 0, tzinfo=UTC)
    event = _master(("RRULE:FREQ=DAILY;COUNT=5000",), start=start)
    assert _pairs(event) == [("main", "s1")]


def test_monthly_impossible_day_kept_within_budget() -> None:
    start = datetime(1900, 1, 1, 15, 0, tzinfo=UTC)
    event = _master(("RRULE:FREQ=MONTHLY;BYMONTH=2;BYMONTHDAY=30;COUNT=5",), start=start)
    result, elapsed = _timed(lambda: _plan(event))
    assert result.deletes == () and result.kept_unbounded == 0 and elapsed < 2.0


def test_daily_count_three_occurrences() -> None:
    found = bounded_occurrences(("RRULE:FREQ=DAILY;COUNT=3",), D)
    assert found == (D, D + timedelta(days=1), D + timedelta(days=2))


def test_daily_count_three_stops_on_second_day() -> None:
    stop = datetime(2024, 1, 2, 9, 0, tzinfo=UTC)
    found = bounded_occurrences(("RRULE:FREQ=DAILY;COUNT=3",), D, stop)
    assert found == (D,)


def test_open_daily_stops_after_three_days() -> None:
    stop = datetime(2024, 1, 4, 0, 0, tzinfo=UTC)
    found = bounded_occurrences(("RRULE:FREQ=DAILY",), D, stop)
    assert found == (D, D + timedelta(days=1), D + timedelta(days=2))


def test_open_daily_from_2000_exceeds_walk() -> None:
    start = datetime(2000, 1, 1, 9, 0, tzinfo=UTC)
    stop = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
    found = bounded_occurrences(("RRULE:FREQ=DAILY",), start, stop)
    assert found is None


def test_hostile_bounded_occurrences_returns_none() -> None:
    found, elapsed = _timed(lambda: bounded_occurrences(HOSTILE, D))
    assert found is None and elapsed < 2.0


def test_nonsense_freq_raises_value_error() -> None:
    raised = False
    try:
        bounded_occurrences(("RRULE:FREQ=NONSENSE",), D)
    except ValueError:
        raised = True
    assert raised


def test_hostile_archive_spans_start_through_cutoff(tmp_path: Path) -> None:
    event = _master(HOSTILE)
    paths, elapsed = _timed(lambda: write_archive({"main": [event]}, tmp_path, CUTOFF))
    assert (
        _names(paths) == [f"cal-mirror-{year}.zip" for year in range(2020, 2025)]
        and all(_holds_master(path) for path in paths)
        and elapsed < 2.0
    )


def test_hostile_all_day_archive_spans_years(tmp_path: Path) -> None:
    event = _master(HOSTILE, start=date(2021, 3, 1), end=date(2021, 3, 2))
    paths = write_archive({"main": [event]}, tmp_path, CUTOFF)
    assert _names(paths) == [f"cal-mirror-{year}.zip" for year in range(2021, 2025)] and all(
        _holds_master(path) for path in paths
    )


def test_hourly_archive_fills_years_through_cutoff(tmp_path: Path) -> None:
    start = datetime(2022, 5, 1, 9, 0, tzinfo=UTC)
    event = _master(("RRULE:FREQ=HOURLY;COUNT=2",), start=start)
    paths = write_archive({"main": [event]}, tmp_path, CUTOFF)
    assert _names(paths) == [f"cal-mirror-{year}.zip" for year in range(2022, 2025)]


def test_open_weekly_archive_two_years(tmp_path: Path) -> None:
    start = datetime(2023, 12, 4, 15, 0, tzinfo=UTC)
    event = _master(("RRULE:FREQ=WEEKLY",), start=start)
    paths = write_archive({"main": [event]}, tmp_path, CUTOFF)
    assert _names(paths) == ["cal-mirror-2023.zip", "cal-mirror-2024.zip"]
