import logging
from datetime import UTC, datetime

import pyotp
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cal_mirror.model import AuthRecord
from cal_mirror.ports import FixedClock
from cal_mirror.web.deps import SESSION_COOKIE, current_user, session_hash
from cal_mirror.web.page_signin import (
    LOCK_SECONDS,
    MAX_FAILURES,
    PENDING_COOKIE,
    SESSION_SECONDS,
    hash_password,
    router,
    verify_password,
)

PASSWORD = "fixture-password-1"
START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


class FakeStore:
    def __init__(self, auth=None):
        self.auth = auth or AuthRecord()
        self.secrets = {}
        self.sessions = {}
        self.saves = 0

    def get_auth(self):
        return self.auth

    def save_auth(self, a):
        self.saves += 1
        self.auth = a

    def get_secret(self, name):
        return self.secrets.get(name)

    def set_secret(self, name, value):
        if value is None:
            self.secrets.pop(name, None)
        else:
            self.secrets[name] = value

    def create_session(self, token_hash, expires):
        self.sessions[token_hash] = expires

    def session_valid(self, token_hash, now):
        expires = self.sessions.get(token_hash)
        return expires is not None and expires > now

    def delete_session(self, token_hash):
        self.sessions.pop(token_hash, None)


class Env:
    def __init__(self, store):
        self.store = store
        self.clock = FixedClock(START)
        app = FastAPI()
        app.state.store = store
        app.state.clock = self.clock
        app.include_router(router)
        app.dependency_overrides[current_user] = lambda: "steve"
        self.client = TestClient(app, base_url="https://testserver", follow_redirects=False)

    def signin(self, password=PASSWORD):
        return self.client.post("/signin", data={"password": password})

    def code(self, code):
        return self.client.post("/signin/totp", data={"code": code})

    def totp_now(self):
        return pyotp.TOTP(self.store.secrets["totp_secret"]).at(self.clock.now())


def with_password(**changes):
    return AuthRecord(password_hash=hash_password(PASSWORD), **changes)


def make_env(auth=None):
    return Env(FakeStore(auth))


def make_2fa_env(recovery=()):
    secret = pyotp.random_base32()
    hashes = tuple(hash_password(c) for c in recovery)
    env = make_env(with_password(totp_enabled=True, recovery_hashes=hashes))
    env.store.set_secret("totp_secret", secret)
    return env


def test_no_password_signin_redirects_to_setup():
    env = make_env()
    r = env.client.get("/signin")
    assert r.status_code == 303
    assert r.headers["location"] == "/setup"


def test_no_password_post_signin_makes_no_session():
    env = make_env()
    r = env.signin()
    assert r.status_code == 303
    assert r.headers["location"] == "/setup"
    assert SESSION_COOKIE not in r.cookies
    assert env.store.sessions == {}


def test_setup_short_password_rejected():
    env = make_env()
    r = env.client.post("/setup", data={"password": "a" * 11, "confirm": "a" * 11})
    assert r.status_code == 400
    assert "Password must be at least 12 characters." in r.text
    assert env.store.saves == 0
    assert not env.store.auth.password_hash


def test_setup_mismatch_rejected():
    env = make_env()
    r = env.client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD + "x"})
    assert r.status_code == 400
    assert "Passwords do not match." in r.text
    assert env.store.saves == 0


def test_setup_stores_hash_then_redirects_afterwards():
    env = make_env()
    r = env.client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD})
    assert r.status_code == 303
    assert r.headers["location"] == "/signin"
    stored = env.store.auth.password_hash
    assert stored.startswith("$argon2")
    assert stored != PASSWORD
    again = env.client.get("/setup")
    assert again.status_code == 303
    assert again.headers["location"] == "/signin"
    post_again = env.client.post("/setup", data={"password": "other-password-9", "confirm": "x"})
    assert post_again.status_code == 303
    assert env.store.auth.password_hash == stored


def test_wrong_password_counts_failure():
    env = make_env(with_password())
    r = env.signin("wrong-password-1")
    assert r.status_code == 401
    assert "Wrong password or code." in r.text
    assert SESSION_COOKIE not in r.cookies
    assert env.store.auth.failures == 1
    assert env.store.sessions == {}


