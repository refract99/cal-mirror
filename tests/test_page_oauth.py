import json
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error

from cal_mirror.model import Status
from cal_mirror.ports import FixedClock
from cal_mirror.web.deps import current_user
from cal_mirror.web.page_oauth import CALENDAR_SCOPE, get_flow, router

TOKEN_JSON = '{"token": "fixture-token-1"}'
AUTH_URL = "https://accounts.google.com/o/oauth2/auth?fixture=1"


class FakeStore:
    def __init__(self, status=None):
        self.secrets = {}
        self.meta = {}
        self.status = status or Status(token_ok=False)

    def get_secret(self, name):
        return self.secrets.get(name)

    def set_secret(self, name, value):
        self.secrets[name] = value

    def get_status(self):
        return self.status

    def set_status(self, status):
        self.status = status

    def get_meta(self, key):
        return self.meta.get(key)

    def set_meta(self, key, value):
        self.meta[key] = value


class FakeCredentials:
    def to_json(self):
        return TOKEN_JSON


class FakeFlow:
    def __init__(self, error=None):
        self.error = error
        self.auth_kwargs = None
        self.code = None
        self.credentials = FakeCredentials()

    def authorization_url(self, **kw):
        self.auth_kwargs = kw
        return AUTH_URL, "fixture-state-1"

    def fetch_token(self, code):
        self.code = code
        if self.error is not None:
            raise self.error


def make_client(store, flow=None, signed_in=True):
    app = FastAPI()
    app.state.store = store
    app.state.clock = FixedClock(datetime(2026, 10, 24, tzinfo=UTC))
    app.include_router(router)
    if signed_in:
        app.dependency_overrides[current_user] = lambda: "steve"
    app.dependency_overrides[get_flow] = lambda: flow
    return TestClient(app, follow_redirects=False)


def callback(store, flow, query, state="fixture-state-1"):
    store.meta["oauth_state"] = state
    return make_client(store, flow).get(f"/google/callback?{query}")


def test_no_secret_shows_connect():
    response = make_client(FakeStore()).get("/google")
    assert 'id="google-state">Connect<' in response.text


def test_token_ok_shows_connected():
    store = FakeStore(Status(token_ok=True))
    store.secrets["google_token"] = TOKEN_JSON
    assert 'id="google-state">Connected<' in make_client(store).get("/google").text


def test_token_not_ok_shows_reconnect():
    store = FakeStore(Status(token_ok=False))
    store.secrets["google_token"] = TOKEN_JSON
    assert 'id="google-state">Reconnect<' in make_client(store).get("/google").text


def test_empty_secret_shows_connect():
    store = FakeStore()
    store.secrets["google_token"] = ""
    assert 'id="google-state">Connect<' in make_client(store).get("/google").text


def test_page_never_shows_token():
    store = FakeStore(Status(token_ok=True))
    store.secrets["google_token"] = TOKEN_JSON
    assert "fixture-token-1" not in make_client(store).get("/google").text


def test_connect_redirects_to_google():
    store, flow = FakeStore(), FakeFlow()
    response = make_client(store, flow).get("/google/connect")
    assert (response.status_code, response.headers["location"]) == (303, AUTH_URL)


def test_connect_stores_state():
    store, flow = FakeStore(), FakeFlow()
    make_client(store, flow).get("/google/connect")
    assert store.meta["oauth_state"] == "fixture-state-1"


def test_connect_requests_offline_consent():
    store, flow = FakeStore(), FakeFlow()
    make_client(store, flow).get("/google/connect")
    assert flow.auth_kwargs == {
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }


def test_callback_redirects_to_google_page():
    response = callback(FakeStore(), FakeFlow(), "state=fixture-state-1&code=c1")
    assert (response.status_code, response.headers["location"]) == (303, "/google")


def test_callback_passes_code():
    flow = FakeFlow()
    callback(FakeStore(), flow, "state=fixture-state-1&code=c1")
    assert flow.code == "c1"


def test_callback_stores_token():
    store = FakeStore()
    callback(store, FakeFlow(), "state=fixture-state-1&code=c1")
    assert store.secrets["google_token"] == TOKEN_JSON


def test_callback_sets_token_ok_only():
    status = Status(
        last_good_sync=datetime(2026, 10, 1, tzinfo=UTC), failure="token", token_ok=False
    )
    store = FakeStore(status)
    callback(store, FakeFlow(), "state=fixture-state-1&code=c1")
    assert store.status == Status(
        last_good_sync=datetime(2026, 10, 1, tzinfo=UTC), failure="token", token_ok=True
    )


def test_callback_clears_state():
    store = FakeStore()
    callback(store, FakeFlow(), "state=fixture-state-1&code=c1")
    assert store.meta["oauth_state"] == ""


def test_callback_and_next_page_hide_token():
    store = FakeStore()
    client = make_client(store, FakeFlow())
    store.meta["oauth_state"] = "fixture-state-1"
    first = client.get("/google/callback?state=fixture-state-1&code=c1")
    second = client.get("/google")
    assert "fixture-token-1" not in first.text + second.text


def test_replayed_callback_fails():
    store = FakeStore()
    client = make_client(store, FakeFlow())
    store.meta["oauth_state"] = "fixture-state-1"
    client.get("/google/callback?state=fixture-state-1&code=c1")
    replay = client.get("/google/callback?state=fixture-state-1&code=c1")
    assert replay.status_code == 400


def test_wrong_state_is_rejected():
    response = callback(FakeStore(), FakeFlow(), "state=wrong&code=c1")
    assert (response.status_code, "OAuth state does not match" in response.text) == (400, True)


