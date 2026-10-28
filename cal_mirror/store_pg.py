from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

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
from cal_mirror.ports import SecretBox, StoreError


def migrate(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            name text PRIMARY KEY,
            applied timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    conn.execute("SELECT pg_advisory_xact_lock(4242)")
    rows = conn.execute("SELECT name FROM schema_version").fetchall()
    applied = {row[0] for row in rows}
    for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
        if path.name in applied:
            continue
        with psycopg.ClientCursor(conn) as cur:
            cur.execute(path.read_text())
        conn.execute("INSERT INTO schema_version (name) VALUES (%s)", (path.name,))


def _dt_out(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _dt_in(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _start_out(start: datetime | date | None) -> str | None:
    if start is None:
        return None
    return start.isoformat()


def _start_in(value: str | None) -> datetime | date | None:
    if value is None:
        return None
    if "T" in value:
        return datetime.fromisoformat(value)
    return date.fromisoformat(value)


def _settings_out(s: Settings) -> dict[str, object]:
    return {
        "main_calendar": s.main_calendar,
        "secondary_calendars": list(s.secondary_calendars),
        "default_calendar": s.default_calendar,
        "owner_email": s.owner_email,
        "time_zone": s.time_zone,
        "sync_interval_minutes": s.sync_interval_minutes,
        "ntfy_server": s.ntfy_server,
        "ntfy_topic": s.ntfy_topic,
        "archive_destinations": list(s.archive_destinations),
        "check_no_link": s.check_no_link,
        "check_hours": s.check_hours,
        "allowed_hours": {
            str(day): [start.isoformat(), end.isoformat()]
            for day, (start, end) in s.allowed_hours.items()
        },
        "web_url": s.web_url,
        "two_factor": s.two_factor,
    }


def _settings_in(data: Mapping[str, object]) -> Settings:
    blank = Settings()
    if "allowed_hours" not in data:
        hours: Mapping[int, tuple[time, time]] = blank.allowed_hours
    else:
        raw = data["allowed_hours"]
        hours = {
            int(day): (time.fromisoformat(span[0]), time.fromisoformat(span[1]))
            for day, span in raw.items()
        }
    return Settings(
        main_calendar=_field(data, "main_calendar", blank.main_calendar),
        secondary_calendars=_tuple_field(data, "secondary_calendars", blank.secondary_calendars),
        default_calendar=_field(data, "default_calendar", blank.default_calendar),
        owner_email=_field(data, "owner_email", blank.owner_email),
        time_zone=_field(data, "time_zone", blank.time_zone),
        sync_interval_minutes=_field(data, "sync_interval_minutes", blank.sync_interval_minutes),
        ntfy_server=_field(data, "ntfy_server", blank.ntfy_server),
        ntfy_topic=_field(data, "ntfy_topic", blank.ntfy_topic),
        archive_destinations=_tuple_field(data, "archive_destinations", blank.archive_destinations),
        check_no_link=_field(data, "check_no_link", blank.check_no_link),
        check_hours=_field(data, "check_hours", blank.check_hours),
        allowed_hours=hours,
        web_url=_field(data, "web_url", blank.web_url),
        two_factor=_field(data, "two_factor", blank.two_factor),
    )


def _alert_out(a: AlertState) -> dict[str, object]:
    return {"active": a.active, "since": _dt_out(a.since), "api_errors": a.api_errors}


def _alert_in(data: Mapping[str, object]) -> AlertState:
    blank = AlertState()
    since = blank.since if "since" not in data else _dt_in(data["since"])
    return AlertState(
        active=_field(data, "active", blank.active),
        since=since,
        api_errors=_field(data, "api_errors", blank.api_errors),
    )


def _status_out(s: Status) -> dict[str, object]:
    return {
        "last_good_sync": _dt_out(s.last_good_sync),
        "failure": s.failure,
        "token_ok": s.token_ok,
        "counts": dict(s.counts),
    }


def _status_in(data: Mapping[str, object]) -> Status:
    blank = Status()
    if "last_good_sync" not in data:
        last = blank.last_good_sync
    else:
        last = _dt_in(data["last_good_sync"])
    if "counts" not in data:
        counts: Mapping[str, int] = blank.counts
    else:
        counts = dict(data["counts"])
    return Status(
        last_good_sync=last,
        failure=_field(data, "failure", blank.failure),
        token_ok=_field(data, "token_ok", blank.token_ok),
        counts=counts,
    )


def _auth_out(a: AuthRecord) -> dict[str, object]:
    return {
        "password_hash": a.password_hash,
        "totp_enabled": a.totp_enabled,
        "recovery_hashes": list(a.recovery_hashes),
        "failures": a.failures,
        "locked_until": _dt_out(a.locked_until),
    }


def _auth_in(data: Mapping[str, object]) -> AuthRecord:
    blank = AuthRecord()
    locked = blank.locked_until if "locked_until" not in data else _dt_in(data["locked_until"])
    return AuthRecord(
        password_hash=_field(data, "password_hash", blank.password_hash),
        totp_enabled=_field(data, "totp_enabled", blank.totp_enabled),
        recovery_hashes=_tuple_field(data, "recovery_hashes", blank.recovery_hashes),
        failures=_field(data, "failures", blank.failures),
        locked_until=locked,
    )


def _field(data: Mapping[str, object], key: str, default: object) -> object:
    if key not in data:
        return default
    return data[key]


def _tuple_field(data: Mapping[str, object], key: str, default: tuple[object, ...]) -> tuple:
    if key not in data:
        return default
    return tuple(data[key])


def _link_in(row: tuple) -> Link:
    source, kind, calendar_id, event_id, start, rule_id = row
    return Link(
        source=source,
        kind=kind,
        calendar_id=calendar_id,
        event_id=event_id,
        start=_start_in(start),
        rule_id=rule_id,
    )


def _placement_in(row: tuple) -> PlacementEntry:
    id_, at, source, calendar_id, title, organizer_email, guest_domains = row
    return PlacementEntry(
        id=id_,
        at=at.astimezone(UTC),
        source=source,
        calendar_id=calendar_id,
        title="" if title is None else title,
        organizer_email="" if organizer_email is None else organizer_email,
        guest_domains=tuple(guest_domains or ()),
    )


def _request_in(row: tuple) -> Request:
    id_, kind, state, params, result, created = row
    return Request(
        id=id_,
        kind=kind,
        state=state,
        params={} if params is None else dict(params),
        result={} if result is None else dict(result),
        created=None if created is None else created.astimezone(UTC),
    )


class PgStore:
    def __init__(self, dsn: str, box: SecretBox) -> None:
        self.dsn = dsn
        self.box = box
        self._call(migrate)

    def _call[T](self, fn: Callable[[psycopg.Connection], T]) -> T:
        try:
            with psycopg.connect(self.dsn) as conn:
                return fn(conn)
        except psycopg.Error as err:
            raise StoreError(str(err)) from err

    def _load_state(self, conn: psycopg.Connection, name: str) -> dict | None:
        row = conn.execute("SELECT data FROM state WHERE name = %s", (name,)).fetchone()
        if row is None:
            return None
        return row[0]

    def _save_state(self, conn: psycopg.Connection, name: str, data: Mapping[str, object]) -> None:
        conn.execute(
            """
            INSERT INTO state (name, data) VALUES (%s, %s)
            ON CONFLICT (name) DO UPDATE SET data = EXCLUDED.data
            """,
            (name, Jsonb(data)),
        )

    def get_settings(self) -> Settings:
        def run(conn: psycopg.Connection) -> Settings:
            data = self._load_state(conn, "settings")
            if data is None:
                return Settings()
            return _settings_in(data)

        return self._call(run)

    def save_settings(self, s: Settings) -> None:
        def run(conn: psycopg.Connection) -> None:
            self._save_state(conn, "settings", _settings_out(s))

        self._call(run)

    def list_rules(self) -> list[Rule]:
        def run(conn: psycopg.Connection) -> list[Rule]:
            rows = conn.execute(
                "SELECT position, id, kind, value, calendar_id FROM rules ORDER BY position"
            ).fetchall()
            return [Rule(id=row[1], kind=row[2], value=row[3], calendar_id=row[4]) for row in rows]

        return self._call(run)

    def save_rules(self, rules: list[Rule]) -> None:
        def run(conn: psycopg.Connection) -> None:
            conn.execute("DELETE FROM rules")
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO rules (position, id, kind, value, calendar_id)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [
                        (position, rule.id, rule.kind, rule.value, rule.calendar_id)
                        for position, rule in enumerate(rules)
                    ],
                )

        self._call(run)

    def list_calendars(self) -> list[CalendarInfo]:
        def run(conn: psycopg.Connection) -> list[CalendarInfo]:
            rows = conn.execute(
                """
                SELECT position, id, summary, access_role, is_primary
                FROM calendars ORDER BY position
                """
            ).fetchall()
            return [
                CalendarInfo(
                    id=row[1],
                    summary="" if row[2] is None else row[2],
                    access_role="" if row[3] is None else row[3],
                    primary=bool(row[4]),
                )
                for row in rows
            ]

        return self._call(run)

    def set_calendars(self, items: list[CalendarInfo]) -> None:
        def run(conn: psycopg.Connection) -> None:
            conn.execute("DELETE FROM calendars")
            with conn.cursor() as cur:
                cur.executemany(
                    """
                    INSERT INTO calendars (position, id, summary, access_role, is_primary)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    [
                        (position, item.id, item.summary, item.access_role, item.primary)
                        for position, item in enumerate(items)
                    ],
                )

        self._call(run)

    def get_sync_token(self, calendar_id: str) -> str | None:
        def run(conn: psycopg.Connection) -> str | None:
            row = conn.execute(
                "SELECT token FROM sync_tokens WHERE calendar_id = %s",
                (calendar_id,),
            ).fetchone()
            if row is None:
                return None
            return row[0]

        return self._call(run)

    def set_sync_token(self, calendar_id: str, token: str | None) -> None:
        def run(conn: psycopg.Connection) -> None:
            if token is None:
                conn.execute("DELETE FROM sync_tokens WHERE calendar_id = %s", (calendar_id,))
                return
            conn.execute(
                """
                INSERT INTO sync_tokens (calendar_id, token) VALUES (%s, %s)
                ON CONFLICT (calendar_id) DO UPDATE SET token = EXCLUDED.token
                """,
                (calendar_id, token),
            )

        self._call(run)

    def get_link(self, source: str) -> Link | None:
        def run(conn: psycopg.Connection) -> Link | None:
            row = conn.execute(
                """
                SELECT source, kind, calendar_id, event_id, start, rule_id
                FROM links WHERE source = %s
                """,
                (source,),
            ).fetchone()
            if row is None:
                return None
            return _link_in(row)

        return self._call(run)

    def put_link(self, link: Link) -> None:
        def run(conn: psycopg.Connection) -> None:
            conn.execute(
                """
                INSERT INTO links (source, kind, calendar_id, event_id, start, rule_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (source) DO UPDATE SET
                    kind = EXCLUDED.kind,
                    calendar_id = EXCLUDED.calendar_id,
                    event_id = EXCLUDED.event_id,
                    start = EXCLUDED.start,
                    rule_id = EXCLUDED.rule_id
                """,
                (
                    link.source,
                    link.kind,
                    link.calendar_id,
                    link.event_id,
                    _start_out(link.start),
                    link.rule_id,
                ),
            )

        self._call(run)

    def delete_link(self, source: str) -> None:
        def run(conn: psycopg.Connection) -> None:
            conn.execute("DELETE FROM links WHERE source = %s", (source,))

        self._call(run)

    def forget_event(self, calendar_id: str, event_id: str) -> None:
        def run(conn: psycopg.Connection) -> None:
            source = f"{calendar_id}/{event_id}"
            conn.execute(
                "DELETE FROM links WHERE source = %s OR (calendar_id = %s AND event_id = %s)",
                (source, calendar_id, event_id),
            )
            conn.execute("DELETE FROM placements WHERE source = %s", (source,))
            conn.execute("DELETE FROM notice_state WHERE source = %s", (source,))

        self._call(run)

    def list_links(self) -> list[Link]:
        def run(conn: psycopg.Connection) -> list[Link]:
            rows = conn.execute(
                "SELECT source, kind, calendar_id, event_id, start, rule_id"
                ' FROM links ORDER BY source COLLATE "C"'
            ).fetchall()
            return [_link_in(row) for row in rows]

        return self._call(run)

    def add_placement(self, entry: PlacementEntry) -> int:
        def run(conn: psycopg.Connection) -> int:
            row = conn.execute(
                """
                INSERT INTO placements (
                    at, source, calendar_id, title, organizer_email, guest_domains
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    entry.at,
                    entry.source,
                    entry.calendar_id,
                    entry.title,
                    entry.organizer_email,
                    list(entry.guest_domains),
                ),
            ).fetchone()
            return int(row[0])

        return self._call(run)

    def list_placements(self, limit: int = 100) -> list[PlacementEntry]:
        if limit <= 0:
            return []

        def run(conn: psycopg.Connection) -> list[PlacementEntry]:
            rows = conn.execute(
                """
                SELECT id, at, source, calendar_id, title, organizer_email, guest_domains
                FROM placements ORDER BY id DESC LIMIT %s
                """,
                (limit,),
            ).fetchall()
            return [_placement_in(row) for row in rows]

        return self._call(run)

    def get_placement(self, id: int) -> PlacementEntry | None:
        def run(conn: psycopg.Connection) -> PlacementEntry | None:
            row = conn.execute(
                """
                SELECT id, at, source, calendar_id, title, organizer_email, guest_domains
                FROM placements WHERE id = %s
                """,
                (id,),
            ).fetchone()
            if row is None:
                return None
            return _placement_in(row)

        return self._call(run)

    def get_notice_state(self, source: str) -> dict[str, str]:
        def run(conn: psycopg.Connection) -> dict[str, str]:
            row = conn.execute(
                "SELECT state FROM notice_state WHERE source = %s",
                (source,),
            ).fetchone()
            if row is None or row[0] is None:
                return {}
            return dict(row[0])

        return self._call(run)

    def set_notice_state(self, source: str, state: Mapping[str, str]) -> None:
        def run(conn: psycopg.Connection) -> None:
            conn.execute(
                """
                INSERT INTO notice_state (source, state) VALUES (%s, %s)
                ON CONFLICT (source) DO UPDATE SET state = EXCLUDED.state
                """,
                (source, Jsonb(dict(state))),
            )

        self._call(run)

    def get_alert_state(self) -> AlertState:
        def run(conn: psycopg.Connection) -> AlertState:
            data = self._load_state(conn, "alert_state")
            if data is None:
                return AlertState()
            return _alert_in(data)

        return self._call(run)

    def set_alert_state(self, a: AlertState) -> None:
        def run(conn: psycopg.Connection) -> None:
            self._save_state(conn, "alert_state", _alert_out(a))

        self._call(run)

    def get_status(self) -> Status:
        def run(conn: psycopg.Connection) -> Status:
            data = self._load_state(conn, "status")
            if data is None:
                return Status()
            return _status_in(data)

        return self._call(run)

    def set_status(self, s: Status) -> None:
        def run(conn: psycopg.Connection) -> None:
            self._save_state(conn, "status", _status_out(s))

        self._call(run)

    def add_request(self, kind: str, params: Mapping) -> int:
        def run(conn: psycopg.Connection) -> int:
            row = conn.execute(
                """
                INSERT INTO requests (kind, state, params, result)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (kind, REQ_PENDING, Jsonb(dict(params)), Jsonb({})),
            ).fetchone()
            return int(row[0])

        return self._call(run)

    def get_request(self, id: int) -> Request | None:
        def run(conn: psycopg.Connection) -> Request | None:
            row = conn.execute(
                """
                SELECT id, kind, state, params, result, created
                FROM requests WHERE id = %s
                """,
                (id,),
            ).fetchone()
            if row is None:
                return None
            return _request_in(row)

        return self._call(run)

    def update_request(self, req: Request) -> None:
        def run(conn: psycopg.Connection) -> None:
            cur = conn.execute(
                """
                UPDATE requests SET kind = %s, state = %s, params = %s, result = %s
                WHERE id = %s
                """,
                (
                    req.kind,
                    req.state,
                    Jsonb(dict(req.params)),
                    Jsonb(dict(req.result)),
                    req.id,
                ),
            )
            if cur.rowcount == 0:
                raise StoreError(f"unknown request {req.id}")

        self._call(run)

    def list_requests(self, state: str | None = None) -> list[Request]:
        def run(conn: psycopg.Connection) -> list[Request]:
            if state is None:
                rows = conn.execute(
                    """
                    SELECT id, kind, state, params, result, created
                    FROM requests ORDER BY id ASC
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, kind, state, params, result, created
                    FROM requests WHERE state = %s ORDER BY id ASC
                    """,
                    (state,),
                ).fetchall()
            return [_request_in(row) for row in rows]

        return self._call(run)

    def get_secret(self, name: str) -> str | None:
        def run(conn: psycopg.Connection) -> str | None:
            row = conn.execute("SELECT token FROM secrets WHERE name = %s", (name,)).fetchone()
            if row is None:
                return None
            return self.box.decrypt(row[0])

        return self._call(run)

    def set_secret(self, name: str, value: str | None) -> None:
        def run(conn: psycopg.Connection) -> None:
            if value is None:
                conn.execute("DELETE FROM secrets WHERE name = %s", (name,))
                return
            token = self.box.encrypt(value)
            conn.execute(
                """
                INSERT INTO secrets (name, token) VALUES (%s, %s)
                ON CONFLICT (name) DO UPDATE SET token = EXCLUDED.token
                """,
                (name, token),
            )

        self._call(run)

    def get_auth(self) -> AuthRecord:
        def run(conn: psycopg.Connection) -> AuthRecord:
            data = self._load_state(conn, "auth")
            if data is None:
                return AuthRecord()
            return _auth_in(data)

        return self._call(run)

    def save_auth(self, a: AuthRecord) -> None:
        def run(conn: psycopg.Connection) -> None:
            self._save_state(conn, "auth", _auth_out(a))

        self._call(run)

    def create_session(self, token_hash: str, expires: datetime) -> None:
        def run(conn: psycopg.Connection) -> None:
            conn.execute(
                """
                INSERT INTO sessions (token_hash, expires) VALUES (%s, %s)
                ON CONFLICT (token_hash) DO UPDATE SET expires = EXCLUDED.expires
                """,
                (token_hash, expires),
            )

        self._call(run)

    def session_valid(self, token_hash: str, now: datetime) -> bool:
        def run(conn: psycopg.Connection) -> bool:
            row = conn.execute(
                "SELECT expires FROM sessions WHERE token_hash = %s",
                (token_hash,),
            ).fetchone()
            if row is None:
                return False
            return row[0].astimezone(UTC) > now

        return self._call(run)

    def delete_session(self, token_hash: str) -> None:
        def run(conn: psycopg.Connection) -> None:
            conn.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))

        self._call(run)

    def get_meta(self, key: str) -> str | None:
        def run(conn: psycopg.Connection) -> str | None:
            row = conn.execute("SELECT value FROM meta WHERE key = %s", (key,)).fetchone()
            if row is None:
                return None
            return row[0]

        return self._call(run)

    def set_meta(self, key: str, value: str) -> None:
        def run(conn: psycopg.Connection) -> None:
            conn.execute(
                """
                INSERT INTO meta (key, value) VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                (key, value),
            )

        self._call(run)
