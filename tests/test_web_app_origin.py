"""PRD-01 R11, R12: every POST must come from the app's own origin."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from cal_mirror.model import SYNC_NOW, AuthRecord
from cal_mirror.ports import FixedClock
from cal_mirror.store_memory import MemoryStore
from cal_mirror.web.app import create_app
from cal_mirror.web.deps import SESSION_COOKIE, session_hash
from cal_mirror.web.page_signin import hash_password

PASSWORD = "fixture-password-1"
TOKEN = "fixture-session-1"
START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
BASE = "https://testserver"


def make(base: str = BASE) -> tuple[MemoryStore, TestClient]:
    store = MemoryStore()
    store.save_auth(AuthRecord(password_hash=hash_password(PASSWORD), totp_enabled=True))
    store.set_secret("totp_secret", "JBSWY3DPEHPK3PXP")
    store.create_session(session_hash(TOKEN), START + timedelta(hours=1))
    c = TestClient(create_app(store, FixedClock(START)), base_url=base, follow_redirects=False)
    c.cookies.set(SESSION_COOKIE, TOKEN)
    return store, c


BAD_ORIGINS = [
    None,
    "null",
    "https://evil.example",
    "http://testserver",
    "https://testserver:8443",
    "https://testserver.evil.example",
]


def headers(origin: str | None) -> dict[str, str]:
    return {} if origin is None else {"Origin": origin}


@pytest.mark.parametrize("origin", BAD_ORIGINS)
def test_foreign_or_missing_origin_is_403_and_changes_nothing(origin) -> None:
    store, c = make()
    response = c.post("/settings/sync-now", headers=headers(origin))
    assert response.status_code == 403
    assert store.get_meta(SYNC_NOW) is None


@pytest.mark.parametrize("origin", BAD_ORIGINS)
def test_signout_from_elsewhere_keeps_the_session(origin) -> None:
    store, c = make()
    assert c.post("/signout", headers=headers(origin)).status_code == 403
    assert store.session_valid(session_hash(TOKEN), START)


@pytest.mark.parametrize("origin", BAD_ORIGINS)
def test_public_post_is_refused_before_the_handler(origin) -> None:
    store, c = make()
    c.cookies.clear()
    before = store.get_auth()
    response = c.post("/signin", data={"password": PASSWORD}, headers=headers(origin))
    assert response.status_code == 403
    assert "set-cookie" not in response.headers
    assert store.get_auth() == before
    setup = c.post(
        "/setup", data={"password": PASSWORD, "confirm": PASSWORD}, headers=headers(origin)
    )
    assert setup.status_code == 403


def test_own_origin_behaves_as_before() -> None:
    store, c = make()
    response = c.post("/settings/sync-now", headers={"Origin": BASE})
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?synced=1"
    assert store.get_meta(SYNC_NOW) == "1"
    c.cookies.clear()
    wrong = c.post("/signin", data={"password": "wrong-password-1"}, headers={"Origin": BASE})
    assert wrong.status_code == 401
    right = c.post("/signin", data={"password": PASSWORD}, headers={"Origin": BASE})
    assert right.status_code == 303
    assert right.headers["location"] == "/signin/totp"


def test_own_origin_includes_the_port() -> None:
    store, c = make("https://cal.local:8443")
    assert c.post("/settings/sync-now", headers={"Origin": "https://cal.local"}).status_code == 403
    ok = c.post("/settings/sync-now", headers={"Origin": "https://cal.local:8443"})
    assert ok.status_code == 303
    assert store.get_meta(SYNC_NOW) == "1"


def test_get_needs_no_origin() -> None:
    _, c = make()
    assert c.get("/status").status_code == 200


def test_browsers_are_told_to_send_the_origin() -> None:
    _, c = make()
    assert c.get("/signin").headers["Referrer-Policy"] == "same-origin"