def test_wrong_state_stores_nothing():
    store = FakeStore()
    callback(store, FakeFlow(), "state=wrong&code=c1")
    assert store.secrets == {}


def test_no_stored_state_is_rejected():
    store = FakeStore()
    response = make_client(store, FakeFlow()).get("/google/callback?state=fixture-state-1&code=c1")
    assert (response.status_code, "OAuth state does not match" in response.text) == (400, True)


def test_empty_stored_state_is_rejected():
    response = callback(FakeStore(), FakeFlow(), "state=fixture-state-1&code=c1", state="")
    assert (response.status_code, "OAuth state does not match" in response.text) == (400, True)


def test_missing_state_param_is_rejected():
    response = callback(FakeStore(), FakeFlow(), "code=c1")
    assert (response.status_code, "OAuth state does not match" in response.text) == (400, True)


def test_oauth_error_is_generic_and_stores_nothing():
    store = FakeStore()
    flow = FakeFlow(OAuth2Error(description="fixture-token-1"))
    response = callback(store, flow, "state=fixture-state-1&code=c1")
    assert (
        response.status_code,
        "Google sign-in failed" in response.text,
        "fixture-token-1" in response.text,
        store.secrets,
    ) == (400, True, False, {})


def test_scope_warning_is_generic():
    flow = FakeFlow(Warning("Scope has changed"))
    response = callback(FakeStore(), flow, "state=fixture-state-1&code=c1")
    assert (response.status_code, "Google sign-in failed" in response.text) == (400, True)


def test_failed_exchange_keeps_state():
    store = FakeStore()
    callback(store, FakeFlow(ValueError("bad")), "state=fixture-state-1&code=c1")
    assert store.meta["oauth_state"] == "fixture-state-1"


def test_missing_code_fails():
    response = callback(FakeStore(), FakeFlow(), "state=fixture-state-1")
    assert (response.status_code, "Google sign-in failed" in response.text) == (400, True)


def test_denied_access_fails():
    response = callback(FakeStore(), FakeFlow(), "state=fixture-state-1&error=access_denied")
    assert (response.status_code, "Google sign-in failed" in response.text) == (400, True)


def test_error_page_has_try_again_and_no_state():
    response = callback(FakeStore(), FakeFlow(), "state=wrong")
    assert ("Try again" in response.text, "google-state" in response.text) == (True, False)


def test_signed_out_redirects_to_signin():
    response = make_client(FakeStore(), signed_in=False).get("/google")
    assert (response.status_code, response.headers["location"]) == (303, "/signin")


CLIENT = {
    "web": {
        "client_id": "fixture-client-1",
        "client_secret": "fixture-password-1",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
}


class FakeRequest:
    base_url = "https://cal.example.net/"


def test_get_flow_prefers_stored_client_and_derives_redirect(monkeypatch):
    monkeypatch.delenv("CAL_MIRROR_GOOGLE_CLIENT", raising=False)
    monkeypatch.delenv("CAL_MIRROR_OAUTH_REDIRECT", raising=False)
    store = FakeStore()
    store.secrets["google_client"] = json.dumps(CLIENT)
    flow = get_flow(FakeRequest(), store)
    assert (isinstance(flow, Flow), flow.redirect_uri, flow.oauth2session.scope) == (
        True,
        "https://cal.example.net/google/callback",
        [CALENDAR_SCOPE],
    )


def test_get_flow_falls_back_to_file_and_env_redirect(monkeypatch, tmp_path):
    path = tmp_path / "client.json"
    path.write_text(json.dumps(CLIENT))
    monkeypatch.setenv("CAL_MIRROR_GOOGLE_CLIENT", str(path))
    monkeypatch.setenv("CAL_MIRROR_OAUTH_REDIRECT", "https://x.example.net/google/callback")
    flow = get_flow(FakeRequest(), FakeStore())
    assert flow.redirect_uri == "https://x.example.net/google/callback"


def test_save_client_stores_it_and_hides_the_secret():
    store = FakeStore()
    client = make_client(store)
    response = client.post("/google/client", data={"client_id": " id-1 ", "client_secret": "s-1"})
    saved = json.loads(store.secrets["google_client"])["web"]
    assert (response.status_code, saved["client_id"], saved["client_secret"]) == (
        303,
        "id-1",
        "s-1",
    )
    page = client.get("/google").text
    assert "s-1" not in page and "Connect Google" in page


def test_save_client_requires_both_fields():
    store = FakeStore()
    response = make_client(store).post("/google/client", data={"client_id": "id-1"})
    assert (response.status_code, "google_client" in store.secrets) == (400, False)


def test_page_without_client_hides_connect_button():
    page = make_client(FakeStore()).get("/google").text
    assert "Connect Google" not in page and 'name="client_id"' in page


def test_connect_without_config_shows_a_readable_error(monkeypatch):
    from cal_mirror.web.app import create_app

    monkeypatch.delenv("CAL_MIRROR_GOOGLE_CLIENT", raising=False)
    store = FakeStore()
    store.session_valid = lambda *_: True
    client = TestClient(create_app(store, FixedClock(datetime(2026, 1, 1, tzinfo=UTC))))
    client.cookies.set("cal_mirror_session", "fixture-session-1")
    response = client.get("/google/connect")
    assert response.status_code == 500
    assert "Enter your Google OAuth client" in response.text


def test_stylesheet_is_served_without_sign_in():
    from cal_mirror.web.app import create_app

    response = TestClient(create_app(FakeStore())).get("/static/style.css")
    assert response.status_code == 200
