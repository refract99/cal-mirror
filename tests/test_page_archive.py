import dataclasses
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cal_mirror.model import (
    REQ_ARCHIVE,
    REQ_CANCELLED,
    REQ_CONFIRMED,
    REQ_DONE,
    REQ_FAILED,
    REQ_PENDING,
    REQ_READY,
    REQ_RULES,
    Request,
    Settings,
)
from cal_mirror.ports import FixedClock
from cal_mirror.web.deps import current_user
from cal_mirror.web.page_archive import router

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
DESTINATIONS = ("/mnt/usb", "/mnt/nas")
COUNTS = {"a@x/2023": 3, "a@x/2024": 2, "b@x/2024": 5}


class FakeStore:
    def __init__(self, requests=(), destinations=DESTINATIONS):
        self.requests = list(requests)
        self.settings = Settings(archive_destinations=destinations)

    def get_settings(self):
        return self.settings

    def add_request(self, kind, params):
        new_id = len(self.requests) + 1
        self.requests.append(Request(id=new_id, kind=kind, params=params, created=NOW))
        return new_id

    def get_request(self, request_id):
        for r in self.requests:
            if r.id == request_id:
                return r
        return None

    def update_request(self, req):
        self.requests = [req if r.id == req.id else r for r in self.requests]

    def list_requests(self, state=None):
        return [r for r in self.requests if state is None or r.state == state]


def make_client(store, signed_in=True):
    app = FastAPI()
    app.state.store = store
    app.state.clock = FixedClock(NOW)
    app.include_router(router)
    if signed_in:
        app.dependency_overrides[current_user] = lambda: "steve"
    return TestClient(app)


def req(id=1, state=REQ_PENDING, result=None, kind=REQ_ARCHIVE):
    return Request(
        id=id,
        kind=kind,
        state=state,
        params={"cutoff": "2026-01-01", "destination": "/mnt/usb"},
        result=result or {},
        created=NOW,
    )


def start(client, cutoff="2026-10-23", destination="/mnt/usb"):
    return client.post(
        "/archive",
        data={"cutoff": cutoff, "destination": destination},
        follow_redirects=False,
    )


def test_start_adds_pending_request_and_redirects():
    store = FakeStore()
    r = start(make_client(store))
    assert r.status_code == 303
    assert r.headers["location"] == "/archive/1"
    assert len(store.requests) == 1
    added = store.requests[0]
    assert added.kind == REQ_ARCHIVE
    assert added.state == REQ_PENDING
    assert added.params == {"cutoff": "2026-10-23", "destination": "/mnt/usb"}


def test_start_refuses_bad_cutoff():
    for cutoff in ("2026-10-24", "2026-11-01", "2026-13-01", ""):
        store = FakeStore()
        r = start(make_client(store), cutoff=cutoff)
        assert r.status_code == 400
        assert "The cutoff must be a date before today." in r.text
        assert store.requests == []


def test_start_refuses_unknown_destination():
    store = FakeStore()
    r = start(make_client(store), destination="/mnt/other")
    assert r.status_code == 400
    assert "Pick one of the archive destinations." in r.text
    assert store.requests == []


def test_start_refused_while_in_progress():
    for state in (REQ_PENDING, REQ_READY, REQ_CONFIRMED):
        store = FakeStore([req(state=state)])
        r = start(make_client(store))
        assert r.status_code == 409
        assert "An archive is already in progress." in r.text
        assert len(store.requests) == 1


def test_start_allowed_after_finished_requests():
    store = FakeStore([req(1, REQ_DONE), req(2, REQ_FAILED), req(3, REQ_CANCELLED)])
    r = start(make_client(store))
    assert r.status_code == 303
    assert r.headers["location"] == "/archive/4"


def test_start_ignores_in_progress_rules_request():
    store = FakeStore([req(1, REQ_READY, kind=REQ_RULES)])
    assert start(make_client(store)).status_code == 303


def test_post_leaves_nothing_confirmed():
    store = FakeStore()
    start(make_client(store))
    assert all(r.state != REQ_CONFIRMED for r in store.requests)


def test_no_destination_message_and_no_form():
    r = make_client(FakeStore(destinations=())).get("/archive")
    assert r.status_code == 200
    assert "Add an archive destination in" in r.text
    assert 'href="/settings"' in r.text
    assert "<form" not in r.text


def test_form_has_date_input_and_options_in_order():
    r = make_client(FakeStore()).get("/archive")
    assert '<form method="post" action="/archive">' in r.text
    assert 'type="date"' in r.text
    assert 'name="cutoff"' in r.text
    assert 'name="destination"' in r.text
    assert r.text.index("/mnt/usb</option>") < r.text.index("/mnt/nas</option>")


def test_list_shows_archive_requests_newest_first_max_20():
    store = FakeStore([req(i, REQ_DONE) for i in range(1, 26)] + [req(26, kind=REQ_RULES)])
    text = make_client(store).get("/archive").text
    assert 'href="/archive/25"' in text
    assert 'href="/archive/6"' in text
    assert 'href="/archive/5"' not in text
    assert 'href="/archive/26"' not in text
    assert text.index('href="/archive/25"') < text.index('href="/archive/24"')
    assert "2026-01-01" in text
    assert "done" in text


