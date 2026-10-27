import os
from datetime import UTC, date, datetime, time, timedelta

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from cal_mirror.model import (
    REQ_ARCHIVE,
    REQ_PENDING,
    REQ_READY,
    REQ_RULES,
    RULE_GUEST_DOMAIN,
    RULE_ORGANIZER,
    RULE_TITLE_WORD,
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
from cal_mirror.store_pg import PgStore, migrate

DSN = os.environ["CAL_MIRROR_TEST_DSN"]


class FakeBox:
    def encrypt(self, plain: str) -> str:
        return "enc:" + plain[::-1]

    def decrypt(self, token: str) -> str:
        return token[len("enc:") :][::-1]


@pytest.fixture(autouse=True)
def clean_schema() -> None:
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")


@pytest.fixture
def box() -> FakeBox:
    return FakeBox()


@pytest.fixture
def store(box: FakeBox, clean_schema: None) -> PgStore:
    return PgStore(DSN, box)


def test_settings_round_trip(store: PgStore) -> None:
    saved = Settings(
        allowed_hours={0: (time(9), time(17))},
        secondary_calendars=("a", "b"),
    )
    store.save_settings(saved)
    got = store.get_settings()
    assert got == saved
    assert got.allowed_hours == {0: (time(9), time(17))}
    assert type(got.allowed_hours) is dict
    with psycopg.connect(DSN) as conn:
        data = conn.execute("SELECT data FROM state WHERE name = %s", ("settings",)).fetchone()[0]
    assert data["allowed_hours"] == {"0": ["09:00:00", "17:00:00"]}
    assert data["secondary_calendars"] == ["a", "b"]

    richer = Settings(
        main_calendar="main",
        secondary_calendars=("a", "b"),
        default_calendar="a",
        owner_email="steve@example.com",
        time_zone="America/Chicago",
        sync_interval_minutes=15,
        ntfy_server="https://ntfy.example",
        ntfy_topic="cal",
        archive_destinations=("/data/a", "/data/b"),
        check_no_link=False,
        check_hours=True,
        allowed_hours={0: (time(9), time(17)), 2: (time(10, 30), time(16))},
        web_url="https://cal.example",
        two_factor=True,
    )
    store.save_settings(richer)
    got = store.get_settings()
    assert got == richer
    assert got.allowed_hours == {0: (time(9), time(17)), 2: (time(10, 30), time(16))}


def test_missing_json_key_uses_field_default(store: PgStore) -> None:
    with psycopg.connect(DSN) as conn:
        conn.execute(
            "INSERT INTO state (name, data) VALUES (%s, %s)",
            ("settings", Jsonb({"main_calendar": "main"})),
        )
    got = store.get_settings()
    assert got == Settings(main_calendar="main")
    assert got.allowed_hours == {}
    assert got.secondary_calendars == ()
    assert got.time_zone == "America/Chicago"
    assert got.check_no_link is True


def test_rules_round_trip(store: PgStore) -> None:
    rules = [
        Rule(3, RULE_TITLE_WORD, "standup", "cal-c"),
        Rule(1, RULE_GUEST_DOMAIN, "acme.com", "cal-a"),
        Rule(2, RULE_ORGANIZER, "ada@acme.com", "cal-b"),
    ]
    store.save_rules(rules)
    assert store.list_rules() == rules


def test_save_rules_replaces_longer_list(store: PgStore) -> None:
    store.save_rules(
        [
            Rule(1, RULE_GUEST_DOMAIN, "acme.com", "cal-a"),
            Rule(2, RULE_ORGANIZER, "ada@acme.com", "cal-b"),
            Rule(3, RULE_TITLE_WORD, "standup", "cal-c"),
        ]
    )
    shorter = [Rule(9, RULE_ORGANIZER, "o@x", "cal-b")]
    store.save_rules(shorter)
    assert store.list_rules() == shorter


def test_calendars_round_trip(store: PgStore) -> None:
    items = [
        CalendarInfo(id="b", summary="Secondary", access_role="writer", primary=False),
        CalendarInfo(id="a", summary="Main", access_role="owner", primary=True),
    ]
    store.set_calendars(items)
    assert store.list_calendars() == items
    store.set_calendars(items[:1])
    assert store.list_calendars() == items[:1]


def test_sync_token_round_trip(store: PgStore) -> None:
    assert store.get_sync_token("cal") is None
    store.set_sync_token("cal", "tok-1")
    assert store.get_sync_token("cal") == "tok-1"
    store.set_sync_token("cal", "tok-2")
    assert store.get_sync_token("cal") == "tok-2"


def test_set_sync_token_none_deletes(store: PgStore) -> None:
    store.set_sync_token("cal", "tok-1")
    store.set_sync_token("cal", None)
    assert store.get_sync_token("cal") is None


def test_links_round_trip(store: PgStore) -> None:
    store.put_link(Link("s/1", "copy", "c", "e", date(2026, 11, 2), 7))
    got = store.get_link("s/1")
    assert got == Link("s/1", "copy", "c", "e", date(2026, 11, 2), 7)
    assert got is not None
    assert got.start == date(2026, 11, 2)

    start = datetime(2026, 11, 2, 15, tzinfo=UTC)
    store.put_link(Link("s/1", "blocked", "m", "e2", start, None))
    got = store.get_link("s/1")
    assert got == Link("s/1", "blocked", "m", "e2", start, None)
    assert got is not None
    assert got.start == start
    assert got.start.tzinfo is not None

    store.put_link(Link("b", "copy", "c", "e3", None))
    store.put_link(Link("a", "copy", "c", "e4", None))
    assert [link.source for link in store.list_links()] == ["a", "b", "s/1"]
    store.delete_link("a")
    assert store.get_link("a") is None
    assert store.get_link("missing") is None


def test_placements_round_trip(store: PgStore) -> None:
    base = datetime(2026, 11, 2, tzinfo=UTC)
    times = [base.replace(hour=10), base.replace(hour=11), base.replace(hour=12)]
    ids = []
    for index, at in enumerate(times):
        domains = ("a.com", "b.com") if index == 2 else ()
        ids.append(
            store.add_placement(
                PlacementEntry(
                    id=1000 + index,
                    at=at,
                    source=f"s/{index}",
                    calendar_id="cal",
                    title=f"t{index}",
                    organizer_email="o@x",
                    guest_domains=domains,
                )
            )
        )
    assert ids[0] != 1000
    listed = store.list_placements(2)
    assert [entry.at for entry in listed] == [times[2], times[1]]
    assert [entry.id for entry in listed] == [ids[2], ids[1]]
    assert listed[0] == PlacementEntry(
        id=ids[2],
        at=times[2],
        source="s/2",
        calendar_id="cal",
        title="t2",
        organizer_email="o@x",
        guest_domains=("a.com", "b.com"),
    )
    assert store.list_placements(0) == []
    assert store.list_placements(-1) == []
    assert store.get_placement(ids[0]) == PlacementEntry(
        id=ids[0],
        at=times[0],
        source="s/0",
        calendar_id="cal",
        title="t0",
        organizer_email="o@x",
    )
    assert store.get_placement(999) is None


def test_notice_state_round_trip(store: PgStore) -> None:
    assert store.get_notice_state("s/1") == {}
    store.set_notice_state("s/1", {"no_call_link": "2026-11-02T15:00:00+00:00"})
    got = store.get_notice_state("s/1")
    assert got == {"no_call_link": "2026-11-02T15:00:00+00:00"}
    assert type(got) is dict
    store.set_notice_state("s/1", {"outside_hours": "later"})
    assert store.get_notice_state("s/1") == {"outside_hours": "later"}


def test_alert_state_round_trip(store: PgStore) -> None:
    since = datetime(2026, 11, 2, 15, tzinfo=UTC)
    saved = AlertState(active="google_api", since=since, api_errors=3)
    store.set_alert_state(saved)
    assert store.get_alert_state() == saved


def test_status_round_trip(store: PgStore) -> None:
    when = datetime(2026, 11, 2, 16, tzinfo=UTC)
    saved = Status(
        last_good_sync=when,
        failure="database",
        token_ok=False,
        counts={"copy": 2, "blocked": 1},
    )
    store.set_status(saved)
    got = store.get_status()
    assert got == Status(last_good_sync=when, failure="database", token_ok=False)
    assert got.counts == {"copy": 2, "blocked": 1}
    assert type(got.counts) is dict


def test_requests_round_trip(store: PgStore) -> None:
    params = {"cutoff": "2026-01-01", "destination": "/data", "rules": [{"kind": "guest_domain"}]}
    first = store.add_request(REQ_ARCHIVE, params)
    second = store.add_request(REQ_RULES, {"rules": []})
    req = store.get_request(first)
    assert req is not None
    assert req.id == first
    assert req.kind == REQ_ARCHIVE
    assert req.state == REQ_PENDING
    assert req.params == params
    assert type(req.params) is dict
    assert req.result == {}
    assert type(req.result) is dict
    assert req.created is not None
    assert req.created.utcoffset() == timedelta(0)
    listed = store.list_requests()
    assert [item.id for item in listed] == [first, second]
    updated = Request(
        id=first,
        kind="other",
        state=REQ_READY,
        params={"cutoff": "2026-02-01"},
        result={"moves": 4},
        created=req.created,
    )
    store.update_request(updated)
    got = store.get_request(first)
    assert got is not None
    assert got.kind == "other"
    assert got.state == REQ_READY
    assert got.params == {"cutoff": "2026-02-01"}
    assert got.result == {"moves": 4}
    assert store.get_request(999) is None


def test_list_requests_ready_filters(store: PgStore) -> None:
    pending = store.add_request(REQ_ARCHIVE, {"cutoff": "2026-01-01"})
    ready = store.add_request(REQ_RULES, {"rules": []})
    store.update_request(
        Request(
            id=ready,
            kind=REQ_RULES,
            state=REQ_READY,
            params={"rules": []},
            result={"moves": 1},
        )
    )
    listed = store.list_requests(REQ_READY)
    assert [item.id for item in listed] == [ready]
    assert listed[0].result == {"moves": 1}
    assert pending not in [item.id for item in listed]
    assert [item.id for item in store.list_requests()] == [pending, ready]


def test_update_request_unknown_id(store: PgStore) -> None:
    with pytest.raises(StoreError) as caught:
        store.update_request(Request(id=999, kind=REQ_ARCHIVE))
    assert str(caught.value) == "unknown request 999"


def test_secrets_round_trip(store: PgStore, box: FakeBox) -> None:
    store.set_secret("google_token", "fixture-token-1")
    with psycopg.connect(DSN) as conn:
        row = conn.execute("SELECT name, token FROM secrets").fetchone()
    assert row is not None
    assert "fixture-token-1" not in row[0]
    assert "fixture-token-1" not in row[1]
    assert row[1] == box.encrypt("fixture-token-1")
    assert store.get_secret("google_token") == "fixture-token-1"
    store.set_secret("totp_secret", "fixture-token-2")
    assert store.get_secret("totp_secret") == "fixture-token-2"
    store.set_secret("totp_secret", None)
    assert store.get_secret("totp_secret") is None
    assert store.get_secret("missing") is None


def test_auth_round_trip(store: PgStore) -> None:
    locked = datetime(2026, 11, 2, 17, tzinfo=UTC)
    saved = AuthRecord(
        password_hash="argon2hash",
        totp_enabled=True,
        recovery_hashes=("h1", "h2"),
        failures=4,
        locked_until=locked,
    )
    store.save_auth(saved)
    assert store.get_auth() == saved
    with psycopg.connect(DSN) as conn:
        data = conn.execute("SELECT data FROM state WHERE name = %s", ("auth",)).fetchone()[0]
    assert data["password_hash"] == "argon2hash"
    assert data["recovery_hashes"] == ["h1", "h2"]


def test_sessions_round_trip(store: PgStore) -> None:
    expires = datetime(2026, 11, 2, 18, tzinfo=UTC)
    store.create_session("h", expires)
    assert store.session_valid("h", expires) is False
    assert store.session_valid("h", expires - timedelta(seconds=1)) is True
    assert store.session_valid("missing", expires - timedelta(seconds=1)) is False
    store.delete_session("h")
    assert store.session_valid("h", expires - timedelta(seconds=1)) is False


def test_meta_round_trip(store: PgStore) -> None:
    assert store.get_meta("oauth_state") is None
    store.set_meta("oauth_state", "xyz")
    assert store.get_meta("oauth_state") == "xyz"
    store.set_meta("first_start_done", "1")
    store.set_meta("oauth_state", "abc")
    assert store.get_meta("oauth_state") == "abc"
    assert store.get_meta("first_start_done") == "1"


def test_defaults_on_empty_database(store: PgStore) -> None:
    assert store.get_settings() == Settings()
    assert store.get_settings().allowed_hours == {}
    assert store.get_alert_state() == AlertState()
    assert store.get_status() == Status()
    assert store.get_status().counts == {}
    assert store.get_auth() == AuthRecord()
    assert store.list_rules() == []
    assert store.list_calendars() == []
    assert store.get_sync_token("cal") is None
    assert store.get_link("s") is None
    assert store.list_links() == []
    assert store.list_placements() == []
    assert store.get_placement(1) is None
    assert store.get_notice_state("missing") == {}
    assert store.get_request(1) is None
    assert store.list_requests() == []
    assert store.get_secret("google_token") is None
    assert store.session_valid("h", datetime(2026, 11, 2, tzinfo=UTC)) is False
    assert store.get_meta("first_start_done") is None


def test_migrate_twice(store: PgStore) -> None:
    with psycopg.connect(DSN) as conn:
        migrate(conn)
        migrate(conn)
        rows = conn.execute("SELECT name FROM schema_version ORDER BY name").fetchall()
    assert rows == [("0001_init.sql",)]


def test_missing_database(box: FakeBox) -> None:
    with pytest.raises(StoreError):
        PgStore(make_conninfo(DSN, dbname="cal_mirror_missing"), box)
