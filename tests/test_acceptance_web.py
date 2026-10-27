import json
import logging
import os
import re
from datetime import UTC, datetime, time

import psycopg
import pyotp
import pytest
from fastapi.testclient import TestClient

from cal_mirror.crypto import Cipher, generate_key
from cal_mirror.fake_google import FakeCalendar, FakeService
from cal_mirror.google_api import GoogleCalendar
from cal_mirror.model import Settings
from cal_mirror.ports import CalendarAuthError, FixedClock
from cal_mirror.store_memory import MemoryStore
from cal_mirror.store_pg import PgStore
from cal_mirror.sync import run_once
from cal_mirror.web import page_oauth
from cal_mirror.web.app import create_app
from cal_mirror.web.deps import SESSION_COOKIE
from cal_mirror.web.page_signin import LOCK_SECONDS, MAX_FAILURES

PASSWORD = "fixture-password-1"
TOKEN = "fixture-token-1"
REFRESH = "fixture-refresh-1"
OAUTH_STATE = "fixture-state-1"


def new_clock() -> FixedClock:
    return FixedClock(datetime(2026, 10, 24, 12, 0, tzinfo=UTC))


def harness_settings() -> Settings:
    hours = {day: (time(8), time(17)) for day in range(5)}
    return Settings(
        main_calendar="main@x",
        secondary_calendars=("work@x", "home@x"),
        default_calendar="home@x",
        owner_email="steve@x",
        allowed_hours=hours,
        web_url="https://cal.example",
    )


def memory_store() -> MemoryStore:
    store = MemoryStore()
    store.save_settings(harness_settings())
    return store


def web_client(store: MemoryStore | PgStore, clk: FixedClock) -> TestClient:
    app = create_app(store, clk)
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def owned_calendar() -> GoogleCalendar:
    state = FakeCalendar()
    for cid in ("main@x", "work@x", "home@x", "other@x"):
        state.add_calendar(cid, access_role="owner")
    return GoogleCalendar(FakeService(state))


class Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, title: str, message: str, click: str = "") -> None:
        self.sent.append((title, message, click))


class AuthFail:
    def __init__(self, inner: GoogleCalendar) -> None:
        self.inner = inner
        self.fail = False

    def list_calendars(self):
        return self.inner.list_calendars()

    def list_changes(self, calendar_id, sync_token, time_min=None):
        if self.fail:
            raise CalendarAuthError("token rejected")
        return self.inner.list_changes(calendar_id, sync_token, time_min=time_min)

    def list_events(self, calendar_id, time_min=None, time_max=None):
        return self.inner.list_events(calendar_id, time_min=time_min, time_max=time_max)

    def get_event(self, calendar_id, event_id):
        return self.inner.get_event(calendar_id, event_id)

    def apply(self, write):
        return self.inner.apply(write)


class StubFlow:
    def authorization_url(self, **kw: object) -> tuple[str, str]:
        return ("https://accounts.example/auth", OAUTH_STATE)

    def fetch_token(self, code: str) -> None:
        return None

    @property
    def credentials(self) -> "_Creds":
        return _Creds()


class _Creds:
    def to_json(self) -> str:
        return json.dumps({"token": TOKEN, "refresh_token": REFRESH})


def _public_rows(dsn: str) -> list[str]:
    found: list[str] = []
    with psycopg.connect(dsn) as conn:
        names = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
        for (name,) in names:
            query = psycopg.sql.SQL("SELECT t::text FROM {} AS t").format(
                psycopg.sql.Identifier(name)
            )
            found.extend(row[0] for row in conn.execute(query).fetchall())
    return found


def test_criterion_8_password_required() -> None:
    clk = new_clock()
    client = web_client(memory_store(), clk)
    blocked = client.get("/status")
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/signin"
    setup = client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    assert setup.status_code == 303
    assert setup.headers["location"] == "/signin"
    wrong = client.post("/signin", data={"password": "fixture-wrong-password"})
    assert wrong.status_code == 401
    right = client.post("/signin", data={"password": PASSWORD})
    assert right.status_code == 303
    assert right.headers["location"] == "/status"
    assert SESSION_COOKIE in right.cookies
    page = client.get("/status")
    assert page.status_code == 200


