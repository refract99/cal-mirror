import zipfile
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cal_mirror.archive import ArchiveError, archive_name, archived_sources, write_archive
from cal_mirror.model import Event

CUTOFF = date(2024, 7, 1)


def _event(
    event_id: str,
    start: datetime | date,
    recurrence: tuple[str, ...] = (),
    end: datetime | date | None = None,
) -> Event:
    return Event(
        calendar_id="c",
        id=event_id,
        summary=event_id,
        description="",
        location="",
        start=start,
        end=end,
        all_day=not isinstance(start, datetime),
        recurrence=recurrence,
        raw={"id": event_id},
    )


def _names(paths: list[Path]) -> list[str]:
    return [path.name for path in paths]


def _members(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name)


def _parts(dest: Path) -> list[Path]:
    return [path for path in dest.iterdir() if path.suffix == ".part"]


def test_archive_name_copy() -> None:
    assert archive_name(2024, 2) == "cal-mirror-2024-2.zip"
    assert archive_name(2024, 1) == "cal-mirror-2024.zip"


def test_rerun_never_overwrites(tmp_path: Path) -> None:
    day = datetime(2024, 2, 1, tzinfo=UTC)
    both = {"cal": [_event("a", day), _event("b", day)]}
    only_a = {"cal": [_event("a", day)]}
    assert write_archive(both, tmp_path, CUTOFF) == [tmp_path / "cal-mirror-2024.zip"]
    first = (tmp_path / "cal-mirror-2024.zip").read_bytes()
    assert write_archive(only_a, tmp_path, CUTOFF) == [tmp_path / "cal-mirror-2024-2.zip"]
    assert (tmp_path / "cal-mirror-2024.zip").read_bytes() == first
    assert archived_sources([tmp_path / "cal-mirror-2024.zip"]) == {"cal/a", "cal/b"}
    assert write_archive(only_a, tmp_path, CUTOFF) == [tmp_path / "cal-mirror-2024-3.zip"]
    assert _parts(tmp_path) == []


def test_free_gap_is_filled(tmp_path: Path) -> None:
    (tmp_path / "cal-mirror-2024.zip").write_bytes(b"x")
    (tmp_path / "cal-mirror-2024-3.zip").write_bytes(b"y")
    events = {"cal": [_event("a", datetime(2024, 2, 1, tzinfo=UTC))]}
    assert write_archive(events, tmp_path, CUTOFF) == [tmp_path / "cal-mirror-2024-2.zip"]
    assert (tmp_path / "cal-mirror-2024.zip").read_bytes() == b"x"
    assert (tmp_path / "cal-mirror-2024-3.zip").read_bytes() == b"y"


def test_directory_name_is_skipped(tmp_path: Path) -> None:
    (tmp_path / "cal-mirror-2023.zip").mkdir()
    events = {"cal": [_event("a", datetime(2023, 2, 1, tzinfo=UTC))]}
    assert write_archive(events, tmp_path, CUTOFF) == [tmp_path / "cal-mirror-2023-2.zip"]
    assert (tmp_path / "cal-mirror-2023.zip").is_dir()
    assert _parts(tmp_path) == []


def test_weekly_series_in_both_years(tmp_path: Path) -> None:
    start = datetime(2023, 12, 4, 15, 0, tzinfo=UTC)
    events = {"c": [_event("weekly", start, ("RRULE:FREQ=WEEKLY",))]}
    paths = write_archive(events, tmp_path, CUTOFF)
    assert _names(paths) == ["cal-mirror-2023.zip", "cal-mirror-2024.zip"]
    for path in paths:
        assert b'"events": [{"id": "weekly"}]' in _members(path, "c.json")
        assert b"RRULE:FREQ=WEEKLY" in _members(path, "c.ics")
    assert archived_sources(paths) == {"c/weekly"}
    assert _parts(tmp_path) == []


def test_until_stops_series(tmp_path: Path) -> None:
    start = datetime(2023, 12, 4, 15, 0, tzinfo=UTC)
    rule = ("RRULE:FREQ=WEEKLY;UNTIL=20231225T150000Z",)
    paths = write_archive({"c": [_event("w", start, rule)]}, tmp_path, CUTOFF)
    assert _names(paths) == ["cal-mirror-2023.zip"]


def test_daily_count_crosses_year(tmp_path: Path) -> None:
    start = datetime(2023, 12, 31, 9, 0, tzinfo=UTC)
    paths = write_archive(
        {"c": [_event("d", start, ("RRULE:FREQ=DAILY;COUNT=3",))]}, tmp_path, CUTOFF
    )
    assert _names(paths) == ["cal-mirror-2023.zip", "cal-mirror-2024.zip"]


def test_all_day_yearly(tmp_path: Path) -> None:
    event = _event("y", date(2020, 3, 1), ("RRULE:FREQ=YEARLY",), end=date(2020, 3, 2))
    paths = write_archive({"c": [event]}, tmp_path, CUTOFF)
    assert _names(paths) == [f"cal-mirror-{year}.zip" for year in range(2020, 2025)]


def test_bound_fills_every_year(tmp_path: Path) -> None:
    event = _event("m", datetime(2020, 1, 1, tzinfo=UTC), ("RRULE:FREQ=MINUTELY",))
    paths = write_archive({"c": [event]}, tmp_path, CUTOFF)
    assert _names(paths) == [f"cal-mirror-{year}.zip" for year in range(2020, 2025)]


def test_bound_ends_at_cutoff_year(tmp_path: Path) -> None:
    event = _event("m", datetime(2020, 1, 1, tzinfo=UTC), ("RRULE:FREQ=MINUTELY",))
    paths = write_archive({"c": [event]}, tmp_path, date(2024, 1, 1))
    assert _names(paths) == [f"cal-mirror-{year}.zip" for year in range(2020, 2024)]


def test_excluded_occurrences_fall_back(tmp_path: Path) -> None:
    rule = ("RRULE:FREQ=DAILY;COUNT=2", "EXDATE:20231231T090000Z,20240101T090000Z")
    event = _event("x", datetime(2023, 12, 31, 9, 0, tzinfo=UTC), rule)
    paths = write_archive({"c": [event]}, tmp_path, CUTOFF)
    assert _names(paths) == ["cal-mirror-2023.zip"]


def test_bad_rule_falls_back(tmp_path: Path) -> None:
    event = _event("n", datetime(2023, 5, 1, tzinfo=UTC), ("RRULE:FREQ=NONSENSE",))
    paths = write_archive({"c": [event]}, tmp_path, CUTOFF)
    assert _names(paths) == ["cal-mirror-2023.zip"]


def test_non_utf8_member(tmp_path: Path) -> None:
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("c.json", b'{"calendar_id": "\xff", "events": []}')
    with pytest.raises(ArchiveError) as raised:
        archived_sources([path])
    assert str(raised.value).startswith(f"cannot read archive {path}")
