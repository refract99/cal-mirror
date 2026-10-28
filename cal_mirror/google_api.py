import copy
import json
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from typing import Any

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from cal_mirror.model import (
    OP_CREATE,
    OP_DELETE,
    OP_MOVE,
    OP_UPDATE,
    Attachment,
    Attendee,
    CalendarInfo,
    Event,
    PlannedWrite,
)
from cal_mirror.ports import CalendarAuthError, CalendarError, SyncTokenExpired

SCOPES = ["https://www.googleapis.com/auth/calendar"]

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"})
_PAGE_SIZE = 2500


def _parse_time(value: Mapping) -> tuple[datetime | date | None, bool]:
    if value.get("dateTime"):
        return datetime.fromisoformat(value["dateTime"]).astimezone(UTC), False
    if value.get("date"):
        return date.fromisoformat(value["date"]), True
    return None, False


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).strftime(_TIME_FORMAT)


def _write_zone(event: Event) -> str:
    if event.time_zone:
        return event.time_zone
    if event.recurrence:
        return "UTC"
    return ""


def _time_body(value: datetime | date, zone: str) -> dict[str, str]:
    if isinstance(value, datetime):
        body = {"dateTime": _utc_text(value)}
        if zone:
            body["timeZone"] = zone
        return body
    return {"date": value.isoformat()}


def _dial_in(entry_points: list) -> tuple[str, ...]:
    lines = []
    for point in entry_points:
        if point.get("entryPointType") != "phone":
            continue
        number = point.get("label") or str(point.get("uri", "")).removeprefix("tel:")
        pin = point.get("pin")
        lines.append(f"{number} PIN {pin}" if pin else number)
    return tuple(lines)


def _meeting_link(item: Mapping, entry_points: list) -> str:
    if item.get("hangoutLink"):
        return item["hangoutLink"]
    for point in entry_points:
        if point.get("entryPointType") == "video":
            return point.get("uri", "")
    return ""


def event_from_google(calendar_id: str, item: Mapping) -> Event:
    start, start_all_day = _parse_time(item.get("start") or {})
    end, end_all_day = _parse_time(item.get("end") or {})
    conference = item.get("conferenceData") or {}
    entry_points = conference.get("entryPoints") or []
    updated = item.get("updated")
    return Event(
        calendar_id=calendar_id,
        id=item.get("id", ""),
        status=item.get("status") or "confirmed",
        summary=item.get("summary", ""),
        description=item.get("description", ""),
        location=item.get("location", ""),
        start=start,
        end=end,
        all_day=start_all_day or end_all_day,
        recurrence=tuple(item.get("recurrence") or ()),
        recurring_event_id=item.get("recurringEventId", ""),
        transparency=item.get("transparency") or "opaque",
        visibility=item.get("visibility") or "default",
        organizer_email=(item.get("organizer") or {}).get("email", ""),
        creator_email=(item.get("creator") or {}).get("email", ""),
        attendees=tuple(
            Attendee(
                email=a.get("email", ""),
                response=a.get("responseStatus") or "needsAction",
                is_self=bool(a.get("self", False)),
                organizer=bool(a.get("organizer", False)),
            )
            for a in item.get("attendees") or ()
        ),
        attachments=tuple(
            Attachment(
                file_url=a.get("fileUrl", ""),
                title=a.get("title", ""),
                mime_type=a.get("mimeType", ""),
                file_id=a.get("fileId", ""),
            )
            for a in item.get("attachments") or ()
        ),
        meeting_link=_meeting_link(item, entry_points),
        dial_in=_dial_in(entry_points),
        conference=conference,
        private=(item.get("extendedProperties") or {}).get("private") or {},
        updated=datetime.fromisoformat(updated).astimezone(UTC) if updated else None,
        raw=dict(item),
        time_zone=(item.get("start") or {}).get("timeZone", ""),
    )


def event_to_google(event: Event) -> dict:
    body: dict[str, Any] = {
        "summary": event.summary,
        "description": event.description,
        "location": event.location,
    }
    zone = _write_zone(event)
    if event.start is not None:
        body["start"] = _time_body(event.start, zone)
    if event.end is not None:
        body["end"] = _time_body(event.end, zone)
    body["recurrence"] = list(event.recurrence)
    body["transparency"] = event.transparency
    body["visibility"] = event.visibility
    body["attachments"] = [
        {key: value for key, value in fields.items() if value}
        for fields in (
            {
                "fileUrl": a.file_url,
                "title": a.title,
                "mimeType": a.mime_type,
                "fileId": a.file_id,
            }
            for a in event.attachments
        )
    ]
    if event.conference:
        conference = copy.deepcopy(dict(event.conference))
        conference.pop("createRequest", None)
        body["conferenceData"] = conference
    body["extendedProperties"] = {"private": dict(event.private)}
    body["attendees"] = []
    return body


def _error_reasons(err: HttpError) -> set[str]:
    details = err.error_details
    if not isinstance(details, list):
        return set()
    return {str(d["reason"]) for d in details if isinstance(d, dict) and "reason" in d}


def _translate(err: HttpError | RefreshError, method: str, *, expired_ok: bool = False):
    if isinstance(err, RefreshError):
        reason = str(err.args[0])[:80] if err.args else ""
        return CalendarAuthError(f"Google token rejected: {reason}".rstrip(": "))
    status = int(err.resp.status)
    reasons = ",".join(sorted(_error_reasons(err)))
    message = f"Google API {method} failed: HTTP {status}" + (f" ({reasons})" if reasons else "")
    if status == 410 and expired_ok:
        return SyncTokenExpired(message)
    if status == 401 or (status == 403 and not _error_reasons(err) & _RATE_REASONS):
        return CalendarAuthError(message)
    return CalendarError(message)


