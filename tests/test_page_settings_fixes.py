from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cal_mirror.model import Settings
from cal_mirror.ports import FixedClock
from cal_mirror.web.deps import current_user
from cal_mirror.web.page_settings import router

START = Settings(
    main_calendar="main",
    secondary_calendars=("s1",),
    default_calendar="s1",
    owner_email="steve@example.com",
)

VALID = {
    "sync_interval_minutes": "10",
    "ntfy_server": "https://ntfy.example.com",
    "ntfy_topic": "cal_mirror-1",
    "archive_destinations": "/mnt/usb",
    "owner_email": "me@example.org",
    "time_zone": "America/Chicago",
    "web_url": "https://cal.example.org",
    "check_no_link": "on",
    "allowed_hours_0": "09:00-17:00",
    "allowed_hours_1": "",
    "allowed_hours_2": "",
    "allowed_hours_3": "",
    "allowed_hours_4": "",
    "allowed_hours_5": "",
    "allowed_hours_6": "",
}

NTFY_URL = "ntfy server must be an http or https URL."
WEB_URL = "Web URL must be an http or https URL."


class FakeStore:
    def __init__(self) -> None:
        self.settings = START
        self.saved: list[Settings] = []

    def get_settings(self) -> Settings:
        return self.settings

    def save_settings(self, s: Settings) -> None:
        self.saved.append(s)
        self.settings = s


def make_client() -> tuple[TestClient, FakeStore]:
    store = FakeStore()
    app = FastAPI()
    app.state.store = store
    app.state.clock = FixedClock(datetime(2026, 10, 24, 12, 0, tzinfo=UTC))
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: "steve"
    return TestClient(app, follow_redirects=False), store


def post(**changes: str):
    client, store = make_client()
    response = client.post("/settings", data={**VALID, **changes})
    return response, store


def test_unbalanced_ntfy_bracket_rejected():
    response, store = post(ntfy_server="https://[")
    assert response.status_code == 400
    assert NTFY_URL in response.text
    assert store.saved == []


def test_unbalanced_web_url_bracket_rejected():
    response, store = post(web_url="https://[")
    assert response.status_code == 400
    assert WEB_URL in response.text
    assert store.saved == []


def test_unclosed_ipv6_ntfy_rejected():
    response, store = post(ntfy_server="http://[::1")
    assert response.status_code == 400
    assert NTFY_URL in response.text
    assert store.saved == []


def test_stray_bracket_ntfy_rejected():
    response, store = post(ntfy_server="http://ntfy]x")
    assert response.status_code == 400
    assert NTFY_URL in response.text
    assert store.saved == []


def test_balanced_ipv6_ntfy_saved():
    response, store = post(ntfy_server="http://[::1]:8080")
    assert (response.status_code, response.headers["location"]) == (303, "/settings")
    assert len(store.saved) == 1
    assert store.saved[0].ntfy_server == "http://[::1]:8080"