def test_right_password_without_2fa_opens_session():
    env = make_env(with_password(failures=2))
    r = env.signin()
    assert r.status_code == 303
    assert r.headers["location"] == "/status"
    token = r.cookies[SESSION_COOKIE]
    assert list(env.store.sessions) == [session_hash(token)]
    assert (env.store.sessions[session_hash(token)] - START).total_seconds() == SESSION_SECONDS
    header = r.headers["set-cookie"]
    assert "HttpOnly" in header
    assert "Secure" in header
    assert "SameSite=lax" in header
    assert env.store.auth.failures == 0


def test_2fa_right_password_waits_for_code():
    env = make_2fa_env()
    r = env.signin()
    assert r.status_code == 303
    assert r.headers["location"] == "/signin/totp"
    assert SESSION_COOKIE not in r.cookies
    assert PENDING_COOKIE in r.cookies
    assert len(env.store.sessions) == 1


def test_2fa_valid_totp_opens_session():
    env = make_2fa_env()
    env.signin()
    r = env.code(env.totp_now())
    assert r.status_code == 303
    assert r.headers["location"] == "/status"
    token = r.cookies[SESSION_COOKIE]
    assert session_hash(token) in env.store.sessions
    assert len(env.store.sessions) == 1


def test_2fa_wrong_code_rejected():
    env = make_2fa_env()
    env.signin()
    wrong = "000000" if env.totp_now() != "000000" else "111111"
    r = env.code(wrong)
    assert r.status_code == 401
    assert SESSION_COOKIE not in r.cookies
    assert len(env.store.sessions) == 1
    assert env.store.auth.failures == 1


def test_2fa_stale_pending_redirects_to_signin():
    env = make_2fa_env()
    env.signin()
    env.clock.advance(301)
    r = env.code(env.totp_now())
    assert r.status_code == 303
    assert r.headers["location"] == "/signin"
    assert SESSION_COOKIE not in r.cookies


def test_2fa_without_pending_cookie_redirects_to_signin():
    env = make_2fa_env()
    r = env.code(env.totp_now())
    assert r.status_code == 303
    assert r.headers["location"] == "/signin"
    assert env.store.sessions == {}


def test_recovery_code_works_once():
    codes = ["abcdef0123", "0123456789"] + [f"{i:010x}" for i in range(7)]
    env = make_2fa_env(recovery=codes[:9] + ["ffffffffff"])
    env.signin()
    r = env.code(" ABCDEF0123 ")
    assert r.status_code == 303
    assert SESSION_COOKIE in r.cookies
    assert len(env.store.auth.recovery_hashes) == 9
    env.client.cookies.clear()
    env.signin()
    again = env.code("abcdef0123")
    assert again.status_code == 401
    assert len(env.store.auth.recovery_hashes) == 9


def test_five_wrong_passwords_lock():
    env = make_env(with_password())
    statuses = [env.signin("wrong-password-1").status_code for _ in range(MAX_FAILURES)]
    assert statuses == [401] * (MAX_FAILURES - 1) + [429]
    r = env.signin()
    assert r.status_code == 429
    assert "Sign-in is locked. Try again later." in r.text
    assert SESSION_COOKIE not in r.cookies
    assert env.store.sessions == {}


def test_lock_expires():
    env = make_env(with_password())
    for _ in range(MAX_FAILURES):
        env.signin("wrong-password-1")
    env.clock.advance(LOCK_SECONDS - 1)
    assert env.signin().status_code == 429
    env.clock.advance(1)
    r = env.signin()
    assert r.status_code == 303
    assert SESSION_COOKIE in r.cookies


def test_wrong_totp_codes_count_toward_lock():
    env = make_2fa_env()
    env.signin()
    wrong = "000000" if env.totp_now() != "000000" else "111111"
    statuses = [env.code(wrong).status_code for _ in range(MAX_FAILURES)]
    assert statuses == [401] * (MAX_FAILURES - 1) + [429]
    assert env.signin().status_code == 429
    assert env.code(env.totp_now()).status_code == 429
    assert SESSION_COOKIE not in env.client.cookies


def test_password_and_totp_failures_share_counter():
    env = make_2fa_env()
    env.signin("wrong-password-1")
    env.signin("wrong-password-1")
    assert env.store.auth.failures == 2
    env.signin()
    assert env.store.auth.failures == 2
    wrong = "000000" if env.totp_now() != "000000" else "111111"
    assert env.code(wrong).status_code == 401
    assert env.store.auth.failures == 3


