from pathlib import Path

from cal_mirror.archive import write_archive
from cal_mirror.cleanup import bounded_occurrences
from tests.test_recurrence_budget import CUTOFF, START, _holds_master, _master, _plan, _timed

_UNTIL = "UNTIL=20300101T000000Z"
_EMBEDDED = (
    "RDATE:20200101T000000Z\n"
    "RRULE:FREQ=SECONDLY;COUNT=2000000000\n"
    "EXRULE:FREQ=SECONDLY;COUNT=2000000000"
)


def test_daily_interval_zero_kept_unbounded() -> None:
    recurrence = (f"RRULE:FREQ=DAILY;INTERVAL=0;{_UNTIL}",)
    event = _master(recurrence)
    (found, result), elapsed = _timed(
        lambda: (bounded_occurrences(recurrence, START), _plan(event))
    )
    assert found is None and result.deletes == () and result.kept_unbounded == 1 and elapsed < 2.0


def test_negative_and_non_int_interval_return_none() -> None:
    negative = (f"RRULE:FREQ=DAILY;INTERVAL=-1;{_UNTIL}",)
    text = (f"RRULE:FREQ=DAILY;INTERVAL=x;{_UNTIL}",)
    (neg, bad), elapsed = _timed(
        lambda: (bounded_occurrences(negative, START), bounded_occurrences(text, START))
    )
    assert neg is None and bad is None and elapsed < 2.0


def test_embedded_newlines_return_none_and_archive_holds_master(tmp_path: Path) -> None:
    variants = (
        (_EMBEDDED,),
        (_EMBEDDED.replace("\n", "\r\n"),),
        (_EMBEDDED.replace("\n", "\n "),),
    )
    event = _master(variants[0])

    def run() -> tuple[tuple[object, ...], list[Path]]:
        found = tuple(bounded_occurrences(recurrence, START) for recurrence in variants)
        paths = write_archive({"main": [event]}, tmp_path, CUTOFF)
        return found, paths

    (found, paths), elapsed = _timed(run)
    assert (
        found == (None, None, None)
        and paths
        and all(_holds_master(path) for path in paths)
        and elapsed < 2.0
    )


def test_positive_interval_unchanged() -> None:
    weekly = ("RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=10",)
    plain = ("RRULE:FREQ=WEEKLY;COUNT=10",)
    same = ("RRULE:FREQ=WEEKLY;INTERVAL=1;COUNT=10",)

    def run() -> tuple[object, object, object, tuple[object, ...]]:
        found = bounded_occurrences(weekly, START)
        deletes = tuple(
            (write.calendar_id, write.event_id) for write in _plan(_master(weekly)).deletes
        )
        return found, bounded_occurrences(plain, START), bounded_occurrences(same, START), deletes

    (found, plain_found, same_found, deletes), elapsed = _timed(run)
    assert (
        found is not None
        and len(found) == 10
        and plain_found == same_found
        and deletes == (("main", "s1"),)
        and elapsed < 2.0
    )
