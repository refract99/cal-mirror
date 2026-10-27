import json
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cal_mirror.model import PlacementEntry, Settings, Status
from cal_mirror.ports import FixedClock
from cal_mirror.web.deps import current_user
from cal_mirror.web.page_status import router

SYNC = datetime(2026, 11, 3, 13, 30, tzinfo=UTC)
NOTICE = {
    "source": "main@x/e1",
    "check": "no_call_link",
    "start": "2026-11-03T13:30:00+00:00",
    "title": "Board sync",
}


class FakeStore:
    def __init__(self, status=None, placements=(), meta=None):
        self.status = status or Status()
        self.placements = list(placements)
        self.meta = meta
        self.calls = []

    def get_settings(self):
        self.calls.append(("get_settings",))
        return Settings(time_zone="America/Chicago")

    def get_status(self):
        self.calls.append(("get_status",))
        return self.status

    def list_placements(self, limit=100):
        self.calls.append(("list_placements", limit))
        return self.placements

    def get_meta(self, key):
        self.calls.append(("get_meta", key))
        return self.meta


def make_client(store, signed_in=True):
    app = FastAPI()
    app.state.store = store
    app.state.clock = FixedClock(datetime(2026, 11, 1, tzinfo=UTC))
    app.include_router(router)
    if signed_in:
        app.dependency_overrides[current_user] = lambda: "steve"
    return TestClient(app, follow_redirects=False)


def placement(**kw):
    values = {"id": 1, "at": SYNC, "source": "main@x/e1", "calendar_id": "work"}
    values.update(kw)
    return PlacementEntry(**values)


def test_last_good_sync_winter():
    page = make_client(FakeStore(Status(last_good_sync=SYNC))).get("/status")
    assert page.status_code == 200
    assert "Tue 2026-11-03 7:30 AM CST" in page.text


def test_last_good_sync_summer():
    when = datetime(2026, 7, 6, 22, 5, tzinfo=UTC)
    page = make_client(FakeStore(Status(last_good_sync=when))).get("/status")
    assert "Mon 2026-07-06 5:05 PM CDT" in page.text


def test_last_good_sync_never():
    page = make_client(FakeStore()).get("/status")
    assert "never" in page.text


def test_failure_shown_and_none():
    page = make_client(FakeStore(Status(failure="database down"))).get("/status")
    assert "database down" in page.text
    assert "Current failure: none" in make_client(FakeStore()).get("/status").text


def test_counts_sorted_by_key():
    store = FakeStore(Status(counts={"copies": 3, "blocked": 7}))
    text = make_client(store).get("/status").text
    assert text.index("blocked: 7") < text.index("copies: 3")


def test_no_counts():
    assert "No counts yet." in make_client(FakeStore()).get("/status").text


def test_token_rejected_links_to_google():
    text = make_client(FakeStore(Status(token_ok=False))).get("/status").text
    assert 'href="/google"' in text
    assert "Google token rejected: reconnect" in text


def test_token_ok():
    text = make_client(FakeStore()).get("/status").text
    assert "Google token: ok" in text
    assert "Google token rejected" not in text


def test_placement_row():
    entry = placement(
        id=42,
        title="Standup",
        organizer_email="boss@a.com",
        guest_domains=("a.com", "b.org"),
    )
    text = make_client(FakeStore(placements=[entry])).get("/status").text
    assert 'href="/rules?from_placement=42"' in text
    assert "a.com, b.org" in text
    assert "Standup" in text
    assert "boss@a.com" in text
    assert "work" in text
    assert "Tue 2026-11-03 7:30 AM CST" in text


def test_no_placements():
    assert "No default placements yet." in make_client(FakeStore()).get("/status").text


def test_placement_title_escaped():
    entry = placement(title="<script>alert(1)</script>")
    text = make_client(FakeStore(placements=[entry])).get("/status").text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<script>alert" not in text


def test_notice_title_escaped():
    meta = json.dumps([{**NOTICE, "title": "<script>alert(1)</script>"}])
    text = make_client(FakeStore(meta=meta)).get("/status").text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<script>alert" not in text


def test_notice_query_shows_notice():
    client = make_client(FakeStore(meta=json.dumps([NOTICE])))
    text = client.get("/status?notice=main%40x%2Fe1").text
    section = text[text.index('id="notice"') :]
    section = section[: section.index("</section>")]
    assert "Board sync" in section
    assert "Tue 2026-11-03 7:30 AM CST" in section
    assert "no call link" in section


def test_notice_query_unknown_source():
    client = make_client(FakeStore(meta=json.dumps([NOTICE])))
    text = client.get("/status?notice=other").text
    assert 'id="notice"' in text
    assert "That notice is no longer in the recent list." in text


def test_no_notice_section_without_query():
    text = make_client(FakeStore(meta=json.dumps([NOTICE]))).get("/status").text
    assert 'id="notice"' not in text
    assert "Board sync" in text


def test_unparseable_start_and_other_check_shown_as_given():
    meta = json.dumps([{**NOTICE, "start": "soon", "check": "outside_hours"}, "junk", 5])
    text = make_client(FakeStore(meta=meta)).get("/status").text
    assert "soon" in text
    assert "outside hours" in text


def test_bad_meta_reads_as_empty():
    for meta in ("not json", "{}", None):
        page = make_client(FakeStore(meta=meta)).get("/status")
        assert page.status_code == 200
        assert "No recent notices." in page.text


def test_list_placements_called_with_50():
    store = FakeStore()
    make_client(store).get("/status")
    assert ("list_placements", 50) in store.calls


def test_signed_out_redirects_to_signin():
    page = make_client(FakeStore(), signed_in=False).get("/status")
    assert page.status_code == 303
    assert page.headers["location"] == "/signin"
