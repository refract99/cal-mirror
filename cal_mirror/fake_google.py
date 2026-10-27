"""In-memory stand-in for the Google Calendar service surface used in tests."""

import copy
import re
from datetime import UTC, datetime, timedelta

import httplib2
from googleapiclient.errors import HttpError

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
_PAGE = re.compile(r"^page-(\d+)$")
_SUFFIX_DATE_TIME = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$")
_SUFFIX_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def _http_error(status: int, reason: str) -> HttpError:
    return HttpError(httplib2.Response({"status": str(status)}), reason.encode())


def _nonempty_recurrence(value: object) -> bool:
    return isinstance(value, list | tuple) and len(value) > 0


def _missing_time_zone(body: dict, recurrence: object) -> bool:
    if not _nonempty_recurrence(recurrence):
        return False
    for key in ("start", "end"):
        point = body.get(key)
        if isinstance(point, dict) and point.get("dateTime") and not point.get("timeZone"):
            return True
    return False


def _updated(change: int) -> str:
    stamp = _EPOCH + timedelta(seconds=change)
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_rfc3339(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _point(value: object) -> datetime | None:
    if not isinstance(value, dict):
        return None
    date_time = value.get("dateTime")
    if isinstance(date_time, str):
        return _parse_rfc3339(date_time)
    date = value.get("date")
    if isinstance(date, str):
        return datetime.fromisoformat(date).replace(tzinfo=UTC)
    return None


def _start_and_end(item: dict) -> tuple[datetime | None, datetime | None]:
    start = _point(item.get("start"))
    if start is None:
        start = _point(item.get("originalStartTime"))
    end = _point(item.get("end"))
    if end is None:
        end = _point(item.get("originalStartTime"))
    return start, end


def _in_window(item: dict, time_min: datetime | None, time_max: datetime | None) -> bool:
    start, end = _start_and_end(item)
    if start is None:
        return True
    if item.get("recurrence"):
        return time_max is None or start < time_max
    if time_min is not None and (end is None or not end > time_min):
        return False
    return time_max is None or start < time_max


def _original_start(suffix: str) -> str | None:
    matched = _SUFFIX_DATE_TIME.fullmatch(suffix)
    if matched is not None:
        year, month, day, hour, minute, second = matched.groups()
        return f"{year}-{month}-{day}T{hour}:{minute}:{second}Z"
    matched = _SUFFIX_DATE.fullmatch(suffix)
    if matched is not None:
        year, month, day = matched.groups()
        return f"{year}-{month}-{day}"
    return None


def _original_start_time(original_start: str) -> dict[str, str]:
    if "T" in original_start:
        return {"dateTime": original_start}
    return {"date": original_start}


class _Call:
    def __init__(self, func):
        self._func = func

    def execute(self, http=None, num_retries=0):
        return self._func()


class FakeCalendar:
    """Calendar and event state. Seeding is not recorded in `writes`."""

    def __init__(self) -> None:
        self.calendars: dict[str, dict] = {}
        self.events: dict[str, dict[str, dict]] = {}
        self.writes: list[dict] = []
        self._change = 0
        self._ids = 0
        self._changed: dict[tuple[str, str], int] = {}
        self._tokens: dict[str, tuple[str, int]] = {}
        self._token_seq = 0

    def add_calendar(
        self,
        id: str,
        summary: str = "",
        access_role: str = "owner",
        primary: bool = False,
    ) -> dict:
        self._change += 1
        item = {
            "id": id,
            "summary": summary,
            "accessRole": access_role,
            "primary": primary,
        }
        self.calendars[id] = item
        self.events.setdefault(id, {})
        return copy.deepcopy(item)

    def add_event(self, calendar_id: str, item: dict) -> dict:
        stored = self._store_new(calendar_id, item, missing="raise")
        return copy.deepcopy(stored)

    def edit_event(self, calendar_id: str, event_id: str, **fields) -> dict:
        item = self._require_event(calendar_id, event_id)
        item.update(copy.deepcopy(fields))
        self._touch(calendar_id, item["id"], item)
        return copy.deepcopy(item)

    def cancel_event(self, calendar_id: str, event_id: str) -> dict:
        item = self._require_event(calendar_id, event_id)
        item["status"] = "cancelled"
        self._touch(calendar_id, event_id, item)
        return copy.deepcopy(item)

    def add_exception(
        self,
        calendar_id: str,
        series_id: str,
        original_start: str,
        **fields,
    ) -> dict:
        item = self._exception_from_series(calendar_id, series_id, original_start)
        item.update(copy.deepcopy(fields))
        self._touch(calendar_id, item["id"], item)
        self.events[calendar_id][item["id"]] = item
        return copy.deepcopy(item)

    def expire_sync_tokens(self) -> None:
        self._tokens.clear()

    def list_calendars(self, page_token: str | None) -> dict:
        if page_token is not None:
            raise _http_error(400, "Invalid page token")
        items = [copy.deepcopy(item) for item in self.calendars.values()]
        return {"items": items}

    def list_events(
        self,
        calendar_id: str,
        sync_token: str | None,
        time_min: str | None,
        time_max: str | None,
        page_token: str | None,
        show_deleted: bool,
        max_results: int,
    ) -> dict:
        events = self._events_or_404(calendar_id)
        if sync_token is not None and (time_min is not None or time_max is not None):
            raise _http_error(400, "timeMin and timeMax cannot be used with syncToken")
        if sync_token is not None:
            floor = self._token_floor(calendar_id, sync_token)
            chosen = [
                item for item in events.values() if self._changed[(calendar_id, item["id"])] > floor
            ]
            chosen.sort(key=lambda item: self._changed[(calendar_id, item["id"])])
        else:
            parsed_min = _parse_rfc3339(time_min) if time_min is not None else None
            parsed_max = _parse_rfc3339(time_max) if time_max is not None else None
            chosen = []
            for item in events.values():
                if not show_deleted and item.get("status") == "cancelled":
                    continue
                if not _in_window(item, parsed_min, parsed_max):
                    continue
                chosen.append(item)
        return self._page(calendar_id, chosen, page_token, max_results)

    def insert_event(self, calendar_id: str, body: dict) -> dict:
        if calendar_id not in self.calendars:
            raise _http_error(404, "Not Found")
        if body.get("id") in self.events[calendar_id]:
            raise _http_error(409, "Conflict")
        if _missing_time_zone(body, body.get("recurrence")):
            raise _http_error(400, "Missing time zone definition for start time.")
        stored = self._store_new(calendar_id, body, missing="error")
        return copy.deepcopy(stored)

    def patch_event(self, calendar_id: str, event_id: str, body: dict) -> dict:
        events = self._events_or_404(calendar_id)
        fresh = False
        if event_id in events:
            item = events[event_id]
        else:
            item = self._exception_from_patch_id(calendar_id, event_id)
            fresh = True
        recurrence = body["recurrence"] if "recurrence" in body else item.get("recurrence")
        if _missing_time_zone(body, recurrence):
            raise _http_error(400, "Missing time zone definition for start time.")
        if fresh:
            events[item["id"]] = item
        item.update(copy.deepcopy(body))
        self._touch(calendar_id, item["id"], item)
        if item["id"] != event_id:
            events.pop(event_id, None)
            events[item["id"]] = item
        return copy.deepcopy(item)

    def delete_event(self, calendar_id: str, event_id: str) -> str:
        item = self._event_or_404(calendar_id, event_id)
        if item.get("status") == "cancelled":
            raise _http_error(410, "Gone")
        item["status"] = "cancelled"
        self._touch(calendar_id, event_id, item)
        return ""

    def move_event(self, calendar_id: str, event_id: str, destination: str) -> dict:
        if calendar_id not in self.calendars or destination not in self.calendars:
            raise _http_error(404, "Not Found")
        if event_id not in self.events[calendar_id]:
            raise _http_error(404, "Not Found")
        source = self.events[calendar_id][event_id]
        moved = copy.deepcopy(source)
        if moved.get("status") == "cancelled":
            moved["status"] = "confirmed"
        self.events[destination][event_id] = moved
        self._touch(destination, event_id, moved)
        source["status"] = "cancelled"
        self._touch(calendar_id, event_id, source)
        return copy.deepcopy(moved)

    def get_event(self, calendar_id: str, event_id: str) -> dict:
        return copy.deepcopy(self._event_or_404(calendar_id, event_id))

    def record(self, method: str, **params) -> None:
        if "body" in params:
            params["body"] = copy.deepcopy(params["body"])
        self.writes.append({"method": method, **params})

    def _store_new(self, calendar_id: str, item: dict, *, missing: str) -> dict:
        if calendar_id not in self.calendars:
            if missing == "raise":
                raise KeyError(calendar_id)
            raise _http_error(404, "Not Found")
        stored = copy.deepcopy(item)
        if "id" not in stored:
            stored["id"] = self._alloc_id()
        if "status" not in stored:
            stored["status"] = "confirmed"
        self._touch(calendar_id, stored["id"], stored)
        self.events[calendar_id][stored["id"]] = stored
        return stored

    def _require_event(self, calendar_id: str, event_id: str) -> dict:
        if calendar_id not in self.calendars:
            raise KeyError(calendar_id)
        try:
            return self.events[calendar_id][event_id]
        except KeyError:
            raise KeyError(event_id) from None

    def _events_or_404(self, calendar_id: str) -> dict[str, dict]:
        try:
            return self.events[calendar_id]
        except KeyError:
            raise _http_error(404, "Not Found") from None

    def _event_or_404(self, calendar_id: str, event_id: str) -> dict:
        events = self._events_or_404(calendar_id)
        try:
            return events[event_id]
        except KeyError:
            raise _http_error(404, "Not Found") from None

    def _exception_from_series(
        self,
        calendar_id: str,
        series_id: str,
        original_start: str,
    ) -> dict:
        if calendar_id not in self.calendars:
            raise KeyError(calendar_id)
        try:
            series = self.events[calendar_id][series_id]
        except KeyError:
            raise KeyError(series_id) from None
        suffix = original_start.replace("-", "").replace(":", "")
        item = copy.deepcopy(series)
        item.pop("recurrence", None)
        item["id"] = f"{series_id}_{suffix}"
        item["recurringEventId"] = series_id
        item["originalStartTime"] = _original_start_time(original_start)
        return item

    def _exception_from_patch_id(self, calendar_id: str, event_id: str) -> dict:
        series_id, separator, suffix = event_id.partition("_")
        if separator == "":
            raise _http_error(404, "Not Found")
        original = _original_start(suffix)
        if original is None or series_id not in self.events[calendar_id]:
            raise _http_error(404, "Not Found")
        series = self.events[calendar_id][series_id]
        item = copy.deepcopy(series)
        item.pop("recurrence", None)
        item["id"] = event_id
        item["recurringEventId"] = series_id
        item["originalStartTime"] = _original_start_time(original)
        return item

    def _alloc_id(self) -> str:
        self._ids += 1
        return f"evt{self._ids}"

    def _touch(self, calendar_id: str, event_id: str, item: dict) -> None:
        self._change += 1
        self._changed[(calendar_id, event_id)] = self._change
        item["updated"] = _updated(self._change)

    def _issue_token(self, calendar_id: str) -> str:
        self._token_seq += 1
        token = f"sync-{self._token_seq}"
        self._tokens[token] = (calendar_id, self._change)
        return token

    def _token_floor(self, calendar_id: str, token: str) -> int:
        found = self._tokens.get(token)
        if found is None or found[0] != calendar_id:
            raise _http_error(410, "Sync token is no longer valid")
        return found[1]

    def _page(
        self,
        calendar_id: str,
        items: list[dict],
        page_token: str | None,
        max_results: int,
    ) -> dict:
        offset = 0 if page_token is None else _page_offset(page_token, len(items))
        chunk = items[offset : offset + max_results]
        copied = [copy.deepcopy(item) for item in chunk]
        if offset + len(chunk) < len(items):
            return {"items": copied, "nextPageToken": f"page-{offset + len(chunk)}"}
        return {"items": copied, "nextSyncToken": self._issue_token(calendar_id)}


def _page_offset(page_token: str, count: int) -> int:
    matched = _PAGE.fullmatch(page_token)
    if matched is None:
        raise _http_error(400, "Invalid page token")
    offset = int(matched.group(1))
    if offset > count or (offset == count and offset != 0):
        raise _http_error(400, "Invalid page token")
    return offset


class _CalendarList:
    def __init__(self, state: FakeCalendar) -> None:
        self._state = state

    def list(self, *, pageToken: str | None = None):
        return _Call(lambda: self._state.list_calendars(pageToken))


class _Events:
    def __init__(self, state: FakeCalendar) -> None:
        self._state = state

    def list(
        self,
        *,
        calendarId: str,
        syncToken: str | None = None,
        timeMin: str | None = None,
        timeMax: str | None = None,
        pageToken: str | None = None,
        showDeleted: bool = True,
        singleEvents: bool = False,
        maxResults: int = 2500,
    ):
        def run():
            return self._state.list_events(
                calendarId,
                syncToken,
                timeMin,
                timeMax,
                pageToken,
                showDeleted,
                maxResults,
            )

        return _Call(run)

    def insert(
        self,
        *,
        calendarId: str,
        body: dict,
        sendUpdates: str | None = None,
        conferenceDataVersion: int = 1,
        supportsAttachments: bool = True,
    ):
        def run():
            item = self._state.insert_event(calendarId, body)
            self._state.record(
                "insert",
                calendarId=calendarId,
                body=body,
                sendUpdates=sendUpdates,
                conferenceDataVersion=conferenceDataVersion,
                supportsAttachments=supportsAttachments,
            )
            return item

        return _Call(run)

    def patch(
        self,
        *,
        calendarId: str,
        eventId: str,
        body: dict,
        sendUpdates: str | None = None,
        conferenceDataVersion: int = 1,
        supportsAttachments: bool = True,
    ):
        def run():
            item = self._state.patch_event(calendarId, eventId, body)
            self._state.record(
                "patch",
                calendarId=calendarId,
                eventId=eventId,
                body=body,
                sendUpdates=sendUpdates,
                conferenceDataVersion=conferenceDataVersion,
                supportsAttachments=supportsAttachments,
            )
            return item

        return _Call(run)

    def delete(
        self,
        *,
        calendarId: str,
        eventId: str,
        sendUpdates: str | None = None,
    ):
        def run():
            result = self._state.delete_event(calendarId, eventId)
            self._state.record(
                "delete",
                calendarId=calendarId,
                eventId=eventId,
                sendUpdates=sendUpdates,
            )
            return result

        return _Call(run)

    def move(
        self,
        *,
        calendarId: str,
        eventId: str,
        destination: str,
        sendUpdates: str | None = None,
    ):
        def run():
            item = self._state.move_event(calendarId, eventId, destination)
            self._state.record(
                "move",
                calendarId=calendarId,
                eventId=eventId,
                destination=destination,
                sendUpdates=sendUpdates,
            )
            return item

        return _Call(run)

    def get(self, *, calendarId: str, eventId: str):
        return _Call(lambda: self._state.get_event(calendarId, eventId))


class FakeService:
    """Keyword-only Calendar API. Nothing is stored until `execute()`."""

    def __init__(self, state: FakeCalendar | None = None) -> None:
        self.state = FakeCalendar() if state is None else state

    def calendarList(self) -> _CalendarList:
        return _CalendarList(self.state)

    def events(self) -> _Events:
        return _Events(self.state)
