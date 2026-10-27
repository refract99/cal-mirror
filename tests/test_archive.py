import json
import zipfile
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from cal_mirror.archive import ArchiveError, archive_name, archived_sources, write_archive
from cal_mirror.model import Event, source_key

CUTOFF = date(2024, 7, 1)


def _event(
    calendar_id: str,
    event_id: str,
    start: datetime | date | None,
    summary: str,
    *,
    end: datetime | date | None = None,
    all_day: bool = False,
    recurrence: tuple[str, ...] = (),
    description: str = "",
    location: str = "",
    raw: dict | None = None,
) -> Event:
    body = {"id": event_id, "summary": summary} if raw is None else raw
    return Event(
        calendar_id=calendar_id,
        id=event_id,
        summary=summary,
        description=description,
        location=location,
        start=start,
        end=end,
        all_day=all_day,
        recurrence=recurrence,
        raw=body,
    )


def test_archive_name() -> None:
    assert archive_name(2023) == "cal-mirror-2023.zip"
    assert archive_name(2024) == "cal-mirror-2024.zip"


def test_two_calendars_two_years(tmp_path: Path) -> None:
    events = {
        "b@x.com": [
            _event("b@x.com", "b23", datetime(2023, 8, 1, 9, 0, tzinfo=UTC), "b23"),
            _event("b@x.com", "b24", datetime(2024, 3, 3, 9, 0, tzinfo=UTC), "b24"),
        ],
        "a@x.com": [
            _event(
                "a@x.com",
                "a23a",
                datetime(2023, 3, 1, 12, 0, tzinfo=UTC),
                "Café meet",
                description="notes",
                location="room",
                raw={"id": "a23a", "summary": "café"},
            ),
            _event("a@x.com", "a23b", datetime(2023, 4, 1, 12, 0, tzinfo=UTC), "second"),
            _event("a@x.com", "a24", datetime(2024, 2, 2, 15, 0, tzinfo=UTC), "a24"),
        ],
    }
    paths = write_archive(events, tmp_path, CUTOFF)
    assert paths == [tmp_path / "cal-mirror-2023.zip", tmp_path / "cal-mirror-2024.zip"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "cal-mirror-2023.zip",
        "cal-mirror-2024.zip",
    ]
    with zipfile.ZipFile(paths[0]) as archive:
        assert archive.namelist() == [
            "a@x.com.ics",
            "a@x.com.json",
            "b@x.com.ics",
            "b@x.com.json",
        ]
        assert sum(name.endswith(".ics") for name in archive.namelist()) == 2
        assert sum(name.endswith(".json") for name in archive.namelist()) == 2
        ics = archive.read("a@x.com.ics")
        assert b"PRODID:-//cal-mirror//archive//EN" in ics
        assert b"VERSION:2.0" in ics
        assert b"UID:a@x.com/a23a" in ics
        assert b"UID:a@x.com/a23b" in ics
        assert b"DTSTART:20230301T120000Z" in ics
        assert b"SUMMARY:Caf" in ics
        assert b"DESCRIPTION:notes" in ics
        assert b"LOCATION:room" in ics
        document = json.loads(archive.read("a@x.com.json"))
        assert document == {
            "calendar_id": "a@x.com",
            "events": [
                {"id": "a23a", "summary": "café"},
                {"id": "a23b", "summary": "second"},
            ],
        }
        raw_json = archive.read("a@x.com.json")
        assert "café".encode() in raw_json
        assert b"\\u00e9" not in raw_json
    with zipfile.ZipFile(paths[1]) as archive:
        assert archive.namelist() == [
            "a@x.com.ics",
            "a@x.com.json",
            "b@x.com.ics",
            "b@x.com.json",
        ]
    written = [
        events["a@x.com"][0],
        events["a@x.com"][1],
        events["a@x.com"][2],
        events["b@x.com"][0],
        events["b@x.com"][1],
    ]
    sources = archived_sources(paths)
    assert len(sources) == len(written)
    assert sources == {source_key(event) for event in written}


