# Archive

`write_archive` stores events that start strictly before a cutoff. `archived_sources` reads those zips back. Neither function talks to a store, the network, the clock, or the environment.

## Zip layout

One file per year, named `cal-mirror-<year>.zip` (`archive_name`). The year is the year of the event's start day:

- an all-day `datetime.date` start uses that date
- a `datetime` start uses `start.astimezone(UTC).date()`
- a missing start is left out

A start day equal to the cutoff is not archived. Calendars in a zip are those with at least one event that year, in sorted calendar-id order. Events stay in input order. Each calendar contributes two members:

- `<safe>.ics` — iCalendar (`PRODID:-//cal-mirror//archive//EN`, `VERSION:2.0`) with one `VEVENT` per event. `UID` is `source_key(event)`. `SUMMARY`, `DESCRIPTION`, and `LOCATION` are written only when non-empty. `DTSTART` is `DTSTART;VALUE=DATE:YYYYMMDD` for a date and `DTSTART:YYYYMMDDTHHMMSSZ` for a UTC datetime. `DTEND` is written only when `end` is set, in the same way. A recurrence line that starts with `RRULE:` becomes an `RRULE` (`icalendar.vRecur.from_ical` of the text after the colon). Other recurrence lines, such as `EXDATE`, are not written to the `.ics`.
- `<safe>.json` — UTF-8 `json.dumps({"calendar_id": <id>, "events": [dict(e.raw) for each event]}, sort_keys=True, ensure_ascii=False)`. The raw Google items keep lines the `.ics` omitted.

`write_archive` returns the paths it wrote, sorted by year. No qualifying event returns `[]` and writes nothing. Years the run does not write are left as they are.

## Safe names

`<safe>` is the calendar id with every character outside `[A-Za-z0-9._@-]` replaced by `_`. `Team Cal/x` becomes `Team_Cal_x`. `sec@x.com` stays `sec@x.com`. Two calendar ids in one call that share a `<safe>` raise before any file is written.

## Atomic rerun

Each zip is built with `ZIP_DEFLATED` in a temp file in the destination (`tempfile.mkstemp`, suffix `.part`), then `os.replace`d onto the claimed name. A rerun writes the first free `cal-mirror-<year>-<n>.zip` (`n` = 2, 3, ...; `archive_name(year, n)`), claiming the name with an exclusive create, and never changes an existing file. No `.part` file is left behind. On `OSError` the temp file is removed and the write fails.

A recurring master is written into every year with an occurrence before the cutoff (the shared recurrence budget is checked before iteration and the walk stops at 5000 occurrences; a series that exceeds that bound is archived in every year from its start through the year before the cutoff), and into its start year only when the rule cannot be expanded or has no occurrence before the cutoff.

The destination is checked first, including when no event qualifies.

## Errors

`ArchiveError` messages:

- `archive destination does not exist: <dest>` — the destination path is missing
- `archive destination is not a directory: <dest>` — the path exists but is not a directory
- `calendar ids share an archive name: <safe>` — two calendar ids in the call sanitize to the same member name
- `cannot write archive <path>: <err>` — `OSError` while writing that year's zip
- `cannot read archive <path>: <err>` — `archived_sources` hit a missing file, a file that is not a zip (`zipfile.BadZipFile`), invalid JSON, a non-UTF-8 member, or a `.json` member missing `calendar_id`, `events`, or an event `id`

`archived_sources` returns `{f"{calendar_id}/{id}"}` for every event in every `.json` member of the given zips.