def test_criterion_8_totp_and_recovery_code() -> None:
    clk = new_clock()
    client = web_client(memory_store(), clk)
    client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    signed = client.post("/signin", data={"password": PASSWORD})
    assert signed.status_code == 303
    enabled = client.post("/account/2fa", data={"action": "enable"})
    assert enabled.status_code == 200
    found = re.search(r"Secret: <code>(\w+)</code>", enabled.text)
    assert found is not None
    secret = found.group(1)
    codes = re.findall(r"<li><code>(\w+)</code>", enabled.text)
    assert codes
    confirmed = client.post(
        "/account/2fa",
        data={"action": "confirm", "code": pyotp.TOTP(secret).at(clk.now())},
    )
    assert confirmed.status_code == 303
    client.post("/signout")
    pending = client.post("/signin", data={"password": PASSWORD})
    assert pending.status_code == 303
    assert pending.headers["location"] == "/signin/totp"
    still = client.get("/status")
    assert still.status_code == 303
    assert still.headers["location"] == "/signin"
    bad = client.post("/signin/totp", data={"code": "000000"})
    assert bad.status_code == 401
    good = client.post("/signin/totp", data={"code": pyotp.TOTP(secret).at(clk.now())})
    assert good.status_code == 303
    assert good.headers["location"] == "/status"
    client.post("/signout")
    client.post("/signin", data={"password": PASSWORD})
    reused = codes[0]
    first = client.post("/signin/totp", data={"code": reused})
    assert first.status_code == 303
    assert first.headers["location"] == "/status"
    client.post("/signout")
    client.post("/signin", data={"password": PASSWORD})
    again = client.post("/signin/totp", data={"code": reused})
    assert again.status_code == 401


def test_criterion_8_lockout() -> None:
    clk = new_clock()
    client = web_client(memory_store(), clk)
    client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    last = client.post("/signin", data={"password": "fixture-wrong-password"})
    for _ in range(MAX_FAILURES - 1):
        last = client.post("/signin", data={"password": "fixture-wrong-password"})
    assert last.status_code == 429
    blocked = client.post("/signin", data={"password": PASSWORD})
    assert blocked.status_code == 429
    clk.advance(LOCK_SECONDS + 1)
    opened = client.post("/signin", data={"password": PASSWORD})
    assert opened.status_code == 303
    assert opened.headers["location"] == "/status"


def test_criterion_9_no_plain_secret(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    dsn = os.environ["CAL_MIRROR_TEST_DSN"]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    store = PgStore(dsn, Cipher(generate_key()))
    store.save_settings(harness_settings())
    clk = new_clock()
    app = create_app(store, clk)
    app.dependency_overrides[page_oauth.get_flow] = lambda: StubFlow()
    client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    client.post("/signin", data={"password": PASSWORD})
    enabled = client.post("/account/2fa", data={"action": "enable"})
    assert enabled.status_code == 200
    found = re.search(r"Secret: <code>(\w+)</code>", enabled.text)
    assert found is not None
    secret = found.group(1)
    codes = re.findall(r"<li><code>(\w+)</code>", enabled.text)
    assert codes
    confirmed = client.post(
        "/account/2fa",
        data={"action": "confirm", "code": pyotp.TOTP(secret).at(clk.now())},
    )
    assert confirmed.status_code == 303
    client.post("/signout")
    client.post("/signin", data={"password": PASSWORD})
    totp = client.post("/signin/totp", data={"code": pyotp.TOTP(secret).at(clk.now())})
    assert totp.status_code == 303
    connect = client.get("/google/connect")
    assert connect.status_code == 303
    back = client.get("/google/callback", params={"state": OAUTH_STATE, "code": "fixture-code-1"})
    assert back.status_code == 303
    wrapped = AuthFail(owned_calendar())
    wrapped.fail = True
    notifier = Recorder()
    run_once(store, wrapped, notifier, clk)
    clk.advance(300)
    run_once(store, wrapped, notifier, clk)
    wrapped.fail = False
    clk.advance(300)
    run_once(store, wrapped, notifier, clk)
    rows = _public_rows(dsn)
    assert rows
    secrets = [PASSWORD, secret, TOKEN, REFRESH, *codes]
    surfaces = [caplog.text, *rows]
    for title, message, click in notifier.sent:
        surfaces.extend((title, message, click))
    for hidden in secrets:
        assert hidden
        for surface in surfaces:
            assert hidden not in surface
    assert store.get_secret("google_token") == _Creds().to_json()