def enable(env):
    r = env.client.post("/account/2fa", data={"action": "enable"})
    assert r.status_code == 200
    return r


def test_account_enable_shows_uri_and_codes():
    env = make_env(with_password())
    r = enable(env)
    assert "otpauth://" in r.text
    secret = env.store.secrets["totp_secret"]
    assert secret in r.text
    assert len(env.store.auth.recovery_hashes) == 10
    assert not env.store.auth.totp_enabled


def test_account_confirm_wrong_then_valid():
    env = make_env(with_password())
    enable(env)
    wrong = "000000" if env.totp_now() != "000000" else "111111"
    r = env.client.post("/account/2fa", data={"action": "confirm", "code": wrong})
    assert r.status_code == 400
    assert "Invalid code." in r.text
    assert not env.store.auth.totp_enabled
    ok = env.client.post("/account/2fa", data={"action": "confirm", "code": env.totp_now()})
    assert ok.status_code == 303
    assert env.store.auth.totp_enabled


def test_account_disable_wrong_then_valid():
    env = make_2fa_env(recovery=["abcdef0123"])
    wrong = "000000" if env.totp_now() != "000000" else "111111"
    r = env.client.post("/account/2fa", data={"action": "disable", "code": wrong})
    assert r.status_code == 400
    assert env.store.auth.totp_enabled
    ok = env.client.post("/account/2fa", data={"action": "disable", "code": env.totp_now()})
    assert ok.status_code == 303
    assert not env.store.auth.totp_enabled
    assert env.store.auth.recovery_hashes == ()
    assert env.store.get_secret("totp_secret") is None


def test_account_page_and_unknown_action():
    env = make_env(with_password())
    page = env.client.get("/account/2fa")
    assert page.status_code == 200
    assert "off" in page.text
    r = env.client.post("/account/2fa", data={"action": "nonsense"})
    assert r.status_code == 400
    r = env.client.post("/account/2fa", data={"action": "disable", "code": "123456"})
    assert r.status_code == 400


def test_signout_removes_session():
    env = make_env(with_password())
    env.signin()
    token = env.client.cookies[SESSION_COOKIE]
    assert session_hash(token) in env.store.sessions
    r = env.client.post("/signout")
    assert r.status_code == 303
    assert r.headers["location"] == "/signin"
    assert env.store.sessions == {}


def test_secrets_not_in_responses_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    env = make_env()
    bodies = []
    bodies.append(env.client.post("/setup", data={"password": "short", "confirm": "short"}).text)
    bodies.append(env.client.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD}).text)
    bodies.append(env.client.get("/signin").text)
    bodies.append(env.signin("wrong-password-1").text)
    env.signin()
    enable_page = enable(env).text
    secret = env.store.secrets["totp_secret"]
    assert secret in enable_page
    bodies.append(env.client.get("/account/2fa").text)
    bodies.append(env.client.post("/account/2fa", data={"action": "confirm", "code": "bad"}).text)
    bodies.append(
        env.client.post("/account/2fa", data={"action": "confirm", "code": env.totp_now()}).text
    )
    env.client.cookies.clear()
    bodies.append(env.signin().text)
    bodies.append(env.code("not-a-valid-code").text)
    codes = [w for w in enable_page.split("<code>")[3:] if "</code>" in w]
    codes = [w.split("</code>")[0] for w in codes]
    assert len(codes) == 10
    needles = [PASSWORD, "wrong-password-1", secret, "not-a-valid-code", *codes]
    for body in bodies:
        for needle in needles:
            assert needle not in body
    for record in caplog.records:
        text = record.getMessage()
        for needle in needles:
            assert needle not in text


def test_hash_and_verify_round_trip():
    hashed = hash_password(PASSWORD)
    assert hashed.startswith("$argon2")
    assert verify_password(hashed, PASSWORD)
    assert not verify_password(hashed, "other-password-1")
    assert not verify_password("not-a-hash", "x")


def test_constants():
    assert (MAX_FAILURES, LOCK_SECONDS, SESSION_SECONDS) == (5, 900, 43200)
    assert PENDING_COOKIE == "cal_mirror_pending"
