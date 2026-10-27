from cal_mirror.cleanup import bounded_occurrences
from tests.test_recurrence_budget import START, _master, _plan, _timed
from tests.test_recurrence_budget_tokens import _PARTS


def test_u2028_hostile_line_kept_unbounded() -> None:
    line = "\u2028".join(_PARTS)

    def run() -> tuple[object, tuple[object, ...], int]:
        found = bounded_occurrences((line,), START)
        plan = _plan(_master((line,)))
        return found, plan.deletes, plan.kept_unbounded

    (found, deletes, kept), elapsed = _timed(run)
    assert found is None and deletes == () and kept == 1 and elapsed < 2.0


def test_u2028_is_python_whitespace() -> None:
    assert "a\u2028b".split() == ["a", "b"]
