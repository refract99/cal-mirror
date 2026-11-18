"""PRD-01 R7, R10: sign-in lock settings on the Settings page; `two_factor` is gone."""

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from cal_mirror.model import AuthRecord, Settings
from cal_mirror.ports import FixedClock
from cal_mirror.store_memory import MemoryStore
from cal_mirror.web.app import create_app
from cal_mirror.web.deps import SESSION_COOKIE, session_hash

ORIGIN = "https://testserver"
TOKEN = "fixture-session-1"
START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)

VALID = {
    "sync_interval_minutes": "10",
    "ntfy_server": "https://ntfy.example.com",
    "ntfy_topic": "cal_mirror-1",
    "archive_destinations": "/mnt/usb",
    "owner_email": "me@example.org",
    "time_zone": "Europe/Berlin",
    "web_url": "https://cal.example.org",
    "check_no_link": "on",
    "allowed_hours_0": "09:00-17:30",
    "allowed_hours_1": "",
    "allowed_hours_2": "",
    "allowed_hours_3": "",
    "allowed_hours_4": "",
    "allowed_hours_5": "",
    "allowed_hours_6": "",
    "signin_failure_limit": "7",
    "signin_lock_seconds": "1200",
}


def signed_in() -> tuple[MemoryStore, TestClient]:
    store = MemoryStore()
    store.save_auth(AuthRecord(password_hash="x", totp_enabled=True))
    store.set_secret("totp_secret", "JBSWY3DPEHPK3PXP")
    store.create_session(session_hash(TOKEN), START + timedelta(hours=1))
    c = TestClient(
        create_app(store, FixedClock(START)),
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
        follow_redirects=False,
    )
    c.cookies.set(SESSION_COOKIE, TOKEN)
    return store, c


def test_settings_has_no_two_factor_field() -> None:
    assert "two_factor" not in {f.name for f in dataclasses.fields(Settings)}


def test_defaults_are_5_failures_and_900_seconds() -> None:
    assert Settings().signin_failure_limit == 5
    assert Settings().signin_lock_seconds == 900


def test_page_shows_and_saves_both_values() -> None:
    store, c = signed_in()
    page = c.get("/settings")
    assert page.status_code == 200
    assert 'name="signin_failure_limit"' in page.text
    assert 'name="signin_lock_seconds"' in page.text
    saved = c.post("/settings", data=VALID)
    assert saved.status_code == 303
    assert store.get_settings().signin_failure_limit == 7
    assert store.get_settings().signin_lock_seconds == 1200
    again = c.get("/settings")
    assert 'value="7"' in again.text
    assert 'value="1200"' in again.text


@pytest.mark.parametrize("name", ["signin_failure_limit", "signin_lock_seconds"])
@pytest.mark.parametrize("value", ["0", "-3"])
def test_value_below_one_is_refused(name: str, value: str) -> None:
    store, c = signed_in()
    before = store.get_settings()
    response = c.post("/settings", data={**VALID, name: value})
    assert response.status_code == 400
    assert store.get_settings() == before
