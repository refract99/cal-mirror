"""PRD-01 R8, R16-R20: link a Google account and sign in with it."""

import base64
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pyotp
import pytest
from fastapi.testclient import TestClient

from cal_mirror.model import AuthRecord, GoogleLink, Settings
from cal_mirror.ports import FixedClock
from cal_mirror.store_memory import MemoryStore
from cal_mirror.web import page_google_signin
from cal_mirror.web.app import create_app
from cal_mirror.web.page_oauth import CALENDAR_SCOPE
from cal_mirror.web.page_signin import hash_password

ORIGIN = "https://testserver"
PASSWORD = "fixture-password-1"
CLIENT_ID = "fixture-client.apps.googleusercontent.com"
CALENDAR_TOKEN = '{"token": "fixture-calendar-token"}'
START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
CALLBACK = "/signin/google/callback"
BUTTON = "Sign in with Google"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def id_token(**changes: object) -> str:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "sub-owner",
        "email": "owner@example.com",
        "email_verified": True,
        "iat": int(START.timestamp()),
        "exp": int((START + timedelta(minutes=10)).timestamp()),
    }
    claims.update(changes)
    header = b64(json.dumps({"alg": "RS256", "kid": "fixture", "typ": "JWT"}).encode())
    return f"{header}.{b64(json.dumps(claims).encode())}.{b64(b'fixture-signature')}"


class StubFlow:
    """Stands in for the OAuth flow: no network, a fixed state and a chosen ID token."""

    def __init__(self) -> None:
        self.state = "fixture-state-1"
        self.token = id_token()
        self.codes: list[str] = []

    def authorization_url(self, **kwargs: object) -> tuple[str, str]:
        return f"https://accounts.google.com/o/oauth2/auth?state={self.state}", self.state

    def fetch_token(self, code: str, **kwargs: object) -> dict:
        self.codes.append(code)
        return {"access_token": "fixture-access", "token_type": "Bearer", "id_token": self.token}


