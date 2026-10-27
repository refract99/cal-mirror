import ast
import ipaddress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cal_mirror.crypto import generate_key
from cal_mirror.model import AuthRecord
from cal_mirror.ports import FixedClock
from cal_mirror.store_memory import MemoryStore
from cal_mirror.web import app as app_module
from cal_mirror.web.app import PUBLIC_PATHS, create_app
from cal_mirror.web.deps import SESSION_COOKIE, session_hash
from cal_mirror.web.page_signin import hash_password

START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
PASSWORD = "fixture-password-1"
SESSION = "fixture-session-1"
TLS_MESSAGE = (
    "CAL_MIRROR_TLS_CERT and CAL_MIRROR_TLS_KEY must both be set; "
    "cal-mirror never serves plain HTTP"
)
DSN = "postgresql://cal-mirror@localhost/cal_mirror"
BANNED = (
    "cal_mirror.google_api",
    "cal_mirror.worker",
    "cal_mirror.sync",
    "cal_mirror.requests_runner",
    "cal_mirror.fake_google",
    "googleapiclient",
)


def _client(store=None, clock=None):
    store = MemoryStore() if store is None else store
    clock = FixedClock(START) if clock is None else clock
    client = TestClient(
        create_app(store, clock),
        base_url="https://testserver",
        follow_redirects=False,
    )
    return store, clock, client


def test_public_paths():
    assert PUBLIC_PATHS == frozenset({"/signin", "/signin/totp", "/setup"})


def test_no_cookie_status_redirects_to_signin():
    _, _, client = _client()
    response = client.get("/status")
    assert response.status_code == 303
    assert response.headers["location"] == "/signin"


def test_no_cookie_rules_redirects_to_signin():
    _, _, client = _client()
    response = client.get("/rules")
    assert response.status_code == 303
    assert response.headers["location"] == "/signin"


def test_no_cookie_root_redirects_to_signin():
    _, _, client = _client()
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/signin"


def test_no_cookie_unknown_path_redirects_to_signin():
    _, _, client = _client()
    response = client.get("/nope")
    assert response.status_code == 303
    assert response.headers["location"] == "/signin"


def test_signin_when_password_is_set():
    store, _, client = _client()
    store.save_auth(AuthRecord(password_hash=hash_password(PASSWORD)))
    response = client.get("/signin")
    assert response.status_code == 200


def test_setup_when_auth_is_empty():
    store, _, client = _client()
    store.save_auth(AuthRecord())
    response = client.get("/setup")
    assert response.status_code == 200


def test_valid_session_opens_status_and_root():
    store, clock, client = _client()
    store.create_session(session_hash(SESSION), clock.now() + timedelta(hours=1))
    client.cookies.set(SESSION_COOKIE, SESSION)
    status = client.get("/status")
    root = client.get("/")
    assert status.status_code == 200
    assert root.status_code == 303
    assert root.headers["location"] == "/status"


def test_expired_session_redirects_to_signin():
    store, clock, client = _client()
    store.create_session(session_hash(SESSION), clock.now() + timedelta(hours=1))
    client.cookies.set(SESSION_COOKIE, SESSION)
    clock.advance(3600)
    response = client.get("/status")
    assert response.status_code == 303
    assert response.headers["location"] == "/signin"


def test_unknown_session_redirects_to_signin():
    _, _, client = _client()
    client.cookies.set(SESSION_COOKIE, "not-a-session")
    response = client.get("/status")
    assert response.status_code == 303
    assert response.headers["location"] == "/signin"


def test_password_sign_in_sets_session_cookie():
    store, _, client = _client()
    store.save_auth(AuthRecord(password_hash=hash_password(PASSWORD)))
    signed_in = client.post("/signin", data={"password": PASSWORD})
    assert signed_in.status_code == 303
    assert signed_in.headers["location"] == "/status"
    assert signed_in.cookies.get(SESSION_COOKIE)
    status = client.get("/status")
    assert status.status_code == 200


def test_security_headers_on_signin():
    store, _, client = _client()
    store.save_auth(AuthRecord(password_hash=hash_password(PASSWORD)))
    response = client.get("/signin")
    assert response.status_code == 200
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Content-Security-Policy"] == "default-src 'self'"
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"


def test_security_headers_on_unauthenticated_redirect():
    _, _, client = _client()
    response = client.get("/status")
    assert response.status_code == 303
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Content-Security-Policy"] == "default-src 'self'"
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"


