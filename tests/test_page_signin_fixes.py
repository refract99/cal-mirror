from datetime import UTC, datetime, timedelta

import pyotp
from fastapi.testclient import TestClient

from cal_mirror.model import AuthRecord
from cal_mirror.ports import FixedClock
from cal_mirror.store_memory import MemoryStore
from cal_mirror.web import page_oauth
from cal_mirror.web.app import create_app
from cal_mirror.web.page_signin import LOCK_SECONDS, PENDING_COOKIE, hash_password

START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
PASSWORD = "fixture-password-1"
SESSION_NAME = "cal_mirror_session"


class StubCredentials:
    def to_json(self) -> str:
        return '{"token": "fixture-token-1"}'


class StubFlow:
    credentials = StubCredentials()

    def fetch_token(self, code: str) -> None:
        self.code = code


class Env:
    def __init__(self, totp=False, recovery=(), **changes):
        self.store = MemoryStore()
        self.app = create_app(self.store, FixedClock(START))
        self.client = TestClient(self.app, base_url="https://testserver", follow_redirects=False)
        self.secret = pyotp.random_base32()
        self.store.set_secret("totp_secret", self.secret)
        self.store.save_auth(
            AuthRecord(
                password_hash=hash_password(PASSWORD),
                totp_enabled=totp,
                recovery_hashes=tuple(hash_password(c) for c in recovery),
                **changes,
            )
        )

    def signin(self, password=PASSWORD):
        return self.client.post("/signin", data={"password": password})

    def code(self, code):
        return self.client.post("/signin/totp", data={"code": code})

    def good(self):
        return pyotp.TOTP(self.secret).at(START)

    def wrong(self):
        return "000000" if self.good() != "000000" else "111111"

    def auth(self):
        return self.store.get_auth()


def cookie_header(response, name):
    headers = [h for h in response.headers.get_list("set-cookie") if h.startswith(name + "=")]
    assert len(headers) == 1
    return headers[0]


def test_session_cookie_is_lax_without_2fa():
    env = Env()
    header = cookie_header(env.signin(), SESSION_NAME)
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "SameSite=lax" in header
    assert "SameSite=strict" not in header


def test_pending_cookie_strict_then_session_lax():
    env = Env(totp=True)
    pending = cookie_header(env.signin(), PENDING_COOKIE)
    assert "HttpOnly" in pending
    assert "Secure" in pending
    assert "SameSite=strict" in pending
    session = cookie_header(env.code(env.good()), SESSION_NAME)
    assert "SameSite=lax" in session


def test_google_callback_after_signin_reaches_google():
    env = Env()
    env.app.dependency_overrides[page_oauth.get_flow] = StubFlow
    env.signin()
    env.store.set_meta("oauth_state", "fixture-state-1")
    r = env.client.get("/google/callback?state=fixture-state-1&code=fixture-code-1")
    assert r.status_code == 303
    assert r.headers["location"] == "/google"


def test_four_wrong_codes_between_right_passwords_still_lock():
    env = Env(totp=True)
    env.signin()
    for _ in range(4):
        assert env.code(env.wrong()).status_code == 401
    env.signin()
    assert env.auth().failures == 4
    r = env.code(env.wrong())
    assert r.status_code == 429
    assert env.auth().locked_until == START + timedelta(seconds=LOCK_SECONDS)
    assert env.auth().failures == 0
    assert SESSION_NAME not in r.headers.get("set-cookie", "")
    assert SESSION_NAME not in env.client.cookies


def test_interleaved_right_passwords_do_not_reset_counter():
    env = Env(totp=True)
    env.signin()
    for _ in range(2):
        env.code(env.wrong())
    env.signin()
    for _ in range(2):
        env.code(env.wrong())
    env.signin()
    assert env.code(env.wrong()).status_code == 429


def test_good_totp_after_failures_resets_counter():
    env = Env(totp=True)
    env.signin("wrong-password-1")
    env.signin("wrong-password-1")
    r = env.signin()
    assert r.status_code == 303
    assert r.headers["location"] == "/signin/totp"
    assert env.auth().failures == 2
    r = env.code(env.good())
    assert r.status_code == 303
    assert r.headers["location"] == "/status"
    assert env.auth().failures == 0
    assert env.auth().locked_until is None


def test_good_recovery_code_resets_counter():
    codes = [f"recovery{i:02d}x" for i in range(10)]
    env = Env(totp=True, recovery=codes, failures=3)
    env.signin()
    r = env.code(codes[0])
    assert r.status_code == 303
    assert env.auth().failures == 0
    assert env.auth().locked_until is None
    assert len(env.auth().recovery_hashes) == 9


def test_right_password_without_2fa_resets_counter():
    env = Env(failures=3)
    r = env.signin()
    assert r.status_code == 303
    assert r.headers["location"] == "/status"
    assert env.auth().failures == 0
    assert env.auth().locked_until is None


def test_right_password_with_2fa_leaves_counter_as_stored():
    stored_lock = START - timedelta(seconds=1)
    env = Env(totp=True, failures=4, locked_until=stored_lock)
    r = env.signin()
    assert r.status_code == 303
    assert env.auth().failures == 4
    assert env.auth().locked_until == stored_lock