class WatchedStore(MemoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.secret_names: list[str] = []

    def get_secret(self, name: str) -> str | None:
        self.secret_names.append(name)
        return super().get_secret(name)

    def set_secret(self, name: str, value: str | None) -> None:
        self.secret_names.append(name)
        super().set_secret(name, value)


class Env:
    def __init__(self, stub: bool = True, limit: int = 5) -> None:
        self.store = WatchedStore()
        self.store.save_settings(Settings(signin_failure_limit=limit))
        self.store.save_auth(AuthRecord(password_hash=hash_password(PASSWORD), totp_enabled=True))
        self.secret = pyotp.random_base32()
        self.store.set_secret("totp_secret", self.secret)
        client = {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": "fixture-client-secret",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        }
        self.store.set_secret("google_client", json.dumps(client))
        self.store.set_secret("google_token", CALENDAR_TOKEN)
        self.clock = FixedClock(START)
        self.app = create_app(self.store, self.clock)
        self.flow = StubFlow()
        if stub:
            self.app.dependency_overrides[page_google_signin.get_signin_flow] = lambda: self.flow

    def client(self) -> TestClient:
        return TestClient(
            self.app, base_url=ORIGIN, headers={"Origin": ORIGIN}, follow_redirects=False
        )

    def owner(self) -> TestClient:
        c = self.client()
        c.post("/signin", data={"password": PASSWORD})
        code = pyotp.TOTP(self.secret).at(self.clock.now())
        assert c.post("/signin/totp", data={"code": code}).status_code == 303
        assert c.get("/status").status_code == 200
        return c

    def link(self, c: TestClient, **claims: object):
        self.flow.token = id_token(**claims)
        start = c.post("/account/google/link")
        assert start.status_code == 303
        return c.get(CALLBACK, params={"state": self.flow.state, "code": "fixture-code"})

    def google_signin(self, c: TestClient, state: str | None = None, **claims: object):
        self.flow.token = id_token(**claims)
        start = c.post("/signin/google")
        assert start.status_code == 303
        given = self.flow.state if state is None else state
        return c.get(CALLBACK, params={"state": given, "code": "fixture-code"})

    def locked(self) -> bool:
        return self.client().post("/signin", data={"password": PASSWORD}).status_code == 429


def query(response) -> dict[str, list[str]]:
    location = response.headers["location"]
    assert location.startswith("https://accounts.google.com/")
    return parse_qs(urlsplit(location).query)


def test_link_asks_for_openid_and_email_only() -> None:
    env = Env(stub=False)
    start = env.owner().post("/account/google/link")
    assert start.status_code == 303
    asked = query(start)
    assert set(asked["scope"][0].split()) == {"openid", "email"}
    assert asked["client_id"] == [CLIENT_ID]
    assert asked["redirect_uri"] == [ORIGIN + CALLBACK]


def test_signin_redirect_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAL_MIRROR_SIGNIN_REDIRECT", "https://cal.example/signin/google/callback")
    env = Env(stub=False)
    c = env.owner()
    asked = query(c.post("/account/google/link"))
    assert asked["redirect_uri"] == ["https://cal.example/signin/google/callback"]
    assert "https://cal.example/signin/google/callback" in c.get("/account/2fa").text


def test_account_page_shows_the_redirect_uri() -> None:
    env = Env()
    page = env.owner().get("/account/2fa")
    assert page.status_code == 200
    assert ORIGIN + CALLBACK in page.text


def test_link_replace_and_unlink() -> None:
    env = Env()
    c = env.owner()
    linked = env.link(c)
    assert linked.status_code == 303
    assert linked.headers["location"] == "/account/2fa"
    assert env.store.get_google_link() == GoogleLink(sub="sub-owner", email="owner@example.com")
    assert "owner@example.com" in c.get("/account/2fa").text
    env.link(c, sub="sub-new", email="new@example.com")
    assert env.store.get_google_link() == GoogleLink(sub="sub-new", email="new@example.com")
    gone = c.post("/account/google/unlink")
    assert gone.status_code == 303
    assert env.store.get_google_link() is None


def test_linked_account_signs_in_without_second_factor() -> None:
    env = Env()
    env.link(env.owner())
    c = env.client()
    assert BUTTON in c.get("/signin").text
    done = env.google_signin(c)
    assert done.status_code == 303
    assert done.headers["location"] == "/status"
    assert c.get("/status").status_code == 200


def test_no_link_no_button_and_no_session() -> None:
    env = Env(limit=1)
    c = env.client()
    assert BUTTON not in c.get("/signin").text
    refused = c.get(CALLBACK, params={"state": env.flow.state, "code": "fixture-code"})
    assert refused.status_code == 401
    assert c.get("/status").status_code == 303
    assert env.locked()


def test_unlinked_after_start_is_refused() -> None:
    env = Env(limit=1)
    env.link(env.owner())
    c = env.client()
    c.post("/signin/google")
    env.store.set_google_link(None)
    refused = c.get(CALLBACK, params={"state": env.flow.state, "code": "fixture-code"})
    assert refused.status_code == 401
    assert c.get("/status").status_code == 303
    assert env.locked()


BAD_TOKENS = [
    {"sub": "sub-stranger"},
    {"aud": "other-client.apps.googleusercontent.com"},
    {"iss": "https://evil.example"},
    {"exp": int((START - timedelta(seconds=5)).timestamp())},
]


@pytest.mark.parametrize("claims", BAD_TOKENS)
def test_bad_id_token_is_refused_and_counted(claims: dict) -> None:
    env = Env(limit=1)
    env.link(env.owner())
    c = env.client()
    refused = env.google_signin(c, **claims)
    assert refused.status_code == 401
    assert c.get("/status").status_code == 303
    assert env.locked()


def test_wrong_state_is_refused_and_counted() -> None:
    env = Env(limit=1)
    env.link(env.owner())
    c = env.client()
    refused = env.google_signin(c, state="forged-state")
    assert refused.status_code == 401
    assert c.get("/status").status_code == 303
    assert env.locked()


def test_google_never_touches_the_calendar_grant() -> None:
    env = Env()
    owner = env.owner()
    env.store.secret_names.clear()
    env.link(owner)
    owner.post("/account/google/unlink")
    env.link(owner)
    env.google_signin(env.client())
    assert "google_token" not in env.store.secret_names
    assert env.store.get_secret("google_token") == CALENDAR_TOKEN


def test_calendar_connect_still_asks_for_calendar_only() -> None:
    env = Env(stub=False)
    asked = query(env.owner().get("/google/connect"))
    assert asked["scope"] == [CALENDAR_SCOPE]
