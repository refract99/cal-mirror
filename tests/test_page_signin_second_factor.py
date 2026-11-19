"""PRD-01 R4, R5, R6: a second factor is required, and the last one cannot be removed."""

from datetime import UTC, datetime

import pyotp
from fastapi.testclient import TestClient

from cal_mirror.model import AuthRecord, Passkey
from cal_mirror.ports import FixedClock
from cal_mirror.store_memory import MemoryStore
from cal_mirror.web.app import create_app
from cal_mirror.web.page_signin import hash_password

ORIGIN = "https://testserver"
PASSWORD = "fixture-password-1"
START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
ENROLL = "/account/2fa"


def make(totp: bool = False, passkey: bool = False):
    store = MemoryStore()
    store.save_auth(AuthRecord(password_hash=hash_password(PASSWORD), totp_enabled=totp))
    secret = pyotp.random_base32()
    if totp:
        store.set_secret("totp_secret", secret)
    if passkey:
        store.add_passkey(
            Passkey(
                credential_id="fixture-credential-1",
                name="Laptop",
                public_key=b"fixture-public-key",
                sign_count=0,
                created=START,
            )
        )
    clock = FixedClock(START)
    c = TestClient(
        create_app(store, clock),
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
        follow_redirects=False,
    )
    return store, clock, c, secret


def test_password_without_factor_only_reaches_enrollment() -> None:
    store, clock, c, _ = make()
    signin = c.post("/signin", data={"password": PASSWORD})
    assert signin.status_code == 303
    for path in ("/status", "/rules", "/settings", "/google", "/calendars", "/"):
        page = c.get(path)
        assert page.status_code == 303, path
        assert page.headers["location"] == ENROLL, path
    assert c.post("/settings/sync-now").headers["location"] == ENROLL
    assert c.get(ENROLL).status_code == 200
    enabled = c.post(ENROLL, data={"action": "enable"})
    assert enabled.status_code == 200
    assert c.get("/status").headers["location"] == ENROLL
    code = pyotp.TOTP(store.get_secret("totp_secret")).at(clock.now())
    confirmed = c.post(ENROLL, data={"action": "confirm", "code": code})
    assert confirmed.status_code == 303
    assert c.get("/status").status_code == 200


def test_signout_works_before_enrollment() -> None:
    _, _, c, _ = make()
    c.post("/signin", data={"password": PASSWORD})
    out = c.post("/signout")
    assert out.status_code == 303
    assert out.headers["location"] == "/signin"
    after = c.get(ENROLL)
    assert after.status_code == 303
    assert after.headers["location"] == "/signin"


def test_password_with_totp_goes_to_second_factor() -> None:
    _, clock, c, secret = make(totp=True)
    signin = c.post("/signin", data={"password": PASSWORD})
    assert signin.status_code == 303
    assert signin.headers["location"] == "/signin/totp"
    status = c.get("/status")
    assert status.status_code == 303
    assert status.headers["location"] == "/signin"
    done = c.post("/signin/totp", data={"code": pyotp.TOTP(secret).at(clock.now())})
    assert done.status_code == 303
    assert done.headers["location"] == "/status"
    assert c.get("/status").status_code == 200


def test_password_with_passkey_only_goes_to_second_factor() -> None:
    _, _, c, _ = make(passkey=True)
    signin = c.post("/signin", data={"password": PASSWORD})
    assert signin.status_code == 303
    assert signin.headers["location"] == "/signin/totp"
    assert c.get("/signin/totp").status_code == 200
    status = c.get("/status")
    assert status.status_code == 303
    assert status.headers["location"] == "/signin"


def signed_in(passkey: bool):
    store, clock, c, secret = make(totp=True, passkey=passkey)
    c.post("/signin", data={"password": PASSWORD})
    c.post("/signin/totp", data={"code": pyotp.TOTP(secret).at(clock.now())})
    assert c.get("/status").status_code == 200
    return store, clock, c, secret


def test_last_factor_totp_cannot_be_turned_off() -> None:
    store, clock, c, secret = signed_in(passkey=False)
    before = store.get_auth()
    code = pyotp.TOTP(secret).at(clock.now())
    response = c.post(ENROLL, data={"action": "disable", "code": code})
    assert response.status_code == 400
    assert store.get_auth() == before
    assert store.get_secret("totp_secret") == secret


def test_totp_can_be_turned_off_while_a_passkey_remains() -> None:
    store, clock, c, secret = signed_in(passkey=True)
    code = pyotp.TOTP(secret).at(clock.now())
    response = c.post(ENROLL, data={"action": "disable", "code": code})
    assert response.status_code == 303
    assert store.get_auth().totp_enabled is False
    assert c.get("/status").status_code == 200
