"""PRD-01 end to end through create_app: setup code, required second factor, passkey,
Google sign-in, per-address lock and same-origin posts."""

import base64
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from cal_mirror.ports import FixedClock
from cal_mirror.store_memory import MemoryStore
from cal_mirror.web import page_google_signin
from cal_mirror.web.app import create_app
from tests import soft_authenticator

BASE = "https://cal.example.org:8443"
RP_ID = "cal.example.org"
START = datetime(2026, 11, 3, 12, tzinfo=UTC)
CODE = "fixture-setup-code-1"
PASSWORD = "fixture-password-1"
CLIENT_ID = "fixture-client.apps.googleusercontent.com"
CLIENT = {
    "web": {
        "client_id": CLIENT_ID,
        "client_secret": "fixture-client-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}
CALENDAR_TOKEN = '{"token": "calendar-grant-1"}'
CLAIMS = {
    "sub": "sub-owner",
    "email": "owner@example.org",
    "aud": CLIENT_ID,
    "iss": "https://accounts.google.com",
    "exp": int(START.timestamp()) + 600,
}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class StubFlow:
    """Google's side of the OAuth flow: no network, a fixed state, an ID token with CLAIMS."""

    state = "fixture-state-1"

    def authorization_url(self, **kwargs: object) -> tuple[str, str]:
        return f"https://accounts.google.com/o/oauth2/auth?state={self.state}", self.state

    def fetch_token(self, code: str, **kwargs: object) -> dict:
        header = b64(json.dumps({"alg": "RS256", "kid": "fixture", "typ": "JWT"}).encode())
        token = f"{header}.{b64(json.dumps(CLAIMS).encode())}.{b64(b'fixture-signature')}"
        return {"access_token": "fixture-access", "token_type": "Bearer", "id_token": token}


def state_of(response) -> str:
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


def test_sign_in_story(monkeypatch):
    monkeypatch.setenv("CAL_MIRROR_SETUP_CODE", CODE)
    monkeypatch.delenv("CAL_MIRROR_SIGNIN_REDIRECT", raising=False)
    store = MemoryStore()
    store.set_secret("google_client", json.dumps(CLIENT))
    store.set_secret("google_token", CALENDAR_TOKEN)
    app = create_app(store, FixedClock(START))
    app.dependency_overrides[page_google_signin.get_signin_flow] = StubFlow

    def client(address: str) -> TestClient:
        return TestClient(
            app,
            base_url=BASE,
            follow_redirects=False,
            client=(address, 4000),
            headers={"Origin": BASE},
        )

    owner = client("198.51.100.1")
    assert owner.get("/status").headers["location"] == "/signin"
    form = {"password": PASSWORD, "confirm": PASSWORD, "setup_code": "guess"}
    assert owner.post("/setup", data=form).status_code == 403
    form["setup_code"] = CODE
    assert owner.post("/setup", data=form).headers["location"] == "/signin"

    signed = owner.post("/signin", data={"password": PASSWORD})
    assert signed.headers["location"] == "/account/2fa"
    assert owner.get("/status").headers["location"] == "/account/2fa"

    authenticator = soft_authenticator.SoftAuthenticator()
    options = owner.post("/account/passkeys/options").json()
    credential = authenticator.register(options["challenge"], RP_ID, BASE)
    added = owner.post(
        "/account/passkeys", data={"name": "Laptop", "credential": json.dumps(credential)}
    )
    assert added.headers["location"] == "/account/2fa"
    assert owner.get("/status").status_code == 200
    assert "Laptop" in owner.get("/account/2fa").text

    assert owner.post("/signout").headers["location"] == "/signin"
    step = owner.post("/signin", data={"password": PASSWORD})
    assert step.headers["location"] == "/signin/totp"  # The second-factor step: code or passkey.
    assert owner.get("/status").headers["location"] == "/signin"
    challenge = owner.post("/signin/passkey/options").json()["challenge"]
    authenticator.sign_count = 1
    assertion = json.dumps(authenticator.assertion(challenge, RP_ID, BASE))
    done = owner.post("/signin/passkey", data={"credential": assertion})
    assert done.headers["location"] == "/status"
    assert owner.get("/status").status_code == 200

    page = owner.get("/account/2fa")
    assert BASE + "/signin/google/callback" in page.text
    state = state_of(owner.post("/account/google/link"))
    linked = owner.get("/signin/google/callback", params={"state": state, "code": "c1"})
    assert linked.headers["location"] == "/account/2fa"
    assert "owner@example.org" in owner.get("/account/2fa").text

    assert owner.post("/signout", headers={"Origin": "https://evil.example.net"}).status_code == 403
    assert owner.get("/status").status_code == 200
    owner.post("/signout")
    assert "Sign in with Google" in owner.get("/signin").text
    state = state_of(owner.post("/signin/google"))
    google = owner.get("/signin/google/callback", params={"state": state, "code": "c2"})
    assert google.headers["location"] == "/status"
    assert owner.get("/status").status_code == 200

    stranger = client("203.0.113.9")
    codes = [stranger.post("/signin", data={"password": "nope"}).status_code for _ in range(5)]
    assert codes == [401, 401, 401, 401, 429]
    assert stranger.post("/signin", data={"password": PASSWORD}).status_code == 429
    owner.post("/signout")
    assert owner.post("/signin", data={"password": PASSWORD}).status_code == 303
    assert store.get_secret("google_token") == CALENDAR_TOKEN
