import dataclasses
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cal_mirror.model import CalendarInfo, Settings
from cal_mirror.ports import FixedClock
from cal_mirror.web.deps import current_user
from cal_mirror.web.page_calendars import router

CALENDARS = [
    CalendarInfo("a", "Alpha", "owner"),
    CalendarInfo("b", "Beta", "owner"),
    CalendarInfo("c", "Gamma", "reader"),
    CalendarInfo("d", "Delta", "owner"),
]


class FakeStore:
    def __init__(self, calendars, settings=None):
        self.calendars = calendars
        self.settings = settings or Settings()
        self.saved = []

    def get_settings(self):
        return self.settings

    def save_settings(self, s):
        self.saved.append(s)
        self.settings = s

    def list_calendars(self):
        return list(self.calendars)


def make_client(store, signed_in=True):
    app = FastAPI()
    app.state.store = store
    app.state.clock = FixedClock(datetime(2026, 11, 3, tzinfo=UTC))
    app.include_router(router)
    if signed_in:
        app.dependency_overrides[current_user] = lambda: "steve"
    return TestClient(app, follow_redirects=False)


def post(store, data):
    return make_client(store).post("/calendars", data=data)


def test_get_lists_only_owned_calendars():
    page = make_client(FakeStore(CALENDARS)).get("/calendars").text
    assert [name for name in ("Alpha", "Beta", "Gamma", "Delta") if name in page] == [
        "Alpha",
        "Beta",
        "Delta",
    ]


def test_get_lists_in_store_order():
    page = make_client(FakeStore(CALENDARS)).get("/calendars").text
    assert page.index("Alpha") < page.index("Beta") < page.index("Delta")


def test_get_blank_summary_shows_id():
    page = make_client(FakeStore([CalendarInfo("zed@x", "", "owner")])).get("/calendars").text
    assert "<td>zed@x</td>" in page


def test_get_has_main_secondary_default_inputs():
    page = make_client(FakeStore(CALENDARS)).get("/calendars").text
    assert 'type="radio" name="main" value="a"' in page
    assert 'type="checkbox" name="secondary" value="a"' in page
    assert 'type="radio" name="default" value="a"' in page


def test_get_checks_current_settings():
    settings = Settings(main_calendar="a", secondary_calendars=("b",), default_calendar="b")
    page = make_client(FakeStore(CALENDARS, settings)).get("/calendars").text
    assert 'name="main" value="a" checked' in page
    assert 'name="secondary" value="b" checked' in page
    assert 'name="default" value="b" checked' in page


def test_get_escapes_summary():
    store = FakeStore([CalendarInfo("a", "<b>x</b>", "owner")])
    assert "&lt;b&gt;x&lt;/b&gt;" in make_client(store).get("/calendars").text


def test_get_without_owned_calendar_shows_message():
    store = FakeStore([CalendarInfo("c", "Gamma", "reader")])
    page = make_client(store).get("/calendars").text
    assert "The worker has not read your calendars yet. Connect Google and wait one sync." in page


def test_get_without_user_redirects_to_signin():
    response = make_client(FakeStore(CALENDARS), signed_in=False).get("/calendars")
    assert (response.status_code, response.headers["location"]) == (303, "/signin")


def test_post_without_user_redirects_to_signin():
    store = FakeStore(CALENDARS)
    response = make_client(store, signed_in=False).post("/calendars", data={"main": "a"})
    assert (response.status_code, response.headers["location"]) == (303, "/signin")


def test_rule_1_no_main():
    store = FakeStore(CALENDARS)
    response = post(store, {"secondary": "b", "default": "b"})
    assert response.status_code == 400
    assert "Pick a main calendar." in response.text
    assert store.saved == []


def test_rule_2_no_secondary():
    store = FakeStore(CALENDARS)
    response = post(store, {"main": "a"})
    assert response.status_code == 400
    assert "Pick at least one secondary calendar." in response.text
    assert store.saved == []


def test_rule_3_not_owned_main():
    store = FakeStore(CALENDARS)
    response = post(store, {"main": "c", "secondary": "a"})
    assert response.status_code == 400
    assert "Pick only calendars you own." in response.text
    assert store.saved == []


def test_rule_3_not_owned_secondary():
    store = FakeStore(CALENDARS)
    response = post(store, {"main": "a", "secondary": ["b", "c"], "default": "b"})
    assert response.status_code == 400
    assert "Pick only calendars you own." in response.text
    assert store.saved == []


def test_rule_3_not_owned_default():
    store = FakeStore(CALENDARS)
    response = post(store, {"main": "a", "secondary": "b", "default": "c"})
    assert response.status_code == 400
    assert "Pick only calendars you own." in response.text
    assert store.saved == []


def test_rule_4_main_among_secondaries():
    store = FakeStore(CALENDARS)
    response = post(store, {"main": "a", "secondary": ["a", "b"], "default": "b"})
    assert response.status_code == 400
    assert "The main calendar cannot also be a secondary calendar." in response.text
    assert store.saved == []


def test_rule_5_default_not_a_secondary():
    store = FakeStore(CALENDARS)
    response = post(store, {"main": "a", "secondary": "b", "default": "a"})
    assert response.status_code == 400
    assert "The default calendar must be one of the secondary calendars." in response.text
    assert store.saved == []


def test_rule_5_default_blank():
    store = FakeStore(CALENDARS)
    response = post(store, {"main": "a", "secondary": "b"})
    assert response.status_code == 400
    assert "The default calendar must be one of the secondary calendars." in response.text
    assert store.saved == []


def test_only_first_failing_rule_is_shown():
    response = post(FakeStore(CALENDARS), {"main": "c"})
    assert "Pick at least one secondary calendar." in response.text
    assert "Pick only calendars you own." not in response.text


def test_error_page_keeps_submitted_values():
    response = post(FakeStore(CALENDARS), {"main": "a", "secondary": ["a", "b"], "default": "b"})
    assert 'name="main" value="a" checked' in response.text
    assert 'name="secondary" value="b" checked' in response.text
    assert 'name="default" value="b" checked' in response.text


def test_valid_post_redirects_and_keeps_first_place_of_repeats():
    store = FakeStore(CALENDARS)
    data = {"main": "a", "secondary": ["d", "b", "d"], "default": "b"}
    response = post(store, data)
    assert (response.status_code, response.headers["location"]) == (303, "/calendars")
    assert store.settings.secondary_calendars == ("d", "b")


def test_round_trip_shows_saved_values_and_keeps_other_fields():
    before = Settings(owner_email="s@x.org", sync_interval_minutes=9, two_factor=True)
    store = FakeStore(CALENDARS, before)
    client = make_client(store)
    client.post("/calendars", data={"main": "a", "secondary": ["b", "d"], "default": "d"})
    page = client.get("/calendars").text
    assert 'name="main" value="a" checked' in page
    assert 'name="secondary" value="b" checked' in page
    assert 'name="secondary" value="d" checked' in page
    assert 'name="default" value="d" checked' in page
    expected = dataclasses.replace(
        before, main_calendar="a", secondary_calendars=("b", "d"), default_calendar="d"
    )
    assert len(store.saved) == 1
    assert store.saved[0] == expected
