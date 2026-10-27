from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time


@dataclass(frozen=True)
class Attendee:
    email: str
    response: str = "needsAction"
    is_self: bool = False
    organizer: bool = False


@dataclass(frozen=True)
class Attachment:
    file_url: str
    title: str = ""
    mime_type: str = ""
    file_id: str = ""


@dataclass(frozen=True)
class Event:
    calendar_id: str
    id: str
    status: str = "confirmed"
    summary: str = ""
    description: str = ""
    location: str = ""
    start: datetime | date | None = None
    end: datetime | date | None = None
    all_day: bool = False
    recurrence: tuple[str, ...] = ()
    recurring_event_id: str = ""
    transparency: str = "opaque"
    visibility: str = "default"
    organizer_email: str = ""
    creator_email: str = ""
    attendees: tuple[Attendee, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    meeting_link: str = ""
    dial_in: tuple[str, ...] = ()
    conference: Mapping = field(default_factory=dict, compare=False)
    private: Mapping[str, str] = field(default_factory=dict, compare=False)
    updated: datetime | None = None
    raw: Mapping = field(default_factory=dict, compare=False)
    time_zone: str = field(default="", compare=False)


MARKER_SOURCE = "cal_mirror_source"
MARKER_KIND = "cal_mirror_kind"
KIND_BLOCKED = "blocked"
KIND_COPY = "copy"
BLOCKED_TITLE = "BLOCKED"


def source_key(event: Event) -> str:
    return f"{event.calendar_id}/{event.id}"


def is_mirror(event: Event) -> bool:
    return MARKER_SOURCE in event.private


def is_busy(event: Event) -> bool:
    return event.transparency != "transparent" and event.status != "cancelled"


def declined_by_self(event: Event) -> bool:
    return any(attendee.is_self and attendee.response == "declined" for attendee in event.attendees)


def is_meeting(event: Event, owner_email: str) -> bool:
    if not is_busy(event) or event.all_day:
        return False
    return any(
        attendee.email != owner_email and not attendee.is_self for attendee in event.attendees
    )


@dataclass(frozen=True)
class PlannedWrite:
    op: str
    calendar_id: str
    event_id: str = ""
    body: Event | None = None
    source: str = ""
    destination: str = ""
    reason: str = ""


OP_CREATE = "create"
OP_UPDATE = "update"
OP_DELETE = "delete"
OP_MOVE = "move"


@dataclass(frozen=True)
class Link:
    source: str
    kind: str
    calendar_id: str
    event_id: str
    start: datetime | date | None = None
    rule_id: int | None = None


@dataclass(frozen=True)
class Rule:
    id: int
    kind: str
    value: str
    calendar_id: str


RULE_GUEST_DOMAIN = "guest_domain"
RULE_ORGANIZER = "organizer"
RULE_TITLE_WORD = "title_word"


@dataclass(frozen=True)
class PlacementEntry:
    id: int
    at: datetime
    source: str
    calendar_id: str
    title: str = ""
    organizer_email: str = ""
    guest_domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    main_calendar: str = ""
    secondary_calendars: tuple[str, ...] = ()
    default_calendar: str = ""
    owner_email: str = ""
    time_zone: str = "America/Chicago"
    sync_interval_minutes: int = 5
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    archive_destinations: tuple[str, ...] = ()
    check_no_link: bool = True
    check_hours: bool = True
    allowed_hours: Mapping[int, tuple[time, time]] = field(default_factory=dict, compare=False)
    web_url: str = ""
    two_factor: bool = False


@dataclass(frozen=True)
class CalendarInfo:
    id: str
    summary: str = ""
    access_role: str = ""
    primary: bool = False


@dataclass(frozen=True)
class Notice:
    source: str
    check: str
    start: datetime


CHECK_NO_LINK = "no_call_link"
CHECK_HOURS = "outside_hours"


@dataclass(frozen=True)
class Failure:
    kind: str
    detail: str = ""


FAIL_TOKEN, FAIL_API, FAIL_DB, FAIL_ARCHIVE = (
    "token",
    "google_api",
    "database",
    "archive",
)


@dataclass(frozen=True)
class AlertState:
    active: str = ""
    since: datetime | None = None
    api_errors: int = 0


@dataclass(frozen=True)
class Status:
    last_good_sync: datetime | None = None
    failure: str = ""
    token_ok: bool = True
    counts: Mapping[str, int] = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class AuthRecord:
    password_hash: str = ""
    totp_enabled: bool = False
    recovery_hashes: tuple[str, ...] = ()
    failures: int = 0
    locked_until: datetime | None = None


@dataclass(frozen=True)
class Request:
    id: int
    kind: str
    state: str = "pending"
    params: Mapping = field(default_factory=dict, compare=False)
    result: Mapping = field(default_factory=dict, compare=False)
    created: datetime | None = None


REQ_ARCHIVE = "archive"
REQ_RULES = "rules"
REQ_PENDING = "pending"
REQ_READY = "ready"
REQ_CONFIRMED = "confirmed"
REQ_DONE = "done"
REQ_FAILED = "failed"
REQ_CANCELLED = "cancelled"