def _patch_startup(monkeypatch):
    runs = []
    stores = []

    class FakeServer:
        def __init__(self, config):
            self.should_exit = False
            self.config = config

        def run(self):
            runs.append(self.config)

    class FakeThread:
        def __init__(self, *args, **kwargs):
            return None

        def start(self):
            return None

    class FakePg:
        def __init__(self, dsn, box):
            stores.append((dsn, box))

    monkeypatch.setattr(app_module.uvicorn, "Server", FakeServer)
    monkeypatch.setattr(app_module.threading, "Thread", FakeThread)
    monkeypatch.setattr(app_module, "PgStore", FakePg)
    return runs, stores


def _set_tls(monkeypatch, tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert")
    key.write_text("key")
    monkeypatch.setenv("CAL_MIRROR_TLS_CERT", str(cert))
    monkeypatch.setenv("CAL_MIRROR_TLS_KEY", str(key))
    return cert, key


def test_main_without_tls_does_not_start(monkeypatch):
    monkeypatch.delenv("CAL_MIRROR_TLS_CERT", raising=False)
    monkeypatch.delenv("CAL_MIRROR_TLS_KEY", raising=False)
    monkeypatch.setenv("CAL_MIRROR_DSN", DSN)
    monkeypatch.setenv("CAL_MIRROR_SECRET_KEY", generate_key())
    runs, stores = _patch_startup(monkeypatch)
    with pytest.raises(SystemExit) as caught:
        app_module.main()
    assert caught.value.code == TLS_MESSAGE
    assert runs == []
    assert stores == []


def test_main_without_tls_key_does_not_start(monkeypatch, tmp_path):
    cert = tmp_path / "cert.pem"
    cert.write_text("cert")
    monkeypatch.setenv("CAL_MIRROR_TLS_CERT", str(cert))
    monkeypatch.delenv("CAL_MIRROR_TLS_KEY", raising=False)
    monkeypatch.setenv("CAL_MIRROR_DSN", DSN)
    monkeypatch.setenv("CAL_MIRROR_SECRET_KEY", generate_key())
    runs, stores = _patch_startup(monkeypatch)
    with pytest.raises(SystemExit) as caught:
        app_module.main()
    assert caught.value.code == TLS_MESSAGE
    assert runs == []
    assert stores == []


def test_main_default_bind_and_port(monkeypatch, tmp_path):
    cert, key = _set_tls(monkeypatch, tmp_path)
    monkeypatch.setenv("CAL_MIRROR_DSN", DSN)
    monkeypatch.setenv("CAL_MIRROR_SECRET_KEY", generate_key())
    monkeypatch.delenv("CAL_MIRROR_BIND", raising=False)
    monkeypatch.delenv("CAL_MIRROR_PORT", raising=False)
    runs, stores = _patch_startup(monkeypatch)
    app_module.main()
    assert len(runs) == 1
    config = runs[0]
    assert isinstance(config.app, FastAPI)
    assert config.host == str(ipaddress.IPv4Address(0))
    assert config.port == 8443
    assert isinstance(config.port, int)
    assert config.ssl_certfile == str(cert)
    assert config.ssl_keyfile == str(key)
    assert stores[0][0] == DSN


def test_main_custom_bind_and_port(monkeypatch, tmp_path):
    _set_tls(monkeypatch, tmp_path)
    monkeypatch.setenv("CAL_MIRROR_DSN", DSN)
    monkeypatch.setenv("CAL_MIRROR_SECRET_KEY", generate_key())
    monkeypatch.setenv("CAL_MIRROR_BIND", "127.0.0.1")
    monkeypatch.setenv("CAL_MIRROR_PORT", "9443")
    runs, _stores = _patch_startup(monkeypatch)
    app_module.main()
    config = runs[0]
    assert config.host == "127.0.0.1"
    assert config.port == 9443


def test_main_without_dsn(monkeypatch, tmp_path):
    _set_tls(monkeypatch, tmp_path)
    monkeypatch.delenv("CAL_MIRROR_DSN", raising=False)
    monkeypatch.setenv("CAL_MIRROR_SECRET_KEY", generate_key())
    runs, stores = _patch_startup(monkeypatch)
    with pytest.raises(SystemExit) as caught:
        app_module.main()
    assert caught.value.code == "CAL_MIRROR_DSN is not set"
    assert runs == []
    assert stores == []


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_web_modules_do_not_import_calendar_stack():
    root = Path(__file__).resolve().parents[1]
    web = root / "cal_mirror" / "web"
    paths = [web / "app.py", *sorted(web.glob("page_*.py"))]
    assert paths
    for path in paths:
        for name in _imported_modules(path):
            banned = any(name == item or name.startswith(item + ".") for item in BANNED)
            assert not banned, f"{path.name} imports {name}"
