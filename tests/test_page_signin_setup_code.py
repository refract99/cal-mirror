"""PRD-01 R1, R2: the first password needs the setup code from the environment."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from cal_mirror.ports import FixedClock
from cal_mirror.store_memory import MemoryStore
from cal_mirror.web.app import create_app

ORIGIN = "https://testserver"
PASSWORD = "fixture-password-1"
CODE = "fixture-setup-code-1"
START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def client(store: MemoryStore) -> TestClient:
    return TestClient(
        create_app(store, FixedClock(START)),
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
        follow_redirects=False,
    )


def setup(c: TestClient, **extra: str):
    return c.post("/setup", data={"password": PASSWORD, "confirm": PASSWORD, **extra})


def test_right_code_sets_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAL_MIRROR_SETUP_CODE", CODE)
    store = MemoryStore()
    form = client(store).get("/setup")
    assert form.status_code == 200
    assert 'name="setup_code"' in form.text
    response = setup(client(store), setup_code=CODE)
    assert response.status_code == 303
    assert response.headers["location"] == "/signin"
    assert store.get_auth().password_hash.startswith("$argon2")


@pytest.mark.parametrize("extra", [{}, {"setup_code": ""}, {"setup_code": "wrong-code-1"}])
def test_missing_or_wrong_code_is_403(monkeypatch: pytest.MonkeyPatch, extra) -> None:
    monkeypatch.setenv("CAL_MIRROR_SETUP_CODE", CODE)
    store = MemoryStore()
    response = setup(client(store), **extra)
    assert response.status_code == 403
    assert store.get_auth().password_hash == ""


@pytest.mark.parametrize("value", [None, ""])
def test_unconfigured_code_refuses_setup(monkeypatch: pytest.MonkeyPatch, value) -> None:
    if value is None:
        monkeypatch.delenv("CAL_MIRROR_SETUP_CODE", raising=False)
    else:
        monkeypatch.setenv("CAL_MIRROR_SETUP_CODE", value)
    store = MemoryStore()
    c = client(store)
    page = c.get("/setup")
    assert page.status_code == 403
    assert "setup code is not configured" in page.text.lower()
    for extra in ({}, {"setup_code": ""}, {"setup_code": CODE}):
        response = setup(c, **extra)
        assert response.status_code == 403
        assert "setup code is not configured" in response.text.lower()
    assert store.get_auth().password_hash == ""
