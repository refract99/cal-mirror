"""PRD-01 R8, R9: failed sign-ins are counted and locked per client address."""

from datetime import UTC, datetime

import pyotp
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from cal_mirror.model import AuthRecord, Settings
from cal_mirror.ports import FixedClock
from cal_mirror.store_memory import MemoryStore
from cal_mirror.web.app import create_app
from cal_mirror.web.page_signin import hash_password

ORIGIN = "https://testserver"
PASSWORD = "fixture-password-1"
WRONG = "wrong-password-1"
START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
A = "198.51.100.7"
B = "203.0.113.9"


class Env:
    def __init__(self, limit: int = 5, lock: int = 900) -> None:
        self.store = MemoryStore()
        self.store.save_settings(Settings(signin_failure_limit=limit, signin_lock_seconds=lock))
        self.store.save_auth(AuthRecord(password_hash=hash_password(PASSWORD), totp_enabled=True))
        self.secret = pyotp.random_base32()
        self.store.set_secret("totp_secret", self.secret)
        self.clock = FixedClock(START)
        self.app = create_app(self.store, self.clock)

    def client(self, address: str) -> TestClient:
        return TestClient(
            self.app,
            base_url=ORIGIN,
            headers={"Origin": ORIGIN},
            client=(address, 50000),
            follow_redirects=False,
        )

    def password(self, address: str, password: str = PASSWORD):
        return self.client(address).post("/signin", data={"password": password})

    def complete(self, address: str):
        c = self.client(address)
        first = c.post("/signin", data={"password": PASSWORD})
        assert first.headers.get("location") == "/signin/totp"
        code = pyotp.TOTP(self.secret).at(self.clock.now())
        done = c.post("/signin/totp", data={"code": code})
        assert done.status_code == 303
        assert done.headers["location"] == "/status"
        return done


def test_limit_locks_only_that_address() -> None:
    env = Env()
    for _ in range(4):
        assert env.password(A, WRONG).status_code == 401
    env.password(A, WRONG)
    locked = env.password(A)
    assert locked.status_code == 429
    assert "set-cookie" not in locked.headers
    assert env.password(A, WRONG).status_code == 429
    other = env.password(B)
    assert other.status_code == 303
    assert other.headers["location"] == "/signin/totp"
    env.complete(B)


def test_lock_lasts_the_lock_time() -> None:
    env = Env(limit=2, lock=60)
    env.password(A, WRONG)
    env.password(A, WRONG)
    assert env.password(A).status_code == 429
    env.clock.advance(59)
    assert env.password(A).status_code == 429
    env.clock.advance(2)
    reopened = env.password(A)
    assert reopened.status_code == 303
    assert reopened.headers["location"] == "/signin/totp"


def test_wrong_codes_count_for_the_address() -> None:
    env = Env(limit=3)
    c = env.client(A)
    assert c.post("/signin", data={"password": PASSWORD}).status_code == 303
    for _ in range(3):
        c.post("/signin/totp", data={"code": "000000"})
    assert env.password(A).status_code == 429
    assert c.post("/signin/totp", data={"code": "000000"}).status_code == 429
    assert env.password(B).status_code == 303


def test_completed_signin_clears_only_its_own_address() -> None:
    env = Env(limit=5)
    for _ in range(3):
        env.password(A, WRONG)
        env.password(B, WRONG)
    env.complete(A)
    for _ in range(4):
        assert env.password(A, WRONG).status_code == 401
    assert env.password(A).status_code == 303
    env.password(B, WRONG)
    env.password(B, WRONG)
    assert env.password(B).status_code == 429


def test_signin_elsewhere_does_not_lift_a_lock() -> None:
    env = Env(limit=2)
    env.password(A, WRONG)
    env.password(A, WRONG)
    assert env.password(A).status_code == 429
    env.complete(B)
    assert env.password(A).status_code == 429


INGRESS = "10.42.0.17"


def test_behind_a_trusted_ingress_the_lock_is_per_visitor() -> None:
    """With the ingress in FORWARDED_ALLOW_IPS, uvicorn takes the client from X-Forwarded-For,
    so one visitor's failures do not lock out another arriving through the same ingress."""
    env = Env()
    proxied = ProxyHeadersMiddleware(env.app, trusted_hosts="10.42.0.0/16")

    def via_ingress(visitor: str):
        return TestClient(
            proxied,
            base_url=ORIGIN,
            headers={"Origin": ORIGIN, "X-Forwarded-For": visitor},
            client=(INGRESS, 50000),
            follow_redirects=False,
        ).post("/signin", data={"password": WRONG})

    for _ in range(5):
        via_ingress(A)
    assert via_ingress(A).status_code == 429
    assert via_ingress(B).status_code == 401
