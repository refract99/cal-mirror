import copy
import dataclasses
import json
from collections.abc import Mapping
from datetime import UTC, datetime

from cal_mirror.model import (
    REQ_PENDING,
    AlertState,
    AuthRecord,
    CalendarInfo,
    Link,
    PlacementEntry,
    Request,
    Rule,
    Settings,
    Status,
)
from cal_mirror.ports import StoreError


def _json_copy(value: Mapping) -> dict:
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError) as exc:
        raise StoreError(f"cannot encode as JSON: {exc}") from exc


class MemoryStore:
    def __init__(self) -> None:
        self._settings = Settings()
        self._rules: list[Rule] = []
        self._calendars: list[CalendarInfo] = []
        self._sync_tokens: dict[str, str] = {}
        self._links: dict[str, Link] = {}
        self._placements: dict[int, PlacementEntry] = {}
        self._next_placement = 1
        self._notices: dict[str, dict[str, str]] = {}
        self._alert_state = AlertState()
        self._status = Status()
        self._requests: dict[int, Request] = {}
        self._next_request = 1
        self._secrets: dict[str, str] = {}
        self._auth = AuthRecord()
        self._sessions: dict[str, datetime] = {}
        self._meta: dict[str, str] = {}

    def get_settings(self) -> Settings:
        return self._settings

    def save_settings(self, s: Settings) -> None:
        self._settings = s

    def list_rules(self) -> list[Rule]:
        return list(self._rules)

    def save_rules(self, rules: list[Rule]) -> None:
        self._rules = list(rules)

    def list_calendars(self) -> list[CalendarInfo]:
        return list(self._calendars)

    def set_calendars(self, items: list[CalendarInfo]) -> None:
        self._calendars = list(items)

    def get_sync_token(self, calendar_id: str) -> str | None:
        return self._sync_tokens.get(calendar_id)

    def set_sync_token(self, calendar_id: str, token: str | None) -> None:
        if token is None:
            self._sync_tokens.pop(calendar_id, None)
        else:
            self._sync_tokens[calendar_id] = token

    def get_link(self, source: str) -> Link | None:
        return self._links.get(source)

    def put_link(self, link: Link) -> None:
        self._links[link.source] = link

    def delete_link(self, source: str) -> None:
        self._links.pop(source, None)

    def list_links(self) -> list[Link]:
        return [self._links[source] for source in sorted(self._links)]

    def add_placement(self, entry: PlacementEntry) -> int:
        new_id = self._next_placement
        self._next_placement += 1
        self._placements[new_id] = dataclasses.replace(entry, id=new_id)
        return new_id

    def list_placements(self, limit: int = 100) -> list[PlacementEntry]:
        if limit <= 0:
            return []
        ids = sorted(self._placements, reverse=True)[:limit]
        return [self._placements[i] for i in ids]

    def get_placement(self, id: int) -> PlacementEntry | None:
        return self._placements.get(id)

    def get_notice_state(self, source: str) -> dict[str, str]:
        return dict(self._notices.get(source, {}))

    def set_notice_state(self, source: str, state: Mapping[str, str]) -> None:
        self._notices[source] = dict(state)

    def get_alert_state(self) -> AlertState:
        return self._alert_state

    def set_alert_state(self, a: AlertState) -> None:
        self._alert_state = a

    def get_status(self) -> Status:
        return self._status

    def set_status(self, s: Status) -> None:
        self._status = s

    def add_request(self, kind: str, params: Mapping) -> int:
        stored = _json_copy(params)
        new_id = self._next_request
        self._next_request += 1
        self._requests[new_id] = Request(
            id=new_id,
            kind=kind,
            state=REQ_PENDING,
            params=stored,
            result={},
            created=datetime.now(UTC),
        )
        return new_id

    def _fresh(self, req: Request) -> Request:
        return dataclasses.replace(
            req, params=copy.deepcopy(req.params), result=copy.deepcopy(req.result)
        )

    def get_request(self, id: int) -> Request | None:
        req = self._requests.get(id)
        return None if req is None else self._fresh(req)

    def update_request(self, req: Request) -> None:
        current = self._requests.get(req.id)
        if current is None:
            raise StoreError(f"unknown request {req.id}")
        params = _json_copy(req.params)
        result = _json_copy(req.result)
        self._requests[req.id] = dataclasses.replace(
            current, kind=req.kind, state=req.state, params=params, result=result
        )

    def list_requests(self, state: str | None = None) -> list[Request]:
        return [
            self._fresh(self._requests[i])
            for i in sorted(self._requests)
            if state is None or self._requests[i].state == state
        ]

    def get_secret(self, name: str) -> str | None:
        return self._secrets.get(name)

    def set_secret(self, name: str, value: str | None) -> None:
        if value is None:
            self._secrets.pop(name, None)
        else:
            self._secrets[name] = value

    def get_auth(self) -> AuthRecord:
        return self._auth

    def save_auth(self, a: AuthRecord) -> None:
        self._auth = a

    def create_session(self, token_hash: str, expires: datetime) -> None:
        self._sessions[token_hash] = expires

    def session_valid(self, token_hash: str, now: datetime) -> bool:
        expires = self._sessions.get(token_hash)
        return expires is not None and expires > now

    def delete_session(self, token_hash: str) -> None:
        self._sessions.pop(token_hash, None)

    def get_meta(self, key: str) -> str | None:
        return self._meta.get(key)

    def set_meta(self, key: str, value: str) -> None:
        self._meta[key] = value
