from dataclasses import replace
from datetime import UTC, datetime, time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cal_mirror.model import Settings
from cal_mirror.ports import FixedClock
from cal_mirror.web.deps import current_user
from cal_mirror.web.page_settings import router

START = Settings(
    main_calendar="main",
    secondary_calendars=("s1", "s2"),
    default_calendar="s1",
    owner_email="steve@example.com",
    two_factor=True,
)

VALID = {
    "sync_interval_minutes": "10",
    "ntfy_server": "https://ntfy.example.com",
    "ntfy_topic": "cal_mirror-1",
    "archive_destinations": "/mnt/usb\n/srv/arch",
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
}


class FakeStore:
    def __init__(self) -> None:
        self.settings = START
        self.saved: list[Settings] = []

    def get_settings(self) -> Settings:
        return self.settings

    def save_settings(self, s: Settings) -> None:
        self.saved.append(s)
        self.settings = s

    def list_calendars(self) -> list:
        return []


def make_client(signed_in: bool = True) -> tuple[TestClient, FakeStore]:
    store = FakeStore()
    app = FastAPI()
    app.state.store = store
    app.state.clock = FixedClock(datetime(2026, 11, 3, tzinfo=UTC))
    app.include_router(router)
    if signed_in:
        app.dependency_overrides[current_user] = lambda: "steve"
    return TestClient(app, follow_redirects=False), store


def post(**changes: str):
    client, store = make_client()
    response = client.post("/settings", data={**VALID, **changes})
    return response, store


def post_without(*names: str):
    client, store = make_client()
    data = {k: v for k, v in VALID.items() if k not in names}
    return client.post("/settings", data=data), store


def assert_rejected(response, store, message: str) -> bool:
    return response.status_code == 400 and message in response.text and store.saved == []


def test_signed_out_get_redirects_to_signin():
    client, _ = make_client(signed_in=False)
    response = client.get("/settings")
    assert (response.status_code, response.headers["location"]) == (303, "/signin")


def test_signed_out_post_redirects_to_signin():
    client, store = make_client(signed_in=False)
    response = client.post("/settings", data=VALID)
    assert (response.status_code, response.headers["location"], store.saved) == (
        303,
        "/signin",
        [],
    )


def test_get_shows_stored_values():
    client, store = make_client()
    store.settings = replace(
        START,
        sync_interval_minutes=7,
        ntfy_topic="mytopic",
        archive_destinations=("/mnt/usb",),
        allowed_hours={2: (time(8, 0), time(16, 30))},
    )
    body = client.get("/settings").text
    assert all(
        text in body
        for text in (
            'value="7"',
            'value="mytopic"',
            "/mnt/usb",
            'name="allowed_hours_2" value="08:00-16:30"',
        )
    )


def test_get_blank_for_missing_weekday():
    client, _ = make_client()
    assert 'name="allowed_hours_0" value=""' in client.get("/settings").text


def test_valid_post_saves_and_redirects():
    response, store = post()
    assert (response.status_code, response.headers["location"], len(store.saved)) == (
        303,
        "/settings",
        1,
    )


def test_round_trip_shows_saved_values():
    client, store = make_client()
    client.post("/settings", data=VALID)
    body = client.get("/settings").text
    assert all(
        text in body
        for text in (
            'value="10"',
            'value="https://ntfy.example.com"',
            'value="cal_mirror-1"',
            "/mnt/usb\n/srv/arch",
            'value="me@example.org"',
            'value="Europe/Berlin"',
            'value="https://cal.example.org"',
            'name="allowed_hours_0" value="09:00-17:30"',
        )
    )


def test_round_trip_keeps_fields_the_page_does_not_own():
    _, store = post()
    saved = store.saved[0]
    assert (
        saved.main_calendar,
        saved.secondary_calendars,
        saved.default_calendar,
        saved.two_factor,
    ) == ("main", ("s1", "s2"), "s1", True)


def test_sync_interval_zero_rejected():
    response, store = post(sync_interval_minutes="0")
    assert assert_rejected(response, store, "Sync interval must be a whole number of minutes")


def test_sync_interval_1441_rejected():
    response, store = post(sync_interval_minutes="1441")
    assert assert_rejected(response, store, "Sync interval must be a whole number of minutes")


def test_sync_interval_decimal_rejected():
    response, store = post(sync_interval_minutes="5.5")
    assert assert_rejected(response, store, "Sync interval must be a whole number of minutes")


def test_sync_interval_text_rejected():
    response, store = post(sync_interval_minutes="abc")
    assert assert_rejected(response, store, "Sync interval must be a whole number of minutes")


def test_sync_interval_one_saves():
    _, store = post(sync_interval_minutes="1")
    assert store.saved[0].sync_interval_minutes == 1


def test_sync_interval_1440_saves():
    _, store = post(sync_interval_minutes="1440")
    assert store.saved[0].sync_interval_minutes == 1440


def test_sync_interval_is_stripped():
    _, store = post(sync_interval_minutes=" 15 ")
    assert store.saved[0].sync_interval_minutes == 15


def test_ntfy_server_ftp_rejected():
    response, store = post(ntfy_server="ftp://x")
    assert assert_rejected(response, store, "ntfy server must be an http or https URL.")


def test_ntfy_server_without_scheme_rejected():
    response, store = post(ntfy_server="ntfy.sh")
    assert assert_rejected(response, store, "ntfy server must be an http or https URL.")


