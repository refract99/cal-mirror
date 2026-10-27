import contextlib
import json
import os
import re
import tempfile
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from icalendar import Calendar, vRecur
from icalendar import Event as IcsEvent

from cal_mirror.cleanup import bounded_occurrences
from cal_mirror.model import Event, source_key

_UNSAFE = re.compile(r"[^A-Za-z0-9._@-]")


class ArchiveError(Exception):
    pass


def archive_name(year: int, copy: int = 1) -> str:
    if copy < 2:
        return f"cal-mirror-{year}.zip"
    return f"cal-mirror-{year}-{copy}.zip"


def _safe_name(calendar_id: str) -> str:
    return _UNSAFE.sub("_", calendar_id)


def _start_day(start: datetime | date | None) -> date | None:
    if start is None:
        return None
    if isinstance(start, datetime):
        return start.astimezone(UTC).date()
    return start


def _ics(events: Sequence[Event]) -> bytes:
    calendar = Calendar()
    calendar.add("prodid", "-//cal-mirror//archive//EN")
    calendar.add("version", "2.0")
    for event in events:
        component = IcsEvent()
        component.add("uid", source_key(event))
        if event.summary:
            component.add("summary", event.summary)
        if event.description:
            component.add("description", event.description)
        if event.location:
            component.add("location", event.location)
        component.add("dtstart", event.start)
        if event.end is not None:
            component.add("dtend", event.end)
        for line in event.recurrence:
            if line.startswith("RRULE:"):
                with contextlib.suppress(ValueError):
                    component.add("rrule", vRecur.from_ical(line.split(":", 1)[1]))
        calendar.add_component(component)
    return calendar.to_ical()


def _json_member(calendar_id: str, events: Sequence[Event]) -> bytes:
    payload = {"calendar_id": calendar_id, "events": [dict(event.raw) for event in events]}
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _reject_shared_names(events: Mapping[str, Sequence[Event]]) -> None:
    seen: set[str] = set()
    for calendar_id in events:
        safe = _safe_name(calendar_id)
        if safe in seen:
            raise ArchiveError(f"calendar ids share an archive name: {safe}")
        seen.add(safe)


def _utc_year(occurrence: datetime | date) -> int:
    if isinstance(occurrence, datetime) and occurrence.tzinfo is not None:
        return occurrence.astimezone(UTC).year
    return occurrence.year


def _occurrence_years(event: Event, day: date, cutoff: date) -> list[int]:
    start = event.start
    if isinstance(start, datetime):
        anchor = start.astimezone(UTC)
        stop = datetime.combine(cutoff, time(0), tzinfo=UTC)
    elif start is not None:
        anchor = datetime.combine(start, time(0))
        stop = datetime.combine(cutoff, time(0))
    else:
        return [day.year]
    try:
        found = bounded_occurrences(event.recurrence, anchor, stop)
    except (ValueError, TypeError):
        return [day.year]
    if found is None:
        last = (cutoff - timedelta(days=1)).year
        return list(range(day.year, last + 1))
    if not found:
        return [day.year]
    return sorted({_utc_year(occurrence) for occurrence in found})


def _events_by_year(
    events: Mapping[str, Sequence[Event]], cutoff: date
) -> dict[int, dict[str, list[Event]]]:
    grouped: dict[int, dict[str, list[Event]]] = {}
    for calendar_id, calendar_events in events.items():
        for event in calendar_events:
            day = _start_day(event.start)
            if day is None or not day < cutoff:
                continue
            years = _occurrence_years(event, day, cutoff) if event.recurrence else [day.year]
            for year in years:
                grouped.setdefault(year, {}).setdefault(calendar_id, []).append(event)
    return grouped


def _claim(dest: Path, year: int) -> Path:
    copy = 1
    while True:
        path = dest / archive_name(year, copy)
        try:
            with open(path, "xb"):
                pass
        except FileExistsError:
            copy += 1
            continue
        except OSError as err:
            raise ArchiveError(f"cannot write archive {path}: {err}") from err
        return path


def _atomic_zip(path: Path, members: Sequence[tuple[str, bytes]]) -> None:
    tmp: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".part")
        os.close(fd)
        tmp = Path(tmp_name)
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in members:
                archive.writestr(name, data)
        os.replace(tmp, path)
    except OSError as err:
        for leftover in (tmp, path):
            if leftover is not None:
                with contextlib.suppress(OSError):
                    os.unlink(leftover)
        raise ArchiveError(f"cannot write archive {path}: {err}") from err


def write_archive(events: Mapping[str, Sequence[Event]], dest: Path, cutoff: date) -> list[Path]:
    if not dest.exists():
        raise ArchiveError(f"archive destination does not exist: {dest}")
    if not dest.is_dir():
        raise ArchiveError(f"archive destination is not a directory: {dest}")
    _reject_shared_names(events)
    grouped = _events_by_year(events, cutoff)
    written: list[Path] = []
    for year in sorted(grouped):
        members: list[tuple[str, bytes]] = []
        for calendar_id in sorted(grouped[year]):
            calendar_events = grouped[year][calendar_id]
            safe = _safe_name(calendar_id)
            members.append((f"{safe}.ics", _ics(calendar_events)))
            members.append((f"{safe}.json", _json_member(calendar_id, calendar_events)))
        path = _claim(dest, year)
        _atomic_zip(path, members)
        written.append(path)
    return written


def archived_sources(paths: Iterable[Path]) -> set[str]:
    found: set[str] = set()
    for path in paths:
        try:
            with zipfile.ZipFile(path) as archive:
                for name in archive.namelist():
                    if not name.endswith(".json"):
                        continue
                    document = json.loads(archive.read(name))
                    calendar_id = document["calendar_id"]
                    for raw in document["events"]:
                        found.add(f"{calendar_id}/{raw['id']}")
        except (
            OSError,
            zipfile.BadZipFile,
            json.JSONDecodeError,
            UnicodeDecodeError,
            KeyError,
            TypeError,
        ) as err:
            raise ArchiveError(f"cannot read archive {path}: {err}") from err
    return found