def test_cutoff_day(tmp_path: Path) -> None:
    kept_id = "june"
    on_cutoff = "midnight"
    shifted = "offset"
    events = {
        "sec": [
            _event("sec", on_cutoff, datetime(2024, 7, 1, 0, 0, tzinfo=UTC), "on cutoff"),
            _event("sec", kept_id, datetime(2024, 6, 30, 23, 59, tzinfo=UTC), "just before"),
            _event(
                "sec",
                shifted,
                datetime(2024, 6, 30, 20, 0, tzinfo=timezone(timedelta(hours=-5))),
                "becomes july",
            ),
        ]
    }
    paths = write_archive(events, tmp_path, CUTOFF)
    assert paths == [tmp_path / "cal-mirror-2024.zip"]
    assert archived_sources(paths) == {"sec/june"}


def test_all_day(tmp_path: Path) -> None:
    events = {
        "main": [
            _event(
                "main",
                "day",
                date(2023, 7, 4),
                "holiday",
                end=date(2023, 7, 5),
                all_day=True,
            )
        ]
    }
    paths = write_archive(events, tmp_path, CUTOFF)
    assert paths == [tmp_path / "cal-mirror-2023.zip"]
    with zipfile.ZipFile(paths[0]) as archive:
        ics = archive.read("main.ics").decode()
        assert "DTSTART;VALUE=DATE:20230704" in ics
        assert "DTEND;VALUE=DATE:20230705" in ics
    assert archived_sources(paths) == {"main/day"}


def test_recurrence_rrule_only(tmp_path: Path) -> None:
    rule = "RRULE:FREQ=WEEKLY;BYDAY=MO"
    exdate = "EXDATE:20230110T150000Z"
    events = {
        "sec": [
            _event(
                "sec",
                "weekly",
                datetime(2023, 1, 5, 15, 0, tzinfo=UTC),
                "weekly",
                recurrence=(rule, exdate),
                raw={
                    "id": "weekly",
                    "summary": "weekly",
                    "recurrence": [rule, exdate],
                },
            )
        ]
    }
    paths = write_archive(events, tmp_path, CUTOFF)
    with zipfile.ZipFile(paths[0]) as archive:
        ics = archive.read("sec.ics").decode()
        assert "RRULE:FREQ=WEEKLY;BYDAY=MO" in ics
        assert "EXDATE" not in ics
        assert "DTSTART:20230105T150000Z" in ics
        document = json.loads(archive.read("sec.json"))
        assert document["events"][0]["recurrence"] == [rule, exdate]


def test_safe_calendar_names(tmp_path: Path) -> None:
    events = {
        "Team Cal/x": [_event("Team Cal/x", "t1", datetime(2023, 2, 2, 8, 0, tzinfo=UTC), "team")],
        "sec@x.com": [_event("sec@x.com", "s1", datetime(2023, 2, 3, 8, 0, tzinfo=UTC), "sec")],
    }
    paths = write_archive(events, tmp_path, CUTOFF)
    with zipfile.ZipFile(paths[0]) as archive:
        assert archive.namelist() == [
            "Team_Cal_x.ics",
            "Team_Cal_x.json",
            "sec@x.com.ics",
            "sec@x.com.json",
        ]
        document = json.loads(archive.read("Team_Cal_x.json"))
        assert document["calendar_id"] == "Team Cal/x"
        assert document["events"] == [{"id": "t1", "summary": "team"}]


def test_shared_safe_name_writes_nothing(tmp_path: Path) -> None:
    events = {
        "Team Cal/x": [_event("Team Cal/x", "t1", datetime(2023, 2, 2, 8, 0, tzinfo=UTC), "team")],
        "Team_Cal_x": [_event("Team_Cal_x", "t2", datetime(2023, 2, 3, 8, 0, tzinfo=UTC), "other")],
    }
    raised = False
    try:
        write_archive(events, tmp_path, CUTOFF)
    except ArchiveError as err:
        raised = True
        assert str(err) == "calendar ids share an archive name: Team_Cal_x"
    assert raised
    assert list(tmp_path.iterdir()) == []