def test_pending_page_refreshes():
    r = make_client(FakeStore([req()])).get("/archive/1")
    assert r.status_code == 200
    assert "Writing the archive and counting..." in r.text
    assert "2026-01-01" in r.text
    assert "/mnt/usb" in r.text
    assert r.headers["Refresh"] == "5"


def test_ready_ok_shows_table_and_forms():
    store = FakeStore([req(state=REQ_READY, result={"counts": COUNTS, "check": "ok"})])
    r = make_client(store).get("/archive/1")
    text = r.text
    assert "Refresh" not in r.headers
    for cell in ("a@x total", "b@x total", "All calendars total", "2023", "2024"):
        assert cell in text
    assert text.index("a@x total") < text.index("b@x total") < text.index("All calendars total")
    assert "<td>10</td>" in text
    assert "<td>5</td>" in text
    assert "<td>3</td>" in text
    assert 'action="/archive/1/confirm"' in text
    assert "Confirm delete" in text
    assert 'action="/archive/1/cancel"' in text
    assert "Cancel" in text


def test_ready_without_counts():
    store = FakeStore([req(state=REQ_READY, result={"check": "ok"})])
    assert "No events before the cutoff." in make_client(store).get("/archive/1").text


def test_ready_failed_check_has_only_cancel():
    store = FakeStore([req(state=REQ_READY, result={"counts": COUNTS, "check": "failed"})])
    text = make_client(store).get("/archive/1").text
    assert "Archive check failed: nothing will be deleted." in text
    assert "Confirm delete" not in text
    assert "/archive/1/confirm" not in text
    assert 'action="/archive/1/cancel"' in text


def test_confirm_refused_when_check_failed():
    store = FakeStore([req(state=REQ_READY, result={"check": "failed"})])
    r = make_client(store).post("/archive/1/confirm", follow_redirects=False)
    assert r.status_code == 409
    assert store.requests[0].state == REQ_READY


def test_confirmed_page_refreshes():
    r = make_client(FakeStore([req(state=REQ_CONFIRMED)])).get("/archive/1")
    assert "Deleting..." in r.text
    assert r.headers["Refresh"] == "5"


def test_done_page_lists_result_except_counts_and_check():
    result = {"counts": COUNTS, "check": "ok", "deleted": 10, "archived": 12}
    r = make_client(FakeStore([req(state=REQ_DONE, result=result)])).get("/archive/1")
    assert "Archive finished." in r.text
    assert r.text.index("archived") < r.text.index("deleted")
    assert "a@x/2023" not in r.text
    assert "check" not in r.text
    assert "Refresh" not in r.headers


def test_failed_page_shows_error():
    store = FakeStore([req(state=REQ_FAILED, result={"error": "disk full"})])
    text = make_client(store).get("/archive/1").text
    assert "Archive failed." in text
    assert "disk full" in text


def test_failed_page_without_error():
    text = make_client(FakeStore([req(state=REQ_FAILED)])).get("/archive/1").text
    assert "Archive failed." in text


def test_cancelled_page():
    text = make_client(FakeStore([req(state=REQ_CANCELLED)])).get("/archive/1").text
    assert "Cancelled. Nothing was deleted." in text


def test_page_escapes_values():
    store = FakeStore([req(state=REQ_FAILED, result={"error": "<script>x</script>"})])
    text = make_client(store).get("/archive/1").text
    assert "<script>x" not in text


def test_missing_and_wrong_kind_are_404():
    client = make_client(FakeStore([req(2, kind=REQ_RULES)]))
    for path in ("/archive/99", "/archive/2"):
        assert client.get(path).status_code == 404
        assert client.post(path + "/confirm", follow_redirects=False).status_code == 404
        assert client.post(path + "/cancel", follow_redirects=False).status_code == 404


def test_confirm_ready_ok_sets_confirmed():
    store = FakeStore([req(state=REQ_READY, result={"counts": COUNTS, "check": "ok"})])
    r = make_client(store).post("/archive/1/confirm", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/archive/1"
    assert store.requests[0].state == REQ_CONFIRMED
    assert store.requests[0].result == {"counts": COUNTS, "check": "ok"}


def test_confirm_other_states_refused():
    for state in (REQ_PENDING, REQ_CONFIRMED, REQ_DONE, REQ_CANCELLED):
        store = FakeStore([req(state=state, result={"check": "ok"})])
        r = make_client(store).post("/archive/1/confirm", follow_redirects=False)
        assert r.status_code == 409
        assert store.requests[0].state == state


def test_cancel_pending_and_ready():
    for state in (REQ_PENDING, REQ_READY):
        store = FakeStore([req(state=state)])
        r = make_client(store).post("/archive/1/cancel", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/archive/1"
        assert store.requests[0].state == REQ_CANCELLED


def test_cancel_confirmed_and_done_refused():
    for state in (REQ_CONFIRMED, REQ_DONE):
        store = FakeStore([req(state=state)])
        r = make_client(store).post("/archive/1/cancel", follow_redirects=False)
        assert r.status_code == 409
        assert store.requests[0].state == state


def test_signed_out_redirects_to_signin():
    r = make_client(FakeStore(), signed_in=False).get("/archive", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/signin"


def test_fake_request_is_frozen_copy():
    original = req()
    assert dataclasses.replace(original, state=REQ_READY).state == REQ_READY
    assert original.state == REQ_PENDING
