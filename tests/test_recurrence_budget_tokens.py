from pathlib import Path

from cal_mirror.archive import write_archive
from cal_mirror.cleanup import bounded_occurrences
from tests.test_recurrence_budget import CUTOFF, START, _holds_master, _master, _plan, _timed

_PARTS = (
    "RDATE:20200101T000000Z",
    "RRULE:FREQ=SECONDLY;COUNT=2000000000",
    "EXRULE:FREQ=SECONDLY;COUNT=2000000000",
)
_INNER = (" ", "\t", "\v", "\f", "\x1c", "\x85", " ")


def _joined(sep: str) -> tuple[str, ...]:
    return (sep.join(_PARTS),)


def test_inner_whitespace_hostile_line_kept_unbounded() -> None:
    recurrences = tuple(_joined(sep) for sep in _INNER)

    def run() -> tuple[tuple[object, ...], tuple[tuple[object, ...], int]]:
        found = tuple(bounded_occurrences(recurrence, START) for recurrence in recurrences)
        plans = tuple(
            (plan.deletes, plan.kept_unbounded)
            for plan in (_plan(_master(recurrence)) for recurrence in recurrences)
        )
        return found, plans

    (found, plans), elapsed = _timed(run)
    assert found == (None,) * len(_INNER) and plans == (((), 1),) * len(_INNER) and elapsed < 2.0


def test_newline_joined_hostile_line_returns_none() -> None:
    recurrences = tuple(_joined(sep) for sep in ("\n", "\r\n", "\n "))
    found, elapsed = _timed(
        lambda: tuple(bounded_occurrences(recurrence, START) for recurrence in recurrences)
    )
    assert found == (None, None, None) and elapsed < 2.0


def test_two_rules_hidden_in_one_line_return_none() -> None:
    recurrence = ("RRULE:FREQ=WEEKLY;COUNT=3 RRULE:FREQ=DAILY;COUNT=3",)
    found, elapsed = _timed(lambda: bounded_occurrences(recurrence, START))
    assert found is None and elapsed < 2.0


def test_blank_recurrence_lines_return_none() -> None:
    (empty, spaces), elapsed = _timed(
        lambda: (bounded_occurrences(("",), START), bounded_occurrences(("   ",), START))
    )
    assert empty is None and spaces is None and elapsed < 2.0


def test_edge_whitespace_weekly_and_interval_counts() -> None:
    padded = ("  RRULE:FREQ=WEEKLY;COUNT=3 ",)
    interval = ("RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=10",)

    def run() -> tuple[object, object]:
        return bounded_occurrences(padded, START), bounded_occurrences(interval, START)

    (padded_found, interval_found), elapsed = _timed(run)
    assert (
        padded_found is not None
        and len(padded_found) == 3
        and interval_found is not None
        and len(interval_found) == 10
        and elapsed < 2.0
    )


def test_space_joined_hostile_archive_holds_master(tmp_path: Path) -> None:
    event = _master(_joined(" "))
    paths, elapsed = _timed(lambda: write_archive({"main": [event]}, tmp_path, CUTOFF))
    assert paths and all(_holds_master(path) for path in paths) and elapsed < 2.0