def test_rerun_replaces_one_year(tmp_path: Path) -> None:
    first = {
        "cal": [
            _event("cal", "old", datetime(2023, 5, 1, 10, 0, tzinfo=UTC), "old"),
            _event("cal", "a", datetime(2024, 5, 1, 10, 0, tzinfo=UTC), "a"),
            _event("cal", "b", datetime(2024, 6, 1, 10, 0, tzinfo=UTC), "b"),
        ]
    }
    write_archive(first, tmp_path, date(2025, 1, 1))
    kept = (tmp_path / "cal-mirror-2023.zip").read_bytes()
    before = archived_sources([tmp_path / "cal-mirror-2024.zip"])
    assert before == {"cal/a", "cal/b"}
    second = {
        "cal": [
            _event("cal", "a", datetime(2024, 5, 1, 10, 0, tzinfo=UTC), "a"),
        ]
    }
    paths = write_archive(second, tmp_path, date(2025, 1, 1))
    assert paths == [tmp_path / "cal-mirror-2024-2.zip"]
    assert archived_sources([tmp_path / "cal-mirror-2024.zip"]) == before
    after = archived_sources(paths)
    assert after == {"cal/a"}
    assert len(after) == len(before) - 1
    assert (tmp_path / "cal-mirror-2023.zip").read_bytes() == kept
    assert [path for path in tmp_path.iterdir() if path.suffix == ".part"] == []


def test_dest_missing(tmp_path: Path) -> None:
    dest = tmp_path / "nope"
    raised = False
    try:
        write_archive({}, dest, CUTOFF)
    except ArchiveError as err:
        raised = True
        assert str(err) == f"archive destination does not exist: {dest}"
    assert raised


def test_dest_not_a_directory(tmp_path: Path) -> None:
    dest = tmp_path / "file"
    dest.write_bytes(b"x")
    raised = False
    try:
        write_archive({}, dest, CUTOFF)
    except ArchiveError as err:
        raised = True
        assert str(err) == f"archive destination is not a directory: {dest}"
    assert raised


def test_archived_sources_not_a_zip(tmp_path: Path) -> None:
    path = tmp_path / "bad.zip"
    path.write_bytes(b"not a zip")
    raised = False
    try:
        archived_sources([path])
    except ArchiveError as err:
        raised = True
        assert str(err).startswith(f"cannot read archive {path}")
    assert raised


def test_archived_sources_missing(tmp_path: Path) -> None:
    path = tmp_path / "missing.zip"
    raised = False
    try:
        archived_sources([path])
    except ArchiveError as err:
        raised = True
        assert str(err).startswith(f"cannot read archive {path}")
    assert raised


def test_archived_sources_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "bad-json.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("cal.json", b"{not json")
    raised = False
    try:
        archived_sources([path])
    except ArchiveError as err:
        raised = True
        assert str(err).startswith(f"cannot read archive {path}")
    assert raised


def test_archived_sources_missing_fields(tmp_path: Path) -> None:
    cases = [
        b'{"events": [{"id": "a"}]}',
        b'{"calendar_id": "c"}',
        b'{"calendar_id": "c", "events": [{}]}',
        b'{"calendar_id": "c", "events": "nope"}',
    ]
    for index, payload in enumerate(cases):
        path = tmp_path / f"case-{index}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("cal.json", payload)
        raised = False
        try:
            archived_sources([path])
        except ArchiveError as err:
            raised = True
            assert str(err).startswith(f"cannot read archive {path}")
        assert raised


def test_no_qualifying_events(tmp_path: Path) -> None:
    events = {
        "c": [
            _event("c", "none", None, "none"),
            _event("c", "late", datetime(2024, 7, 1, 0, 0, tzinfo=UTC), "late"),
            _event("c", "after", date(2024, 7, 2), "after", all_day=True),
        ]
    }
    paths = write_archive(events, tmp_path, CUTOFF)
    assert paths == []
    assert list(tmp_path.iterdir()) == []