def _call(method: str, request: Callable[[], Any], *, expired_ok: bool = False) -> Any:
    try:
        return request()
    except (HttpError, RefreshError) as err:
        raise _translate(err, method, expired_ok=expired_ok) from None


def _update_body(event: Event, *, confirm: bool = False) -> dict[str, Any]:
    body = event_to_google(event)
    if not event.conference:
        body["conferenceData"] = None
    if confirm:
        body["status"] = "confirmed"
    return body


def _insert_args(calendar_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "calendarId": calendar_id,
        "body": body,
        "sendUpdates": "none",
        "conferenceDataVersion": 1,
        "supportsAttachments": True,
    }


def _patch_args(calendar_id: str, event_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "calendarId": calendar_id,
        "eventId": event_id,
        "body": body,
        "sendUpdates": "none",
        "conferenceDataVersion": 1,
        "supportsAttachments": True,
    }


def send_write(service: Any, write: PlannedWrite) -> str:
    """The one place the app writes to Google; sendUpdates is always "none"."""
    events = service.events()
    if write.op in (OP_CREATE, OP_UPDATE):
        event = write.body
        if event is None:
            raise ValueError(f"{write.op} needs a body")
        if write.op == OP_CREATE:
            body = event_to_google(event)
            if write.event_id:
                body = {**body, "id": write.event_id}
            args = _insert_args(write.calendar_id, body)
            try:
                return events.insert(**args).execute()["id"]
            except RefreshError as err:
                raise _translate(err, "events.insert") from None
            except HttpError as err:
                if not write.event_id or int(err.resp.status) != 409:
                    raise _translate(err, "events.insert") from None
            patch = _patch_args(
                write.calendar_id, write.event_id, _update_body(event, confirm=True)
            )
            return _call("events.patch", lambda: events.patch(**patch).execute())["id"]
        args = _patch_args(write.calendar_id, write.event_id, _update_body(event))
        return _call("events.patch", lambda: events.patch(**args).execute())["id"]
    if write.op == OP_DELETE:
        _call(
            "events.delete",
            lambda: events.delete(
                calendarId=write.calendar_id, eventId=write.event_id, sendUpdates="none"
            ).execute(),
        )
        return write.event_id
    if write.op == OP_MOVE:
        moved = _call(
            "events.move",
            lambda: events.move(
                calendarId=write.calendar_id,
                eventId=write.event_id,
                destination=write.destination,
                sendUpdates="none",
            ).execute(),
        )
        return moved["id"]
    raise ValueError(f"unknown write op: {write.op!r}")


class GoogleCalendar:
    def __init__(self, service: Any) -> None:
        self._service = service

    def list_calendars(self) -> list[CalendarInfo]:
        found: list[CalendarInfo] = []
        page_token = None
        while True:
            response = _call(
                "calendarList.list",
                lambda token=page_token: (
                    self._service.calendarList().list(pageToken=token).execute()
                ),
            )
            for item in response.get("items") or ():
                found.append(
                    CalendarInfo(
                        id=item.get("id", ""),
                        summary=item.get("summary", ""),
                        access_role=item.get("accessRole", ""),
                        primary=bool(item.get("primary", False)),
                    )
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                return found

    def _list_events(
        self, calendar_id: str, extra: Mapping[str, Any], *, expired_ok: bool = False
    ) -> tuple[list[Event], str]:
        found: list[Event] = []
        page_token = None
        while True:
            response = _call(
                "events.list",
                lambda token=page_token: (
                    self._service.events()
                    .list(
                        calendarId=calendar_id,
                        pageToken=token,
                        showDeleted=True,
                        singleEvents=False,
                        maxResults=_PAGE_SIZE,
                        **extra,
                    )
                    .execute()
                ),
                expired_ok=expired_ok,
            )
            found.extend(event_from_google(calendar_id, i) for i in response.get("items") or ())
            page_token = response.get("nextPageToken")
            if not page_token:
                return found, response.get("nextSyncToken") or ""

    def list_changes(
        self,
        calendar_id: str,
        sync_token: str | None,
        time_min: datetime | None = None,
    ) -> tuple[list[Event], str]:
        extra: dict[str, Any] = {}
        if sync_token:
            extra["syncToken"] = sync_token
        elif time_min is not None:
            extra["timeMin"] = _utc_text(time_min)
        return self._list_events(calendar_id, extra, expired_ok=True)

    def list_events(
        self,
        calendar_id: str,
        time_min: datetime | None = None,
        time_max: datetime | None = None,
    ) -> list[Event]:
        extra: dict[str, Any] = {}
        if time_min is not None:
            extra["timeMin"] = _utc_text(time_min)
        if time_max is not None:
            extra["timeMax"] = _utc_text(time_max)
        return self._list_events(calendar_id, extra)[0]

    def get_event(self, calendar_id: str, event_id: str) -> Event | None:
        try:
            item = self._service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        except HttpError as err:
            if int(err.resp.status) == 404:
                return None
            raise _translate(err, "events.get") from None
        except RefreshError as err:
            raise _translate(err, "events.get") from None
        return event_from_google(calendar_id, item)

    def apply(self, write: PlannedWrite) -> str:
        return send_write(self._service, write)


def build_service(token_json: str, client_config: Mapping) -> Any:
    info = json.loads(token_json)
    client = client_config.get("web") or client_config.get("installed") or {}
    for key in ("client_id", "client_secret"):
        if not info.get(key):
            info[key] = client.get(key, "")
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    return build("calendar", "v3", credentials=creds, cache_discovery=False, static_discovery=True)
