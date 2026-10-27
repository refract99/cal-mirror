from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol

from cal_mirror.model import (
    AlertState,
    AuthRecord,
    CalendarInfo,
    Event,
    Link,
    PlacementEntry,
    PlannedWrite,
    Request,
    Rule,
    Settings,
    Status,
)


class CalendarError(Exception):
    pass


class CalendarAuthError(CalendarError):
    pass


class SyncTokenExpired(CalendarError):
    pass


class NotifyError(Exception):
    pass


class StoreError(Exception):
    pass


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    def __init__(self, at: datetime) -> None:
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, seconds: float) -> None:
        self._at += timedelta(seconds=seconds)


class SecretBox(Protocol):
    def encrypt(self, plain: str) -> str: ...

    def decrypt(self, token: str) -> str: ...


class Notifier(Protocol):
    def send(self, title: str, message: str, click: str = "") -> None: ...


class Calendar(Protocol):
    def list_calendars(self) -> list[CalendarInfo]: ...

    def list_changes(
        self,
        calendar_id: str,
        sync_token: str | None,
        time_min: datetime | None = None,
    ) -> tuple[list[Event], str]: ...

    def list_events(
        self,
        calendar_id: str,
        time_min: datetime | None = None,
        time_max: datetime | None = None,
    ) -> list[Event]: ...

    def get_event(self, calendar_id: str, event_id: str) -> Event | None: ...

    def apply(self, write: PlannedWrite) -> str: ...


class Store(Protocol):
    def get_settings(self) -> Settings: ...

    def save_settings(self, s: Settings) -> None: ...

    def list_rules(self) -> list[Rule]: ...

    def save_rules(self, rules: list[Rule]) -> None: ...

    def list_calendars(self) -> list[CalendarInfo]: ...

    def set_calendars(self, items: list[CalendarInfo]) -> None: ...

    def get_sync_token(self, calendar_id: str) -> str | None: ...

    def set_sync_token(self, calendar_id: str, token: str | None) -> None: ...

    def get_link(self, source: str) -> Link | None: ...

    def put_link(self, link: Link) -> None: ...

    def delete_link(self, source: str) -> None: ...

    def list_links(self) -> list[Link]: ...

    def add_placement(self, entry: PlacementEntry) -> int: ...

    def list_placements(self, limit: int = 100) -> list[PlacementEntry]: ...

    def get_placement(self, id: int) -> PlacementEntry | None: ...

    def get_notice_state(self, source: str) -> dict[str, str]: ...

    def set_notice_state(self, source: str, state: Mapping[str, str]) -> None: ...

    def get_alert_state(self) -> AlertState: ...

    def set_alert_state(self, a: AlertState) -> None: ...

    def get_status(self) -> Status: ...

    def set_status(self, s: Status) -> None: ...

    def add_request(self, kind: str, params: Mapping) -> int: ...

    def get_request(self, id: int) -> Request | None: ...

    def update_request(self, req: Request) -> None: ...

    def list_requests(self, state: str | None = None) -> list[Request]: ...

    def get_secret(self, name: str) -> str | None: ...

    def set_secret(self, name: str, value: str | None) -> None: ...

    def get_auth(self) -> AuthRecord: ...

    def save_auth(self, a: AuthRecord) -> None: ...

    def create_session(self, token_hash: str, expires: datetime) -> None: ...

    def session_valid(self, token_hash: str, now: datetime) -> bool: ...

    def delete_session(self, token_hash: str) -> None: ...

    def get_meta(self, key: str) -> str | None: ...

    def set_meta(self, key: str, value: str) -> None: ...