def test_ntfy_server_without_host_rejected():
    response, store = post(ntfy_server="https://")
    assert assert_rejected(response, store, "ntfy server must be an http or https URL.")


def test_ntfy_server_http_with_port_saves():
    _, store = post(ntfy_server="http://10.0.0.5:8080")
    assert store.saved[0].ntfy_server == "http://10.0.0.5:8080"


def test_ntfy_topic_empty_rejected():
    response, store = post(ntfy_topic="")
    assert assert_rejected(response, store, "ntfy topic must be 1 to 64 letters, digits, - or _.")


def test_ntfy_topic_too_long_rejected():
    response, store = post(ntfy_topic="a" * 65)
    assert assert_rejected(response, store, "ntfy topic must be 1 to 64 letters, digits, - or _.")


def test_ntfy_topic_with_space_rejected():
    response, store = post(ntfy_topic="a b")
    assert assert_rejected(response, store, "ntfy topic must be 1 to 64 letters, digits, - or _.")


def test_ntfy_topic_valid_saves():
    _, store = post(ntfy_topic="cal_mirror-1")
    assert store.saved[0].ntfy_topic == "cal_mirror-1"


def test_archive_relative_path_rejected():
    response, store = post(archive_destinations="/mnt/usb\n\nrelative/dir")
    assert assert_rejected(response, store, "Each archive destination must be an absolute path.")


def test_archive_blank_lines_dropped_and_stripped():
    _, store = post(archive_destinations="/mnt/usb\n /srv/arch \n")
    assert store.saved[0].archive_destinations == ("/mnt/usb", "/srv/arch")


def test_archive_empty_allowed():
    _, store = post(archive_destinations="")
    assert store.saved[0].archive_destinations == ()


def test_owner_email_without_at_rejected():
    response, store = post(owner_email="steve.example.com")
    assert assert_rejected(response, store, "Owner email must be an email address.")


def test_owner_email_two_ats_rejected():
    response, store = post(owner_email="a@b@c")
    assert assert_rejected(response, store, "Owner email must be an email address.")


def test_owner_email_missing_local_part_rejected():
    response, store = post(owner_email="@example.com")
    assert assert_rejected(response, store, "Owner email must be an email address.")


def test_owner_email_missing_domain_rejected():
    response, store = post(owner_email="steve@")
    assert assert_rejected(response, store, "Owner email must be an email address.")


def test_owner_email_whitespace_rejected():
    response, store = post(owner_email="st eve@example.com")
    assert assert_rejected(response, store, "Owner email must be an email address.")


def test_time_zone_unknown_rejected():
    response, store = post(time_zone="Mars/Base")
    assert assert_rejected(
        response, store, "Time zone must be a zone name such as America/Chicago."
    )


def test_time_zone_known_saves():
    _, store = post(time_zone="Europe/Berlin")
    assert store.saved[0].time_zone == "Europe/Berlin"


def test_web_url_bad_rejected():
    response, store = post(web_url="cal.example.org")
    assert assert_rejected(response, store, "Web URL must be an http or https URL.")


def test_web_url_http_saves():
    _, store = post(web_url="http://cal.example.org")
    assert store.saved[0].web_url == "http://cal.example.org"


def test_hours_start_after_end_rejected():
    response, store = post(allowed_hours_1="17:00-09:00")
    assert assert_rejected(
        response,
        store,
        "Allowed hours for Tuesday must be HH:MM-HH:MM with start before end.",
    )


def test_hours_equal_start_end_rejected():
    response, store = post(allowed_hours_3="09:00-09:00")
    assert assert_rejected(
        response,
        store,
        "Allowed hours for Thursday must be HH:MM-HH:MM with start before end.",
    )


def test_hours_out_of_range_rejected():
    response, store = post(allowed_hours_1="24:00-25:00")
    assert assert_rejected(response, store, "Allowed hours for Tuesday")


def test_hours_single_digit_hour_rejected():
    response, store = post(allowed_hours_1="9:00-17:00")
    assert assert_rejected(response, store, "Allowed hours for Tuesday")


def test_hours_first_failing_weekday_named():
    response, store = post(allowed_hours_6="bad", allowed_hours_4="bad")
    assert assert_rejected(response, store, "Allowed hours for Friday")


def test_hours_valid_monday_only_saves_mapping():
    _, store = post()
    assert dict(store.saved[0].allowed_hours) == {0: (time(9, 0), time(17, 30))}


def test_checkboxes_absent_save_false():
    _, store = post_without("check_no_link", "check_hours")
    assert (store.saved[0].check_no_link, store.saved[0].check_hours) == (False, False)


def test_checkboxes_present_save_true():
    _, store = post(check_hours="on")
    assert (store.saved[0].check_no_link, store.saved[0].check_hours) == (True, True)


def test_error_page_shows_submitted_values():
    response, _ = post(ntfy_topic="a b", owner_email="x@y.z")
    assert 'value="a b"' in response.text and 'value="x@y.z"' in response.text


def test_only_first_failing_rule_shown():
    response, store = post(sync_interval_minutes="0", ntfy_topic="")
    assert assert_rejected(response, store, "Sync interval must be") and "ntfy topic must" not in (
        response.text
    )


def test_error_message_is_escaped():
    response, _ = post(ntfy_topic="<b>x</b>")
    assert "&lt;b&gt;x&lt;/b&gt;" in response.text
